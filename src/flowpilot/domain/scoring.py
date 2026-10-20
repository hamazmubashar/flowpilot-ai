"""Deterministic lead scoring rules. See docs/scoring-rules.md for the business rationale.

Changing any table or threshold below must bump RULES_VERSION so historical scores remain
attributable to the rules that produced them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from flowpilot.domain.models import (
    BudgetBand,
    CompanySize,
    Industry,
    LeadIn,
    Route,
    ScoreComponent,
    ScoreResult,
    ServiceLine,
    Tier,
    Timeline,
)

RULES_VERSION = "2026.10-v1"

BUDGET_POINTS: Mapping[BudgetBand, int] = {
    BudgetBand.UNDER_10K: 5,
    BudgetBand.K10_25: 15,
    BudgetBand.K25_50: 25,
    BudgetBand.K50_100: 30,
    BudgetBand.OVER_100K: 35,
    BudgetBand.UNDISCLOSED: 10,
}

COMPANY_SIZE_POINTS: Mapping[CompanySize, int] = {
    CompanySize.S_1_10: 5,
    CompanySize.S_11_50: 12,
    CompanySize.S_51_200: 20,
    CompanySize.S_201_1000: 18,
    CompanySize.S_1000_PLUS: 15,
}

TIMELINE_POINTS: Mapping[Timeline, int] = {
    Timeline.IMMEDIATE: 20,
    Timeline.M1_3: 15,
    Timeline.M3_6: 8,
    Timeline.EXPLORING: 3,
}

SERVICE_FIT_POINTS: Mapping[ServiceLine, int] = {
    ServiceLine.PROCESS_AUTOMATION: 15,
    ServiceLine.DATA_ANALYTICS: 15,
    ServiceLine.AI_STRATEGY: 12,
    ServiceLine.SYSTEMS_INTEGRATION: 12,
    ServiceLine.DIGITAL_TRANSFORMATION: 10,
    ServiceLine.OTHER: 3,
}

INDUSTRY_FIT_POINTS: Mapping[Industry, int] = {
    Industry.SAAS: 10,
    Industry.FINTECH: 10,
    Industry.LOGISTICS: 10,
    Industry.PROFESSIONAL_SERVICES: 10,
    Industry.HEALTHCARE: 6,
    Industry.RETAIL_ECOMMERCE: 6,
    Industry.MANUFACTURING: 6,
    Industry.NONPROFIT: 4,
    Industry.OTHER: 2,
}

HOT_THRESHOLD = 70
WARM_THRESHOLD = 45

TIER_ROUTES: Mapping[Tier, Route] = {
    Tier.HOT: Route.PARTNER_REVIEW,
    Tier.WARM: Route.SALES_QUEUE,
    Tier.COLD: Route.NURTURE,
}


def tier_for(total: int) -> Tier:
    if total >= HOT_THRESHOLD:
        return Tier.HOT
    if total >= WARM_THRESHOLD:
        return Tier.WARM
    return Tier.COLD


def _component[K: StrEnum](rule: str, table: Mapping[K, int], value: K) -> ScoreComponent:
    return ScoreComponent(
        rule=rule, input=value.value, points=table[value], max_points=max(table.values())
    )


def score_lead(lead: LeadIn) -> ScoreResult:
    breakdown = [
        _component("budget", BUDGET_POINTS, lead.budget_band),
        _component("company_size", COMPANY_SIZE_POINTS, lead.company_size),
        _component("timeline", TIMELINE_POINTS, lead.timeline),
        _component("service_fit", SERVICE_FIT_POINTS, lead.service_interest),
        _component("industry_fit", INDUSTRY_FIT_POINTS, lead.industry),
    ]
    total = sum(c.points for c in breakdown)
    tier = tier_for(total)
    return ScoreResult(
        rules_version=RULES_VERSION,
        total=total,
        tier=tier,
        route=TIER_ROUTES[tier],
        breakdown=breakdown,
    )
