"""M5 Leads page: helpers and the API client, including runs against the real FastAPI app."""

import httpx
import pytest
from fastapi.testclient import TestClient

from flowpilot.domain.models import LeadIn, LeadStatus, Tier
from flowpilot.ui.api import (
    ApiOutcome,
    CreateOutcome,
    UIConfig,
    create_lead,
    fetch_lead_page,
    fetch_lead_totals,
)
from flowpilot.ui.leads import (
    FORM_OPTIONS,
    STATUS_FILTER,
    TIER_FILTER,
    audit_actor,
    build_payload,
    idempotency_key_for,
    page_window,
    payload_fingerprint,
    server_filter,
    showing_label,
)
from tests.conftest import API_KEY

CONFIG = UIConfig(api_url="http://127.0.0.1:8000", api_key=API_KEY)

FORM = {
    "company_name": "  Glasswing Logistics ",
    "contact_name": "Rowan Hollis",
    "contact_email": "rowan@glasswing.example",
    "company_size": "51-200",
    "industry": "logistics",
    "service_interest": "process_automation",
    "budget_band": "50k_100k",
    "timeline": "1_3_months",
    "source": "referral",
    "message": "We re-key every order from email into three systems.",
}


# --- helpers -----------------------------------------------------------------------------------


def test_filters_offer_exactly_the_api_values() -> None:
    assert list(STATUS_FILTER)[1:] == [s.value for s in LeadStatus]
    assert list(TIER_FILTER)[1:] == [t.value for t in Tier]
    assert server_filter("all") is None
    assert server_filter("hot") == "hot"


def test_form_options_cover_every_lead_in_enum() -> None:
    for field, options in FORM_OPTIONS.items():
        allowed = LeadIn.model_fields[field].annotation
        assert {str(v) for v in options} == {m.value for m in allowed}, field


def test_payload_is_a_valid_lead_in_body() -> None:
    payload = build_payload({**FORM, "extra": "ignored"})
    assert payload["company_name"] == "Glasswing Logistics"
    assert set(payload) == set(LeadIn.model_fields)
    LeadIn(**payload)  # what the API will validate


def test_idempotency_key_is_reused_only_for_the_same_payload() -> None:
    payload = build_payload(FORM)
    first = idempotency_key_for(payload, None)
    assert first.startswith("console-")
    attempt = (payload_fingerprint(payload), first)
    assert idempotency_key_for(payload, attempt) == first  # retry or double click
    changed = build_payload({**FORM, "message": "A different request entirely."})
    assert idempotency_key_for(changed, attempt) != first


def test_audit_actor_follows_the_api_pattern() -> None:
    assert audit_actor("reviewer:avery") == "reviewer:avery"
    assert audit_actor("not allowed!") == "console"


@pytest.mark.parametrize(
    ("page", "total", "shown", "expected"),
    [
        (1, 60, 25, (1, 3, 0, 1, 25, "Showing 1\N{EN DASH}25 of 60 leads")),
        (3, 60, 10, (3, 3, 50, 51, 60, "Showing 51\N{EN DASH}60 of 60 leads")),
        (9, 60, None, (3, 3, 50, 51, 60, "Showing 51\N{EN DASH}60 of 60 leads")),  # clamped
        (1, 1, 1, (1, 1, 0, 1, 1, "Showing 1\N{EN DASH}1 of 1 lead")),
        (1, 0, 0, (1, 1, 0, 0, 0, "No leads to show")),
    ],
)
def test_page_window(page: int, total: int, shown: int | None, expected: tuple) -> None:
    w = page_window(page, total, 25, shown)
    assert (w.page, w.pages, w.offset, w.first, w.last, showing_label(w, total)) == expected


# --- API client (mocked) -----------------------------------------------------------------------


def test_lead_page_sends_server_filters_and_paging() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 25, "offset": 50})

    result = fetch_lead_page(
        CONFIG, status="drafted", tier=None, offset=50, transport=httpx.MockTransport(handler)
    )
    assert result.outcome is ApiOutcome.OK
    assert dict(seen[0].url.params) == {"status": "drafted", "limit": "25", "offset": "50"}
    assert seen[0].headers["X-API-Key"] == API_KEY


@pytest.mark.parametrize(
    ("status", "body", "outcome"),
    [
        (401, {}, ApiOutcome.API_KEY_REJECTED),
        (503, {}, ApiOutcome.API_UNAVAILABLE),
        (422, {}, ApiOutcome.UNEXPECTED),
        (200, {"items": None, "total": 0}, ApiOutcome.UNEXPECTED),
    ],
)
def test_lead_page_failures(status: int, body: dict, outcome: ApiOutcome) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    assert fetch_lead_page(CONFIG, transport=transport).outcome is outcome


def test_create_lead_failures() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def respond(status: int, body: object) -> httpx.MockTransport:
        return httpx.MockTransport(lambda r: httpx.Response(status, json=body))

    payload = build_payload(FORM)
    call = {"actor": "reviewer:avery", "idempotency_key": "console-1"}
    outcomes = [
        create_lead(CONFIG, payload, transport=transport, **call).outcome
        for transport in (
            httpx.MockTransport(refuse),
            respond(401, {}),
            respond(201, {"lead": {}}),
            respond(422, {"detail": {"code": "idempotency_key_reused", "message": "x"}}),
        )
    ]
    assert outcomes == [
        CreateOutcome.API_UNAVAILABLE,
        CreateOutcome.API_KEY_REJECTED,
        CreateOutcome.UNEXPECTED,
        CreateOutcome.KEY_REUSED,
    ]


# --- against the real API ----------------------------------------------------------------------


def make(client: TestClient, n: int, key: str | None = None, **changes):
    body = build_payload(
        {**FORM, "contact_email": f"p{n}@lead{n}.example", "company_name": f"Lead Co {n}"}
    )
    body.update(changes)
    return create_lead(
        CONFIG,
        body,
        actor="reviewer:avery",
        idempotency_key=key or f"t-{n}",
        transport=client._transport,
    )


def test_create_duplicate_replay_and_validation_against_the_api(client: TestClient) -> None:
    created = make(client, 1)
    assert created.outcome is CreateOutcome.CREATED
    assert created.lead is not None
    assert created.lead["score"]["tier"] in {"hot", "warm", "cold"}

    again = make(client, 1)  # same idempotency key and payload: replayed, nothing new
    assert again.outcome is CreateOutcome.CREATED
    assert again.replayed
    assert again.lead["id"] == created.lead["id"]

    dup = make(client, 1, key="t-other")  # same email and company, new key: a duplicate
    assert dup.outcome is CreateOutcome.DUPLICATE
    assert dup.lead["id"] == created.lead["id"]

    bad = make(client, 2, contact_email="real@gmail.com", message="short")
    assert bad.outcome is CreateOutcome.INVALID
    fields = dict(bad.errors)
    assert "only synthetic addresses" in fields["contact_email"]
    assert "message" in fields

    page = fetch_lead_page(CONFIG, transport=client._transport)
    assert page.total == 1  # the replay, duplicate and invalid attempts created nothing


def test_server_filters_paging_and_totals_against_the_api(client: TestClient, auth: dict) -> None:
    for n in range(30):
        assert make(client, 100 + n).outcome is CreateOutcome.CREATED
    lead_ids = [i["id"] for i in fetch_lead_page(CONFIG, transport=client._transport).items]
    assert client.post(f"/leads/{lead_ids[0]}/drafts", headers=auth).status_code == 201

    first = fetch_lead_page(CONFIG, transport=client._transport)
    second = fetch_lead_page(CONFIG, offset=25, transport=client._transport)
    assert (first.total, len(first.items), len(second.items)) == (30, 25, 5)
    assert {i["id"] for i in first.items}.isdisjoint(i["id"] for i in second.items)

    drafted = fetch_lead_page(CONFIG, status="drafted", transport=client._transport)
    assert drafted.total == 1
    assert drafted.items[0]["id"] == lead_ids[0]

    outcome, totals = fetch_lead_totals(
        CONFIG,
        {"total": {}, "drafted": {"status": "drafted"}, "hot": {"tier": "hot"}},
        transport=client._transport,
    )
    assert outcome is ApiOutcome.OK
    assert (totals["total"], totals["drafted"]) == (30, 1)
    tiers = [fetch_lead_page(CONFIG, tier=t, transport=client._transport).total for t in Tier]
    assert sum(tiers) == 30
    assert totals["hot"] == tiers[0]
