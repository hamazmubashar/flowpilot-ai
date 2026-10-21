"""Human review of drafts (M4): approve or reject one exact draft version.

The caller is authenticated by the API layer as an individual reviewer (a personal reviewer
token that automated clients such as n8n never hold). Everything here runs in one `BEGIN
IMMEDIATE` transaction, so the checks and writes cannot interleave with another decision, a new
draft, or the reviewer's revocation.
Approval enqueues exactly one simulated outbox row; nothing is ever sent.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from flowpilot.db import repository as repo
from flowpilot.db.connection import transaction
from flowpilot.domain.models import DraftDecisionIn, LeadStatus, ReviewDecision
from flowpilot.services.assist import LeadNotFound
from flowpilot.services.intake import Clock, IdempotencyKeyReused, iso, utc_now
from flowpilot.services.reviewers import ReviewerIdentity

REVIEW_SCOPE = "POST /leads/{id}/drafts/{version}/approval"
REVIEWABLE = frozenset({LeadStatus.DRAFTED, LeadStatus.AWAITING_APPROVAL})
DECISION_STATUS = {
    ReviewDecision.APPROVED: LeadStatus.APPROVED,
    ReviewDecision.REJECTED: LeadStatus.REJECTED,
}


class ReviewerNotActive(Exception):
    """The authenticated reviewer was revoked before the decision was written."""


class DraftNotFound(Exception):
    def __init__(self, lead_id: int, version: int) -> None:
        super().__init__(lead_id, version)
        self.lead_id = lead_id
        self.version = version


class DraftAlreadyDecided(Exception):
    def __init__(self, version: int, decision: str) -> None:
        super().__init__(version, decision)
        self.version = version
        self.decision = decision


class DraftSuperseded(Exception):
    def __init__(self, version: int, latest: int) -> None:
        super().__init__(version, latest)
        self.version = version
        self.latest = latest


class LeadNotReviewable(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class ReviewResult:
    approval_id: int
    replayed: bool


def _request_hash(
    reviewer: ReviewerIdentity, lead_id: int, version: int, decision: DraftDecisionIn
) -> str:
    # The reviewer is part of the request: another reviewer reusing a key is a different request.
    digest = hashlib.sha256()
    parts = (REVIEW_SCOPE, str(reviewer.id), str(lead_id), str(version), decision.decision.value)
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    digest.update((decision.reason or "").encode("utf-8"))
    return digest.hexdigest()


def decide_draft(
    conn: sqlite3.Connection,
    lead_id: int,
    version: int,
    decision: DraftDecisionIn,
    *,
    reviewer: ReviewerIdentity,
    clock: Clock = utc_now,
    idempotency_key: str | None = None,
    request_id: str | None = None,
) -> ReviewResult:
    request_hash = _request_hash(reviewer, lead_id, version, decision)
    now = iso(clock())
    with transaction(conn):
        if repo.get_active_reviewer(conn, reviewer.id) is None:
            raise ReviewerNotActive
        if idempotency_key is not None:
            prior = repo.get_idempotency(conn, REVIEW_SCOPE, idempotency_key)
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise IdempotencyKeyReused(idempotency_key)
                return ReviewResult(approval_id=int(prior["resource_id"]), replayed=True)

        lead = repo.get_lead(conn, lead_id)
        if lead is None:
            raise LeadNotFound(lead_id)
        draft = repo.get_draft_by_version(conn, lead_id, version)
        if draft is None:
            raise DraftNotFound(lead_id, version)
        existing = repo.get_approval_for_draft(conn, draft["id"])
        if existing is not None:
            raise DraftAlreadyDecided(version, existing["decision"])
        latest = repo.next_draft_version(conn, lead_id) - 1
        if version != latest:
            raise DraftSuperseded(version, latest)
        old_status = LeadStatus(lead["status"])
        if old_status not in REVIEWABLE:
            raise LeadNotReviewable(old_status.value)

        approval_id = repo.insert_approval(
            conn,
            draft_id=draft["id"],
            decision=decision.decision.value,
            reviewer_id=reviewer.id,
            decided_by=reviewer.name,
            reason=decision.reason,
            now=now,
        )
        new_status = DECISION_STATUS[decision.decision]
        repo.update_lead_status(conn, lead_id, new_status, now)
        outbox_id = None
        if decision.decision is ReviewDecision.APPROVED:
            outbox_id = repo.insert_outbox(
                conn,
                approval_id=approval_id,
                draft_id=draft["id"],
                recipient=lead["contact_email"],
                subject=draft["subject"],
                body=draft["body"],
                now=now,
            )
        repo.insert_audit(
            conn,
            occurred_at=now,
            actor=reviewer.name,
            action=f"lead.{decision.decision.value}",
            entity_type="lead",
            entity_id=lead_id,
            before={"status": old_status.value},
            after={
                "status": new_status.value,
                "draft_id": draft["id"],
                "version": version,
                "approval_id": approval_id,
                "reviewer_id": reviewer.id,
                "reason": decision.reason,
                "outbox_id": outbox_id,
            },
            request_id=request_id,
        )
        if outbox_id is not None:
            repo.insert_audit(
                conn,
                occurred_at=now,
                actor=reviewer.name,
                action="outbox.enqueued",
                entity_type="outbox",
                entity_id=outbox_id,
                after={
                    "approval_id": approval_id,
                    "draft_id": draft["id"],
                    "channel": "simulated",
                    "status": "pending",
                },
                request_id=request_id,
            )
        if idempotency_key is not None:
            repo.insert_idempotency(
                conn,
                scope=REVIEW_SCOPE,
                key=idempotency_key,
                request_hash=request_hash,
                status_code=201,
                resource_type="approval",
                resource_id=approval_id,
                now=now,
            )
    return ReviewResult(approval_id=approval_id, replayed=False)
