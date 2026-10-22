"""Signed-in reviewer workspace (M5): sidebar navigation, header, and the Dashboard and Leads pages.

Rendered by `app.py` only for a signed-in session, so there is no separate Streamlit page (a
file under `pages/` would be reachable by URL without signing in). Every render first re-checks
the reviewer's token with `GET /reviewers/me`; a revoked token ends the session before any page
is shown. Both pages read live data from the FlowPilot API.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

import streamlit as st

from flowpilot.ui import (
    api,
    dashboard_page,
    drafts_page,
    leads_page,
    outbox_page,
    settings_page,
)
from flowpilot.ui.api import LoginOutcome, ReviewerIdentity, UIConfig
from flowpilot.ui.theme import workspace_stylesheet

PAGE_KEY = "fp_page"
DASHBOARD, LEADS, DRAFTS, OUTBOX, SETTINGS = "dashboard", "leads", "drafts", "outbox", "settings"
# (page id or None when not built yet, label, icon class), in sidebar order.
NAV = (
    (DASHBOARD, "Dashboard", "home"),
    (LEADS, "Leads", "leads"),
    (DRAFTS, "Draft Responses", "draft"),
    (OUTBOX, "Outbox (Simulated)", "outbox"),
    (SETTINGS, "Settings", "settings"),
)

CREDIT = (
    '<div class="fp-credit"><p class="fp-credit-intro">Designed &amp; developed by</p>'
    '<p class="fp-credit-name">Hamaz Mubashar</p>'
    '<p class="fp-credit-role">AI Automation &amp; LLM Engineer</p></div>'
)

LIVE_NOTE = (
    '<p class="fp-sample-note"><span class="fp-live">Live data</span>'
    "Figures come from the FlowPilot API each time this page loads.</p>"
)


def _go(page: str) -> None:
    st.session_state[PAGE_KEY] = page


def current_page() -> str:
    page = st.session_state.get(PAGE_KEY, DASHBOARD)
    return page if page in (DASHBOARD, LEADS, DRAFTS, OUTBOX, SETTINGS) else DASHBOARD


def sidebar(active: str) -> None:
    unavailable = "".join(
        f'<div class="fp-nav-item" aria-disabled="true" title="Not available yet">'
        f'<i class="fp-ic fp-ic-{icon}"></i><span>{label}</span>'
        '<em class="fp-soon">Soon</em></div>'
        for page, label, icon in NAV
        if page is None
    )
    with st.sidebar:
        st.html(
            '<div class="fp-side-brand"><span class="fp-mark"></span>'
            '<span class="fp-wordmark">FlowPilot <span>AI</span></span></div>'
        )
        for page, label, _icon in NAV:
            if page is not None:
                st.button(
                    label,
                    key=f"fp_nav_{page}",
                    type="primary" if page == active else "tertiary",
                    width="stretch",
                    on_click=_go,
                    args=(page,),
                )
        if unavailable:
            st.html(f'<nav class="fp-nav" aria-label="Not yet available">{unavailable}</nav>')
        # The approval/sending assurances appear on the sign-in page only (app.assurances).
        st.html(f'<div class="fp-side-foot">{CREDIT}</div>')


def header(
    reviewer: dict,
    on_sign_out: Callable[[], None],
    *,
    title: str = "Leads",
    subtitle: str = "Capture, validate, enrich and manage your leads.",
    note: str = LIVE_NOTE,
    add_lead: bool = True,
) -> None:
    st.html(
        '<div class="fp-mobilebar"><span class="fp-mark"></span>'
        '<span class="fp-wordmark">FlowPilot <span>AI</span></span></div>'
    )
    with st.container(key="fp_head", horizontal=True, vertical_alignment="top"):
        st.html(f'<div class="fp-title"><h1>{title}</h1><p>{subtitle}</p>{note}</div>')
        with st.container(key="fp_head_actions", width="content", horizontal_alignment="right"):
            name = str(reviewer["name"])
            with st.popover(name, icon=":material/account_circle:", key="fp_operator"):
                st.html(
                    f'<p class="fp-who">Signed in as <strong>{escape(name)}</strong><br>'
                    f"Reviewer #{int(reviewer['id'])}</p>"
                )
                st.button(
                    "Sign out",
                    key="fp_sign_out",
                    icon=":material/logout:",
                    on_click=on_sign_out,
                    width="stretch",
                )
            if add_lead:
                st.button(
                    "Add Lead",
                    key="fp_add_lead",
                    type="primary",
                    icon=":material/add:",
                    on_click=leads_page.toggle_add,
                )


VERIFY_FAILURES = {
    LoginOutcome.API_UNAVAILABLE: (
        "The FlowPilot API is not reachable",
        "Your session could not be confirmed, so no lead data is shown. Start the API, then try "
        "again.",
    ),
    LoginOutcome.API_KEY_REJECTED: (
        "The console is not authorised to reach the FlowPilot API",
        "Check that it uses the same FLOWPILOT_API_KEY as the API.",
    ),
    LoginOutcome.UNEXPECTED: (
        "Your session could not be confirmed",
        "The FlowPilot API returned an unexpected response. Try again.",
    ),
}


def render(
    reviewer: dict,
    token: str,
    config: UIConfig,
    *,
    on_sign_out: Callable[[], None],
    on_session_rejected: Callable[[], None],
) -> None:
    st.html(workspace_stylesheet())
    check = api.verify_reviewer(config, token)
    if check.outcome is LoginOutcome.INVALID_TOKEN:
        on_session_rejected()  # revoked or unknown: back to sign-in before anything is shown
        st.rerun()
    page = current_page()
    sidebar(page)
    if page == LEADS:
        header(reviewer, on_sign_out)
    elif page == DRAFTS:
        header(
            reviewer,
            on_sign_out,
            title="Draft Responses",
            subtitle="Review AI-assisted replies before making a decision.",
            add_lead=False,
        )
    elif page == SETTINGS:
        header(
            reviewer,
            on_sign_out,
            title="Settings",
            subtitle="Your session and the FlowPilot API connection. Read-only.",
            add_lead=False,
        )
    elif page == OUTBOX:
        header(
            reviewer,
            on_sign_out,
            title="Outbox (Simulated)",
            subtitle="Replies approved by a reviewer. Nothing is actually sent.",
            add_lead=False,
        )
    else:
        header(
            reviewer,
            on_sign_out,
            title="Dashboard",
            subtitle="Overview of your lead pipeline and workflow activity.",
            add_lead=False,
        )
    if check.outcome is not LoginOutcome.OK:
        title, body = VERIFY_FAILURES[check.outcome]
        dashboard_page.notice(title, body)
        st.button("Try again", key="fp_retry", icon=":material/refresh:")
        return
    if page == LEADS:
        leads_page.render(config, reviewer)
    elif page == DRAFTS:
        drafts_page.render(config, token, on_session_rejected=on_session_rejected)
    elif page == OUTBOX:
        outbox_page.render(config)
    elif page == SETTINGS:
        confirmed = check.reviewer or ReviewerIdentity(int(reviewer["id"]), str(reviewer["name"]))
        settings_page.render(config, confirmed)
    else:
        dashboard_page.render(config)
