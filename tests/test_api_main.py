from pathlib import Path
from typing import Any

import pytest
import uvicorn

from flowpilot.api.__main__ import main
from tests.conftest import API_KEY


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    return calls


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no stray .env
    for name in ("API_KEY", "DB_PATH", "HOST", "PORT", "BACKUP_DIR", "ENABLE_DOCS"):
        monkeypatch.delenv(f"FLOWPILOT_{name}", raising=False)


@pytest.mark.usefixtures("clean_env")
def test_refuses_to_start_without_key(no_server, capsys) -> None:
    assert main() == 2
    assert "FLOWPILOT_API_KEY is not set" in capsys.readouterr().err
    assert no_server == []


@pytest.mark.usefixtures("clean_env")
def test_refuses_non_loopback_host(monkeypatch, no_server, capsys) -> None:
    monkeypatch.setenv("FLOWPILOT_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWPILOT_HOST", "0.0.0.0")  # noqa: S104
    assert main() == 2
    assert "loopback" in capsys.readouterr().err
    assert no_server == []


@pytest.mark.usefixtures("clean_env")
def test_refuses_missing_database(monkeypatch, no_server, capsys) -> None:
    monkeypatch.setenv("FLOWPILOT_API_KEY", API_KEY)
    assert main() == 2
    assert "flowpilot-admin migrate" in capsys.readouterr().err
    assert no_server == []


@pytest.mark.usefixtures("clean_env")
def test_serves_on_loopback(monkeypatch, no_server, db_path: Path) -> None:
    monkeypatch.setenv("FLOWPILOT_API_KEY", API_KEY)
    monkeypatch.setenv("FLOWPILOT_DB_PATH", str(db_path))
    assert main() == 0
    assert no_server == [
        {
            "host": "127.0.0.1",
            "port": 8000,
            "proxy_headers": False,
            "server_header": False,
            "access_log": False,  # replaced by flowpilot's privacy-safe request log
        }
    ]
