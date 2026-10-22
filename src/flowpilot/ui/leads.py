"""Leads page helpers (M5): filter and form options, payloads and paging. No Streamlit import.

Every option comes from the domain enums the API validates against, so the page can only offer
values `GET /leads` filters by and `POST /leads` accepts.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from math import ceil

from flowpilot.domain.models import (
    BudgetBand,
    CompanySize,
    Industry,
    LeadSource,
    LeadStatus,
    ServiceLine,
    Tier,
    Timeline,
)
from flowpilot.ui.dashboard import SOURCE_LABELS, STATUS_LABELS, TIER_LABELS

ALL = "all"

STATUS_FILTER = {ALL: "All statuses", **{s.value: STATUS_LABELS[s] for s in LeadStatus}}
TIER_FILTER = {ALL: "All tiers", **{t.value: TIER_LABELS[t] for t in Tier}}

FORM_OPTIONS: dict[str, dict[str, str]] = {
    "company_size": {s.value: f"{s.value} employees" for s in CompanySize},
    "industry": {i.value: i.value.replace("_", " ").capitalize() for i in Industry},
    "service_interest": {s.value: s.value.replace("_", " ").capitalize() for s in ServiceLine},
    "budget_band": {
        BudgetBand.UNDER_10K: "Under $10k",
        BudgetBand.K10_25: "$10k to $25k",
        BudgetBand.K25_50: "$25k to $50k",
        BudgetBand.K50_100: "$50k to $100k",
        BudgetBand.OVER_100K: "Over $100k",
        BudgetBand.UNDISCLOSED: "Not disclosed",
    },
    "timeline": {
        Timeline.IMMEDIATE: "Immediately",
        Timeline.M1_3: "In 1 to 3 months",
        Timeline.M3_6: "In 3 to 6 months",
        Timeline.EXPLORING: "Just exploring",
    },
    "source": {s.value: SOURCE_LABELS[s] for s in LeadSource},
}
TEXT_FIELDS = ("company_name", "contact_name", "contact_email", "message")
FIELD_LABELS = {
    "company_name": "Company",
    "contact_name": "Contact name",
    "contact_email": "Email",
    "company_size": "Company size",
    "industry": "Industry",
    "service_interest": "Service interest",
    "budget_band": "Budget",
    "timeline": "Timeline",
    "source": "Source",
    "message": "Message",
    "request": "Request",
}
ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")  # the API's X-Actor pattern


def server_filter(value: str) -> str | None:
    return None if value == ALL else value


def build_payload(values: dict[str, str]) -> dict[str, str]:
    """The `LeadIn` body: trimmed text fields plus the selected enum values, nothing else."""
    payload = {field: str(values.get(field, "")).strip() for field in TEXT_FIELDS}
    payload.update({field: str(values[field]) for field in FORM_OPTIONS})
    return payload


def payload_fingerprint(payload: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def idempotency_key_for(payload: dict[str, str], previous: tuple[str, str] | None) -> str:
    """Reuse the key of an unfinished attempt with the same payload (a retry or double click
    replays it); any change to the payload gets a fresh key."""
    fingerprint = payload_fingerprint(payload)
    if previous is not None and previous[0] == fingerprint:
        return previous[1]
    return f"console-{uuid.uuid4().hex}"


def audit_actor(reviewer_name: str) -> str:
    return reviewer_name if ACTOR_PATTERN.match(reviewer_name) else "console"


@dataclass(frozen=True)
class PageWindow:
    page: int  # 1-based, clamped to the available pages
    pages: int
    offset: int
    first: int  # 1-based index of the first row shown (0 when empty)
    last: int


def page_window(page: int, total: int, size: int, shown: int | None = None) -> PageWindow:
    pages = max(1, ceil(total / size))
    page = min(max(1, page), pages)
    offset = (page - 1) * size
    count = min(size, max(0, total - offset)) if shown is None else shown
    return PageWindow(page, pages, offset, offset + 1 if count else 0, offset + count)


def showing_label(window: PageWindow, total: int, noun: tuple[str, str] = ("lead", "leads")) -> str:
    if total == 0 or window.last == 0:
        return f"No {noun[1]} to show"
    return f"Showing {window.first}\N{EN DASH}{window.last} of {total} {noun[total != 1]}"
