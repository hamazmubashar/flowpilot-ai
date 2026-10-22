"""Outbox (Simulated) page (M5): read-only view of `GET /outbox`.

Each entry is a reply a human reviewer approved. FlowPilot has no way to send email or messages
(the database only allows the `simulated` channel), so this page shows what would be sent and
offers no send, retry or edit actions. `workspace.render` re-checks the reviewer's token first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

import streamlit as st

from flowpilot.ui import api
from flowpilot.ui.api import ApiOutcome, UIConfig
from flowpilot.ui.dashboard_page import FAILURES, notice
from flowpilot.ui.drafts_page import md_text
from flowpilot.ui.leads import page_window, showing_label

STATUS_KEY = "fp_ob_status"
PAGE_KEY = "fp_ob_page"
SELECTED_KEY = "fp_ob_sel"
STATUS_FILTER = {"all": "All statuses", "pending": "Pending", "sent": "Sent", "failed": "Failed"}
STATUS_TONES = {"pending": "pending", "sent": "valid", "failed": "duplicate"}


def _when(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return f"{moment:%b} {moment.day}, {moment.year}, {moment:%H:%M} UTC"


def _first_page() -> None:
    st.session_state[PAGE_KEY] = 1
    st.session_state.pop(SELECTED_KEY, None)


def _go_to(page: int) -> None:
    st.session_state[PAGE_KEY] = page
    st.session_state.pop(SELECTED_KEY, None)


def _badge(status: str) -> str:
    tone = STATUS_TONES.get(status, "neutral")
    return f'<b class="fp-badge fp-{tone}">{escape(STATUS_FILTER.get(status, status))}</b>'


def summary(config: UIConfig) -> bool:
    total = api.fetch_outbox(config, limit=1)
    pending = api.fetch_outbox(config, status="pending", limit=1)
    for result in (total, pending):
        if result.outcome is not ApiOutcome.OK:
            title, body = FAILURES[result.outcome]
            notice(title, body)
            st.button("Try again", key="fp_retry", icon=":material/refresh:")
            return False
    cards = (("Simulated entries", total.total, "blue"), ("Pending", pending.total, "review"))
    html = "".join(
        f'<div class="fp-metric fp-tone-{tone}"><i class="fp-metric-ic"></i>'
        f"<div><strong>{value}</strong><span>{label}</span></div></div>"
        for label, value, tone in cards
    )
    st.html(
        f'<div class="fp-metrics fp-metrics-2" aria-label="Outbox totals">{html}</div>'
        '<div class="fp-notice fp-notice-info fp-ob-note" role="note"><p class="fp-pop-title">'
        'Nothing here is actually sent</p><p class="fp-pop-copy">Each entry is a reply a '
        "reviewer approved. FlowPilot has no email or messaging connection, so entries stay in "
        "this simulated outbox.</p></div>"
    )
    return True


def _label(item: dict) -> str:
    return (
        f"**{md_text(str(item['contact_name']))}**, {md_text(str(item['company_name']))}  \n"
        f"{md_text(str(item['subject']))}"
    )


def _detail_html(item: dict) -> str:
    facts = (
        ("To", str(item["recipient"])),
        ("Lead", f"#{int(item['lead_id'])} {item['contact_name']}, {item['company_name']}"),
        ("Draft", f"Version {int(item['draft_version'])}"),
        ("Approved by", f"{item['decided_by']} on {_when(str(item['decided_at']))}"),
        ("Queued", _when(str(item["created_at"]))),
    )
    dl = "".join(f"<dt>{k}</dt><dd>{escape(v)}</dd>" for k, v in facts)
    return (
        '<section class="fp-panel fp-dr-draft">'
        f'<div class="fp-dr-head"><div><h2>Entry #{int(item["id"])}</h2>'
        '<p>Simulated delivery, not sent</p></div><div class="fp-dr-tags">'
        f'<b class="fp-badge fp-info">Simulated</b>{_badge(str(item["status"]))}</div></div>'
        f'<dl class="fp-details">{dl}</dl>'
        f'<p class="fp-dr-label">Subject</p><p class="fp-dr-subject">'
        f"{escape(str(item['subject']))}</p>"
        f'<p class="fp-dr-label">Message</p><div class="fp-dr-body">'
        f"{escape(str(item['body']))}</div></section>"
    )


def entries(config: UIConfig) -> None:
    status = st.session_state.get(STATUS_KEY, "all")
    status = None if status == "all" else status
    size = api.LEADS_PAGE_SIZE
    requested = max(1, int(st.session_state.get(PAGE_KEY, 1)))
    result = api.fetch_outbox(config, status=status, offset=(requested - 1) * size)
    if result.outcome is ApiOutcome.OK:
        window = page_window(requested, result.total, size)
        if window.page != requested:  # the page no longer exists
            st.session_state[PAGE_KEY] = window.page
            result = api.fetch_outbox(config, status=status, offset=window.offset)
    if result.outcome is not ApiOutcome.OK:
        title, body = FAILURES[result.outcome]
        notice(title, body)
        return
    if not result.items:
        notice(
            "No outbox entries" if status is None else "No entries with this status",
            "Entries appear here after a reviewer approves a draft on the Draft Responses page."
            if status is None
            else "Try another status.",
            tone="info",
        )
        return

    by_id = {int(i["id"]): i for i in result.items}
    window = page_window(st.session_state.get(PAGE_KEY, 1), result.total, size, len(by_id))
    list_col, detail_col = st.columns([1, 1.9], gap="medium")
    with list_col, st.container(key="fp_ob_list"):
        if st.session_state.get(SELECTED_KEY) not in by_id:
            st.session_state[SELECTED_KEY] = next(iter(by_id))
        selected = st.radio(
            "Entries", list(by_id), key=SELECTED_KEY, label_visibility="collapsed",
            format_func=lambda i: _label(by_id[i]),
        )  # fmt: skip
        with st.container(key="fp_ob_pager", horizontal=True, vertical_alignment="center"):
            st.button(
                "", icon=":material/chevron_left:", key="fp_ob_prev",
                disabled=window.page <= 1, help="Newer entries",
                on_click=_go_to, args=(window.page - 1,),
            )  # fmt: skip
            shown = showing_label(window, result.total, ("entry", "entries"))
            st.html(f'<span class="fp-page-of">{shown}</span>')
            st.button(
                "", icon=":material/chevron_right:", key="fp_ob_next",
                disabled=window.page >= window.pages, help="Older entries",
                on_click=_go_to, args=(window.page + 1,),
            )  # fmt: skip
    with detail_col:
        item = by_id[int(selected)]
        st.html(_detail_html(item))
        with st.expander("Copy message text"):
            st.code(
                f"To: {item['recipient']}\nSubject: {item['subject']}\n\n{item['body']}",
                language=None, wrap_lines=True,
            )  # fmt: skip


def render(config: UIConfig) -> None:
    with st.spinner("Loading the outbox…"):
        if not summary(config):
            return
        with st.container(key="fp_ob_filters"):
            st.selectbox(
                "Status", list(STATUS_FILTER), key=STATUS_KEY, format_func=STATUS_FILTER.get,
                label_visibility="collapsed", on_change=_first_page,
            )  # fmt: skip
        entries(config)
