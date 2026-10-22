"""Leads page (M5): live leads from `GET /leads` and lead creation through `POST /leads`.

`workspace.render` re-checks the reviewer's token before calling `render`. Filtering (status,
tier) and paging happen on the server; the API has no text, source or date filters, so the page
offers none. Nothing here touches the database directly.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from flowpilot.ui import api
from flowpilot.ui.api import ApiOutcome, CreateOutcome, CreateResult, UIConfig
from flowpilot.ui.dashboard import parse_lead, source_label
from flowpilot.ui.dashboard_page import FAILURES, day_label, notice, score_pill, status_badge
from flowpilot.ui.leads import (
    ALL,
    FIELD_LABELS,
    FORM_OPTIONS,
    STATUS_FILTER,
    TEXT_FIELDS,
    TIER_FILTER,
    audit_actor,
    build_payload,
    idempotency_key_for,
    page_window,
    payload_fingerprint,
    server_filter,
    showing_label,
)

STATUS_KEY = "fp_lf_status"
TIER_KEY = "fp_lf_tier"
PAGE_KEY = "fp_lf_page"
ADD_OPEN_KEY = "fp_add_open"
ATTEMPT_KEY = "fp_add_attempt"  # (payload fingerprint, idempotency key) of an unfinished attempt
RESULT_KEY = "fp_add_result"  # outcome of the last successful submission, shown once
FORM_ERROR_KEY = "fp_add_error"
FIELD_PREFIX = "fp_new_"

CREATE_FAILURES = {
    CreateOutcome.API_UNAVAILABLE: "The FlowPilot API is not reachable. Nothing was saved; your "
    "entries are kept so you can try again.",
    CreateOutcome.API_KEY_REJECTED: "The console is not authorised to add leads. Check that it "
    "uses the same FLOWPILOT_API_KEY as the API.",
    CreateOutcome.UNEXPECTED: "The FlowPilot API returned an unexpected response. Check the "
    "Leads list before trying again.",
    CreateOutcome.KEY_REUSED: "This submission conflicted with an earlier attempt. Submit again "
    "to send it as a new request.",
}


# --- state callbacks ---------------------------------------------------------------------------


def toggle_add() -> None:
    st.session_state[ADD_OPEN_KEY] = not st.session_state.get(ADD_OPEN_KEY, False)
    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(FORM_ERROR_KEY, None)


def _first_page() -> None:
    st.session_state[PAGE_KEY] = 1


def _go_to(page: int) -> None:
    st.session_state[PAGE_KEY] = page


def _clear_filters() -> None:
    st.session_state.pop(STATUS_KEY, None)
    st.session_state.pop(TIER_KEY, None)
    _first_page()


def _submit(config: UIConfig, actor: str) -> None:
    """Form callback: runs once per click, before the page re-renders."""
    st.session_state.pop(FORM_ERROR_KEY, None)
    values = {f: st.session_state.get(FIELD_PREFIX + f) for f in (*TEXT_FIELDS, *FORM_OPTIONS)}
    missing = [FIELD_LABELS[f] for f in FORM_OPTIONS if values[f] is None]
    if missing:
        st.session_state[FORM_ERROR_KEY] = CreateResult(
            CreateOutcome.INVALID, errors=tuple((m, "choose an option") for m in missing)
        )
        return
    payload = build_payload(values)  # type: ignore[arg-type]
    key = idempotency_key_for(payload, st.session_state.get(ATTEMPT_KEY))
    st.session_state[ATTEMPT_KEY] = (payload_fingerprint(payload), key)
    result = api.create_lead(config, payload, actor=actor, idempotency_key=key)
    if result.outcome in (CreateOutcome.CREATED, CreateOutcome.DUPLICATE):
        st.session_state[RESULT_KEY] = result
        st.session_state[ADD_OPEN_KEY] = False
        st.session_state.pop(ATTEMPT_KEY, None)
        for field in (*TEXT_FIELDS, *FORM_OPTIONS):  # an empty form for the next lead
            st.session_state.pop(FIELD_PREFIX + field, None)
        return
    if result.outcome is CreateOutcome.KEY_REUSED:
        st.session_state.pop(ATTEMPT_KEY, None)
    st.session_state[FORM_ERROR_KEY] = result


# --- sections ----------------------------------------------------------------------------------


def created_banner() -> None:
    result: CreateResult | None = st.session_state.pop(RESULT_KEY, None)
    if result is None or result.lead is None:
        return
    lead = result.lead
    who = f"{lead.get('contact_name', '')}, {lead.get('company_name', '')}"
    if result.outcome is CreateOutcome.DUPLICATE:
        notice(
            f"Already in FlowPilot as lead #{int(lead['id'])}",
            f"{who} matches an existing lead (same email and company), so nothing new was created.",
            tone="info",
        )
        return
    score = lead.get("score") or {}
    scored = (
        f" It was scored {score['total']} ({str(score['tier']).title()} tier)."
        if "total" in score and "tier" in score
        else ""
    )
    notice(f"Lead #{int(lead['id'])} added", f"{who} was saved.{scored}", tone="success")


def add_form(config: UIConfig, reviewer: dict) -> None:
    if not st.session_state.get(ADD_OPEN_KEY):
        return
    with st.container(key="fp_add_panel"):
        st.html(
            '<div class="fp-title"><h2 class="fp-section">Add a lead</h2>'
            '<p class="fp-pop-copy">Saved through the FlowPilot API, which checks, de-duplicates '
            "and scores it. Only synthetic emails are accepted (example.com/.net/.org or a "
            ".example/.test/.invalid domain).</p></div>"
        )
        with st.form("fp_add_form", border=False, enter_to_submit=False):
            left, right = st.columns(2)
            left.text_input("Company", key=FIELD_PREFIX + "company_name", max_chars=120)
            left.text_input("Contact name", key=FIELD_PREFIX + "contact_name", max_chars=120)
            left.text_input(
                "Email", key=FIELD_PREFIX + "contact_email", max_chars=254,
                placeholder="name@company.example",
            )  # fmt: skip
            left.selectbox(
                "Source", list(FORM_OPTIONS["source"]), key=FIELD_PREFIX + "source",
                format_func=FORM_OPTIONS["source"].get,
            )  # fmt: skip
            for field in ("company_size", "industry", "service_interest", "budget_band"):
                right.selectbox(
                    FIELD_LABELS[field], list(FORM_OPTIONS[field]), key=FIELD_PREFIX + field,
                    format_func=FORM_OPTIONS[field].get, index=None, placeholder="Choose…",
                )  # fmt: skip
            left.selectbox(
                "Timeline", list(FORM_OPTIONS["timeline"]), key=FIELD_PREFIX + "timeline",
                format_func=FORM_OPTIONS["timeline"].get, index=None, placeholder="Choose…",
            )  # fmt: skip
            st.text_area(
                "Message", key=FIELD_PREFIX + "message", max_chars=5000, height=110,
                help="What the lead asked for (at least 10 characters).",
            )  # fmt: skip
            st.form_submit_button(
                "Add lead", type="primary", icon=":material/add:",
                on_click=_submit, args=(config, audit_actor(str(reviewer["name"]))),
            )  # fmt: skip
        st.button("Cancel", key="fp_add_cancel", on_click=toggle_add)

        error: CreateResult | None = st.session_state.get(FORM_ERROR_KEY)
        if error is None:
            return
        if error.outcome is CreateOutcome.INVALID:
            lines = (
                "".join(
                    f"<li><b>{escape(FIELD_LABELS.get(field, field))}:</b> {escape(msg)}</li>"
                    for field, msg in error.errors
                )
                or "<li>The API rejected the lead.</li>"
            )
            st.html(
                '<div class="fp-notice fp-notice-error" role="alert"><p class="fp-pop-title">'
                f'Check these fields</p><ul class="fp-errors">{lines}</ul></div>'
            )
        else:
            notice("The lead was not added", CREATE_FAILURES[error.outcome])


def kpis(config: UIConfig) -> int | None:
    """Server totals for the summary cards; returns the overall total, or None on failure."""
    outcome, totals = api.fetch_lead_totals(
        config,
        {
            "total": {},
            "hot": {"tier": "hot"},
            "warm": {"tier": "warm"},
            "drafted": {"status": "drafted"},
            "awaiting": {"status": "awaiting_approval"},
        },
    )
    if outcome is not ApiOutcome.OK:
        title, body = FAILURES[outcome]
        notice(title, body)
        st.button("Try again", key="fp_retry", icon=":material/refresh:")
        return None
    cards = (
        ("Total leads", totals["total"], "blue"),
        ("Hot leads", totals["hot"], "hot"),
        ("Warm leads", totals["warm"], "warm"),
        ("Awaiting review", totals["drafted"] + totals["awaiting"], "review"),
    )
    html = "".join(
        f'<div class="fp-metric fp-tone-{tone}"><i class="fp-metric-ic"></i>'
        f"<div><strong>{value}</strong><span>{label}</span></div></div>"
        for label, value, tone in cards
    )
    st.html(f'<div class="fp-metrics fp-leads-kpis" aria-label="Lead totals">{html}</div>')
    return totals["total"]


def _row_html(item: dict) -> str:
    row = parse_lead(item)
    return (
        '<div class="fp-row">'
        f'<span class="c-num">{row.id}</span>'
        f'<span class="c-name">{escape(row.contact)}</span>'
        f'<span class="c-company">{escape(row.company)}</span>'
        f'<span class="c-contact fp-email">{escape(str(item["contact_email"]))}</span>'
        f'<span class="c-source">{escape(source_label(row.source))}</span>'
        f'<span class="c-status">{status_badge(row.status)}</span>'
        f'<span class="c-score">{score_pill(row.score, row.tier)}</span>'
        f'<span class="c-created">{day_label(row.created)}</span></div>'
    )


def _details_html(item: dict) -> str:
    def label(field: str) -> str:
        value = str(item.get(field, ""))
        return FORM_OPTIONS.get(field, {}).get(value, value)  # type: ignore[call-overload]

    score = item.get("score") or {}
    rows = [
        ("Email", str(item["contact_email"])),
        ("Company size", label("company_size")),
        ("Industry", label("industry")),
        ("Service", label("service_interest")),
        ("Budget", label("budget_band")),
        ("Timeline", label("timeline")),
        ("Route", str(score.get("route", "Not scored")).replace("_", " ")),
    ]
    items = "".join(f"<dt>{k}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    return (
        f'<p class="fp-pop-title">{escape(str(item["contact_name"]))}, '
        f'{escape(str(item["company_name"]))}</p><dl class="fp-details">{items}</dl>'
        f'<p class="fp-pop-copy fp-message">{escape(str(item.get("message", "")))}</p>'
    )


def table(config: UIConfig) -> None:
    status = server_filter(st.session_state.get(STATUS_KEY, ALL))
    tier = server_filter(st.session_state.get(TIER_KEY, ALL))
    size = api.LEADS_PAGE_SIZE
    requested = int(st.session_state.get(PAGE_KEY, 1))
    result = api.fetch_lead_page(
        config, status=status, tier=tier, offset=(max(1, requested) - 1) * size
    )
    if result.outcome is ApiOutcome.OK:
        window = page_window(requested, result.total, size)
        if window.page != requested:  # the page no longer exists (filters or data changed)
            st.session_state[PAGE_KEY] = window.page
            result = api.fetch_lead_page(config, status=status, tier=tier, offset=window.offset)
    rows: list[str] = []
    if result.outcome is ApiOutcome.OK:
        try:
            rows = [_row_html(item) for item in result.items]
        except (KeyError, TypeError, ValueError):
            result = api.LeadPageResult(ApiOutcome.UNEXPECTED)
    if result.outcome is not ApiOutcome.OK:
        title, body = FAILURES[result.outcome]
        notice(title, body)
        return

    window = page_window(st.session_state.get(PAGE_KEY, 1), result.total, size, len(rows))
    with st.container(key="fp_table"):
        head = st.columns([10, 0.8], vertical_alignment="center", wrap=False)
        head[0].html(
            '<div class="fp-row fp-row-head"><span class="c-num">#</span>'
            '<span class="c-name">Name</span><span class="c-company">Company</span>'
            '<span class="c-contact">Email</span><span class="c-source">Source</span>'
            '<span class="c-status">Status</span><span class="c-score">Score</span>'
            '<span class="c-created">Created</span></div>'
        )
        head[1].html('<div class="fp-actions-head">Details</div>')
        for item, row_html in zip(result.items, rows, strict=True):
            with st.container(key=f"fp_lead_{int(item['id'])}"):
                cells = st.columns([10, 0.8], vertical_alignment="center", wrap=False)
                cells[0].html(row_html)
                with cells[1].popover(
                    "", icon=":material/more_horiz:", key=f"fp_details_{int(item['id'])}",
                    help="Lead details",
                ):  # fmt: skip
                    st.html(_details_html(item))

    if result.total == 0:
        filtered = status is not None or tier is not None
        st.html(
            '<div class="fp-empty"><p class="fp-pop-title">'
            + ("No leads match these filters" if filtered else "No leads yet")
            + '</p><p class="fp-pop-copy">'
            + ("Try another status or tier." if filtered else "Add the first one above.")
            + "</p></div>"
        )
        if filtered:
            st.button("Clear filters", key="fp_clear", on_click=_clear_filters)

    with st.container(key="fp_tfoot", horizontal=True, vertical_alignment="center"):
        st.html(
            f'<div class="fp-tfoot-note"><span class="fp-count">'
            f"{showing_label(window, result.total)}</span></div>"
        )
        with st.container(key="fp_pager", horizontal=True, horizontal_alignment="right"):
            st.button(
                "", icon=":material/chevron_left:", key="fp_prev", disabled=window.page <= 1,
                help="Previous page", on_click=_go_to, args=(window.page - 1,),
            )  # fmt: skip
            st.html(
                f'<span class="fp-page-of" aria-current="page">Page {window.page} of '
                f"{window.pages}</span>"
            )
            st.button(
                "", icon=":material/chevron_right:", key="fp_next",
                disabled=window.page >= window.pages, help="Next page",
                on_click=_go_to, args=(window.page + 1,),
            )  # fmt: skip


def filters() -> None:
    with st.container(key="fp_filters"):
        status_col, tier_col, note_col = st.columns([1, 1, 1.6], vertical_alignment="center")
        status_col.selectbox(
            "Status", list(STATUS_FILTER), key=STATUS_KEY, format_func=STATUS_FILTER.get,
            label_visibility="collapsed", on_change=_first_page,
        )  # fmt: skip
        tier_col.selectbox(
            "Tier", list(TIER_FILTER), key=TIER_KEY, format_func=TIER_FILTER.get,
            label_visibility="collapsed", on_change=_first_page,
        )  # fmt: skip
        note_col.html(
            '<p class="fp-filter-note">Search and source or date filters are not available: '
            "the API filters leads by status and tier only.</p>"
        )


def render(config: UIConfig, reviewer: dict) -> None:
    created_banner()
    add_form(config, reviewer)
    with st.spinner("Loading leads…"):
        if kpis(config) is None:
            return
        with st.container(key="fp_card"):
            filters()
            table(config)
