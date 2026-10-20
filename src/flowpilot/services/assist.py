"""Advisory lead qualification and reply drafting with deterministic fallback.

The model is called outside any database transaction; results are written in one short
`BEGIN IMMEDIATE` transaction. Nothing here writes lead_scores (drafting only reads the route),
and nothing reads or writes approvals or outbox: LLM output can only add advisory rows and move
a lead from `scored` to `drafted`.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from flowpilot.db import repository as repo
from flowpilot.db.connection import transaction
from flowpilot.domain.fallbacks import rules_assessment, template_draft
from flowpilot.domain.models import (
    Assessment,
    AssessmentFlag,
    AssessmentSource,
    DraftContent,
    DraftGenerator,
    LeadStatus,
    ServiceLine,
    Timeline,
)
from flowpilot.llm import prompts
from flowpilot.llm.client import LLMClient, LLMError
from flowpilot.llm.guards import check_draft, check_summary, detect_injection
from flowpilot.llm.schemas import AssessmentOutput, DraftOutput, json_schema
from flowpilot.services.intake import Clock, IdempotencyKeyReused, iso, utc_now

QUALIFY_SCOPE = "POST /leads/{id}/qualification"
DRAFT_SCOPE = "POST /leads/{id}/drafts"
DRAFTABLE = frozenset({LeadStatus.SCORED, LeadStatus.DRAFTED})
RAW_OUTPUT_LIMIT = 4096
ERROR_LIMIT = 500


class LeadNotFound(Exception):
    pass


class LeadNotDraftable(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class _GuardRejected(Exception):
    def __init__(self, violations: list[str]) -> None:
        super().__init__(",".join(violations))


@dataclass(frozen=True)
class AssistResult:
    resource_id: int
    replayed: bool


@dataclass
class _Attempt:
    fallback_reason: str | None = None
    error: str | None = None
    raw_output: str | None = None
    latency_ms: int | None = None


def _sha256(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _replay(
    conn: sqlite3.Connection, scope: str, key: str | None, request_hash: str
) -> AssistResult | None:
    if key is None:
        return None
    prior = repo.get_idempotency(conn, scope, key)
    if prior is None:
        return None
    if prior["request_hash"] != request_hash:
        raise IdempotencyKeyReused(key)
    return AssistResult(resource_id=int(prior["resource_id"]), replayed=True)


def _call_model[T](
    llm: LLMClient,
    *,
    system: str,
    user: str,
    schema: dict,
    parse: Callable[[str], T],
    timer: Callable[[], float],
) -> tuple[T | None, _Attempt]:
    attempt = _Attempt()
    started = timer()
    try:
        raw = llm.chat_json(system=system, user=user, schema=schema)
    except LLMError as exc:
        attempt.fallback_reason = exc.reason
        attempt.error = str(exc)[:ERROR_LIMIT]
        return None, attempt
    finally:
        attempt.latency_ms = max(0, round((timer() - started) * 1000))
    attempt.raw_output = raw[:RAW_OUTPUT_LIMIT]
    try:
        return parse(raw), attempt
    except ValidationError as exc:
        attempt.fallback_reason = "invalid_output"
        attempt.error = f"{exc.error_count()} validation error(s)"[:ERROR_LIMIT]
    except _GuardRejected as exc:
        attempt.fallback_reason = "guard_rejected"
        attempt.error = str(exc)[:ERROR_LIMIT]
    return None, attempt


def _parse_assessment(raw: str) -> Assessment:
    out = AssessmentOutput.model_validate_json(raw)
    violations = check_summary(out.summary)
    if violations:
        raise _GuardRejected(violations)
    return Assessment(
        source=AssessmentSource.LLM,
        summary=out.summary,
        service_line=out.service_line,
        urgency=out.urgency,
        flags=[AssessmentFlag(flag.value) for flag in out.flags],
    )


def _parse_draft(raw: str) -> DraftContent:
    out = DraftOutput.model_validate_json(raw)
    violations = check_draft(out.subject, out.body)
    if violations:
        raise _GuardRejected(violations)
    return DraftContent(subject=out.subject, body=out.body)


def _injection(lead: dict) -> list[str]:
    return detect_injection(lead["company_name"], lead["contact_name"], lead["message"])


def qualify_lead(
    conn: sqlite3.Connection,
    lead_id: int,
    *,
    llm: LLMClient | None,
    actor: str,
    clock: Clock = utc_now,
    timer: Callable[[], float] = time.monotonic,
    idempotency_key: str | None = None,
    request_id: str | None = None,
) -> AssistResult:
    request_hash = _sha256(QUALIFY_SCOPE, str(lead_id))
    replay = _replay(conn, QUALIFY_SCOPE, idempotency_key, request_hash)
    if replay is not None:
        return replay

    row = repo.get_lead(conn, lead_id)
    if row is None:
        raise LeadNotFound(lead_id)
    lead = dict(row)
    user = prompts.user_message(prompts.lead_data(lead))
    model = llm.model if llm is not None else None
    injection = _injection(lead)

    assessment: Assessment | None = None
    attempt = _Attempt()
    if llm is None:
        attempt.fallback_reason = "disabled"
    elif injection:
        attempt.fallback_reason = "injection_suspected"
        attempt.error = ",".join(injection)
    else:
        assessment, attempt = _call_model(
            llm,
            system=prompts.ASSESSMENT_SYSTEM,
            user=user,
            schema=json_schema(AssessmentOutput),
            parse=_parse_assessment,
            timer=timer,
        )
    if assessment is None:
        assessment = rules_assessment(
            company_size=lead["company_size"],
            industry=lead["industry"],
            service_interest=ServiceLine(lead["service_interest"]),
            timeline=Timeline(lead["timeline"]),
            message=lead["message"],
            injection_suspected=bool(injection),
        )

    now = iso(clock())
    with transaction(conn):
        raced = _replay(conn, QUALIFY_SCOPE, idempotency_key, request_hash)
        if raced is not None:
            return raced
        run_id = repo.insert_llm_run(
            conn,
            lead_id=lead_id,
            task="qualification",
            mode="ollama" if llm is not None else "off",
            model=model,
            prompt_version=prompts.PROMPT_VERSION,
            fallback_reason=attempt.fallback_reason,
            error=attempt.error,
            latency_ms=attempt.latency_ms,
            input_sha256=_sha256(
                prompts.PROMPT_VERSION, model or "", prompts.ASSESSMENT_SYSTEM, user
            ),
            raw_output=attempt.raw_output,
            now=now,
        )
        assessment_id = repo.insert_assessment(
            conn, lead_id=lead_id, llm_run_id=run_id, assessment=assessment, now=now
        )
        repo.insert_audit(
            conn,
            occurred_at=now,
            actor=actor,
            action="lead.qualified",
            entity_type="lead",
            entity_id=lead_id,
            after={
                "assessment_id": assessment_id,
                "source": assessment.source.value,
                "urgency": assessment.urgency.value,
                "flags": [f.value for f in assessment.flags],
                "llm_run_id": run_id,
                "model": model,
                "prompt_version": prompts.PROMPT_VERSION,
                "fallback_reason": attempt.fallback_reason,
            },
            request_id=request_id,
        )
        if idempotency_key is not None:
            repo.insert_idempotency(
                conn,
                scope=QUALIFY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash,
                status_code=201,
                resource_type="lead_assessment",
                resource_id=assessment_id,
                now=now,
            )
    return AssistResult(resource_id=assessment_id, replayed=False)


def draft_reply(
    conn: sqlite3.Connection,
    lead_id: int,
    *,
    llm: LLMClient | None,
    actor: str,
    clock: Clock = utc_now,
    timer: Callable[[], float] = time.monotonic,
    idempotency_key: str | None = None,
    request_id: str | None = None,
) -> AssistResult:
    request_hash = _sha256(DRAFT_SCOPE, str(lead_id))
    replay = _replay(conn, DRAFT_SCOPE, idempotency_key, request_hash)
    if replay is not None:
        return replay

    row = repo.get_lead(conn, lead_id)
    if row is None:
        raise LeadNotFound(lead_id)
    lead = dict(row)
    score = repo.get_latest_score(conn, lead_id)
    if LeadStatus(lead["status"]) not in DRAFTABLE or score is None:
        raise LeadNotDraftable(lead["status"])

    user = prompts.user_message(prompts.lead_data(lead, route=score.route.value))
    model = llm.model if llm is not None else None
    injection = _injection(lead)

    content: DraftContent | None = None
    attempt = _Attempt()
    if llm is None:
        attempt.fallback_reason = "disabled"
    elif injection:
        attempt.fallback_reason = "injection_suspected"
        attempt.error = ",".join(injection)
    else:
        content, attempt = _call_model(
            llm,
            system=prompts.DRAFT_SYSTEM,
            user=user,
            schema=json_schema(DraftOutput),
            parse=_parse_draft,
            timer=timer,
        )
    generator = DraftGenerator.LLM if content is not None else DraftGenerator.TEMPLATE
    if content is None:
        content = template_draft(
            # A suspicious lead gets a generic salutation rather than any of its own text.
            contact_name="" if injection else lead["contact_name"],
            service_interest=ServiceLine(lead["service_interest"]),
            route=score.route,
        )

    now = iso(clock())
    with transaction(conn):
        raced = _replay(conn, DRAFT_SCOPE, idempotency_key, request_hash)
        if raced is not None:
            return raced
        current = repo.get_lead(conn, lead_id)
        old_status = LeadStatus(current["status"])  # re-check under the write lock
        if old_status not in DRAFTABLE:
            raise LeadNotDraftable(old_status.value)
        run_id = repo.insert_llm_run(
            conn,
            lead_id=lead_id,
            task="draft",
            mode="ollama" if llm is not None else "off",
            model=model,
            prompt_version=prompts.PROMPT_VERSION,
            fallback_reason=attempt.fallback_reason,
            error=attempt.error,
            latency_ms=attempt.latency_ms,
            input_sha256=_sha256(prompts.PROMPT_VERSION, model or "", prompts.DRAFT_SYSTEM, user),
            raw_output=attempt.raw_output,
            now=now,
        )
        is_llm = generator is DraftGenerator.LLM
        version = repo.next_draft_version(conn, lead_id)
        draft_id = repo.insert_draft(
            conn,
            lead_id=lead_id,
            version=version,
            content=content,
            generator=generator,
            model=model if is_llm else None,
            prompt_version=prompts.PROMPT_VERSION if is_llm else None,
            llm_run_id=run_id,
            fallback_reason=attempt.fallback_reason,
            now=now,
        )
        new_status = LeadStatus.DRAFTED
        if old_status is not new_status:
            repo.update_lead_status(conn, lead_id, new_status, now)
        repo.insert_audit(
            conn,
            occurred_at=now,
            actor=actor,
            action="lead.drafted",
            entity_type="lead",
            entity_id=lead_id,
            before={"status": old_status.value},
            after={
                "status": new_status.value,
                "draft_id": draft_id,
                "version": version,
                "generator": generator.value,
                "llm_run_id": run_id,
                "model": model if is_llm else None,
                "fallback_reason": attempt.fallback_reason,
            },
            request_id=request_id,
        )
        if idempotency_key is not None:
            repo.insert_idempotency(
                conn,
                scope=DRAFT_SCOPE,
                key=idempotency_key,
                request_hash=request_hash,
                status_code=201,
                resource_type="draft",
                resource_id=draft_id,
                now=now,
            )
    return AssistResult(resource_id=draft_id, replayed=False)
