"""Structured, privacy-safe application logs: what is logged, and what never is."""

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpilot.api.app import create_app
from flowpilot.logs import LOGGER, JsonFormatter, configure_logging
from flowpilot.settings import Settings, SettingsError, load_settings
from tests.conftest import API_KEY, FIXED_NOW
from tests.fakes import FakeLLMClient


@pytest.fixture
def captured() -> list[logging.LogRecord]:
    records: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Keep()
    logger = logging.getLogger(LOGGER)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def lines(records: list[logging.LogRecord]) -> list[dict]:
    formatter = JsonFormatter()
    return [json.loads(formatter.format(r)) for r in records]


def test_request_log_has_operational_fields_only(
    client: TestClient, auth: dict, lead_payload: dict, captured: list
) -> None:
    created = client.post("/leads", json=lead_payload, headers=auth)
    lead_id = created.json()["lead"]["id"]
    client.get(f"/leads/{lead_id}?secret=query-value", headers={**auth, "X-Request-ID": "rid-1"})

    logged = lines(captured)
    request = [entry for entry in logged if entry.get("event") == "request"]
    assert [(e["method"], e["route"], e["status"]) for e in request] == [
        ("POST", "/leads", 201),
        ("GET", "/leads/{lead_id}", 200),
    ]
    assert request[1]["request_id"] == "rid-1"
    assert isinstance(request[1]["duration_ms"], float)
    text = json.dumps(logged)
    for secret in (API_KEY, "query-value", lead_payload["contact_email"], lead_payload["message"]):
        assert secret.lower() not in text.lower()


def test_unmatched_paths_are_not_logged_verbatim(client: TestClient, captured: list) -> None:
    client.get("/no/such/rowan.hollis@glasswing.example")
    (entry,) = lines(captured)
    assert (entry["route"], entry["status"]) == ("unmatched", 404)
    assert "rowan" not in json.dumps(entry)


def test_unhandled_error_logs_type_and_location_not_message(
    settings: Settings, auth: dict, lead_payload: dict, captured: list
) -> None:
    crash = FakeLLMClient(RuntimeError("Rowan Hollis said: confidential lead text"))
    app = create_app(settings, clock=lambda: FIXED_NOW, llm=crash)
    with TestClient(app, raise_server_exceptions=False) as client:
        lead_id = client.post("/leads", json=lead_payload, headers=auth).json()["lead"]["id"]
        response = client.post(f"/leads/{lead_id}/qualification", headers=auth)
    assert response.status_code == 500
    assert "confidential" not in response.text
    errors = [e for e in lines(captured) if e.get("event") == "unhandled_error"]
    assert len(errors) == 1
    error = errors[0]
    assert (error["level"], error["error_type"]) == ("ERROR", "RuntimeError")
    assert error["route"] == "/leads/{lead_id}/qualification"
    assert any("assist.py" in frame for frame in error["stack"])
    assert "confidential" not in json.dumps(error)
    assert "Rowan" not in json.dumps(error)


def test_configure_logging_writes_json_lines_once(capsys) -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream)
    configure_logging("INFO", stream)  # idempotent: no duplicate handlers
    logger = logging.getLogger("flowpilot.test")
    logger.debug("hidden at INFO")
    logger.info("hello", extra={"event": "probe", "status": 200, "unlisted": "dropped"})
    output = stream.getvalue().strip().splitlines()
    assert len(output) == 1
    entry = json.loads(output[0])
    assert (entry["msg"], entry["event"], entry["status"], entry["level"]) == (
        "hello", "probe", 200, "INFO",
    )  # fmt: skip
    assert "unlisted" not in entry
    assert capsys.readouterr().err == ""  # not propagated to the root logger


def test_log_level_setting(tmp_path: Path) -> None:
    env = {"FLOWPILOT_API_KEY": API_KEY, "FLOWPILOT_DB_PATH": str(tmp_path / "x.db")}
    assert load_settings(env, env_file=None).log_level == "INFO"
    assert load_settings({**env, "FLOWPILOT_LOG_LEVEL": "debug"}, env_file=None).log_level == (
        "DEBUG"
    )
    with pytest.raises(SettingsError, match="FLOWPILOT_LOG_LEVEL"):
        load_settings({**env, "FLOWPILOT_LOG_LEVEL": "verbose"}, env_file=None)
    assert API_KEY not in repr(load_settings(env, env_file=None))
