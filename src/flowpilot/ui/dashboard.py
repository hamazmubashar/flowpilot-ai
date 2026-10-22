"""Dashboard figures computed from real `GET /leads` and `GET /leads/{id}/audit` responses.

No Streamlit import. Statuses, tiers and sources come from the domain enums, so the labels can
only show values the API can actually return; anything unknown is counted under its raw value.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from flowpilot.domain.models import LeadSource, LeadStatus, Tier

STATUS_LABELS = {
    LeadStatus.NEW: "New",
    LeadStatus.SCORED: "Scored",
    LeadStatus.DRAFTED: "Drafted",
    LeadStatus.AWAITING_APPROVAL: "Awaiting approval",
    LeadStatus.APPROVED: "Approved",
    LeadStatus.REJECTED: "Rejected",
    LeadStatus.SENT: "Sent",
    LeadStatus.CLOSED: "Closed",
}
TIER_LABELS = {Tier.HOT: "Hot", Tier.WARM: "Warm", Tier.COLD: "Cold"}
SOURCE_LABELS = {
    LeadSource.WEB_FORM: "Web form",
    LeadSource.REFERRAL: "Referral",
    LeadSource.EVENT: "Event",
    LeadSource.PARTNER: "Partner",
}
# A draft exists and no reviewer has decided it (ADR 0004 accepts decisions from both statuses).
REVIEW_STATUSES = (LeadStatus.DRAFTED, LeadStatus.AWAITING_APPROVAL)

ACTION_LABELS = {
    "lead.created": "New lead added",
    "lead.scored": "Lead scored",
    "lead.duplicate_submission": "Duplicate submission",
    "lead.qualified": "Lead qualified",
    "lead.drafted": "Draft prepared",
    "lead.approved": "Draft approved",
    "lead.rejected": "Draft rejected",
}

TREND_DAYS = 14
RECENT_COUNT = 5


@dataclass(frozen=True)
class LeadRow:
    id: int
    company: str
    contact: str
    source: str
    status: str
    created: datetime
    updated: datetime
    score: int | None
    tier: str | None


@dataclass(frozen=True)
class Slice:
    key: str
    label: str
    count: int
    share: float


@dataclass(frozen=True)
class Overview:
    total: int
    new_7d: int
    new_prev_7d: int
    hot: int
    warm: int
    cold: int
    unscored: int
    awaiting_review: int
    approved: int
    by_status: tuple[Slice, ...]
    by_source: tuple[Slice, ...]
    daily: tuple[tuple[date, int], ...]
    recent: tuple[LeadRow, ...]
    review_queue: tuple[LeadRow, ...]


@dataclass(frozen=True)
class Activity:
    when: datetime
    action: str
    label: str
    lead_id: int
    subject: str


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_lead(item: dict) -> LeadRow:
    """One `LeadSummary`; raises KeyError/TypeError/ValueError on a malformed item."""
    score = item.get("score")
    return LeadRow(
        id=int(item["id"]),
        company=str(item["company_name"]),
        contact=str(item["contact_name"]),
        source=str(item["source"]),
        status=str(item["status"]),
        created=_timestamp(item["created_at"]),
        updated=_timestamp(item["updated_at"]),
        score=None if score is None else int(score["total"]),
        tier=None if score is None else str(score["tier"]),
    )


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)  # type: ignore[call-overload]


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)  # type: ignore[call-overload]


def _slices(counts: Counter[str], order: list[str], label) -> tuple[Slice, ...]:
    total = sum(counts.values())
    keys = [k for k in order if counts[k]] + sorted(k for k in counts if k not in order)
    return tuple(Slice(k, label(k), counts[k], counts[k] / total if total else 0.0) for k in keys)


def build_overview(rows: list[LeadRow], now: datetime) -> Overview:
    today = now.astimezone(UTC).date()
    week_start = today - timedelta(days=6)
    prev_start = week_start - timedelta(days=7)
    created = Counter(row.created.astimezone(UTC).date() for row in rows)
    tiers = Counter(row.tier for row in rows)
    statuses = Counter(row.status for row in rows)
    newest_first = sorted(rows, key=lambda r: (r.created, r.id), reverse=True)
    waiting = sorted(
        (r for r in rows if r.status in REVIEW_STATUSES), key=lambda r: (r.updated, r.id)
    )
    return Overview(
        total=len(rows),
        new_7d=sum(n for day, n in created.items() if week_start <= day <= today),
        new_prev_7d=sum(n for day, n in created.items() if prev_start <= day < week_start),
        hot=tiers[Tier.HOT],
        warm=tiers[Tier.WARM],
        cold=tiers[Tier.COLD],
        unscored=tiers[None],
        awaiting_review=len(waiting),
        approved=statuses[LeadStatus.APPROVED],
        by_status=_slices(statuses, [s.value for s in LeadStatus], status_label),
        by_source=_slices(Counter(r.source for r in rows), [s.value for s in LeadSource],
                          source_label),
        daily=tuple(
            (day, created[day])
            for day in (today - timedelta(days=offset) for offset in range(TREND_DAYS - 1, -1, -1))
        ),
        recent=tuple(newest_first[:RECENT_COUNT]),
        review_queue=tuple(waiting[:RECENT_COUNT]),
    )  # fmt: skip


def activity_lead_ids(rows: list[LeadRow], count: int = RECENT_COUNT) -> list[int]:
    """Leads whose audit trails are fetched: the most recently updated ones."""
    return [r.id for r in sorted(rows, key=lambda r: (r.updated, r.id), reverse=True)[:count]]


def build_activity(
    entries: dict[int, list[dict]], rows: list[LeadRow], limit: int = RECENT_COUNT
) -> tuple[Activity, ...]:
    by_id = {row.id: row for row in rows}
    events = []
    for lead_id, items in entries.items():
        row = by_id.get(lead_id)
        subject = f"{row.contact}, {row.company}" if row else f"Lead #{lead_id}"
        for item in items:
            action = str(item.get("action", ""))
            if not action.startswith("lead."):
                continue
            label = ACTION_LABELS.get(action, action.removeprefix("lead.").replace("_", " "))
            events.append(
                Activity(_timestamp(item["occurred_at"]), action, label, lead_id, subject)
            )
    events.sort(key=lambda e: e.when, reverse=True)
    return tuple(events[:limit])


def relative_time(when: datetime, now: datetime) -> str:
    seconds = max(0, int((now - when).total_seconds()))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def week_change(current: int, previous: int) -> str | None:
    """'+50% vs previous 7 days', or None when there is nothing to compare with."""
    if previous == 0:
        return None
    pct = round((current - previous) * 100 / previous)
    return f"{pct:+d}% vs previous 7 days"
