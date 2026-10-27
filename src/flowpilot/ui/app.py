"""FlowPilot AI reviewer console (M5), page 1: reviewer sign-in.

Run from the repository root: `uv run --group ui streamlit run src/flowpilot/ui/app.py`.

The reviewer's token is checked with `GET /reviewers/me` (read-only) and kept only in this
browser session's server-side `st.session_state`; it is never rendered, logged, written to disk
or sent to the browser. A page reload starts a new session and requires signing in again.
"""

from __future__ import annotations

import os

import streamlit as st

from flowpilot.settings import LOOPBACK_HOSTS, SettingsError
from flowpilot.ui import workspace
from flowpilot.ui.api import LoginOutcome, UIConfig, load_ui_config, verify_reviewer
from flowpilot.ui.theme import stylesheet

REVIEWER_KEY = "fp_reviewer"
# Session-state key names, not secrets. The token lives only in server-side session memory, and
# the input widget's state is emptied on every submit.
TOKEN_KEY = "fp_reviewer_token"  # noqa: S105
TOKEN_INPUT_KEY = "fp_token_input"  # noqa: S105
LOGIN_ERROR_KEY = "fp_login_error"
EMPTY = "empty"
GUEST_UNAVAILABLE = "guest_unavailable"
# Public demo only (ADR 0006): the shared demo reviewer token, which is public by design (ADR 0005
# decision 6). Never read outside FLOWPILOT_PUBLIC_DEMO=1, never rendered or logged.
GUEST_TOKEN_ENV = "FLOWPILOT_DEMO_REVIEWER_TOKEN"  # noqa: S105 - variable name, not a secret

ERRORS = {
    LoginOutcome.INVALID_TOKEN: (
        "That reviewer token was not accepted. It may be mistyped or revoked; ask your "
        "administrator for a new one if needed."
    ),
    LoginOutcome.API_KEY_REJECTED: (
        "The console is not authorised to reach the FlowPilot API. Check that it uses the same "
        "FLOWPILOT_API_KEY as the API."
    ),
    LoginOutcome.API_UNAVAILABLE: (
        "The FlowPilot API is not reachable. Make sure it is running, then try again."
    ),
    LoginOutcome.UNEXPECTED: "The FlowPilot API returned an unexpected response. Try again.",
    GUEST_UNAVAILABLE: (
        "The demo is refreshing its sample data right now. Give it a minute and try again."
    ),
}


def guest_token() -> str | None:
    """The demo reviewer token, offered as one-click guest access. Exists only in the explicit
    public demo; a normal console never offers guest access whatever its environment holds."""
    if os.environ.get("FLOWPILOT_PUBLIC_DEMO") != "1":
        return None
    token = os.environ.get(GUEST_TOKEN_ENV, "").strip()
    return token or None


def runtime_is_local() -> bool:
    """Fail closed if Streamlit was started without the repository's privacy settings. Only the
    explicit public demo (FLOWPILOT_PUBLIC_DEMO=1, ADR 0005) may bind 0.0.0.0, inside a private
    container network behind the TLS proxy."""
    allowed = set(LOOPBACK_HOSTS)
    if os.environ.get("FLOWPILOT_PUBLIC_DEMO") == "1":
        allowed.add("0.0.0.0")  # noqa: S104
    return (
        st.get_option("browser.gatherUsageStats") is False
        and st.get_option("server.address") in allowed
    )


def sign_out() -> None:
    """Forget the token, identity and all workspace state (filters, selections) of the session."""
    for key in [k for k in st.session_state if str(k).startswith("fp_")]:
        st.session_state.pop(key, None)


def story_panel() -> None:
    """Left half: brand, the real FlowPilot pipeline, and the promise of the product."""
    st.html(
        """<div class="fp-story">
        <div class="fp-brand"><span class="fp-mark"></span>
          <span class="fp-wordmark">FlowPilot <span>AI</span></span></div>
        <ul class="fp-tagline"><li>Lead management</li><li>AI workflows</li>
          <li>Human control</li></ul>
        <div class="fp-flow" role="img"
          aria-label="Workflow: capture leads, qualify with AI, then human review">
          <div class="fp-node"><i></i><b>Capture<br>leads</b></div>
          <div class="fp-node"><i></i><b>Qualify<br>with AI</b></div>
          <div class="fp-node"><i></i><b>Human<br>review</b></div>
        </div>
        <h2 class="fp-headline">Turn every lead into the next <span>opportunity.</span></h2>
        <p class="fp-copy">Qualify with clear rules, draft with local AI, and keep every
          decision under human control.</p>
        </div>"""
    )


def assurances() -> None:
    st.html(
        """
        <div class="fp-footer">
          <div class="fp-assure">
            <span>Human approval required</span>
            <span class="fp-assure-separator">·</span>
            <span>No messages sent automatically</span>
          </div>
          <div class="fp-credit">
            <p class="fp-credit-intro">Designed &amp; developed by</p>
            <p class="fp-credit-name">Hamaz Mubashar</p>
            <p class="fp-credit-role">AI Automation &amp; LLM Engineer</p>
          </div>
        </div>
        """
    )


def submit_login(config: UIConfig) -> None:
    """Form callback: runs before the page re-renders, so the token is wiped from the widget
    state whatever the outcome and only a successful check keeps it (server-side)."""
    token = st.session_state.get(TOKEN_INPUT_KEY, "").strip()
    st.session_state[TOKEN_INPUT_KEY] = ""
    st.session_state.pop(LOGIN_ERROR_KEY, None)
    if not token:
        st.session_state[LOGIN_ERROR_KEY] = EMPTY
        return
    result = verify_reviewer(config, token)
    if result.outcome is LoginOutcome.OK and result.reviewer is not None:
        st.session_state[TOKEN_KEY] = token
        st.session_state[REVIEWER_KEY] = {"id": result.reviewer.id, "name": result.reviewer.name}
    else:
        st.session_state[LOGIN_ERROR_KEY] = result.outcome


def start_guest(config: UIConfig) -> None:
    """Button callback: sign in as the demo reviewer with the server-side demo token. The token
    never reaches the browser; the session holds it exactly as after a typed sign-in."""
    st.session_state.pop(LOGIN_ERROR_KEY, None)
    token = guest_token()
    if token is None:
        st.session_state[LOGIN_ERROR_KEY] = GUEST_UNAVAILABLE
        return
    result = verify_reviewer(config, token)
    if result.outcome is LoginOutcome.OK and result.reviewer is not None:
        st.session_state[TOKEN_KEY] = token
        st.session_state[REVIEWER_KEY] = {"id": result.reviewer.id, "name": result.reviewer.name}
    elif result.outcome is LoginOutcome.INVALID_TOKEN:
        st.session_state[LOGIN_ERROR_KEY] = GUEST_UNAVAILABLE
    else:
        st.session_state[LOGIN_ERROR_KEY] = result.outcome


def guest_entry(config: UIConfig) -> None:
    st.html(
        '<div class="fp-welcome"><h1>Try the live demo</h1>'
        "<p>No sign-up needed. You'll review leads as the demo reviewer.</p></div>"
    )
    st.button(
        "Explore the demo",
        key="fp_guest",
        type="primary",
        icon=":material/arrow_forward:",
        icon_position="right",
        width="stretch",
        on_click=start_guest,
        args=(config,),
    )
    st.html(
        '<p class="fp-note">Sample data only. It resets every hour, and approving a draft '
        "only writes to a simulated outbox.</p>"
        '<p class="fp-note">Have a reviewer token? Sign in below.</p>'
    )


def login_form(config: UIConfig) -> None:
    guest = guest_token() is not None
    if guest:
        guest_entry(config)
    else:
        st.html(
            '<div class="fp-welcome"><h1>Welcome back</h1>'
            "<p>Sign in to your reviewer workspace</p></div>"
        )
    with (
        st.container(key="fp_token_secondary" if guest else "fp_token_primary"),
        st.form("reviewer_login", border=False),
    ):
        st.text_input(
            "Reviewer token",
            key=TOKEN_INPUT_KEY,
            type="password",  # Streamlit's own show/hide toggle, client-side only
            placeholder="fpr_…",
            autocomplete="off",
        )
        st.form_submit_button(
            "Sign in",
            type="secondary" if guest else "primary",
            icon=":material/arrow_forward:",
            icon_position="right",
            width="stretch",
            on_click=submit_login,
            args=(config,),
        )

    error = st.session_state.pop(LOGIN_ERROR_KEY, None)
    if error == EMPTY:
        st.warning("Enter your reviewer token to sign in.")
    elif error is not None:
        st.error(ERRORS[error])
    st.html(
        '<p class="fp-note">Your token stays in this session and is never saved.<br>'
        "Nothing is sent without human approval.</p>"
    )


def end_rejected_session() -> None:
    """The API no longer accepts the session's token (revoked): sign out and say why."""
    sign_out()
    st.session_state[LOGIN_ERROR_KEY] = LoginOutcome.INVALID_TOKEN


def load_config_or_stop() -> UIConfig:
    try:
        return load_ui_config()
    except SettingsError as exc:
        st.error(f"Console configuration error: {exc}")
        st.stop()


def require_local_runtime() -> None:
    if not runtime_is_local():
        st.error(
            "Refusing to run: start the console from the repository root so "
            ".streamlit/config.toml applies (loopback bind, usage statistics off)."
        )
        st.stop()


def main() -> None:
    reviewer = st.session_state.get(REVIEWER_KEY)
    is_signed_in = reviewer is not None and TOKEN_KEY in st.session_state
    st.set_page_config(
        page_title="Leads · FlowPilot AI" if is_signed_in else "Sign in · FlowPilot AI",
        page_icon=":material/shield_lock:",
        layout="wide",
        initial_sidebar_state="locked" if is_signed_in else "collapsed",
        menu_items={},
    )
    if is_signed_in:
        require_local_runtime()
        workspace.render(
            reviewer,
            st.session_state[TOKEN_KEY],
            load_config_or_stop(),
            on_sign_out=sign_out,
            on_session_rejected=end_rejected_session,
        )
        return

    st.html(stylesheet())
    require_local_runtime()
    config = load_config_or_stop()

    with st.container(key="fp_shell"):
        story, signin = st.columns([1.12, 1], gap=None)
        with story:
            story_panel()
        with signin, st.container(key="fp_signin"):
            login_form(config)
            assurances()


main()
