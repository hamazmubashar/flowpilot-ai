"""M5 page 1: reviewer sign-in, rendered with Streamlit's in-process AppTest.

Skipped unless the optional `ui` group is installed (`uv run --group ui pytest`).
"""

from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from flowpilot.ui import api
from flowpilot.ui.api import LoginOutcome, LoginResult, ReviewerIdentity, UIConfig
from tests.conftest import API_KEY

APP = str(Path(__file__).resolve().parent.parent / "src" / "flowpilot" / "ui" / "app.py")
TOKEN = "fpr_" + "s3cret" * 7


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Never read the real `.env`; record tokens instead of calling the API."""
    seen: list[str] = []
    monkeypatch.setattr(
        api, "load_ui_config", lambda: UIConfig(api_url="http://127.0.0.1:8000", api_key=API_KEY)
    )
    # After sign-in the Dashboard loads; answer for the API instead of calling a server.
    monkeypatch.setattr(api, "fetch_leads", lambda c: api.LeadsSnapshot(api.ApiOutcome.OK, (), 0))
    return seen


def fake_verify(monkeypatch: pytest.MonkeyPatch, seen: list[str], result: LoginResult) -> None:
    def verify(config: UIConfig, token: str) -> LoginResult:
        seen.append(token)
        return result

    monkeypatch.setattr(api, "verify_reviewer", verify)


def rendered(at: AppTest) -> str:
    parts = [str(e.value) for e in (*at.markdown, *at.error, *at.warning, *at.caption)]
    parts += [str(getattr(e, "proto", "")) for e in at.get("html")]
    return "\n".join(parts)


def sign_in(at: AppTest, token: str) -> AppTest:
    at.text_input[0].input(token)
    at.button[0].click()
    return at.run()


def test_login_form_uses_password_input(calls: list[str]) -> None:
    at = AppTest.from_file(APP).run()
    assert not at.exception
    (field,) = at.text_input
    assert field.label == "Reviewer token"
    assert field.proto.type == field.proto.PASSWORD
    page = rendered(at)
    assert "Welcome back" in page
    assert "Turn every lead into the next" in page
    assert "Human<br>review" in page
    assert "Hamaz Mubashar" in page
    assert "Human approval required" in page
    assert "No messages sent automatically" in page
    assert "<a " not in page  # the credit is plain text, no external links
    # Reviewer tokens only: no fake email/password, social, remember-me or recovery features.
    assert len(at.button) == 1
    assert len(at.checkbox) == 0
    for absent in ("email", "Forgot", "Remember me", "Google", "Sign up"):
        assert absent not in page


def test_valid_token_signs_in_without_displaying_it(
    monkeypatch: pytest.MonkeyPatch, calls: list[str]
) -> None:
    fake_verify(monkeypatch, calls, LoginResult(LoginOutcome.OK, ReviewerIdentity(7, "avery")))
    at = sign_in(AppTest.from_file(APP).run(), f" {TOKEN} ")

    assert not at.exception
    assert calls == [TOKEN, TOKEN]  # sign-in, then the workspace's session re-check
    assert at.session_state["fp_reviewer"] == {"id": 7, "name": "avery"}
    assert at.session_state["fp_reviewer_token"] == TOKEN
    assert at.session_state["fp_token_input"] == ""
    assert len(at.text_input) == 0  # token form replaced by the Dashboard
    page = rendered(at)
    assert "<h1>Dashboard</h1>" in page
    assert TOKEN not in page

    at.button(key="fp_sign_out").click()
    at.run()
    assert "fp_reviewer_token" not in at.session_state
    assert "fp_reviewer" not in at.session_state
    assert at.text_input[0].value == ""


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (LoginOutcome.INVALID_TOKEN, "not accepted"),
        (LoginOutcome.API_UNAVAILABLE, "not reachable"),
        (LoginOutcome.API_KEY_REJECTED, "not authorised"),
        (LoginOutcome.UNEXPECTED, "unexpected response"),
    ],
)
def test_failures_show_safe_message_and_clear_the_field(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], outcome: LoginOutcome, message: str
) -> None:
    fake_verify(monkeypatch, calls, LoginResult(outcome))
    at = sign_in(AppTest.from_file(APP).run(), TOKEN)

    assert not at.exception
    assert message in at.error[0].value
    assert "fp_reviewer_token" not in at.session_state
    assert at.session_state["fp_token_input"] == ""
    assert at.text_input[0].value == ""
    assert TOKEN not in rendered(at)

    at.run()  # an unrelated rerun clears the message
    assert not at.error


def test_empty_submission_does_not_call_api(
    monkeypatch: pytest.MonkeyPatch, calls: list[str]
) -> None:
    fake_verify(monkeypatch, calls, LoginResult(LoginOutcome.OK, ReviewerIdentity(1, "x")))
    at = sign_in(AppTest.from_file(APP).run(), "   ")
    assert calls == []
    assert "Enter your reviewer token" in at.warning[0].value


def test_stylesheet_is_local_only() -> None:
    from flowpilot.ui.theme import SVG_NS, stylesheet

    css = stylesheet()
    assert "@import" not in css
    assert "<link" not in css
    assert "<script" not in css
    # The SVG namespace identifier is the only URL, and it is never fetched.
    assert css.replace(quote(SVG_NS, safe=" =:/,.()-"), "").count("http") == 0
    assert "prefers-reduced-motion" in css


def test_configuration_error_is_reported_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> UIConfig:
        return UIConfig(api_url="http://10.0.0.5:8000", api_key=API_KEY)

    monkeypatch.setattr(api, "load_ui_config", broken)
    at = AppTest.from_file(APP).run()
    assert "FLOWPILOT_API_URL" in at.error[0].value
    assert API_KEY not in rendered(at)
    assert len(at.text_input) == 0


# --- public demo guest access (ADR 0006) ---------------------------------------------------------

DEMO_TOKEN = "fpr_" + "PublicDemoGuest_" * 3


@pytest.fixture
def public_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWPILOT_PUBLIC_DEMO", "1")
    monkeypatch.setenv("FLOWPILOT_DEMO_REVIEWER_TOKEN", DEMO_TOKEN)


def test_guest_access_is_never_offered_outside_the_public_demo(
    monkeypatch: pytest.MonkeyPatch, calls: list[str]
) -> None:
    monkeypatch.delenv("FLOWPILOT_PUBLIC_DEMO", raising=False)
    monkeypatch.setenv("FLOWPILOT_DEMO_REVIEWER_TOKEN", DEMO_TOKEN)  # present but must be ignored
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert len(at.button) == 1  # only "Sign in"
    page = rendered(at)
    assert "Explore the demo" not in page
    assert "Welcome back" in page


def test_guest_button_signs_in_without_rendering_the_token(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], public_demo: None
) -> None:
    fake_verify(monkeypatch, calls, LoginResult(LoginOutcome.OK, ReviewerIdentity(3, "demo")))
    at = AppTest.from_file(APP).run()
    assert not at.exception
    page = rendered(at)
    assert "Try the live demo" in page
    assert DEMO_TOKEN not in page
    assert len(at.text_input) == 1  # token sign-in stays available

    at.button(key="fp_guest").click()
    at.run()

    assert not at.exception
    assert calls == [DEMO_TOKEN, DEMO_TOKEN]  # guest sign-in, then the workspace re-check
    assert at.session_state["fp_reviewer"] == {"id": 3, "name": "demo"}
    page = rendered(at)
    assert "<h1>Dashboard</h1>" in page
    assert DEMO_TOKEN not in page


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (LoginOutcome.INVALID_TOKEN, "refreshing its sample data"),
        (LoginOutcome.API_UNAVAILABLE, "not reachable"),
    ],
)
def test_guest_failure_explains_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    public_demo: None,
    outcome: LoginOutcome,
    message: str,
) -> None:
    fake_verify(monkeypatch, calls, LoginResult(outcome))
    at = AppTest.from_file(APP).run()
    at.button(key="fp_guest").click()
    at.run()
    assert not at.exception
    assert message in at.error[0].value
    assert "fp_reviewer_token" not in at.session_state
    assert DEMO_TOKEN not in rendered(at)


def test_public_demo_without_a_token_falls_back_to_sign_in(
    monkeypatch: pytest.MonkeyPatch, calls: list[str]
) -> None:
    monkeypatch.setenv("FLOWPILOT_PUBLIC_DEMO", "1")
    monkeypatch.setenv("FLOWPILOT_DEMO_REVIEWER_TOKEN", "   ")
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert len(at.button) == 1
    assert "Explore the demo" not in rendered(at)


def test_guest_button_styles_outrank_the_panel_button_rule() -> None:
    """Regression: `.st-key-fp_signin [data-testid="stButton"] button` (white background) beat a
    plain `.st-key-fp_guest button` rule, leaving white text on white: an empty-looking box."""
    from flowpilot.ui.theme import stylesheet

    css = stylesheet()
    guest = '.st-key-fp_signin .st-key-fp_guest [data-testid="stButton"] button'
    panel = '.st-key-fp_signin [data-testid="stButton"] button {'
    # One more class than the panel rule, so the guest style wins wherever it appears.
    assert f"{guest} {{" in css
    assert f"{guest} span {{" in css
    assert panel in css
    assert "\n.st-key-fp_guest button" not in css
