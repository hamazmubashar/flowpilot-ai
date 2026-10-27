"""M5 Leads page and shared workspace shell, rendered with Streamlit's AppTest.

Skipped unless the optional `ui` group is installed (`uv run --group ui pytest`).
"""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from flowpilot.ui import api
from flowpilot.ui.api import (
    ApiOutcome,
    CreateOutcome,
    CreateResult,
    LeadPageResult,
    LeadsSnapshot,
    LoginOutcome,
    LoginResult,
    ReviewerIdentity,
    UIConfig,
)
from tests.conftest import API_KEY

APP = str(Path(__file__).resolve().parent.parent / "src" / "flowpilot" / "ui" / "app.py")
TOKEN = "fpr_" + "w0rkspace" * 5
CONFIG = UIConfig(api_url="http://127.0.0.1:8000", api_key=API_KEY)
KPIS = 'aria-label="Lead totals"'
SUBMIT = "FormSubmitter:fp_add_form-Add lead"


def item(lead_id: int, status: str = "scored", tier: str | None = "warm") -> dict:
    return {
        "id": lead_id,
        "company_name": f"Company {lead_id}",
        "contact_name": f"Person {lead_id}",
        "contact_email": f"p{lead_id}@company{lead_id}.example",
        "company_size": "11-50",
        "industry": "saas",
        "service_interest": "ai_strategy",
        "budget_band": "25k_50k",
        "timeline": "exploring",
        "source": "web_form",
        "message": "<script>alert(1)</script> Looking for help.",
        "status": status,
        "created_at": "2026-10-21T09:00:00+00:00",
        "updated_at": "2026-10-21T10:00:00+00:00",
        "score": None if tier is None else {"total": 64, "tier": tier, "route": "sales_queue"},
    }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stands in for the API: never reads `.env` or calls a server."""
    created = {**item(61), "score": {"total": 81, "tier": "hot", "route": "partner_review"}}
    state: dict = {
        "verify": LoginResult(LoginOutcome.OK, ReviewerIdentity(7, "reviewer:avery")),
        "totals": (
            ApiOutcome.OK,
            {"total": 60, "hot": 12, "warm": 30, "drafted": 3, "awaiting": 1},
        ),
        "items": [item(i) for i in range(1, 26)],
        "total": 60,
        "page_outcome": ApiOutcome.OK,
        "page_calls": [],
        "create": CreateResult(CreateOutcome.CREATED, lead=created),
        "creates": [],
    }
    monkeypatch.setattr(api, "load_ui_config", lambda: CONFIG)
    monkeypatch.setattr(api, "verify_reviewer", lambda c, t: state["verify"])
    monkeypatch.setattr(api, "fetch_leads", lambda c: LeadsSnapshot(ApiOutcome.OK, (), 0))
    monkeypatch.setattr(api, "fetch_lead_totals", lambda c, f: state["totals"])

    def page(config, *, status=None, tier=None, offset=0, limit=25):
        state["page_calls"].append({"status": status, "tier": tier, "offset": offset})
        if state["page_outcome"] is not ApiOutcome.OK:
            return LeadPageResult(state["page_outcome"])
        return LeadPageResult(ApiOutcome.OK, tuple(state["items"]), state["total"])

    def create(config, payload, *, actor, idempotency_key):
        state["creates"].append((payload, actor, idempotency_key))
        return state["create"]

    monkeypatch.setattr(api, "fetch_lead_page", page)
    monkeypatch.setattr(api, "create_lead", create)
    return state


def html(at: AppTest) -> str:
    return "\n".join(e.proto.body for e in at.get("html"))


def leads() -> AppTest:
    """Signed in, then moved from the Dashboard (the first page) to Leads."""
    at = AppTest.from_file(APP)
    at.session_state["fp_reviewer"] = {"id": 7, "name": "reviewer:avery"}
    at.session_state["fp_reviewer_token"] = TOKEN
    at.run()
    return at.button(key="fp_nav_leads").click().run()


def open_form(at: AppTest, **values: str) -> AppTest:
    at.button(key="fp_add_lead").click().run()
    text = {
        "company_name": "Glasswing Logistics",
        "contact_name": "Rowan Hollis",
        "contact_email": "rowan@glasswing.example",
        **values,
    }
    for name, value in text.items():
        at.text_input(key=f"fp_new_{name}").input(value)
    at.text_area(key="fp_new_message").input("We re-key every order into three systems.")
    choices = {
        "company_size": "51-200",
        "industry": "logistics",
        "service_interest": "process_automation",
        "budget_band": "50k_100k",
        "timeline": "1_3_months",
    }
    for name, value in choices.items():
        at.selectbox(key=f"fp_new_{name}").set_value(value)
    return at


def test_leads_page_shows_live_rows_and_totals(fake: dict) -> None:
    at = leads()
    assert not at.exception
    page = html(at)
    assert "<h1>Leads</h1>" in page
    assert "Live data" in page
    assert "Sample data" not in page
    assert "Ali Khan" not in page  # the old fabricated sample is gone
    assert "<strong>60</strong><span>Total leads</span>" in page
    assert "<strong>12</strong><span>Hot leads</span>" in page
    assert "<strong>4</strong><span>Awaiting review</span>" in page  # drafted + awaiting
    assert page.count('class="fp-row"') == 25
    assert "p1@company1.example" in page
    assert '<b class="fp-pill fp-medium" title="Warm tier">64</b>' in page
    assert "Showing 1\N{EN DASH}25 of 60 leads" in page
    assert "Page 1 of 3" in page
    assert TOKEN not in page
    assert API_KEY not in page


def test_lead_details_escape_api_text(fake: dict) -> None:
    page = html(leads())
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_status_and_tier_filters_are_sent_to_the_server(fake: dict) -> None:
    at = leads()
    at.button(key="fp_next").click().run()
    assert fake["page_calls"][-1]["offset"] == 25
    at.selectbox(key="fp_lf_status").set_value("drafted").run()
    assert fake["page_calls"][-1] == {"status": "drafted", "tier": None, "offset": 0}
    at.selectbox(key="fp_lf_tier").set_value("hot").run()
    assert fake["page_calls"][-1] == {"status": "drafted", "tier": "hot", "offset": 0}
    assert at.selectbox(key="fp_lf_status").value == "drafted"  # kept when the tier changed
    assert "the API filters leads by status and tier only" in html(at)


def test_pagination_buttons_follow_the_server_total(fake: dict) -> None:
    at = leads()
    assert at.button(key="fp_prev").disabled
    assert not at.button(key="fp_next").disabled
    at.button(key="fp_next").click().run()
    at.button(key="fp_next").click().run()
    assert "Page 3 of 3" in html(at)
    assert at.button(key="fp_next").disabled


def test_out_of_range_page_is_clamped(fake: dict) -> None:
    at = leads()
    at.button(key="fp_next").click().run()
    fake["total"] = 10  # the data shrank meanwhile
    at.run()
    assert "Page 1 of 1" in html(at)
    assert fake["page_calls"][-1]["offset"] == 0


def test_empty_and_filtered_empty_states(fake: dict) -> None:
    fake["items"], fake["total"] = [], 0
    at = leads()
    assert "No leads yet" in html(at)
    at.selectbox(key="fp_lf_tier").set_value("cold").run()
    assert "No leads match these filters" in html(at)
    at.button(key="fp_clear").click().run()
    assert at.selectbox(key="fp_lf_tier").value == "all"


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (ApiOutcome.API_UNAVAILABLE, "not reachable"),
        (ApiOutcome.API_KEY_REJECTED, "not authorised"),
        (ApiOutcome.UNEXPECTED, "unexpected response"),
    ],
)
def test_api_errors(fake: dict, outcome: ApiOutcome, message: str) -> None:
    fake["page_outcome"] = outcome
    at = leads()
    assert message in html(at)
    assert 'class="fp-row"' not in html(at)

    fake["page_outcome"], fake["totals"] = ApiOutcome.OK, (outcome, {})
    at.run()
    assert message in html(at)
    assert KPIS not in html(at)


def test_malformed_rows_are_an_error(fake: dict) -> None:
    fake["items"] = [{"id": 1}]
    page = html(leads())
    assert "unexpected response" in page
    assert 'class="fp-row"' not in page


def test_revoked_session_is_signed_out_before_leads_load(fake: dict) -> None:
    at = leads()
    fake["verify"] = LoginResult(LoginOutcome.INVALID_TOKEN)
    calls = len(fake["page_calls"])
    at.run()
    assert "Welcome back" in html(at)
    assert len(fake["page_calls"]) == calls
    assert "fp_reviewer_token" not in at.session_state


def test_add_lead_creates_once_and_resets_the_form(fake: dict) -> None:
    at = open_form(leads())
    at.button(key=SUBMIT).click().run()
    assert len(fake["creates"]) == 1
    payload, actor, key = fake["creates"][0]
    assert payload["contact_email"] == "rowan@glasswing.example"
    assert payload["source"] == "web_form"
    assert actor == "reviewer:avery"
    assert key.startswith("console-")
    page = html(at)
    assert "Lead #61 added" in page
    assert "Hot tier" in page
    assert "fp_new_company_name" not in at.session_state  # form closed and cleared
    assert "fp_add_attempt" not in at.session_state


def test_retry_after_network_failure_reuses_the_idempotency_key(fake: dict) -> None:
    fake["create"] = CreateResult(CreateOutcome.API_UNAVAILABLE)
    at = open_form(leads())
    at.button(key=SUBMIT).click().run()
    assert "Nothing was saved" in html(at)
    assert at.text_input(key="fp_new_company_name").value == "Glasswing Logistics"  # kept
    at.button(key=SUBMIT).click().run()
    keys = [key for _, _, key in fake["creates"]]
    assert len(keys) == 2
    assert keys[0] == keys[1]  # the API replays it instead of creating a second lead


def test_validation_and_duplicate_feedback(fake: dict) -> None:
    fake["create"] = CreateResult(
        CreateOutcome.INVALID,
        errors=(("contact_email", "only synthetic addresses are accepted"),),
    )
    at = open_form(leads(), contact_email="someone@gmail.com")
    at.button(key=SUBMIT).click().run()
    assert "Check these fields" in html(at)
    assert "<b>Email:</b> only synthetic addresses are accepted" in html(at)

    fake["create"] = CreateResult(CreateOutcome.DUPLICATE, lead=item(5))
    at.button(key=SUBMIT).click().run()
    assert "Already in FlowPilot as lead #5" in html(at)
    assert "nothing new was created" in html(at)


def test_missing_choices_are_caught_before_calling_the_api(fake: dict) -> None:
    at = leads()
    at.button(key="fp_add_lead").click().run()
    at.button(key=SUBMIT).click().run()
    assert fake["creates"] == []
    assert "<b>Company size:</b> choose an option" in html(at)


def test_sidebar_navigation_and_footer(fake: dict) -> None:
    at = leads()
    page = html(at)
    assert [b.key for b in at.button if b.key.startswith("fp_nav_")] == [
        "fp_nav_dashboard", "fp_nav_leads", "fp_nav_import", "fp_nav_drafts", "fp_nav_outbox",
        "fp_nav_settings",
    ]  # fmt: skip
    assert 'title="Not available yet"' not in page  # no placeholder entries remain
    # Sign-in page only: never in the authenticated shell.
    assert "Human approval required" not in page
    assert "No messages sent automatically" not in page
    assert "Hamaz Mubashar" in page
    assert "Runs locally" not in page


def test_workspace_stylesheet_is_local_only() -> None:
    from urllib.parse import quote

    from flowpilot.ui.theme import (
        NAV_DRAFT,
        NAV_HOME,
        NAV_LEADS,
        NAV_OUTBOX,
        NAV_SETTINGS,
        SVG_NS,
        workspace_stylesheet,
    )

    css = workspace_stylesheet()
    # Every live nav entry gets its icon; Draft Responses uses the existing document icon.
    for page, icon in (
        ("dashboard", NAV_HOME), ("leads", NAV_LEADS), ("drafts", NAV_DRAFT),
        ("outbox", NAV_OUTBOX), ("settings", NAV_SETTINGS),
    ):  # fmt: skip
        assert f".st-key-fp_nav_{page} button {{ --fp-ic: {icon}; }}" in css
    assert "@import" not in css
    assert "<script" not in css
    assert css.replace(quote(SVG_NS, safe=" =:/,.()-"), "").count("http") == 0
