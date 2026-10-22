"""M5 reviewer console: configuration and the read-only reviewer check (no Streamlit needed)."""

import tomllib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from flowpilot.db.connection import connect
from flowpilot.services.reviewers import add_reviewer, revoke_reviewer
from flowpilot.settings import SettingsError
from flowpilot.ui.api import (
    DEFAULT_API_URL,
    LoginOutcome,
    ReviewerIdentity,
    UIConfig,
    load_ui_config,
    verify_reviewer,
)
from tests.conftest import API_KEY, FIXED_NOW

ROOT = Path(__file__).resolve().parent.parent
CONFIG = UIConfig(api_url=DEFAULT_API_URL, api_key=API_KEY)
TOKEN = "fpr_" + "a" * 43


def transport(status: int, body: object = None, seen: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


# --- configuration -------------------------------------------------------------------------


def test_defaults_to_local_api(tmp_path: Path) -> None:
    config = load_ui_config({"FLOWPILOT_API_KEY": API_KEY}, env_file=tmp_path / "missing")
    assert config.api_url == DEFAULT_API_URL


def test_reads_env_file_and_env_overrides(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"FLOWPILOT_API_KEY={API_KEY}\nFLOWPILOT_API_URL=http://localhost:9000/\n")
    assert load_ui_config({}, env_file=env).api_url == "http://localhost:9000"
    overridden = load_ui_config({"FLOWPILOT_API_URL": "http://[::1]:8000"}, env_file=env)
    assert overridden.api_url == "http://[::1]:8000"


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.65.254:8000",
        "http://api.example.com",
        "http://0.0.0.0:8000",
        "ftp://127.0.0.1",
        "http://user:pw@127.0.0.1:8000",
        "http://127.0.0.1:8000/v1",
        "http://127.0.0.1:8000?x=1",
        "http://127.0.0.1:99999",
        "127.0.0.1:8000",
    ],
)
def test_rejects_non_loopback_or_unsafe_api_url(url: str) -> None:
    with pytest.raises(SettingsError, match="FLOWPILOT_API_URL"):
        UIConfig(api_url=url, api_key=API_KEY)


@pytest.mark.parametrize("key", ["", "k3y-0123"])
def test_rejects_missing_or_short_api_key(key: str) -> None:
    with pytest.raises(SettingsError, match="FLOWPILOT_API_KEY") as excinfo:
        UIConfig(api_url=DEFAULT_API_URL, api_key=key)
    assert not key or key not in str(excinfo.value)


def test_repr_hides_api_key() -> None:
    assert API_KEY not in repr(CONFIG)


def test_streamlit_config_is_local_first() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["browser"]["gatherUsageStats"] is False
    assert config["server"]["address"] == "127.0.0.1"
    assert config["server"]["enableXsrfProtection"] is True
    assert config["client"]["showErrorDetails"] == "none"


# --- reviewer check -------------------------------------------------------------------------


def test_ok_sends_both_headers_to_read_only_endpoint() -> None:
    seen: list[httpx.Request] = []
    result = verify_reviewer(
        CONFIG, f"  {TOKEN}\n", transport=transport(200, {"id": 3, "name": "avery"}, seen)
    )
    assert result.outcome is LoginOutcome.OK
    assert result.reviewer == ReviewerIdentity(id=3, name="avery")
    (request,) = seen
    assert request.method == "GET"
    assert request.url == f"{DEFAULT_API_URL}/reviewers/me"
    assert request.headers["X-API-Key"] == API_KEY
    assert request.headers["X-Reviewer-Token"] == TOKEN
    assert TOKEN not in repr(result)


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (403, LoginOutcome.INVALID_TOKEN),
        (401, LoginOutcome.API_KEY_REJECTED),
        (503, LoginOutcome.API_UNAVAILABLE),
        (500, LoginOutcome.API_UNAVAILABLE),
        (404, LoginOutcome.UNEXPECTED),
        (307, LoginOutcome.UNEXPECTED),
    ],
)
def test_maps_api_errors(status: int, outcome: LoginOutcome) -> None:
    result = verify_reviewer(CONFIG, TOKEN, transport=transport(status, {"detail": {}}))
    assert result.outcome is outcome
    assert result.reviewer is None


@pytest.mark.parametrize("body", [None, [], {"id": 1}, {"id": "1", "name": "x"}, {"name": "x"}])
def test_malformed_success_is_unexpected(body: object) -> None:
    assert verify_reviewer(CONFIG, TOKEN, transport=transport(200, body)).outcome is (
        LoginOutcome.UNEXPECTED
    )


def test_connection_error_is_api_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = verify_reviewer(CONFIG, TOKEN, transport=httpx.MockTransport(refuse))
    assert result.outcome is LoginOutcome.API_UNAVAILABLE


@pytest.mark.parametrize("token", ["", "   ", "fpr_ä", "fpr_\x00", "fpr_" + "a" * 300])
def test_unsendable_tokens_are_rejected_without_a_request(token: str) -> None:
    seen: list[httpx.Request] = []
    result = verify_reviewer(CONFIG, token, transport=transport(200, {}, seen))
    assert result.outcome is LoginOutcome.INVALID_TOKEN
    assert seen == []


def test_end_to_end_against_api(client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        token = add_reviewer(conn, "reviewer:avery", clock=lambda: FIXED_NOW)[1]
    finally:
        conn.close()
    config = UIConfig(api_url="http://127.0.0.1:8000", api_key=API_KEY)
    api = client._transport  # the in-process FastAPI app

    ok = verify_reviewer(config, token, transport=api)
    assert ok.outcome is LoginOutcome.OK
    assert ok.reviewer is not None
    assert ok.reviewer.name == "reviewer:avery"

    conn = connect(db_path)
    try:
        revoke_reviewer(conn, "reviewer:avery", clock=lambda: FIXED_NOW)
    finally:
        conn.close()
    assert verify_reviewer(config, token, transport=api).outcome is LoginOutcome.INVALID_TOKEN

    wrong_key = UIConfig(api_url=config.api_url, api_key=API_KEY + "x")
    assert verify_reviewer(wrong_key, token, transport=api).outcome is (
        LoginOutcome.API_KEY_REJECTED
    )
