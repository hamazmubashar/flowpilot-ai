"""M5 Settings: the health client and the read-only page."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from flowpilot.ui import api
from flowpilot.ui.api import (
    ApiOutcome,
    HealthResult,
    LeadsSnapshot,
    LoginOutcome,
    LoginResult,
    ReviewerIdentity,
    UIConfig,
    fetch_health,
)
from tests.conftest import API_KEY

CONFIG = UIConfig(api_url="http://127.0.0.1:8000", api_key=API_KEY)
APP = str(Path(__file__).resolve().parent.parent / "src" / "flowpilot" / "ui" / "app.py")
TOKEN = "fpr_" + "s3ttings" * 5


def test_health_against_the_real_api(client: TestClient) -> None:
    result = fetch_health(CONFIG, transport=client._transport)
    assert result == HealthResult(ApiOutcome.OK, "ok", "ok")


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (503, {"status": "degraded", "database": "migrations_pending"},
         HealthResult(ApiOutcome.OK, "degraded", "migrations_pending")),
        (500, {"status": "ok", "database": "ok"}, HealthResult(ApiOutcome.UNEXPECTED)),
        (200, {"status": 1, "database": "ok"}, HealthResult(ApiOutcome.UNEXPECTED)),
        (200, ["ok"], HealthResult(ApiOutcome.UNEXPECTED)),
    ],
)  # fmt: skip
def test_health_responses(status: int, body: object, expected: HealthResult) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    assert fetch_health(CONFIG, transport=transport) == expected


def test_health_unreachable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert fetch_health(CONFIG, transport=httpx.MockTransport(refuse)).outcome is (
        ApiOutcome.API_UNAVAILABLE
    )


# --- rendered page -------------------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stands in for the API: never reads `.env` or calls a server."""
    state: dict = {
        # The API's answer, deliberately different from the stored session identity.
        "verify": LoginResult(LoginOutcome.OK, ReviewerIdentity(7, "reviewer:avery")),
        "health": HealthResult(ApiOutcome.OK, "ok", "ok"),
        "health_calls": 0,
    }
    monkeypatch.setattr(api, "load_ui_config", lambda: CONFIG)
    monkeypatch.setattr(api, "verify_reviewer", lambda c, t: state["verify"])
    monkeypatch.setattr(api, "fetch_leads", lambda c: LeadsSnapshot(ApiOutcome.OK, (), 0))

    def health(config):
        state["health_calls"] += 1
        return state["health"]

    monkeypatch.setattr(api, "fetch_health", health)
    return state


def html(at) -> str:
    return "\n".join(e.proto.body for e in at.get("html"))


def settings():
    app_test = pytest.importorskip("streamlit.testing.v1").AppTest  # needs the `ui` group
    at = app_test.from_file(APP)
    at.session_state["fp_reviewer"] = {"id": 99, "name": "stale-session-name"}
    at.session_state["fp_reviewer_token"] = TOKEN
    at.run()
    return at.button(key="fp_nav_settings").click().run()


def test_settings_shows_confirmed_identity_and_connection(fake: dict) -> None:
    at = settings()
    assert not at.exception
    page = html(at)
    assert "<h1>Settings</h1>" in page
    assert "Read-only" in page
    assert "<dd>reviewer:avery</dd>" in page  # from the API check, not the stored session
    assert "<dd>#7</dd>" in page
    assert "stale-session-name</dd>" not in page
    assert "<dd>http://127.0.0.1:8000</dd>" in page
    assert '<b class="fp-badge fp-valid">Connected</b>' in page
    assert '<b class="fp-badge fp-valid">Up to date</b>' in page
    assert "flowpilot-admin reviewer add NAME" in page
    assert "flowpilot-admin reviewer revoke NAME" in page
    assert at.button(key="fp_nav_settings").proto.type == "primary"


def test_settings_never_shows_secrets_or_unknown_configuration(fake: dict) -> None:
    page = html(settings())
    assert TOKEN not in page
    assert API_KEY not in page
    assert "Configured (never displayed)" in page
    assert "FLOWPILOT_LLM_MODE" in page  # named only to say it is not exposed
    assert "not exposed to the console" in page
    for mode in ("Mode: off", "Mode: ollama", "qwen"):
        assert mode not in page


def test_settings_is_read_only(fake: dict) -> None:
    at = settings()
    assert len(at.text_input) == 0
    assert len(at.selectbox) == 0
    assert len(at.checkbox) == 0
    actions = [b.key for b in at.button if not b.key.startswith("fp_nav_")]
    assert actions == ["fp_sign_out", "fp_settings_refresh"]
    at.button(key="fp_settings_refresh").click().run()
    assert fake["health_calls"] == 2


@pytest.mark.parametrize(
    ("health", "api_text", "db_text"),
    [
        (HealthResult(ApiOutcome.OK, "degraded", "migrations_pending"), "Degraded",
         "Migrations pending"),
        (HealthResult(ApiOutcome.OK, "degraded", "unavailable"), "Degraded", "Unavailable"),
        (HealthResult(ApiOutcome.API_UNAVAILABLE), "Not reachable", "Unknown"),
        (HealthResult(ApiOutcome.UNEXPECTED), "Unexpected response", "Unknown"),
    ],
)  # fmt: skip
def test_health_states(fake: dict, health: HealthResult, api_text: str, db_text: str) -> None:
    fake["health"] = health
    page = html(settings())
    assert api_text in page
    assert db_text in page


def test_revoked_session_never_reaches_settings(fake: dict) -> None:
    at = settings()
    fake["verify"] = LoginResult(LoginOutcome.INVALID_TOKEN)
    calls = fake["health_calls"]
    at.run()
    assert "Welcome back" in html(at)
    assert fake["health_calls"] == calls
    assert "fp_reviewer_token" not in at.session_state


def test_unconfirmed_session_shows_no_settings(fake: dict) -> None:
    at = settings()
    fake["verify"] = LoginResult(LoginOutcome.API_UNAVAILABLE)
    at.run()
    page = html(at)
    assert "not reachable" in page
    assert "Your reviewer account" not in page
