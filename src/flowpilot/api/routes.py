"""HTTP routes. Business rules live in services/domain; this module only translates HTTP."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)

from flowpilot.api.deps import Actor, Conn, Reviewer, require_api_key
from flowpilot.api.schemas import (
    ApprovalOut,
    AssessmentOut,
    AuditEntry,
    DraftOut,
    ErrorResponse,
    Health,
    LeadDetail,
    LeadPage,
    LeadSubmission,
    LeadSummary,
    LLMRunInfo,
    OutboxEntry,
    OutboxOut,
    OutboxPage,
    ReviewerOut,
    ScoreSummary,
)
from flowpilot.db import repository as repo
from flowpilot.db.connection import connect
from flowpilot.db.migrate import pending
from flowpilot.domain.models import DraftDecisionIn, LeadIn, LeadStatus, Tier
from flowpilot.services.assist import draft_reply, qualify_lead
from flowpilot.services.intake import submit_lead
from flowpilot.services.review import decide_draft

IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:-]{1,255}$"
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_KEY_PATTERN)
]

public = APIRouter()
protected = APIRouter(
    dependencies=[Depends(require_api_key)],
    responses={401: {"model": ErrorResponse, "description": "Missing or invalid API key"}},
)


@public.get("/health", response_model=Health, responses={503: {"model": Health}})
def health(request: Request, response: Response) -> Health:
    try:
        conn = connect(request.app.state.settings.db_path)
        try:
            db_state = "ok" if not pending(conn) else "migrations_pending"
        finally:
            conn.close()
    except Exception:  # health must report, never raise
        db_state = "unavailable"
    if db_state != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="degraded", database=db_state)
    return Health(status="ok", database="ok")


def _not_found(lead_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "lead_not_found", "message": f"lead {lead_id} does not exist"},
    )


def _lead_detail(conn: sqlite3.Connection, lead_id: int) -> LeadDetail:
    row = repo.get_lead(conn, lead_id)
    if row is None:
        raise _not_found(lead_id)
    return LeadDetail(**dict(row), score=repo.get_latest_score(conn, lead_id))


@protected.post(
    "/leads",
    response_model=LeadSubmission,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": LeadSubmission, "description": "Duplicate lead; existing record returned"},
        422: {"description": "Validation error or idempotency key reused with another payload"},
    },
)
def create_lead(
    lead: LeadIn,
    request: Request,
    response: Response,
    conn: Conn,
    actor: Actor,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_KEY_PATTERN)
    ] = None,
) -> LeadSubmission:
    result = submit_lead(
        conn,
        lead,
        actor=actor,
        clock=request.app.state.clock,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    response.status_code = result.status_code
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return LeadSubmission(
        lead=_lead_detail(conn, result.lead_id),
        duplicate=result.duplicate,
        replayed=result.replayed,
    )


@protected.get("/leads", response_model=LeadPage)
def list_leads(
    conn: Conn,
    status_filter: Annotated[LeadStatus | None, Query(alias="status")] = None,
    tier: Tier | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LeadPage:
    rows, total = repo.list_leads(
        conn,
        status=status_filter.value if status_filter else None,
        tier=tier.value if tier else None,
        limit=limit,
        offset=offset,
    )
    items = []
    for row in rows:
        data = dict(row)
        score_total = data.pop("score_total")
        score_tier = data.pop("score_tier")
        score_route = data.pop("score_route")
        score = (
            None
            if score_total is None
            else ScoreSummary(total=score_total, tier=score_tier, route=score_route)
        )
        items.append(LeadSummary(**data, score=score))
    return LeadPage(items=items, total=total, limit=limit, offset=offset)


@protected.get(
    "/leads/{lead_id}", response_model=LeadDetail, responses={404: {"model": ErrorResponse}}
)
def get_lead(lead_id: int, conn: Conn) -> LeadDetail:
    return _lead_detail(conn, lead_id)


@protected.get(
    "/leads/{lead_id}/audit",
    response_model=list[AuditEntry],
    responses={404: {"model": ErrorResponse}},
)
def get_lead_audit(lead_id: int, conn: Conn) -> list[AuditEntry]:
    if repo.get_lead(conn, lead_id) is None:
        raise _not_found(lead_id)
    return [AuditEntry(**entry) for entry in repo.list_audit(conn, "lead", lead_id)]


# --- M2: advisory qualification and drafting -------------------------------------------------


def _assessment_out(data: dict) -> AssessmentOut:
    run = LLMRunInfo(
        model=data.pop("model"),
        prompt_version=data.pop("prompt_version"),
        outcome=data.pop("outcome"),
        fallback_reason=data.pop("fallback_reason"),
        latency_ms=data.pop("latency_ms"),
    )
    return AssessmentOut(**data, run=run)


def _replay_header(response: Response, replayed: bool) -> None:
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"


@protected.post(
    "/leads/{lead_id}/qualification",
    response_model=AssessmentOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}, 422: {"description": "Idempotency key reused"}},
)
def create_qualification(
    lead_id: int,
    request: Request,
    response: Response,
    conn: Conn,
    actor: Actor,
    idempotency_key: IdempotencyKey = None,
) -> AssessmentOut:
    result = qualify_lead(
        conn,
        lead_id,
        llm=request.app.state.llm,
        actor=actor,
        clock=request.app.state.clock,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    _replay_header(response, result.replayed)
    return _assessment_out(repo.get_assessment(conn, result.resource_id))


@protected.get(
    "/leads/{lead_id}/qualification",
    response_model=AssessmentOut,
    responses={404: {"model": ErrorResponse}},
)
def get_qualification(lead_id: int, conn: Conn) -> AssessmentOut:
    if repo.get_lead(conn, lead_id) is None:
        raise _not_found(lead_id)
    latest = repo.get_latest_assessment(conn, lead_id)
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "assessment_not_found", "message": f"lead {lead_id} not qualified"},
        )
    return _assessment_out(latest)


@protected.post(
    "/leads/{lead_id}/drafts",
    response_model=DraftOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "Lead status does not allow drafting"},
        422: {"description": "Idempotency key reused"},
    },
)
def create_draft(
    lead_id: int,
    request: Request,
    response: Response,
    conn: Conn,
    actor: Actor,
    idempotency_key: IdempotencyKey = None,
) -> DraftOut:
    result = draft_reply(
        conn,
        lead_id,
        llm=request.app.state.llm,
        actor=actor,
        clock=request.app.state.clock,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    _replay_header(response, result.replayed)
    return DraftOut(**dict(repo.get_draft(conn, result.resource_id)))


@protected.get(
    "/leads/{lead_id}/drafts",
    response_model=list[DraftOut],
    responses={404: {"model": ErrorResponse}},
)
def get_drafts(lead_id: int, conn: Conn) -> list[DraftOut]:
    if repo.get_lead(conn, lead_id) is None:
        raise _not_found(lead_id)
    return [DraftOut(**dict(row)) for row in repo.list_drafts(conn, lead_id)]


# --- M4: human review --------------------------------------------------------------------------


def _approval_out(conn: sqlite3.Connection, approval_id: int) -> ApprovalOut:
    approval = repo.get_approval(conn, approval_id)
    draft = repo.get_draft(conn, approval["draft_id"])
    outbox = repo.get_outbox_for_approval(conn, approval_id)
    return ApprovalOut(
        id=approval["id"],
        lead_id=draft["lead_id"],
        draft_id=draft["id"],
        version=draft["version"],
        decision=approval["decision"],
        actor_type=approval["actor_type"],
        reviewer_id=approval["reviewer_id"],
        decided_by=approval["decided_by"],
        reason=approval["reason"],
        decided_at=approval["decided_at"],
        outbox=None
        if outbox is None
        else OutboxOut(**{k: outbox[k] for k in OutboxOut.model_fields}),
    )


@protected.get("/outbox", response_model=OutboxPage)
def list_outbox(
    conn: Conn,
    status_filter: Annotated[
        Literal["pending", "sent", "failed"] | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OutboxPage:
    """Simulated outbox entries, newest first. Read-only: nothing is written, audited or sent."""
    rows, total = repo.list_outbox(conn, status=status_filter, limit=limit, offset=offset)
    return OutboxPage(
        items=[OutboxEntry(**dict(row)) for row in rows], total=total, limit=limit, offset=offset
    )


@protected.get(
    "/reviewers/me",
    response_model=ReviewerOut,
    responses={
        403: {"model": ErrorResponse, "description": "Missing, unknown or revoked reviewer token"}
    },
)
def get_current_reviewer(reviewer: Reviewer) -> ReviewerOut:
    """The active reviewer holding `X-Reviewer-Token` (M5 login check). Read-only: it verifies
    the token with the same check as review decisions and writes nothing."""
    return ReviewerOut(id=reviewer.id, name=reviewer.name)


@protected.post(
    "/leads/{lead_id}/drafts/{version}/approval",
    response_model=ApprovalOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"model": ErrorResponse, "description": "Missing, unknown or revoked reviewer token"},
        404: {"model": ErrorResponse},
        409: {
            "model": ErrorResponse,
            "description": "Already decided, superseded, or wrong status",
        },
        422: {"description": "Validation error or idempotency key reused"},
    },
)
def decide(
    lead_id: int,
    version: Annotated[int, Path(ge=1)],
    body: DraftDecisionIn,
    request: Request,
    response: Response,
    conn: Conn,
    reviewer: Reviewer,
    idempotency_key: IdempotencyKey = None,
) -> ApprovalOut:
    """Record a human reviewer's approval or rejection of one exact draft version. Approval
    enqueues one simulated outbox entry; nothing is sent."""
    result = decide_draft(
        conn,
        lead_id,
        version,
        body,
        reviewer=reviewer,
        clock=request.app.state.clock,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    _replay_header(response, result.replayed)
    return _approval_out(conn, result.approval_id)
