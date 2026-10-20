from typing import Any

import pytest

from flowpilot.domain import scoring
from flowpilot.domain.models import (
    BudgetBand,
    CompanySize,
    Industry,
    LeadIn,
    Route,
    ServiceLine,
    Tier,
    Timeline,
)


def make_lead(lead_payload: dict[str, Any], **overrides: str) -> LeadIn:
    return LeadIn(**{**lead_payload, **overrides})


@pytest.mark.parametrize(
    ("table", "enum"),
    [
        (scoring.BUDGET_POINTS, BudgetBand),
        (scoring.COMPANY_SIZE_POINTS, CompanySize),
        (scoring.TIMELINE_POINTS, Timeline),
        (scoring.SERVICE_FIT_POINTS, ServiceLine),
        (scoring.INDUSTRY_FIT_POINTS, Industry),
    ],
)
def test_every_enum_value_has_points(table: dict, enum: type) -> None:
    assert set(table) == set(enum)


def test_component_maxima_sum_to_100() -> None:
    tables = [
        scoring.BUDGET_POINTS,
        scoring.COMPANY_SIZE_POINTS,
        scoring.TIMELINE_POINTS,
        scoring.SERVICE_FIT_POINTS,
        scoring.INDUSTRY_FIT_POINTS,
    ]
    assert sum(max(t.values()) for t in tables) == 100


def test_best_possible_lead_scores_100(lead_payload: dict[str, Any]) -> None:
    result = scoring.score_lead(
        make_lead(
            lead_payload,
            budget_band="over_100k",
            company_size="51-200",
            timeline="immediate",
            service_interest="data_analytics",
            industry="saas",
        )
    )
    assert result.total == 100
    assert result.tier is Tier.HOT
    assert result.route is Route.PARTNER_REVIEW


def test_worst_possible_lead_is_cold(lead_payload: dict[str, Any]) -> None:
    result = scoring.score_lead(
        make_lead(
            lead_payload,
            budget_band="under_10k",
            company_size="1-10",
            timeline="exploring",
            service_interest="other",
            industry="other",
        )
    )
    assert result.total == 5 + 5 + 3 + 3 + 2
    assert result.tier is Tier.COLD
    assert result.route is Route.NURTURE


def test_breakdown_sums_to_total_and_records_inputs(lead_payload: dict[str, Any]) -> None:
    result = scoring.score_lead(make_lead(lead_payload))
    assert sum(c.points for c in result.breakdown) == result.total
    assert [c.rule for c in result.breakdown] == [
        "budget",
        "company_size",
        "timeline",
        "service_fit",
        "industry_fit",
    ]
    by_rule = {c.rule: c for c in result.breakdown}
    assert by_rule["budget"].input == "50k_100k"
    assert by_rule["budget"].points == 30
    assert by_rule["budget"].max_points == 35
    # 30 + 20 + 15 + 15 + 10
    assert result.total == 90
    assert result.rules_version == scoring.RULES_VERSION


@pytest.mark.parametrize(
    ("total", "tier"),
    [(100, Tier.HOT), (70, Tier.HOT), (69, Tier.WARM), (45, Tier.WARM), (44, Tier.COLD),
     (0, Tier.COLD)],
)  # fmt: skip
def test_tier_boundaries(total: int, tier: Tier) -> None:
    assert scoring.tier_for(total) is tier


def test_scoring_is_deterministic(lead_payload: dict[str, Any]) -> None:
    lead = make_lead(lead_payload)
    assert scoring.score_lead(lead) == scoring.score_lead(lead)
