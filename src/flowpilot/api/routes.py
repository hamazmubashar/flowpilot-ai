"""HTTP routes. Business rules live in services/domain; this module only translates HTTP."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from flowpilot.api.deps import Actor, Conn, require_api_key
from flowpilot.api.schemas import (
    AuditEntry,
    ErrorResponse,
    Health,
    LeadDetail,
    LeadPage,
    LeadSubmission,
    LeadSummary,
    ScoreSummary,
)
from flowpilot.db import repository as repo
from flowpilot.db.connection import connect
from flowpilot.db.migrate import pending
from flowpilot.domain.models import LeadIn, LeadStatus, Tier
from flowpilot.services.intake import submit_lead

IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:-]{1,255}$"

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
