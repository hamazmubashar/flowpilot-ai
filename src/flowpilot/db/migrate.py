"""Minimal forward-only migration runner (stdlib only).

Migrations are `NNNN_name.sql` files applied in order, each in its own transaction, and
recorded with a SHA-256 checksum. Editing an already-applied migration is detected and refused.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable

MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """Raised when migrations are inconsistent or fail to apply."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def default_source() -> Traversable:
    return resources.files("flowpilot.db") / "migrations"


def discover(source: Traversable | None = None) -> list[Migration]:
    source = source or default_source()
    found: list[Migration] = []
    for entry in source.iterdir():
        match = MIGRATION_NAME.match(entry.name)
        if not match:
            continue
        found.append(
            Migration(
                version=int(match["version"]),
                name=match["name"],
                sql=entry.read_text(encoding="utf-8"),
            )
        )
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration versions: {versions}")
    return found


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            checksum   TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    return row is not None


def applied(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    """Read-only: works on read-only connections and on databases never migrated."""
    if not _has_table(conn):
        return {}
    rows = conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
    return {row[0]: row for row in rows}


def pending(conn: sqlite3.Connection, source: Traversable | None = None) -> list[Migration]:
    """Return unapplied migrations after verifying applied ones are unchanged."""
    available = discover(source)
    done = applied(conn)
    by_version = {m.version: m for m in available}
    for version, row in done.items():
        migration = by_version.get(version)
        if migration is None:
            raise MigrationError(f"database has migration {version:04d} unknown to this code")
        if migration.checksum != row[2]:
            raise MigrationError(f"migration {migration.label} was modified after being applied")
    return [m for m in available if m.version not in done]


def current_version(conn: sqlite3.Connection) -> int:
    return max(applied(conn), default=0)


def migrate(conn: sqlite3.Connection, source: Traversable | None = None) -> list[Migration]:
    """Apply all pending migrations; each one is atomic. Returns what was applied."""
    _ensure_table(conn)
    todo = pending(conn, source)
    for migration in todo:
        applied_at = datetime.now(UTC).isoformat(timespec="seconds")
        # executescript() cannot bind parameters. All interpolated values are controlled:
        # version is an int, name matches [a-z0-9_]+, checksum is hex, applied_at is ISO.
        record = (
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "  # noqa: S608
            f"VALUES ({migration.version}, '{migration.name}', '{migration.checksum}', "
            f"'{applied_at}');"
        )
        script = f"BEGIN IMMEDIATE;\n{migration.sql}\n{record}\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"migration {migration.label} failed: {exc}") from exc
    return todo
