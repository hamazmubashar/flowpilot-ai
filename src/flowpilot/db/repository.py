"""Repository layer: the only module that issues SQL against business tables.

All functions take an open connection; callers own transaction boundaries.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from flowpilot.domain.models import LeadIn, LeadStatus, ScoreResult

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
