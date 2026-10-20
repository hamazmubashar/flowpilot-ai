import sqlite3
from pathlib import Path

import pytest

from flowpilot.cli import main
from flowpilot.db.connection import connect


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def paths(tmp_path: Path) -> tuple[str, str]:
    return str(tmp_path / "var" / "fp.db"), str(tmp_path / "backups")


def count_leads(db: str) -> int:
    conn = connect(Path(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    finally:
        conn.close()


def test_migrate_creates_db_and_is_idempotent(capsys, paths) -> None:
    db, bk = paths
    code, out, _ = run(capsys, "--db", db, "--backup-dir", bk, "migrate")
    assert code == 0
    assert "applied 0001_initial" in out
    assert "applied 0002_llm" in out
    code, out, _ = run(capsys, "--db", db, "migrate")
    assert code == 0
    assert "up to date" in out
    code, out, _ = run(capsys, "--db", db, "status")
    assert "schema version: 2" in out
    assert "pending migrations: none" in out


def test_commands_require_existing_db(capsys, paths) -> None:
    db, _ = paths
    for command in ("status", "seed", "backup", "verify"):
        code, _, err = run(capsys, "--db", db, command)
        assert code == 1
        assert "does not exist" in err


def test_seed_refuses_unmigrated_db(capsys, tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    db.touch()
    code, _, err = run(capsys, "--db", str(db), "seed")
    assert code == 1
    assert "pending migrations" in err


def test_seed_is_idempotent(capsys, paths) -> None:
    db, _ = paths
    run(capsys, "--db", db, "migrate")
    code, out, _ = run(capsys, "--db", db, "seed", "--count", "25")
    assert code == 0
    assert "25 created, 0 already present" in out
    code, out, _ = run(capsys, "--db", db, "seed", "--count", "25")
    assert "0 created, 25 already present" in out
    assert count_leads(db) == 25


def test_backup_restore_roundtrip(capsys, paths, tmp_path: Path) -> None:
    db, bk = paths
    run(capsys, "--db", db, "migrate")
    run(capsys, "--db", db, "seed", "--count", "10")
    snapshot = str(tmp_path / "snap.db")
    code, out, _ = run(capsys, "--db", db, "backup", "--out", snapshot)
    assert code == 0
    assert "backup written" in out

    run(capsys, "--db", db, "seed", "--count", "20")  # adds 10 more after the snapshot
    assert count_leads(db) == 20

    code, _, err = run(capsys, "--db", db, "--backup-dir", bk, "restore", snapshot)
    assert code == 1
    assert "--yes" in err

    code, out, _ = run(capsys, "--db", db, "--backup-dir", bk, "restore", snapshot, "--yes")
    assert code == 0
    assert "pre-restore" in out
    assert count_leads(db) == 10
    safety = list(Path(bk).glob("pre-restore-*.db"))
    assert len(safety) == 1
    assert count_leads(str(safety[0])) == 20  # the overwritten state is recoverable

    code, out, _ = run(capsys, "--db", db, "verify")
    assert code == 0
    assert out.startswith("OK")


def test_default_backup_path_and_no_overwrite(capsys, paths, tmp_path: Path) -> None:
    db, bk = paths
    run(capsys, "--db", db, "migrate")
    code, _, _ = run(capsys, "--db", db, "--backup-dir", bk, "backup")
    assert code == 0
    assert len(list(Path(bk).glob("flowpilot-*.db"))) == 1
    target = str(tmp_path / "x.db")
    run(capsys, "--db", db, "backup", "--out", target)
    code, _, err = run(capsys, "--db", db, "backup", "--out", target)
    assert code == 1
    assert "refusing to overwrite" in err


def test_restore_rejects_bad_backups(capsys, paths, tmp_path: Path) -> None:
    db, bk = paths
    run(capsys, "--db", db, "migrate")
    code, _, err = run(capsys, "--db", db, "restore", str(tmp_path / "missing.db"), "--yes")
    assert code == 1
    assert "does not exist" in err

    code, _, err = run(capsys, "--db", db, "restore", db, "--yes")
    assert code == 1
    assert "same file" in err

    garbage = tmp_path / "garbage.db"
    sqlite3.connect(garbage).close()  # valid SQLite file, but never migrated
    code, _, err = run(capsys, "--db", db, "--backup-dir", bk, "restore", str(garbage), "--yes")
    assert code == 1
    assert "failed verification" in err
    assert not Path(bk).exists()  # nothing touched when verification fails


def test_verify_reports_tampered_migration(capsys, paths) -> None:
    db, _ = paths
    run(capsys, "--db", db, "migrate")
    raw = sqlite3.connect(db)
    raw.execute("UPDATE schema_migrations SET checksum = 'deadbeef'")
    raw.commit()
    raw.close()
    code, out, _ = run(capsys, "--db", db, "verify")
    assert code == 1
    assert "modified after being applied" in out


def test_gen_key(capsys) -> None:
    code, out, _ = run(capsys, "gen-key")
    assert code == 0
    assert len(out.strip()) >= 32


# --- M2: llm-check (no inference) ------------------------------------------------------------


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)  # no stray .env
    for name in ("LLM_MODE", "OLLAMA_URL", "LLM_MODEL", "LLM_TIMEOUT_S"):
        monkeypatch.delenv(f"FLOWPILOT_{name}", raising=False)
    return monkeypatch


def _mock_ollama(monkeypatch: pytest.MonkeyPatch, handler) -> list:
    import httpx

    from flowpilot import cli
    from flowpilot.llm.client import OllamaClient

    seen: list = []

    def recording(request):
        seen.append((request.method, request.url.path))
        return handler(request)

    def factory(**kwargs):
        return OllamaClient(**kwargs, transport=httpx.MockTransport(recording))

    monkeypatch.setattr(cli, "OllamaClient", factory)
    return seen


def test_llm_check_ok_and_uses_tags_only(capsys, llm_env) -> None:
    import httpx

    seen = _mock_ollama(
        llm_env, lambda r: httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
    )
    code, out, _ = run(capsys, "llm-check")
    assert code == 0
    assert "mode: off" in out
    assert "FLOWPILOT_LLM_MODE=off" in out
    assert "OK model 'qwen2.5:3b' is installed" in out
    assert seen == [("GET", "/api/tags")]  # never /api/chat or /api/pull


def test_llm_check_model_missing(capsys, llm_env) -> None:
    import httpx

    llm_env.setenv("FLOWPILOT_LLM_MODE", "ollama")
    llm_env.setenv("FLOWPILOT_LLM_MODEL", "llama9:70b")
    _mock_ollama(llm_env, lambda r: httpx.Response(200, json={"models": []}))
    code, out, _ = run(capsys, "llm-check")
    assert code == 1
    assert "installed models: none" in out
    assert "nothing was downloaded" in out


def test_llm_check_unreachable(capsys, llm_env) -> None:
    import httpx

    def refuse(request):
        raise httpx.ConnectError("refused")

    _mock_ollama(llm_env, refuse)
    code, out, _ = run(capsys, "llm-check")
    assert code == 1
    assert "FAIL ollama unreachable" in out


def test_llm_check_rejects_unsafe_url(capsys, llm_env) -> None:
    llm_env.setenv("FLOWPILOT_OLLAMA_URL", "http://203.0.113.5:11434")
    code, _, err = run(capsys, "llm-check")
    assert code == 1
    assert "loopback" in err
