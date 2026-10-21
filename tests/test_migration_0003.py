"""Migration 0003: reviewers are added without rewriting existing approval history."""

import shutil
from pathlib import Path

from flowpilot.db.connection import connect
from flowpilot.db.migrate import current_version, default_source, migrate

NOW = "2026-10-20T12:00:00+00:00"


def test_upgrade_keeps_existing_approvals_unchanged(tmp_path: Path) -> None:
    m2_only = tmp_path / "m2"
    m2_only.mkdir()
    for name in ("0001_initial.sql", "0002_llm.sql"):
        shutil.copy(Path(str(default_source() / name)), m2_only / name)
    conn = connect(tmp_path / "upgrade.db")
    try:
        migrate(conn, m2_only)
        assert current_version(conn) == 2
        conn.execute(
            """
            INSERT INTO leads (company_name, company_key, contact_name, contact_email,
                company_size, industry, service_interest, budget_band, timeline, source, message,
                created_at, updated_at)
            VALUES ('Kestrel Labs', 'kestrel labs', 'Sage Ivers', 'sage@kestrel.example', '11-50',
                'saas', 'ai_strategy', '25k_50k', 'immediate', 'event', 'hello there', ?, ?)
            """,
            (NOW, NOW),
        )
        conn.execute(
            "INSERT INTO drafts (lead_id, version, subject, body, generator, created_at) "
            "VALUES (1, 1, 'Re: enquiry', 'Thanks!', 'template', ?)",
            (NOW,),
        )
        # An approval as M4 (schema version 2) wrote it: decision, simulated outbox row, audit.
        conn.execute(
            "INSERT INTO approvals (draft_id, decision, actor_type, decided_by, reason, "
            "decided_at) VALUES (1, 'approved', 'human', 'legacy-reviewer', 'Looks right.', ?)",
            (NOW,),
        )
        conn.execute(
            "INSERT INTO outbox (approval_id, draft_id, recipient, subject, body, "
            "idempotency_key, created_at) "
            "VALUES (1, 1, 'sage@kestrel.example', 'Re: enquiry', 'Thanks!', 'approval:1', ?)",
            (NOW,),
        )
        for action, entity_type in (("lead.approved", "lead"), ("outbox.enqueued", "outbox")):
            conn.execute(
                "INSERT INTO audit_log (occurred_at, actor, action, entity_type, entity_id) "
                "VALUES (?, 'legacy-reviewer', ?, ?, '1')",
                (NOW, action, entity_type),
            )

        def snapshot(table: str) -> list[dict]:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]  # noqa: S608

        before = {t: snapshot(t) for t in ("approvals", "outbox", "audit_log")}

        applied = migrate(conn)

        assert [m.label for m in applied] == ["0003_reviewers"]
        assert current_version(conn) == 3
        [approval] = snapshot("approvals")
        assert approval["reviewer_id"] is None
        assert {k: v for k, v in approval.items() if k != "reviewer_id"} == before["approvals"][0]
        assert snapshot("outbox") == before["outbox"]
        assert snapshot("audit_log") == before["audit_log"]
        assert conn.execute("SELECT COUNT(*) FROM reviewers").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
