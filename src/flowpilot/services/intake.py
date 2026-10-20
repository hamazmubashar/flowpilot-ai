"""Lead intake: idempotency check, dedupe, persist, score, and audit, all in one transaction."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from flowpilot.db import repository as repo
from flowpilot.db.connection import transaction
from flowpilot.domain.models import LeadIn, LeadStatus
from flowpilot.domain.normalize import company_key
from flowpilot.domain.scoring import score_lead

Clock = Callable[[], datetime]
INTAKE_SCOPE = "POST /leads"


class IdempotencyKeyReused(Exception):
    """The idempotency key was already used with a different request payload."""


@dataclass(frozen=True)
class IntakeResult:
    lead_id: int
    status_code: int  # 201 = created, 200 = existing lead returned
    duplicate: bool
    replayed: bool


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds")


def request_hash(lead: LeadIn) -> str:
    canonical = json.dumps(lead.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def submit_lead(
    conn: sqlite3.Connection,
    lead: LeadIn,
    *,
    actor: str,
    clock: Clock = utc_now,
    idempotency_key: str | None = None,
    request_id: str | None = None,
) -> IntakeResult:
    payload_hash = request_hash(lead)
    with transaction(conn):
        if idempotency_key is not None:
            prior = repo.get_idempotency(conn, INTAKE_SCOPE, idempotency_key)
            if prior is not None:
                if prior["request_hash"] != payload_hash:
                    raise IdempotencyKeyReused(idempotency_key)
                return IntakeResult(
                    lead_id=int(prior["resource_id"]),
                    status_code=int(prior["status_code"]),
                    duplicate=int(prior["status_code"]) == 200,
                    replayed=True,
                )

        now = iso(clock())
        key = company_key(lead.company_name)
        existing = repo.find_lead_by_dedupe(conn, lead.contact_email, key)

        if existing is not None:
            lead_id = int(existing["id"])
            repo.insert_audit(
                conn,
                occurred_at=now,
                actor=actor,
                action="lead.duplicate_submission",
                entity_type="lead",
                entity_id=lead_id,
                after={"source": lead.source.value, "idempotency_key": idempotency_key},
                request_id=request_id,
            )
            result = IntakeResult(lead_id, 200, duplicate=True, replayed=False)
        else:
            lead_id = repo.insert_lead(conn, lead, key, now)
            repo.insert_audit(
                conn,
                occurred_at=now,
                actor=actor,
                action="lead.created",
                entity_type="lead",
                entity_id=lead_id,
                after={**lead.model_dump(mode="json"), "status": LeadStatus.NEW.value},
                request_id=request_id,
            )
            score = score_lead(lead)
            repo.insert_score(conn, lead_id, score, now)
            repo.update_lead_status(conn, lead_id, LeadStatus.SCORED, now)
            repo.insert_audit(
                conn,
                occurred_at=now,
                actor=actor,
                action="lead.scored",
                entity_type="lead",
                entity_id=lead_id,
                before={"status": LeadStatus.NEW.value},
                after={
                    "status": LeadStatus.SCORED.value,
                    "rules_version": score.rules_version,
                    "total": score.total,
                    "tier": score.tier.value,
                    "route": score.route.value,
                },
                request_id=request_id,
            )
            result = IntakeResult(lead_id, 201, duplicate=False, replayed=False)

        if idempotency_key is not None:
            repo.insert_idempotency(
                conn,
                scope=INTAKE_SCOPE,
                key=idempotency_key,
                request_hash=payload_hash,
                status_code=result.status_code,
                resource_type="lead",
                resource_id=lead_id,
                now=now,
            )
    return result
