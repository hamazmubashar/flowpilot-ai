import pytest

from flowpilot.domain.models import LeadIn
from flowpilot.domain.normalize import company_key, email_domain, is_synthetic_domain
from flowpilot.synthetic import generate_leads


def test_same_seed_same_output() -> None:
    assert generate_leads(60, seed=7) == generate_leads(60, seed=7)


def test_different_seed_different_output() -> None:
    assert generate_leads(20, seed=1) != generate_leads(20, seed=2)


def test_prefix_stability() -> None:
    assert generate_leads(10, seed=3) == generate_leads(30, seed=3)[:10]


def test_all_leads_valid_synthetic_and_unique() -> None:
    leads = [LeadIn(**p) for p in generate_leads(160, seed=42)]
    assert len(leads) == 160
    for lead in leads:
        domain = email_domain(lead.contact_email)
        assert domain is not None
        assert is_synthetic_domain(domain)
    keys = {(lead.contact_email, company_key(lead.company_name)) for lead in leads}
    assert len(keys) == len(leads)


@pytest.mark.parametrize("count", [-1, 161])
def test_count_bounds(count: int) -> None:
    with pytest.raises(ValueError, match="count"):
        generate_leads(count)
