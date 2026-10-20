from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpilot.api.app import StartupError, create_app
from flowpilot.settings import Settings
from tests.conftest import API_KEY

PROTECTED = [
    ("POST", "/leads"),
    ("GET", "/leads"),
    ("GET", "/leads/1"),
    ("GET", "/leads/1/audit"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_missing_key_is_401(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_wrong_key_is_401(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, headers={"X-API-Key": API_KEY + "x"})
    assert response.status_code == 401


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_request_id_generated_and_echoed(client: TestClient) -> None:
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) == 32
    assert (
        client.get("/health", headers={"X-Request-ID": "n8n-exec-42"}).headers["X-Request-ID"]
        == "n8n-exec-42"
    )
    # unsafe values are replaced, not reflected
    assert (
        client.get("/health", headers={"X-Request-ID": "bad id\x7f"}).headers["X-Request-ID"]
        != "bad id\x7f"
    )


def test_docs_disabled_by_default(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_docs_can_be_enabled(settings: Settings) -> None:
    enabled = Settings(
        api_key=settings.api_key,
        db_path=settings.db_path,
        backup_dir=settings.backup_dir,
        enable_docs=True,
    )
    with TestClient(create_app(enabled)) as c:
        assert c.get("/docs").status_code == 200


def test_startup_refuses_missing_database(tmp_path: Path) -> None:
    s = Settings(api_key=API_KEY, db_path=tmp_path / "absent.db", backup_dir=tmp_path)
    with pytest.raises(StartupError, match="does not exist"), TestClient(create_app(s)):
        pass


def test_startup_refuses_unmigrated_database(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    empty.touch()
    s = Settings(api_key=API_KEY, db_path=empty, backup_dir=tmp_path)
    with pytest.raises(StartupError, match="pending migrations"), TestClient(create_app(s)):
        pass


def test_health_degraded_when_migrations_pending(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    empty.touch()
    s = Settings(api_key=API_KEY, db_path=empty, backup_dir=tmp_path)
    c = TestClient(create_app(s))  # not a context manager: skip lifespan startup check
    response = c.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "migrations_pending"}


def test_health_unavailable_when_db_unreadable(tmp_path: Path) -> None:
    s = Settings(api_key=API_KEY, db_path=tmp_path / "no-dir" / "x.db", backup_dir=tmp_path)
    response = TestClient(create_app(s)).get("/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_llm_client_built_from_settings_and_closed(settings: Settings) -> None:
    from flowpilot.llm.client import OllamaClient
    from flowpilot.settings import LLMConfig

    enabled = Settings(
        api_key=settings.api_key,
        db_path=settings.db_path,
        backup_dir=settings.backup_dir,
        llm=LLMConfig(mode="ollama"),
    )
    app = create_app(enabled)
    client = app.state.llm
    assert isinstance(client, OllamaClient)  # constructing it makes no network call
    with TestClient(app):
        pass
    assert client._http.is_closed


def test_llm_client_absent_when_mode_off(settings: Settings) -> None:
    assert create_app(settings).state.llm is None
