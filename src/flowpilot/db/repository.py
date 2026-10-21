"""Repository layer: the only module that issues SQL against business tables.

All functions take an open connection; callers own transaction boundaries.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from flowpilot.domain.models import (
    Assessment,
    DraftContent,
    DraftGenerator,
    LeadIn,
    LeadStatus,
    ScoreResult,
)

LEAD_COLUMNS = (
    "id, company_name, contact_name, contact_email, company_size, industry, service_interest, "
    "budget_band, timeline, source, message, status, created_at, updated_at"
)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"))


# --- leads -----------------------------------------------------------------------------------


def insert_lead(conn: sqlite3.Connection, lead: LeadIn, company_key: str, now: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO leads (company_name, company_key, contact_name, contact_email, company_size,
                           industry, service_interest, budget_band, timeline, source, message,
                           status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead.company_name,
            company_key,
            lead.contact_name,
            lead.contact_email,
            lead.company_size.value,
            lead.industry.value,
            lead.service_interest.value,
            lead.budget_band.value,
            lead.timeline.value,
            lead.source.value,
            lead.message,
            LeadStatus.NEW.value,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def find_lead_by_dedupe(
    conn: sqlite3.Connection, contact_email: str, company_key: str
) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {LEAD_COLUMNS} FROM leads WHERE contact_email = ? AND company_key = ?",  # noqa: S608
        (contact_email, company_key),
    ).fetchone()


def get_lead(conn: sqlite3.Connection, lead_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {LEAD_COLUMNS} FROM leads WHERE id = ?",  # noqa: S608
        (lead_id,),
    ).fetchone()


def update_lead_status(
    conn: sqlite3.Connection, lead_id: int, status: LeadStatus, now: str
) -> None:
    conn.execute(
        "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
        (status.value, now, lead_id),
    )


_LIST_FILTER = """
    FROM leads l
    LEFT JOIN lead_scores s
           ON s.id = (SELECT MAX(id) FROM lead_scores WHERE lead_id = l.id)
    WHERE (:status IS NULL OR l.status = :status)
      AND (:tier IS NULL OR s.tier = :tier)
"""


def list_leads(
    conn: sqlite3.Connection,
    *,
    status: str | None,
    tier: str | None,
    limit: int,
    offset: int,
) -> tuple[list[sqlite3.Row], int]:
    params = {"status": status, "tier": tier, "limit": limit, "offset": offset}
    total = conn.execute(f"SELECT COUNT(*) AS n {_LIST_FILTER}", params).fetchone()["n"]
    cols = ", ".join(f"l.{c.strip()}" for c in LEAD_COLUMNS.split(","))
    rows = conn.execute(
        f"SELECT {cols}, s.total AS score_total, s.tier AS score_tier, s.route AS score_route "
        f"{_LIST_FILTER} ORDER BY l.id LIMIT :limit OFFSET :offset",
        params,
    ).fetchall()
    return rows, int(total)


# --- scores ----------------------------------------------------------------------------------


def insert_score(conn: sqlite3.Connection, lead_id: int, score: ScoreResult, now: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO lead_scores (lead_id, rules_version, total, tier, route, breakdown_json,
                                 created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead_id,
            score.rules_version,
            score.total,
            score.tier.value,
            score.route.value,
            _json([c.model_dump() for c in score.breakdown]),
            now,
        ),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def get_latest_score(conn: sqlite3.Connection, lead_id: int) -> ScoreResult | None:
    row = conn.execute(
        """
        SELECT rules_version, total, tier, route, breakdown_json FROM lead_scores
        WHERE lead_id = ? ORDER BY id DESC LIMIT 1
        """,
        (lead_id,),
    ).fetchone()
    if row is None:
        return None
    return ScoreResult(
        rules_version=row["rules_version"],
        total=row["total"],
        tier=row["tier"],
        route=row["route"],
        breakdown=json.loads(row["breakdown_json"]),
    )


# --- audit -----------------------------------------------------------------------------------


def insert_audit(
    conn: sqlite3.Connection,
    *,
    occurred_at: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | int,
    before: Any = None,
    after: Any = None,
    request_id: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO audit_log (occurred_at, actor, action, entity_type, entity_id,
                               before_json, after_json, request_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            occurred_at,
            actor,
            action,
            entity_type,
            str(entity_id),
            _json(before),
            _json(after),
            request_id,
        ),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def list_audit(conn: sqlite3.Connection, entity_type: str, entity_id: str | int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, occurred_at, actor, action, entity_type, entity_id, before_json, after_json,
               request_id
        FROM audit_log WHERE entity_type = ? AND entity_id = ? ORDER BY id
        """,
        (entity_type, str(entity_id)),
    ).fetchall()
    entries = []
    for row in rows:
        entry = dict(row)
        entry["before"] = json.loads(entry.pop("before_json") or "null")
        entry["after"] = json.loads(entry.pop("after_json") or "null")
        entries.append(entry)
    return entries


# --- idempotency -----------------------------------------------------------------------------


def get_idempotency(conn: sqlite3.Connection, scope: str, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM idempotency_keys WHERE scope = ? AND key = ?", (scope, key)
    ).fetchone()


def insert_idempotency(
    conn: sqlite3.Connection,
    *,
    scope: str,
    key: str,
    request_hash: str,
    status_code: int,
    resource_type: str,
    resource_id: str | int,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO idempotency_keys (key, scope, request_hash, status_code, resource_type,
                                      resource_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (key, scope, request_hash, status_code, resource_type, str(resource_id), now),
    )


# --- LLM runs, assessments, drafts (M2) -------------------------------------------------------

_ASSESSMENT_SELECT = """
    SELECT a.id, a.lead_id, a.llm_run_id, a.source, a.summary, a.service_line, a.urgency,
           a.flags_json, a.created_at, r.model, r.prompt_version, r.outcome, r.fallback_reason,
           r.latency_ms
    FROM lead_assessments a JOIN llm_runs r ON r.id = a.llm_run_id
"""

_DRAFT_SELECT = """
    SELECT id, lead_id, version, subject, body, generator, model, prompt_version, llm_run_id,
           fallback_reason, created_at
    FROM drafts
"""


def insert_llm_run(
    conn: sqlite3.Connection,
    *,
    lead_id: int,
    task: str,
    mode: str,
    model: str | None,
    prompt_version: str,
    fallback_reason: str | None,
    error: str | None,
    latency_ms: int | None,
    input_sha256: str,
    raw_output: str | None,
    now: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO llm_runs (lead_id, task, mode, model, prompt_version, outcome,
                              fallback_reason, error, latency_ms, input_sha256, raw_output,
                              created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead_id,
            task,
            mode,
            model,
            prompt_version,
            "ok" if fallback_reason is None else "fallback",
            fallback_reason,
            error,
            latency_ms,
            input_sha256,
            raw_output,
            now,
        ),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def list_llm_runs(conn: sqlite3.Connection, lead_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM llm_runs WHERE lead_id = ? ORDER BY id", (lead_id,)
    ).fetchall()


def insert_assessment(
    conn: sqlite3.Connection, *, lead_id: int, llm_run_id: int, assessment: Assessment, now: str
) -> int:
    cur = conn.execute(
        """
        INSERT INTO lead_assessments (lead_id, llm_run_id, source, summary, service_line,
                                      urgency, flags_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead_id,
            llm_run_id,
            assessment.source.value,
            assessment.summary,
            assessment.service_line.value,
            assessment.urgency.value,
            _json([f.value for f in assessment.flags]),
            now,
        ),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def _assessment_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["flags"] = json.loads(data.pop("flags_json"))
    return data


def get_assessment(conn: sqlite3.Connection, assessment_id: int) -> dict[str, Any] | None:
    return _assessment_dict(
        conn.execute(f"{_ASSESSMENT_SELECT} WHERE a.id = ?", (assessment_id,)).fetchone()
    )


def get_latest_assessment(conn: sqlite3.Connection, lead_id: int) -> dict[str, Any] | None:
    return _assessment_dict(
        conn.execute(
            f"{_ASSESSMENT_SELECT} WHERE a.lead_id = ? ORDER BY a.id DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
    )


def next_draft_version(conn: sqlite3.Connection, lead_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM drafts WHERE lead_id = ?", (lead_id,)
    ).fetchone()
    return int(row[0])


def insert_draft(
    conn: sqlite3.Connection,
    *,
    lead_id: int,
    version: int,
    content: DraftContent,
    generator: DraftGenerator,
    model: str | None,
    prompt_version: str | None,
    llm_run_id: int | None,
    fallback_reason: str | None,
    now: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO drafts (lead_id, version, subject, body, generator, model, prompt_version,
                            llm_run_id, fallback_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead_id,
            version,
            content.subject,
            content.body,
            generator.value,
            model,
            prompt_version,
            llm_run_id,
            fallback_reason,
            now,
        ),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def get_draft(conn: sqlite3.Connection, draft_id: int) -> sqlite3.Row | None:
    return conn.execute(f"{_DRAFT_SELECT} WHERE id = ?", (draft_id,)).fetchone()


def list_drafts(conn: sqlite3.Connection, lead_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        f"{_DRAFT_SELECT} WHERE lead_id = ? ORDER BY version",
        (lead_id,),
    ).fetchall()


def get_draft_by_version(
    conn: sqlite3.Connection, lead_id: int, version: int
) -> sqlite3.Row | None:
    return conn.execute(
        f"{_DRAFT_SELECT} WHERE lead_id = ? AND version = ?", (lead_id, version)
    ).fetchone()


# --- Human review and simulated outbox (M4) ---------------------------------------------------


def insert_approval(
    conn: sqlite3.Connection,
    *,
    draft_id: int,
    decision: str,
    decided_by: str,
    reason: str | None,
    now: str,
) -> int:
    # actor_type is fixed: the schema accepts only 'human', and so does this code.
    cur = conn.execute(
        """
        INSERT INTO approvals (draft_id, decision, actor_type, decided_by, reason, decided_at)
        VALUES (?, ?, 'human', ?, ?, ?)
        """,
        (draft_id, decision, decided_by, reason, now),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def get_approval(conn: sqlite3.Connection, approval_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()


def get_approval_for_draft(conn: sqlite3.Connection, draft_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM approvals WHERE draft_id = ?", (draft_id,)).fetchone()


def insert_outbox(
    conn: sqlite3.Connection,
    *,
    approval_id: int,
    draft_id: int,
    recipient: str,
    subject: str,
    body: str,
    now: str,
) -> int:
    # channel and status use the schema defaults: 'simulated' (the only allowed value) and
    # 'pending'. The outbox_requires_approved_draft trigger re-checks the approval.
    cur = conn.execute(
        """
        INSERT INTO outbox (approval_id, draft_id, recipient, subject, body, idempotency_key,
                            created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (approval_id, draft_id, recipient, subject, body, f"approval:{approval_id}", now),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def get_outbox_for_approval(conn: sqlite3.Connection, approval_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM outbox WHERE approval_id = ?", (approval_id,)).fetchone()
