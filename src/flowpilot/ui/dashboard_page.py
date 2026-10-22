"""Dashboard content (M5 page 2): live figures from the FlowPilot API, rendered as HTML/CSS.

`workspace.render` checks the reviewer's token before calling `render`, so nothing here is shown
to a revoked session. Every number comes from `GET /leads` (and the activity strip from
`GET /leads/{id}/audit`); when the API cannot answer, the page says so instead of guessing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

import streamlit as st

from flowpilot.domain.models import LeadStatus, Tier
from flowpilot.ui import api
from flowpilot.ui.dashboard import (
    Activity,
    LeadRow,
    Overview,
    Slice,
    activity_lead_ids,
    build_activity,
    build_overview,
    parse_lead,
    relative_time,
    source_label,
    status_label,
    week_change,
)

STATUS_COLORS = {
    LeadStatus.NEW: "#94A3B8",
    LeadStatus.SCORED: "#2546CC",
    LeadStatus.DRAFTED: "#7C5CFA",
    LeadStatus.AWAITING_APPROVAL: "#F2A93B",
    LeadStatus.APPROVED: "#1F9D5A",
    LeadStatus.REJECTED: "#D92D20",
    LeadStatus.SENT: "#0EA5A4",
    LeadStatus.CLOSED: "#64748B",
}
SOURCE_COLORS = ("#2546CC", "#1F9D5A", "#F2A93B", "#7C5CFA", "#94A3B8")
STATUS_TONES = {
    LeadStatus.NEW: "info",
    LeadStatus.SCORED: "info",
    LeadStatus.DRAFTED: "pending",
    LeadStatus.AWAITING_APPROVAL: "pending",
    LeadStatus.APPROVED: "valid",
    LeadStatus.REJECTED: "duplicate",
}
TIER_BANDS = {Tier.HOT: "high", Tier.WARM: "medium", Tier.COLD: "low"}
ACTIVITY_TONES = {
    "lead.created": "green",
    "lead.qualified": "green",
    "lead.approved": "green",
    "lead.scored": "blue",
    "lead.drafted": "blue",
    "lead.rejected": "red",
    "lead.duplicate_submission": "red",
}
FAILURES = {
    api.ApiOutcome.API_UNAVAILABLE: (
        "The FlowPilot API is not reachable",
        "Make sure it is running, then try again.",
    ),
    api.ApiOutcome.API_KEY_REJECTED: (
        "The console is not authorised to read leads",
        "Check that it uses the same FLOWPILOT_API_KEY as the API.",
    ),
    api.ApiOutcome.UNEXPECTED: (
        "The FlowPilot API returned an unexpected response",
        "Try again. If it keeps happening, check the API logs.",
    ),
    api.ApiOutcome.ENDPOINT_MISSING: (
        "The running FlowPilot API does not offer this page's data yet",
        "It was probably started before the latest update. Restart it (uv run flowpilot-api), "
        "then try again.",
    ),
}


def day_label(when: datetime) -> str:
    return f"{when:%b} {when.day}, {when.year}"


def status_badge(status: str) -> str:
    tone = STATUS_TONES.get(status, "neutral")  # type: ignore[call-overload]
    return f'<b class="fp-badge fp-{tone}">{escape(status_label(status))}</b>'


def score_pill(score: int | None, tier: str | None) -> str:
    """Score coloured by the backend's tier (hot/warm/cold), not by a UI threshold."""
    if score is None:
        return '<span class="fp-muted">Not scored</span>'
    band = TIER_BANDS.get(tier, "neutral")  # type: ignore[call-overload]
    label = escape(str(tier).title())
    return f'<b class="fp-pill fp-{band}" title="{label} tier">{int(score)}</b>'


def notice(title: str, body: str, tone: str = "error") -> None:
    st.html(
        f'<div class="fp-notice fp-notice-{tone}" role="status"><p class="fp-pop-title">'
        f'{escape(title)}</p><p class="fp-pop-copy">{escape(body)}</p></div>'
    )


def kpis(ov: Overview) -> None:
    hot_note = f"Warm {ov.warm}, cold {ov.cold}"
    if ov.unscored:
        hot_note += f", not scored {ov.unscored}"
    cards = (
        ("Total leads", ov.total, "blue", "All leads in FlowPilot"),
        ("New this week", ov.new_7d, "green",
         week_change(ov.new_7d, ov.new_prev_7d) or "Created in the last 7 days"),
        ("Hot leads", ov.hot, "red", hot_note),
        ("Awaiting review", ov.awaiting_review, "amber", "Drafts waiting for a reviewer"),
    )  # fmt: skip
    html = "".join(
        f'<div class="fp-metric fp-tone-{tone}"><i class="fp-metric-ic"></i><div>'
        f"<strong>{value}</strong><span>{label}</span><small>{escape(note)}</small></div></div>"
        for label, value, tone, note in cards
    )
    st.html(f'<div class="fp-metrics fp-kpis" aria-label="Pipeline totals">{html}</div>')


def _trend(ov: Overview) -> str:
    peak = max((n for _, n in ov.daily), default=0)
    bars = []
    for i, (day, n) in enumerate(ov.daily):
        height = 0 if peak == 0 else max(4, round(n * 100 / peak))
        label = f"{day:%b} {day.day}" if i % 2 == len(ov.daily) % 2 else ""
        bars.append(
            f'<div class="fp-bar" title="{day:%b} {day.day}: {n} new">'
            f'<em>{n or ""}</em><span style="height:{height}%"></span><small>{label}</small></div>'
        )
    return f'<div class="fp-bars" role="img" aria-label="New leads per day">{"".join(bars)}</div>'


def _donut(slices: tuple[Slice, ...], colors: list[str], total: int, caption: str) -> str:
    stops, start = [], 0.0
    for piece, color in zip(slices, colors, strict=True):
        end = start + piece.share * 360
        stops.append(f"{color} {start:.2f}deg {end:.2f}deg")
        start = end
    gradient = ", ".join(stops) or "#E3E8F1 0deg 360deg"
    legend = "".join(
        f'<li><i style="background:{color}"></i><span>{escape(piece.label)}</span>'
        f"<b>{piece.count}</b><em>({round(piece.share * 100)}%)</em></li>"
        for piece, color in zip(slices, colors, strict=True)
    )
    return (
        f'<div class="fp-donut-wrap"><div class="fp-donut" role="img" aria-label="{caption}" '
        f'style="background:conic-gradient({gradient})"><div><strong>{total}</strong>'
        f'<span>Total leads</span></div></div><ul class="fp-legend">{legend}</ul></div>'
    )


def charts(ov: Overview) -> None:
    status_colors = [STATUS_COLORS.get(s.key, "#94A3B8") for s in ov.by_status]  # type: ignore[call-overload]
    source_colors = [SOURCE_COLORS[min(i, 4)] for i in range(len(ov.by_source))]
    st.html(
        '<div class="fp-grid fp-grid-charts">'
        '<section class="fp-panel"><h2>New leads</h2>'
        "<p>Leads created per day over the last 14 days.</p>"
        f"{_trend(ov)}</section>"
        '<section class="fp-panel"><h2>Leads by source</h2>'
        "<p>Where your leads came from.</p>"
        f"{_donut(ov.by_source, source_colors, ov.total, 'Leads by source')}</section>"
        '<section class="fp-panel"><h2>Lead status</h2>'
        "<p>Where each lead is in the pipeline.</p>"
        f"{_donut(ov.by_status, status_colors, ov.total, 'Leads by status')}</section></div>"
    )


def _table(head: tuple[str, ...], rows: list[tuple[str, ...]], empty: str) -> str:
    if not rows:
        return f'<p class="fp-panel-empty">{escape(empty)}</p>'
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="fp-table-scroll"><table class="fp-dtable"><thead><tr>{th}</tr></thead>' \
        f"<tbody>{body}</tbody></table></div>"  # fmt: skip


def tables(ov: Overview) -> None:
    recent = [
        (
            str(r.id),
            f"<strong>{escape(r.contact)}</strong>",
            escape(r.company),
            escape(source_label(r.source)),
            status_badge(r.status),
            score_pill(r.score, r.tier),
            day_label(r.created),
        )
        for r in ov.recent
    ]
    queue = [
        (
            str(r.id),
            f"<strong>{escape(r.contact)}</strong>",
            escape(r.company),
            score_pill(r.score, r.tier),
            status_badge(r.status),
            day_label(r.updated),
        )
        for r in ov.review_queue
    ]
    more = ov.awaiting_review - len(ov.review_queue)
    queue_note = f'<p class="fp-panel-foot">And {more} more waiting.</p>' if more > 0 else ""
    st.html(
        '<div class="fp-grid fp-grid-tables">'
        '<section class="fp-panel"><h2>Recent leads</h2><p>The latest leads added.</p>'
        + _table(
            ("#", "Name", "Company", "Source", "Status", "Score", "Created"),
            recent,
            "No leads yet.",
        )
        + '</section><section class="fp-panel"><h2>Pending review</h2>'
        "<p>Leads with a draft that no reviewer has approved or rejected yet.</p>"
        + _table(
            ("#", "Name", "Company", "Score", "Status", "Waiting since"),
            queue,
            "Nothing is waiting for review.",
        )
        + queue_note
        + "</section></div>"
    )


def activity(events: tuple[Activity, ...], now: datetime, outcome: api.ApiOutcome) -> None:
    if outcome is not api.ApiOutcome.OK:
        body = '<p class="fp-panel-empty">Activity could not be loaded from the API.</p>'
    elif not events:
        body = '<p class="fp-panel-empty">No activity recorded yet.</p>'
    else:
        body = '<ol class="fp-activity">' + "".join(
            f'<li class="fp-act-{ACTIVITY_TONES.get(e.action, "blue")}"><i></i><div>'
            f"<strong>{escape(e.label)}</strong><span>{escape(e.subject)}</span>"
            f'<time datetime="{e.when.isoformat()}">{relative_time(e.when, now)}</time></div></li>'
            for e in events
        ) + "</ol>"  # fmt: skip
    st.html(
        '<section class="fp-panel fp-panel-wide"><h2>Recent activity</h2>'
        "<p>Latest audit events on the most recently updated leads.</p>"
        f"{body}</section>"
    )


def render(config: api.UIConfig) -> None:
    with st.spinner("Loading your pipeline…"):
        snapshot = api.fetch_leads(config)
    rows: list[LeadRow] = []
    outcome = snapshot.outcome
    if outcome is api.ApiOutcome.OK:
        try:
            rows = [parse_lead(item) for item in snapshot.leads]
        except (KeyError, TypeError, ValueError):
            outcome = api.ApiOutcome.UNEXPECTED
    if outcome is not api.ApiOutcome.OK:
        title, body = FAILURES[outcome]
        notice(title, body)
        st.button("Try again", key="fp_retry", icon=":material/refresh:")
        return
    if not rows:
        notice(
            "No leads yet",
            "Leads appear here as soon as they arrive through the intake API (POST /leads).",
            tone="info",
        )
        return

    now = datetime.now(UTC)
    overview = build_overview(rows, now)
    if snapshot.truncated:
        notice(
            "Showing part of your pipeline",
            f"These figures cover the first {len(rows)} of {snapshot.total} leads.",
            tone="info",
        )
    kpis(overview)
    charts(overview)
    tables(overview)
    audit_outcome, entries = api.fetch_audit(config, activity_lead_ids(rows))
    activity(build_activity(entries, rows), now, audit_outcome)
