"""Draft Responses page (M5): review AI-assisted replies and record a human decision.

The queue is `GET /leads` filtered to `drafted` and `awaiting_approval`, the two statuses from
which ADR 0004 accepts a decision; only the latest draft version of such a lead can be decided.
Decisions go to `POST /leads/{id}/drafts/{version}/approval` with the reviewer's own token, so
the server records who decided. Approval only enqueues a simulated outbox entry; nothing is sent.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from html import escape

import streamlit as st

from flowpilot.ui import api
from flowpilot.ui.api import ApiOutcome, DecisionOutcome, DecisionResult, UIConfig
from flowpilot.ui.dashboard import parse_lead, source_label
from flowpilot.ui.dashboard_page import FAILURES, day_label, notice, score_pill, status_badge
from flowpilot.ui.leads import FORM_OPTIONS

VIEW_KEY = "fp_dr_view"
SELECTED_KEY = "fp_dr_sel"
PENDING_KEY = "fp_dr_pending"  # (lead id, version, decision) awaiting confirmation
ATTEMPT_KEY = "fp_dr_attempt"  # ((lead id, version, decision), idempotency key)
REASON_KEY = "fp_dr_reason"
RESULT_KEY = "fp_dr_result"
ERROR_KEY = "fp_dr_error"
QUEUE_LIMIT = 100  # the API's maximum page size; more is reported, not hidden

VIEWS = {
    "pending": ("Awaiting review", ("drafted", "awaiting_approval")),
    "approved": ("Approved", ("approved",)),
    "rejected": ("Rejected", ("rejected",)),
}
GENERATORS = {"template": "Template", "llm": "Local AI model", "human_edit": "Human edit"}
FLAGS = {
    "requests_pricing": "Asks about pricing",
    "off_topic": "Off topic",
    "unclear_need": "Unclear need",
    "possible_prompt_injection": "Possible prompt injection",
}
DECISION_FAILURES = {
    DecisionOutcome.SUPERSEDED: "A newer draft version exists. Review the latest version.",
    DecisionOutcome.ALREADY_DECIDED: "This draft was already decided.",
    DecisionOutcome.NOT_REVIEWABLE: "This lead is no longer waiting for a decision.",
    DecisionOutcome.NOT_FOUND: "The lead or draft no longer exists.",
    DecisionOutcome.INVALID: "The API rejected the request.",
    DecisionOutcome.API_KEY_REJECTED: "The console is not authorised to reach the FlowPilot API.",
    DecisionOutcome.API_UNAVAILABLE: "The FlowPilot API is not reachable. Nothing was decided; "
    "confirm again to retry.",
    DecisionOutcome.UNEXPECTED: "The FlowPilot API returned an unexpected response. Check this "
    "lead's status before trying again.",
}
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~:$])")


def md_text(text: str) -> str:
    """Plain text for a widget label, which Streamlit renders as Markdown."""
    return _MARKDOWN.sub(r"\\\1", text)


# --- state callbacks ---------------------------------------------------------------------------


def _reset_selection() -> None:
    for key in (SELECTED_KEY, PENDING_KEY, ERROR_KEY, REASON_KEY):
        st.session_state.pop(key, None)


def _ask(lead_id: int, version: int, decision: str) -> None:
    st.session_state[PENDING_KEY] = (lead_id, version, decision)
    st.session_state.pop(ERROR_KEY, None)
    st.session_state.pop(RESULT_KEY, None)


def _clear_pending() -> None:
    st.session_state.pop(PENDING_KEY, None)
    st.session_state.pop(ERROR_KEY, None)


def _cancel() -> None:
    for key in (PENDING_KEY, ERROR_KEY, REASON_KEY):
        st.session_state.pop(key, None)


def _confirm(
    config: UIConfig, token: str, label: str, on_session_rejected: Callable[[], None]
) -> None:
    """Runs once per click on the confirmation button, before the page re-renders."""
    pending = st.session_state.get(PENDING_KEY)
    if pending is None:
        return
    lead_id, version, decision = pending
    previous = st.session_state.get(ATTEMPT_KEY)
    key = previous[1] if previous and previous[0] == pending else f"console-{uuid.uuid4().hex}"
    st.session_state[ATTEMPT_KEY] = (pending, key)
    reason = str(st.session_state.get(REASON_KEY) or "").strip() or None
    result = api.decide_draft(
        config, token, lead_id, version, decision=decision, reason=reason, idempotency_key=key
    )
    if result.outcome is DecisionOutcome.REVIEWER_REJECTED:
        on_session_rejected()  # revoked meanwhile: sign out; the API recorded nothing
        return
    if result.outcome is DecisionOutcome.API_UNAVAILABLE:
        st.session_state[ERROR_KEY] = result  # keep the key: a retry replays, never doubles
        return
    for k in (PENDING_KEY, ATTEMPT_KEY, REASON_KEY):
        st.session_state.pop(k, None)
    if result.outcome is DecisionOutcome.DECIDED:
        st.session_state[RESULT_KEY] = (result, label)
        st.session_state.pop(SELECTED_KEY, None)
    else:
        st.session_state[ERROR_KEY] = result


# --- sections ----------------------------------------------------------------------------------


def result_banner() -> None:
    stored = st.session_state.pop(RESULT_KEY, None)
    if stored is None:
        return
    result, label = stored
    approval = result.approval or {}
    version = int(approval.get("version", 0))
    who = escape(str(approval.get("decided_by", "")))
    if approval.get("decision") == "approved":
        outbox = approval.get("outbox") or {}
        queued = (
            f" Simulated outbox entry #{int(outbox['id'])} was added with status "
            f"{escape(str(outbox.get('status', '')))}."
            if "id" in outbox
            else ""
        )
        notice(
            f"Approved version {version} for {label}",
            f"Recorded by the API as decided by {who}.{queued} Nothing was sent.",
            tone="success",
        )
    else:
        notice(
            f"Rejected version {version} for {label}",
            f"Recorded by the API as decided by {who}. No outbox entry was created.",
            tone="info",
        )


def summary(config: UIConfig) -> bool:
    outcome, totals = api.fetch_lead_totals(
        config,
        {
            "drafted": {"status": "drafted"},
            "awaiting": {"status": "awaiting_approval"},
            "approved": {"status": "approved"},
            "rejected": {"status": "rejected"},
        },
    )
    if outcome is not ApiOutcome.OK:
        title, body = FAILURES[outcome]
        notice(title, body)
        st.button("Try again", key="fp_retry", icon=":material/refresh:")
        return False
    cards = (
        ("Awaiting review", totals["drafted"] + totals["awaiting"], "review"),
        ("Approved", totals["approved"], "green"),
        ("Rejected", totals["rejected"], "red"),
    )
    html = "".join(
        f'<div class="fp-metric fp-tone-{tone}"><i class="fp-metric-ic"></i>'
        f"<div><strong>{value}</strong><span>{label}</span></div></div>"
        for label, value, tone in cards
    )
    st.html(f'<div class="fp-metrics fp-metrics-3" aria-label="Draft totals">{html}</div>')
    return True


def _queue(config: UIConfig, statuses: tuple[str, ...]) -> tuple[ApiOutcome, list[dict], int]:
    items: list[dict] = []
    total = 0
    for status in statuses:
        page = api.fetch_lead_page(config, status=status, limit=QUEUE_LIMIT)
        if page.outcome is not ApiOutcome.OK:
            return page.outcome, [], 0
        items.extend(page.items)
        total += page.total
    try:
        rows = sorted(items, key=lambda i: (parse_lead(i).updated, int(i["id"])))
    except (KeyError, TypeError, ValueError):
        return ApiOutcome.UNEXPECTED, [], 0
    return ApiOutcome.OK, rows, total


def _option_label(item: dict) -> str:
    row = parse_lead(item)
    score = "not scored" if row.score is None else f"{row.score} ({str(row.tier).lower()})"
    return f"**{md_text(row.contact)}**  \n{md_text(row.company)}, score {md_text(score)}"


def _context_html(item: dict, assessment: api.ItemResult) -> str:
    row = parse_lead(item)

    def option(field: str) -> str:
        value = str(item.get(field, ""))
        return FORM_OPTIONS.get(field, {}).get(value, value)  # type: ignore[call-overload]

    facts = (
        ("Email", str(item["contact_email"])),
        ("Source", source_label(row.source)),
        ("Service", option("service_interest")),
        ("Budget", option("budget_band")),
        ("Timeline", option("timeline")),
        ("Received", day_label(row.created)),
    )
    dl = "".join(f"<dt>{k}</dt><dd>{escape(v)}</dd>" for k, v in facts)
    html = (
        '<section class="fp-panel fp-dr-context">'
        f'<div class="fp-dr-head"><div><h2>{escape(row.contact)}</h2>'
        f'<p>{escape(row.company)}</p></div><div class="fp-dr-tags">'
        f"{status_badge(row.status)}{score_pill(row.score, row.tier)}</div></div>"
        f'<dl class="fp-details">{dl}</dl>'
        f'<p class="fp-dr-label">Their message</p>'
        f'<p class="fp-dr-message">{escape(str(item.get("message", "")))}</p>'
    )
    if assessment.outcome is not ApiOutcome.OK:
        html += '<p class="fp-panel-foot">The advisory assessment could not be loaded.</p>'
    elif assessment.data is None:
        html += '<p class="fp-panel-foot">This lead has no advisory assessment yet.</p>'
    else:
        data: dict = assessment.data  # type: ignore[assignment]
        flags = [str(f) for f in data.get("flags") or []]
        warn = "possible_prompt_injection" in flags
        chips = "".join(
            f'<b class="fp-badge fp-{"duplicate" if f == "possible_prompt_injection" else "info"}">'
            f"{escape(FLAGS.get(f, f))}</b>"
            for f in flags
        )
        html += (
            '<div class="fp-dr-assess"><p class="fp-dr-label">Advisory assessment '
            f"({escape('local AI' if data.get('source') == 'llm' else 'rules')}, urgency "
            f"{escape(str(data.get('urgency', '')))})</p>"
            f"<p>{escape(str(data.get('summary', '')))}</p>"
            + (f'<div class="fp-dr-flags">{chips}</div>' if chips else "")
            + (
                '<p class="fp-dr-warn">The lead\'s message looked like an attempt to instruct '
                "the AI. Read the draft carefully before deciding.</p>"
                if warn
                else ""
            )
            + "</div>"
        )
    return html + "</section>"


def _draft_html(draft: dict, count: int) -> str:
    generator = GENERATORS.get(str(draft["generator"]), str(draft["generator"]))
    via = f"{generator} ({escape(str(draft['model']))})" if draft.get("model") else generator
    fallback = (
        f'<p class="fp-panel-foot">Fallback used: {escape(str(draft["fallback_reason"]))}.</p>'
        if draft.get("fallback_reason")
        else ""
    )
    return (
        '<section class="fp-panel fp-dr-draft">'
        f'<div class="fp-dr-head"><div><h2>Proposed reply</h2><p>Version {int(draft["version"])} '
        f"of {count}, {escape(via)}</p></div></div>"
        f'<p class="fp-dr-label">Subject</p><p class="fp-dr-subject">'
        f"{escape(str(draft['subject']))}</p>"
        f'<p class="fp-dr-label">Message</p><div class="fp-dr-body">{escape(str(draft["body"]))}'
        f"</div>{fallback}</section>"
    )


def decision_controls(
    config: UIConfig,
    token: str,
    item: dict,
    draft: dict,
    on_session_rejected: Callable[[], None],
) -> None:
    lead_id, version = int(item["id"]), int(draft["version"])
    label = f"{item['contact_name']}, {item['company_name']}"
    pending = st.session_state.get(PENDING_KEY)
    error: DecisionResult | None = st.session_state.get(ERROR_KEY)
    with st.container(key="fp_dr_decide"):
        if pending is None or pending[:2] != (lead_id, version):
            with st.container(horizontal=True):
                st.button(
                    f"Approve version {version}", key="fp_dr_approve", type="primary",
                    icon=":material/check:", on_click=_ask, args=(lead_id, version, "approved"),
                )  # fmt: skip
                st.button(
                    f"Reject version {version}", key="fp_dr_reject", icon=":material/close:",
                    on_click=_ask, args=(lead_id, version, "rejected"),
                )  # fmt: skip
        else:
            approving = pending[2] == "approved"
            st.html(
                '<div class="fp-notice fp-notice-info" role="alert"><p class="fp-pop-title">'
                + (
                    f'Approve version {version}?</p><p class="fp-pop-copy">Approval is final. '
                    "It adds one simulated outbox entry; no email or message is sent."
                    if approving
                    else f'Reject version {version}?</p><p class="fp-pop-copy">Rejection is '
                    "final. This lead cannot be redrafted afterwards."
                )
                + "</p></div>"
            )
            st.text_input(
                "Reason (optional)", key=REASON_KEY, max_chars=500,
                placeholder="Recorded with the decision",
            )  # fmt: skip
            with st.container(horizontal=True):
                st.button(
                    "Confirm approval" if approving else "Confirm rejection",
                    key="fp_dr_confirm", type="primary",
                    on_click=_confirm, args=(config, token, label, on_session_rejected),
                )  # fmt: skip
                st.button("Cancel", key="fp_dr_cancel", on_click=_cancel)
        if error is not None:
            detail = f" {error.message}" if error.message else ""
            notice("No decision was recorded", DECISION_FAILURES[error.outcome] + detail)


def detail(
    config: UIConfig,
    token: str,
    item: dict,
    on_session_rejected: Callable[[], None],
) -> None:
    lead_id = int(item["id"])
    drafts = api.fetch_drafts(config, lead_id)
    assessment = api.fetch_assessment(config, lead_id)
    try:
        st.html(_context_html(item, assessment))
    except (KeyError, TypeError, ValueError):
        notice(*FAILURES[ApiOutcome.UNEXPECTED])
        return
    if drafts.outcome is not ApiOutcome.OK:
        title, body = FAILURES[drafts.outcome]
        notice(title, body)
        return
    versions: list[dict] = drafts.data  # type: ignore[assignment]
    if not versions:
        notice("No draft yet", "This lead has no draft version to review.", tone="info")
        return
    try:
        latest = max(versions, key=lambda d: int(d["version"]))
        draft_html = _draft_html(latest, len(versions))
        str(latest["body"])
    except (KeyError, TypeError, ValueError):
        notice(*FAILURES[ApiOutcome.UNEXPECTED])
        return
    st.html(draft_html)
    with st.expander("Copy reply text"):
        st.code(f"Subject: {latest['subject']}\n\n{latest['body']}", language=None,
                wrap_lines=True)  # fmt: skip
    older = [d for d in versions if d is not latest]
    if older:
        with st.expander(f"Earlier versions ({len(older)})"):
            for d in sorted(older, key=lambda d: int(d["version"]), reverse=True):
                st.html(
                    f'<p class="fp-dr-label">Version {int(d["version"])}, superseded</p>'
                    f'<p class="fp-dr-subject">{escape(str(d.get("subject", "")))}</p>'
                    f'<div class="fp-dr-body fp-dr-old">{escape(str(d.get("body", "")))}</div>'
                )
    if item["status"] in VIEWS["pending"][1]:
        decision_controls(config, token, item, latest, on_session_rejected)
    else:
        st.html(
            '<p class="fp-panel-foot">This lead is '
            f"{escape(str(item['status']))}; version {int(latest['version'])} was the one "
            "decided. Decisions are final.</p>"
        )


def render(config: UIConfig, token: str, *, on_session_rejected: Callable[[], None]) -> None:
    result_banner()
    with st.spinner("Loading drafts…"):
        if not summary(config):
            return
        view = st.radio(
            "Show", list(VIEWS), key=VIEW_KEY, horizontal=True,
            format_func=lambda v: VIEWS[v][0], label_visibility="collapsed",
            on_change=_reset_selection,
        )  # fmt: skip
        outcome, items, total = _queue(config, VIEWS[view][1])
    if outcome is not ApiOutcome.OK:
        title, body = FAILURES[outcome]
        notice(title, body)
        return
    if not items:
        empty = {
            "pending": ("Nothing is waiting for review", "New drafts appear here once a lead "
                        "has been drafted."),
            "approved": ("No approved drafts yet", "Approved replies appear here."),
            "rejected": ("No rejected drafts", "Rejected replies appear here."),
        }[view]  # fmt: skip
        notice(*empty, tone="info")
        return

    by_id = {int(i["id"]): i for i in items}
    queue_col, detail_col = st.columns([1, 1.9], gap="medium")
    with queue_col, st.container(key="fp_dr_queue"):
        st.html(
            f'<p class="fp-dr-label">{escape(VIEWS[view][0])}: {total}</p>'
            + (
                f'<p class="fp-panel-foot">Showing the first {len(items)}.</p>'
                if total > len(items)
                else ""
            )
        )
        if st.session_state.get(SELECTED_KEY) not in by_id:
            st.session_state[SELECTED_KEY] = next(iter(by_id))
        selected = st.radio(
            "Leads", list(by_id), key=SELECTED_KEY, label_visibility="collapsed",
            format_func=lambda i: _option_label(by_id[i]),
            on_change=_clear_pending,
        )  # fmt: skip
    with detail_col:
        detail(config, token, by_id[int(selected)], on_session_rejected)
