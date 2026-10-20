from typing import Any

import pytest
from pydantic import ValidationError

from flowpilot.domain.models import LeadIn
from flowpilot.domain.normalize import company_key, is_synthetic_domain


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("ACME, Inc.", "acme"),
        ("Acme", "acme"),
        ("  acme   corp ", "acme"),
        ("Acme Holdings Ltd", "acme holdings"),
        ("Co", "co"),  # a lone suffix-like word is kept, not stripped to empty
        ("Blue-Fen  Analytics LLC", "blue fen analytics"),
    ],
)
def test_company_key(name: str, key: str) -> None:
    assert company_key(name) == key


@pytest.mark.parametrize(
    ("domain", "synthetic"),
    [
        ("example.com", True),
        ("sub.example.org", True),
        ("acme.example", True),
        ("acme.test", True),
        ("acme.invalid", True),
        ("EXAMPLE.NET.", True),
        ("gmail.com", False),
        ("notexample.com", False),
        ("example.com.evil.io", False),
    ],
)
def test_synthetic_domains(domain: str, synthetic: bool) -> None:
    assert is_synthetic_domain(domain) is synthetic


def test_email_is_normalized(lead_payload: dict[str, Any]) -> None:
    lead = LeadIn(**{**lead_payload, "contact_email": "  Rowan.Hollis@Glasswing.EXAMPLE "})
    assert lead.contact_email == "rowan.hollis@glasswing.example"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contact_email", "rowan@gmail.com"),
        ("contact_email", "not-an-email"),
        ("company_name", "!!"),
        ("company_name", "A"),
        ("message", "too short"),
        ("budget_band", "a_lot"),
        ("contact_name", "   "),
    ],
)
def test_invalid_fields_rejected(lead_payload: dict[str, Any], field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        LeadIn(**{**lead_payload, field: value})


def test_unknown_fields_rejected(lead_payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="extra"):
        LeadIn(**lead_payload, phone="555-0100")


def test_source_defaults_to_web_form(lead_payload: dict[str, Any]) -> None:
    payload = {k: v for k, v in lead_payload.items() if k != "source"}
    assert LeadIn(**payload).source.value == "web_form"
