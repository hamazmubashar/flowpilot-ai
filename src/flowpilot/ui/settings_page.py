"""Settings page (M5): read-only facts about this console session and its API connection.

Shows only what the console can know accurately: the reviewer confirmed by `GET /reviewers/me`
on this page load, the configured (loopback) API address, `GET /health`, and how reviewers are
managed. Nothing here can be edited; reviewer management stays in the `flowpilot-admin` CLI
(ADR 0004), and the API's AI mode is not exposed to the console, so it is not shown.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from flowpilot.ui import api
from flowpilot.ui.api import ApiOutcome, ReviewerIdentity, UIConfig

HEALTH = {
    "ok": ("valid", "Connected"),
    "degraded": ("pending", "Degraded"),
}
DATABASE = {
    "ok": ("valid", "Up to date"),
    "migrations_pending": ("pending", "Migrations pending: run flowpilot-admin migrate"),
    "unavailable": ("duplicate", "Unavailable"),
}
CLI = (
    ("uv run flowpilot-admin reviewer add NAME", "Create a reviewer; the token is shown once"),
    ("uv run flowpilot-admin reviewer list", "Names and status, never tokens"),
    ("uv run flowpilot-admin reviewer revoke NAME", "Takes effect on the reviewer's next request"),
)


def _badge(tone: str, text: str) -> str:
    return f'<b class="fp-badge fp-{tone}">{escape(text)}</b>'


def _facts(rows: tuple[tuple[str, str], ...]) -> str:
    return (
        '<dl class="fp-details">' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"
    )


def _connection(config: UIConfig, health: api.HealthResult) -> str:
    if health.outcome is ApiOutcome.API_UNAVAILABLE:
        api_state, db_state = _badge("duplicate", "Not reachable"), _badge("neutral", "Unknown")
    elif health.outcome is not ApiOutcome.OK:
        api_state, db_state = (
            _badge("duplicate", "Unexpected response"),
            _badge("neutral", "Unknown"),
        )
    else:
        api_state = _badge(*HEALTH.get(health.status, ("neutral", health.status)))
        db_state = _badge(*DATABASE.get(health.database, ("neutral", health.database)))
    return (
        '<section class="fp-panel"><h2>FlowPilot API</h2>'
        "<p>The console reaches the API over this machine's loopback address only.</p>"
        + _facts(
            (
                ("Address", escape(config.api_url)),
                ("API key", "Configured (never displayed)"),
                ("Status", api_state),
                ("Database", db_state),
            )
        )
        + '<p class="fp-panel-foot">Checked with <code>GET /health</code> when this page loaded.'
        "</p></section>"
    )


def render(config: UIConfig, reviewer: ReviewerIdentity) -> None:
    with st.spinner("Checking the API…"):
        health = api.fetch_health(config)
    commands = "".join(
        f"<li><code>{escape(cmd)}</code><span>{escape(note)}</span></li>" for cmd, note in CLI
    )
    st.html(
        '<div class="fp-grid fp-grid-tables">'
        '<section class="fp-panel"><h2>Your reviewer account</h2>'
        "<p>Confirmed by the FlowPilot API when this page loaded.</p>"
        + _facts(
            (
                ("Name", escape(reviewer.name)),
                ("Reviewer", f"#{int(reviewer.id)}"),
                ("Signed in with", "Your personal reviewer token (never displayed)"),
            )
        )
        + '<p class="fp-panel-foot">Your token is held only in this browser session on the '
        "console server. It is checked again on every page load, so a revoked token signs you "
        "out on the next one. Reloading the page also signs you out.</p></section>"
        + _connection(config, health)
        + '<section class="fp-panel"><h2>Managing reviewers</h2>'
        "<p>Reviewers are managed by an administrator on the machine that runs FlowPilot, not "
        "from this console.</p>"
        f'<ul class="fp-cli">{commands}</ul></section>'
        '<section class="fp-panel"><h2>What this console does not show</h2>'
        "<p>Some settings live only on the API server and are not exposed to the console:</p>"
        '<ul class="fp-plain"><li>AI drafting mode (<code>FLOWPILOT_LLM_MODE</code>) and the '
        "local model in use.</li><li>Secrets such as the API key and reviewer tokens.</li></ul>"
        '<p class="fp-panel-foot">Nothing on this page can be changed here.</p></section>'
        "</div>"
    )
    st.button("Check again", key="fp_settings_refresh", icon=":material/refresh:")
