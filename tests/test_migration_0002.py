"""Migration 0002: upgrade path from an M1 database and new schema guardrails."""

import shutil
import sqlite3
from pathlib import Path

import pytest

from flowpilot.db.connection import connect
from flowpilot.db.migrate import current_version, default_source, migrate
from flowpilot.domain.models import LeadIn
from flowpilot.services.intake import submit_lead

NOW = "2026-10-20T12:00:00+00:00"


def test_upgrade_from_m1_preserves_data(tmp_path: Path, lead_payload: dict) -> None:
    m1_only = tmp_path / "m1"
    m1_only.mkdir()
    shutil.copy(Path(str(default_source() / "0001_initial.sql")), m1_only / "0001_initial.sql")
    conn = connect(tmp_path / "upgrade.db")
    try:
        migrate(conn, m1_only)
        assert current_version(conn) == 1
        lead_id = submit_lead(conn, LeadIn(**lead_payload), actor="m1").lead_id
        scores_before = [tuple(r) for r in conn.execute("SELECT * FROM lead_scores")]

        applied = migrate(conn)
        assert [m.label for m in applied] == ["0002_llm"]
        assert current_version(conn) == 2
        assert [tuple(r) for r in conn.execute("SELECT * FROM lead_scores")] == scores_before
        assert conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()[0] == (
            "scored"
        )
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(drafts)")}
        assert {"prompt_version", "llm_run_id", "fallback_reason"} <= columns
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def _lead(conn: sqlite3.Connection, lead_payload: dict) -> int:
    return submit_lead(conn, LeadIn(**lead_payload), actor="test").lead_id


def _run(conn: sqlite3.Connection, lead_id: int, outcome: str = "ok") -> int:
    reason = None if outcome == "ok" else "timeout"
    cur = conn.execute(
        "INSERT INTO llm_runs (lead_id, task, mode, model, prompt_version, outcome, "
        "fallback_reason, input_sha256, created_at) VALUES (?, 'draft', 'ollama', 'm', 'p', ?, ?, "
        "'h', ?)",
        (lead_id, outcome, reason, NOW),
    )
    return int(cur.lastrowid)


def _draft(conn: sqlite3.Connection, lead_id: int, run_id: int | None, **cols) -> None:
    values = {"generator": "llm", "model": "m", "fallback_reason": None, "version": 1, **cols}
    conn.execute(
        "INSERT INTO drafts (lead_id, version, subject, body, generator, model, llm_run_id, "
        "fallback_reason, created_at) VALUES (?, ?, 's', 'b', ?, ?, ?, ?, ?)",
        (
            lead_id,
            values["version"],
            values["generator"],
            values["model"],
            run_id,
            values["fallback_reason"],
            NOW,
        ),
    )


def test_llm_draft_requires_successful_run(conn: sqlite3.Connection, lead_payload: dict) -> None:
    lead_id = _lead(conn, lead_payload)
    with pytest.raises(sqlite3.IntegrityError, match="successful llm_run"):
        _draft(conn, lead_id, run_id=None)
    with pytest.raises(sqlite3.IntegrityError, match="successful llm_run"):
        _draft(conn, lead_id, run_id=_run(conn, lead_id, outcome="fallback"))
    with pytest.raises(sqlite3.IntegrityError, match="successful llm_run"):
        _draft(conn, lead_id, run_id=_run(conn, lead_id), model=None)
    _draft(conn, lead_id, run_id=_run(conn, lead_id))  # valid provenance is accepted


def test_llm_draft_run_must_belong_to_same_lead(
    conn: sqlite3.Connection, lead_payload: dict
) -> None:
    a = _lead(conn, lead_payload)
    b = _lead(conn, {**lead_payload, "company_name": "Different Co"})
    with pytest.raises(sqlite3.IntegrityError, match="successful llm_run"):
        _draft(conn, b, run_id=_run(conn, a))


def test_drafts_are_immutable(conn: sqlite3.Connection, lead_payload: dict) -> None:
    lead_id = _lead(conn, lead_payload)
    _draft(conn, lead_id, run_id=_run(conn, lead_id))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE drafts SET body = 'edited'")


def test_llm_run_outcome_must_match_reason(conn: sqlite3.Connection, lead_payload: dict) -> None:
    lead_id = _lead(conn, lead_payload)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO llm_runs (lead_id, task, mode, prompt_version, outcome, fallback_reason, "
            "input_sha256, created_at) VALUES (?, 'draft', 'off', 'p', 'ok', 'disabled', 'h', ?)",
            (lead_id, NOW),
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO llm_runs (lead_id, task, mode, prompt_version, outcome, fallback_reason, "
            "input_sha256, created_at) "
            "VALUES (?, 'draft', 'off', 'p', 'fallback', 'made_up', 'h', ?)",
            (lead_id, NOW),
        )
