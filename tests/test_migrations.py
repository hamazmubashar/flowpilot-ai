import sqlite3
from pathlib import Path

import pytest

from flowpilot.db.connection import connect
from flowpilot.db.migrate import (
    MigrationError,
    applied,
    current_version,
    discover,
    migrate,
    pending,
)

EXPECTED_TABLES = {
    "leads",
    "lead_scores",
    "drafts",
    "approvals",
    "outbox",
    "audit_log",
    "idempotency_keys",
    "schema_migrations",
}


def test_initial_schema_applied(conn: sqlite3.Connection) -> None:
    tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert tables >= EXPECTED_TABLES
    assert current_version(conn) == 1
    assert pending(conn) == []


def test_wal_and_foreign_keys_enabled(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    assert migrate(conn) == []
    assert current_version(conn) == 1


def test_fresh_database_reports_version_zero(tmp_path: Path) -> None:
    fresh = connect(tmp_path / "fresh.db")
    try:
        assert applied(fresh) == {}
        assert current_version(fresh) == 0
        assert [m.version for m in pending(fresh)] == [1]
    finally:
        fresh.close()


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_failed_migration_rolls_back_completely(tmp_path: Path) -> None:
    src = tmp_path / "migrations"
    src.mkdir()
    _write(src, "0001_ok.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write(
        src,
        "0002_broken.sql",
        "CREATE TABLE b (id INTEGER PRIMARY KEY);\nINSERT INTO missing_table VALUES (1);",
    )
    db = connect(tmp_path / "m.db")
    try:
        with pytest.raises(MigrationError, match="0002_broken failed"):
            migrate(db, src)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "a" in tables
        assert "b" not in tables  # partial work of the failed file was rolled back
        assert current_version(db) == 1
        assert not db.in_transaction
    finally:
        db.close()


def test_modified_applied_migration_is_detected(tmp_path: Path) -> None:
    src = tmp_path / "migrations"
    src.mkdir()
    _write(src, "0001_ok.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db = connect(tmp_path / "m.db")
    try:
        migrate(db, src)
        _write(src, "0001_ok.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY, x TEXT);")
        with pytest.raises(MigrationError, match="modified after being applied"):
            pending(db, src)
    finally:
        db.close()


def test_unknown_applied_migration_is_detected(tmp_path: Path) -> None:
    src = tmp_path / "migrations"
    src.mkdir()
    _write(src, "0001_ok.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    db = connect(tmp_path / "m.db")
    try:
        migrate(db, src)
        (src / "0001_ok.sql").unlink()
        with pytest.raises(MigrationError, match="unknown to this code"):
            pending(db, src)
    finally:
        db.close()


def test_discover_ignores_non_migration_files_and_rejects_duplicates(tmp_path: Path) -> None:
    src = tmp_path / "migrations"
    src.mkdir()
    _write(src, "0001_ok.sql", "SELECT 1;")
    _write(src, "README.md", "not a migration")
    assert [m.name for m in discover(src)] == ["ok"]
    _write(src, "0001_again.sql", "SELECT 1;")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(src)
