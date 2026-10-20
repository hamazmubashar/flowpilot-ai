"""Database-level guardrails: append-only audit, human-only approval, simulated-only sending."""

import sqlite3

import pytest

NOW = "2026-10-20T12:00:00+00:00"


def _lead(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        INSERT INTO leads (company_name, company_key, contact_name, contact_email, company_size,
            industry, service_interest, budget_band, timeline, source, message, created_at,
            updated_at)
        VALUES ('Kestrel Labs', 'kestrel labs', 'Sage Ivers', 'sage@kestrel.example', '11-50',
            'saas', 'ai_strategy', '25k_50k', 'immediate', 'event', 'hello there', ?, ?)
        """,
        (NOW, NOW),
    )
    return int(cur.lastrowid)


def _draft(conn: sqlite3.Connection, lead_id: int, version: int = 1) -> int:
    cur = conn.execute(
        "INSERT INTO drafts (lead_id, version, subject, body, generator, created_at) "
        "VALUES (?, ?, 'Re: enquiry', 'Thanks!', 'template', ?)",
        (lead_id, version, NOW),
    )
    return int(cur.lastrowid)


def _approval(conn: sqlite3.Connection, draft_id: int, decision: str = "approved") -> int:
    cur = conn.execute(
        "INSERT INTO approvals (draft_id, decision, actor_type, decided_by, decided_at) "
        "VALUES (?, ?, 'human', 'reviewer@northstar.example', ?)",
        (draft_id, decision, NOW),
    )
    return int(cur.lastrowid)


def _outbox(conn: sqlite3.Connection, approval_id: int, draft_id: int, **overrides: str) -> None:
    values = {
        "channel": "simulated",
        "recipient": "sage@kestrel.example",
        "idempotency_key": f"out-{approval_id}-{draft_id}",
        **overrides,
    }
    conn.execute(
        "INSERT INTO outbox (approval_id, draft_id, channel, recipient, subject, body, "
        "idempotency_key, created_at) VALUES (?, ?, ?, ?, 'Re: enquiry', 'Thanks!', ?, ?)",
        (
            approval_id,
            draft_id,
            values["channel"],
            values["recipient"],
            values["idempotency_key"],
            NOW,
        ),
    )


def _audit(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log (occurred_at, actor, action, entity_type, entity_id) "
        "VALUES (?, 'test', 'x', 'lead', '1')",
        (NOW,),
    )
    return int(cur.lastrowid)


def test_audit_log_rejects_update(conn: sqlite3.Connection) -> None:
    audit_id = _audit(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'tampered' WHERE id = ?", (audit_id,))


def test_audit_log_rejects_delete(conn: sqlite3.Connection) -> None:
    audit_id = _audit(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE id = ?", (audit_id,))


def test_approval_requires_human_actor(conn: sqlite3.Connection) -> None:
    draft_id = _draft(conn, _lead(conn))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO approvals (draft_id, decision, actor_type, decided_by, decided_at) "
            "VALUES (?, 'approved', 'system', 'auto', ?)",
            (draft_id, NOW),
        )


def test_approval_requires_named_reviewer(conn: sqlite3.Connection) -> None:
    draft_id = _draft(conn, _lead(conn))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO approvals (draft_id, decision, actor_type, decided_by, decided_at) "
            "VALUES (?, 'approved', 'human', '   ', ?)",
            (draft_id, NOW),
        )


def test_outbox_accepts_approved_draft(conn: sqlite3.Connection) -> None:
    draft_id = _draft(conn, _lead(conn))
    _outbox(conn, _approval(conn, draft_id), draft_id)
    assert tuple(conn.execute("SELECT status, channel FROM outbox").fetchone()) == (
        "pending",
        "simulated",
    )


def test_outbox_rejects_rejected_draft(conn: sqlite3.Connection) -> None:
    draft_id = _draft(conn, _lead(conn))
    approval_id = _approval(conn, draft_id, decision="rejected")
    with pytest.raises(sqlite3.IntegrityError, match="requires a human approval"):
        _outbox(conn, approval_id, draft_id)


def test_outbox_rejects_approval_of_different_draft_version(conn: sqlite3.Connection) -> None:
    lead_id = _lead(conn)
    v1 = _draft(conn, lead_id, version=1)
    v2 = _draft(conn, lead_id, version=2)
    approval_v1 = _approval(conn, v1)
    with pytest.raises(sqlite3.IntegrityError, match="requires a human approval"):
        _outbox(conn, approval_v1, v2)


def test_outbox_rejects_non_simulated_channel(conn: sqlite3.Connection) -> None:
    draft_id = _draft(conn, _lead(conn))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _outbox(conn, _approval(conn, draft_id), draft_id, channel="smtp")


def test_outbox_content_immutable_after_insert(conn: sqlite3.Connection) -> None:
    draft_id = _draft(conn, _lead(conn))
    _outbox(conn, _approval(conn, draft_id), draft_id)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE outbox SET body = 'swapped after approval'")
    conn.execute("UPDATE outbox SET status = 'sent', sent_at = ?", (NOW,))  # status may change


def test_foreign_keys_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _draft(conn, lead_id=9999)
