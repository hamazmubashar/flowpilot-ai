"""Deterministic synthetic lead generator (stdlib `random`, no Faker).

Every email uses the reserved `.example` TLD, so generated data can never identify a real
person. Names are composed from generic word lists and are fictional.
"""

from __future__ import annotations

import random
import re
from typing import Any

from flowpilot.domain.models import (
    BudgetBand,
    CompanySize,
    Industry,
    LeadSource,
    ServiceLine,
    Timeline,
)

DEFAULT_SEED = 42
DEFAULT_COUNT = 60

FIRST_NAMES = (
    "Avery", "Blake", "Casey", "Devon", "Emerson", "Finley", "Harper", "Jordan", "Kai",
    "Logan", "Morgan", "Noel", "Parker", "Quinn", "Reese", "Rowan", "Sage", "Taylor",
)  # fmt: skip
LAST_NAMES = (
    "Abbott", "Barlow", "Calder", "Dunmore", "Ellison", "Fairway", "Garrick", "Hollis",
    "Ivers", "Jessup", "Kendrick", "Lockhart", "Marlow", "Norcross", "Oakley", "Prescott",
)  # fmt: skip
COMPANY_PREFIXES = (
    "Bluefen", "Cobalt Ridge", "Driftwood", "Emberline", "Fernvale", "Glasswing",
    "Harborlight", "Ironbark", "Juniper Peak", "Kestrel", "Lumenhold", "Maplecrest",
    "Northfold", "Orchardview", "Pinewhistle", "Quillstone",
)  # fmt: skip
COMPANY_SUFFIXES = (
    "Analytics", "Freight", "Health", "Labs", "Logistics", "Manufacturing", "Partners",
    "Retail", "Software", "Systems",
)  # fmt: skip

PAIN_POINTS = (
    "Our order intake is still handled by copying data between spreadsheets.",
    "Reporting takes our finance team three days every month-end.",
    "We have five tools that do not talk to each other.",
    "Leadership wants a realistic plan for using AI without risking client data.",
    "Our support backlog has doubled since last quarter.",
    "We are migrating off a legacy ERP and need an integration plan.",
)

SIZE_WEIGHTS = {
    CompanySize.S_1_10: 2, CompanySize.S_11_50: 4, CompanySize.S_51_200: 4,
    CompanySize.S_201_1000: 3, CompanySize.S_1000_PLUS: 1,
}  # fmt: skip
BUDGET_WEIGHTS = {
    BudgetBand.UNDER_10K: 2, BudgetBand.K10_25: 3, BudgetBand.K25_50: 3,
    BudgetBand.K50_100: 2, BudgetBand.OVER_100K: 1, BudgetBand.UNDISCLOSED: 2,
}  # fmt: skip
TIMELINE_WEIGHTS = {
    Timeline.IMMEDIATE: 2, Timeline.M1_3: 4, Timeline.M3_6: 3, Timeline.EXPLORING: 2,
}  # fmt: skip
SOURCE_WEIGHTS = {
    LeadSource.WEB_FORM: 5, LeadSource.REFERRAL: 2, LeadSource.EVENT: 2, LeadSource.PARTNER: 1,
}  # fmt: skip


def _weighted[T](rng: random.Random, weights: dict[T, int]) -> T:
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def generate_leads(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    """Return `count` lead payloads; identical (count, seed) always yields identical output."""
    if count < 0:
        raise ValueError("count must be >= 0")
    max_unique = len(COMPANY_PREFIXES) * len(COMPANY_SUFFIXES)
    if count > max_unique:
        raise ValueError(f"count must be <= {max_unique}")

    rng = random.Random(seed)  # noqa: S311 - synthetic test data, not security-sensitive
    companies = [f"{p} {s}" for p in COMPANY_PREFIXES for s in COMPANY_SUFFIXES]
    rng.shuffle(companies)

    leads = []
    for company in companies[:count]:
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        size = _weighted(rng, SIZE_WEIGHTS)
        industry = rng.choice(list(Industry))
        service = rng.choice(list(ServiceLine))
        timeline = _weighted(rng, TIMELINE_WEIGHTS)
        message = (
            f"Hi Northstar team, we are a {size.value}-person "
            f"{industry.value.replace('_', ' ')} company interested in "
            f"{service.value.replace('_', ' ')}. {rng.choice(PAIN_POINTS)} "
            f"Timeline: {timeline.value.replace('_', ' ')}."
        )
        leads.append(
            {
                "company_name": company,
                "contact_name": f"{first} {last}",
                "contact_email": f"{first}.{last}@{_slug(company)}.example".lower(),
                "company_size": size.value,
                "industry": industry.value,
                "service_interest": service.value,
                "budget_band": _weighted(rng, BUDGET_WEIGHTS).value,
                "timeline": timeline.value,
                "source": _weighted(rng, SOURCE_WEIGHTS).value,
                "message": message,
            }
        )
    return leads
