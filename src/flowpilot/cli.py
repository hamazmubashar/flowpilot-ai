"""`flowpilot-admin`: migrations, seeding, backup/restore, integrity checks, key generation,
reviewer management, and a no-inference Ollama readiness check."""

from __future__ import annotations

import argparse
import secrets
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from flowpilot.db import repository as repo
from flowpilot.db.connection import connect
from flowpilot.db.migrate import MigrationError, current_version, migrate, pending
from flowpilot.domain.models import LeadIn
from flowpilot.llm.client import LLMError, OllamaClient
from flowpilot.services.intake import submit_lead
from flowpilot.services.reviewers import ReviewerError, add_reviewer, revoke_reviewer
from flowpilot.settings import SettingsError, load_llm_config, load_storage_paths
from flowpilot.synthetic import DEFAULT_COUNT, DEFAULT_SEED, generate_leads

SEED_ACTOR = "seed-script"


class CommandError(RuntimeError):
    pass


def _require_db(path: Path) -> None:
    if not path.is_file():
        raise CommandError(f"database {path} does not exist; run `flowpilot-admin migrate`")


def integrity_problems(conn: sqlite3.Connection) -> list[str]:
    problems = []
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        problems.append(f"integrity_check: {result}")
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        problems.append(f"foreign_key_check: {len(fk)} violation(s)")
    try:
        todo = pending(conn)
    except MigrationError as exc:
        problems.append(f"migrations: {exc}")
    else:
        if todo:
            problems.append(f"migrations: {len(todo)} pending")
    return problems


def cmd_migrate(db: Path, _: argparse.Namespace) -> int:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    try:
        done = migrate(conn)
        version = current_version(conn)
    finally:
        conn.close()
    for m in done:
        print(f"applied {m.label}")
    print(f"schema version {version} ({'up to date' if not done else f'{len(done)} applied'})")
    return 0


def cmd_status(db: Path, _: argparse.Namespace) -> int:
    _require_db(db)
    conn = connect(db)
    try:
        todo = pending(conn)
        print(f"database: {db}")
        print(f"schema version: {current_version(conn)}")
        print(f"pending migrations: {', '.join(m.label for m in todo) or 'none'}")
    finally:
        conn.close()
    return 0


def cmd_seed(db: Path, args: argparse.Namespace) -> int:
    _require_db(db)
    conn = connect(db)
    created = existing = 0
    try:
        if pending(conn):
            raise CommandError("pending migrations; run `flowpilot-admin migrate` first")
        for i, payload in enumerate(generate_leads(args.count, args.seed)):
            # Same seed + index => same idempotency key, so re-running seed is a no-op.
            result = submit_lead(
                conn,
                LeadIn(**payload),
                actor=SEED_ACTOR,
                idempotency_key=f"seed:{args.seed}:{i}",
            )
            if result.status_code == 201 and not result.replayed:
                created += 1
            else:
                existing += 1
    finally:
        conn.close()
    print(f"seed={args.seed} count={args.count}: {created} created, {existing} already present")
    return 0


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def backup_database(src_path: Path, dest_path: Path) -> None:
    """Online-safe copy via the SQLite backup API (consistent even with WAL and live writers)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        raise CommandError(f"refusing to overwrite existing file {dest_path}")
    src = sqlite3.connect(src_path)
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
        result = dest.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dest.close()
        src.close()
    if result != "ok":
        raise CommandError(f"backup {dest_path} failed integrity_check: {result}")


def cmd_backup(db: Path, args: argparse.Namespace) -> int:
    _require_db(db)
    out = Path(args.out) if args.out else args.backup_dir / f"flowpilot-{_timestamp()}.db"
    backup_database(db, out)
    print(f"backup written: {out}")
    return 0


def cmd_restore(db: Path, args: argparse.Namespace) -> int:
    source = Path(args.source)
    if not args.yes:
        raise CommandError("restore overwrites the live database; re-run with --yes to confirm")
    if not source.is_file():
        raise CommandError(f"backup {source} does not exist")
    if source.resolve() == db.resolve():
        raise CommandError("backup and target are the same file")

    check = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    check.row_factory = sqlite3.Row
    try:
        problems = integrity_problems(check)
    finally:
        check.close()
    if problems:
        raise CommandError(f"backup {source} failed verification: {'; '.join(problems)}")

    if db.is_file():
        safety = args.backup_dir / f"pre-restore-{_timestamp()}.db"
        backup_database(db, safety)
        print(f"current database saved to: {safety}")

    db.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dest = sqlite3.connect(db)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    print(f"restored {source} -> {db}")
    return 0


def cmd_verify(db: Path, _: argparse.Namespace) -> int:
    _require_db(db)
    conn = connect(db)
    try:
        problems = integrity_problems(conn)
    finally:
        conn.close()
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    print("OK integrity_check, foreign_key_check, migration checksums")
    return 0


def _open_migrated(db: Path) -> sqlite3.Connection:
    _require_db(db)
    conn = connect(db)
    if pending(conn):
        conn.close()
        raise CommandError("pending migrations; run `flowpilot-admin migrate` first")
    return conn


def cmd_reviewer_add(db: Path, args: argparse.Namespace) -> int:
    conn = _open_migrated(db)
    try:
        reviewer, token = add_reviewer(conn, args.name)
    except ReviewerError as exc:
        raise CommandError(str(exc)) from None
    finally:
        conn.close()
    # The only place a reviewer token is ever shown; only its SHA-256 is stored.
    print(f"reviewer {reviewer.name!r} added (id {reviewer.id}).")
    print("Token (shown once; give it only to this reviewer, never to n8n or any automation):")
    print(token)
    return 0


def cmd_reviewer_list(db: Path, _: argparse.Namespace) -> int:
    conn = _open_migrated(db)
    try:
        rows = repo.list_reviewers(conn)
    finally:
        conn.close()
    if not rows:
        print("no reviewers")
    for row in rows:
        status = "active" if row["revoked_at"] is None else f"revoked {row['revoked_at']}"
        print(f"{row['id']}\t{row['name']}\t{status}\tadded {row['created_at']}")
    return 0


def cmd_reviewer_revoke(db: Path, args: argparse.Namespace) -> int:
    conn = _open_migrated(db)
    try:
        revoke_reviewer(conn, args.name)
    except ReviewerError as exc:
        raise CommandError(str(exc)) from None
    finally:
        conn.close()
    print(f"reviewer {args.name!r} revoked; their token no longer works.")
    return 0


def cmd_gen_key(_: Path, __: argparse.Namespace) -> int:
    print(secrets.token_urlsafe(32))
    return 0


def cmd_llm_check(_: Path, __: argparse.Namespace) -> int:
    """Verify Ollama is reachable on loopback and the model is installed. No inference."""
    config = load_llm_config()
    print(f"mode: {config.mode}  url: {config.ollama_url}  model: {config.model}")
    if not config.enabled:
        print("note: FLOWPILOT_LLM_MODE=off, so the API uses deterministic fallbacks only")
    client = OllamaClient(
        base_url=config.ollama_url, model=config.model, timeout_s=config.timeout_s
    )
    try:
        installed = client.list_models()
    except LLMError as exc:
        print(f"FAIL ollama unreachable: {exc}")
        return 1
    finally:
        client.close()
    print(f"installed models: {', '.join(installed) or 'none'}")
    if config.model not in installed:
        print(f"FAIL model {config.model!r} is not installed (nothing was downloaded)")
        return 1
    print(f"OK model {config.model!r} is installed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flowpilot-admin", description=__doc__)
    parser.add_argument("--db", type=Path, help="database path (default: FLOWPILOT_DB_PATH)")
    parser.add_argument(
        "--backup-dir", type=Path, help="backup directory (default: FLOWPILOT_BACKUP_DIR)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending migrations").set_defaults(func=cmd_migrate)
    sub.add_parser("status", help="show schema version").set_defaults(func=cmd_status)

    seed = sub.add_parser("seed", help="insert deterministic synthetic leads (idempotent)")
    seed.add_argument("--count", type=int, default=DEFAULT_COUNT)
    seed.add_argument("--seed", type=int, default=DEFAULT_SEED)
    seed.set_defaults(func=cmd_seed)

    backup = sub.add_parser("backup", help="write a consistent online backup")
    backup.add_argument("--out", help="output file (default: <backup-dir>/flowpilot-<ts>.db)")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="verify a backup and restore it (stop the API first)")
    restore.add_argument("source", help="backup file to restore")
    restore.add_argument("--yes", action="store_true", help="confirm overwriting the database")
    restore.set_defaults(func=cmd_restore)

    sub.add_parser("verify", help="integrity + migration checks").set_defaults(func=cmd_verify)
    sub.add_parser("gen-key", help="print a new random API key").set_defaults(func=cmd_gen_key)

    reviewer = sub.add_parser("reviewer", help="manage individual human reviewers (M4.1)")
    reviewer_sub = reviewer.add_subparsers(dest="reviewer_command", required=True)
    add = reviewer_sub.add_parser("add", help="create a reviewer and print their token once")
    add.add_argument("name")
    add.set_defaults(func=cmd_reviewer_add)
    reviewer_sub.add_parser("list", help="list reviewers (never tokens)").set_defaults(
        func=cmd_reviewer_list
    )
    revoke = reviewer_sub.add_parser("revoke", help="revoke a reviewer's token immediately")
    revoke.add_argument("name")
    revoke.set_defaults(func=cmd_reviewer_revoke)
    sub.add_parser(
        "llm-check", help="check Ollama reachability and model presence (no inference)"
    ).set_defaults(func=cmd_llm_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = load_storage_paths()
    db = args.db or paths.db_path
    args.backup_dir = args.backup_dir or paths.backup_dir
    try:
        return args.func(db, args)
    except (CommandError, MigrationError, SettingsError) as exc:
        print(f"flowpilot-admin: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
