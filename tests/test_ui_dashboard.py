"""M5 Dashboard: figures from real API responses, the API client, and the rendered page."""

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from flowpilot.domain.models import LeadStatus
from flowpilot.ui import api
from flowpilot.ui.api import (
    ApiOutcome,
    LeadsSnapshot,
    LoginOutcome,
    LoginResult,
    ReviewerIdentity,
    UIConfig,
    fetch_audit,
    fetch_leads,
)
from flowpilot.ui.dashboard import (
    STATUS_LABELS,
    activity_lead_ids,
    build_activity,
    build_overview,
    parse_lead,
    relative_time,
    week_change,
)
from tests.conftest import API_KEY

CONFIG = UIConfig(api_url="http://127.0.0.1:8000", api_key=API_KEY)
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
APP = str(Path(__file__).resolve().parent.parent / "src" / "flowpilot" / "ui" / "app.py")
TOKEN = "fpr_" + "d4shboard" * 5
KPIS = 'aria-label="Pipeline totals"'  # the KPI block itself, not its CSS


def item(lead_id: int, status: str, tier: str | None, source: str, day: int, **extra) -> dict:
    return {
        "id": lead_id,
        "company_name": f"Company {lead_id}",
        "contact_name": f"Person {lead_id}",
        "source": source,
        "status": status,
        "created_at": f"2026-10-{day:02d}T09:00:00+00:00",
        "updated_at": extra.get("updated", f"2026-10-{day:02d}T10:00:00+00:00"),
        "score": None if tier is None else {"total": 60, "tier": tier, "route": "sales_queue"},
    }


ITEMS = (
    item(1, "scored", "hot", "web_form", 5),
    item(2, "drafted", "warm", "referral", 12),
    item(3, "awaiting_approval", "cold", "web_form", 18),
    item(4, "approved", "hot", "partner", 20),
    item(5, "new", None, "event", 22),
)


def rows():
    return [parse_lead(i) for i in ITEMS]


# --- figures ---------------------------------------------------------------------------------


def test_overview_counts_match_the_records() -> None:
    ov = build_overview(rows(), NOW)
    assert ov.total == 5
    assert (ov.hot, ov.warm, ov.cold, ov.unscored) == (2, 1, 1, 1)
    assert ov.awaiting_review == 2  # drafted + awaiting_approval
    assert [r.id for r in ov.review_queue] == [2, 3]  # oldest waiting first
    assert ov.approved == 1
    assert ov.new_7d == 3  # Oct 16-22: leads 3, 4, 5
    assert ov.new_prev_7d == 1  # Oct 9-15: lead 2
    assert [r.id for r in ov.recent] == [5, 4, 3, 2, 1]


def test_status_and_source_use_the_domain_values_in_domain_order() -> None:
    ov = build_overview(rows(), NOW)
    assert [(s.key, s.label, s.count) for s in ov.by_status] == [
        ("new", "New", 1), ("scored", "Scored", 1), ("drafted", "Drafted", 1),
        ("awaiting_approval", "Awaiting approval", 1), ("approved", "Approved", 1),
    ]  # fmt: skip
    assert [(s.label, s.count) for s in ov.by_source] == [
        ("Web form", 2), ("Referral", 1), ("Event", 1), ("Partner", 1),
    ]  # fmt: skip
    assert sum(s.share for s in ov.by_source) == pytest.approx(1)
    assert set(STATUS_LABELS) == set(LeadStatus)  # every real status has a label


def test_unknown_values_are_kept_not_relabelled() -> None:
    ov = build_overview([parse_lead(item(9, "mystery", "hot", "carrier_pigeon", 22))], NOW)
    assert [(s.key, s.label) for s in ov.by_status] == [("mystery", "mystery")]
    assert [(s.key, s.label) for s in ov.by_source] == [("carrier_pigeon", "carrier_pigeon")]


def test_daily_trend_covers_fourteen_days_ending_today() -> None:
    daily = build_overview(rows(), NOW).daily
    assert len(daily) == 14
    assert daily[0][0] == date(2026, 10, 9)
    assert daily[-1] == (date(2026, 10, 22), 1)
    assert sum(n for _, n in daily) == 4  # lead 1 (Oct 5) is outside the window


def test_empty_dataset() -> None:
    ov = build_overview([], NOW)
    assert ov.total == ov.awaiting_review == ov.new_7d == 0
    assert ov.by_status == ov.by_source == ov.recent == ()


def test_malformed_lead_is_rejected() -> None:
    bad = dict(ITEMS[0])
    del bad["status"]
    with pytest.raises(KeyError):
        parse_lead(bad)


def test_activity_merges_lead_events_newest_first() -> None:
    entries = {
        4: [{"action": "lead.created", "occurred_at": "2026-10-20T09:00:00+00:00"},
            {"action": "lead.approved", "occurred_at": "2026-10-21T15:00:00+00:00"}],
        3: [{"action": "lead.drafted", "occurred_at": "2026-10-21T16:00:00+00:00"},
            {"action": "outbox.enqueued", "occurred_at": "2026-10-21T17:00:00+00:00"}],
    }  # fmt: skip
    events = build_activity(entries, rows())
    assert [(e.label, e.subject) for e in events] == [
        ("Draft prepared", "Person 3, Company 3"),
        ("Draft approved", "Person 4, Company 4"),
        ("New lead added", "Person 4, Company 4"),
    ]
    assert activity_lead_ids(rows(), 2) == [5, 4]


def test_relative_time_and_week_change() -> None:
    assert relative_time(datetime(2026, 10, 22, 10, 0, tzinfo=UTC), NOW) == "2 hours ago"
    assert relative_time(datetime(2026, 10, 21, 12, 0, tzinfo=UTC), NOW) == "1 day ago"
    assert relative_time(NOW, NOW) == "just now"
    assert week_change(3, 2) == "+50% vs previous 7 days"
    assert week_change(1, 2) == "-50% vs previous 7 days"
    assert week_change(3, 0) is None


# --- API client --------------------------------------------------------------------------------


def paged(total_items: list[dict], seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        page = total_items[offset : offset + limit]
        return httpx.Response(200, json={"items": page, "total": len(total_items)})

    return httpx.MockTransport(handler)


def test_fetch_leads_reads_every_page_with_the_api_key_only() -> None:
    many = [item(i, "scored", "warm", "web_form", 20) for i in range(1, 251)]
    seen: list[httpx.Request] = []
    snap = fetch_leads(CONFIG, transport=paged(many, seen))
    assert snap.outcome is ApiOutcome.OK
    assert (len(snap.leads), snap.total, snap.truncated) == (250, 250, False)
    assert [r.url.params["offset"] for r in seen] == ["0", "100", "200"]
    assert all(r.headers["X-API-Key"] == API_KEY for r in seen)
    assert all("X-Reviewer-Token" not in r.headers for r in seen)


def test_fetch_leads_reports_truncation() -> None:
    many = [item(i, "scored", "warm", "web_form", 20) for i in range(1, 251)]
    snap = fetch_leads(CONFIG, transport=paged(many), max_leads=200)
    assert (len(snap.leads), snap.total, snap.truncated) == (200, 250, True)


@pytest.mark.parametrize(
    ("status", "body", "outcome"),
    [
        (401, {}, ApiOutcome.API_KEY_REJECTED),
        (500, {}, ApiOutcome.API_UNAVAILABLE),
        (404, {}, ApiOutcome.UNEXPECTED),
        (200, {"items": "nope", "total": 1}, ApiOutcome.UNEXPECTED),
        (200, {"total": 1}, ApiOutcome.UNEXPECTED),
    ],
)
def test_fetch_leads_failures(status: int, body: dict, outcome: ApiOutcome) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    snap = fetch_leads(CONFIG, transport=transport)
    assert snap.outcome is outcome
    assert snap.leads == ()


def test_fetch_leads_connection_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert fetch_leads(CONFIG, transport=httpx.MockTransport(refuse)).outcome is (
        ApiOutcome.API_UNAVAILABLE
    )


def test_fetch_audit_skips_missing_leads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/leads/2/audit":
            return httpx.Response(404, json={})
        return httpx.Response(200, json=[{"action": "lead.created"}])

    outcome, entries = fetch_audit(CONFIG, [1, 2], transport=httpx.MockTransport(handler))
    assert outcome is ApiOutcome.OK
    assert list(entries) == [1]


def test_end_to_end_against_the_real_api(
    client: TestClient, auth: dict, lead_payload: dict
) -> None:
    ids = []
    for n in range(3):
        payload = {
            **lead_payload,
            "contact_email": f"person{n}@lead{n}.example",
            "company_name": f"Lead Co {n}",
        }
        ids.append(client.post("/leads", json=payload, headers=auth).json()["lead"]["id"])
    assert client.post(f"/leads/{ids[0]}/drafts", headers=auth).status_code == 201
    api_transport = client._transport

    snap = fetch_leads(CONFIG, transport=api_transport)
    assert snap.outcome is ApiOutcome.OK
    ov = build_overview([parse_lead(i) for i in snap.leads], datetime.now(UTC))
    assert ov.total == 3
    assert ov.awaiting_review == 1
    assert {s.key: s.count for s in ov.by_status} == {"scored": 2, "drafted": 1}
    assert ov.hot + ov.warm + ov.cold == 3  # every intake lead is scored

    outcome, entries = fetch_audit(CONFIG, [ids[0]], transport=api_transport)
    assert outcome is ApiOutcome.OK
    actions = [e.action for e in build_activity(entries, [parse_lead(i) for i in snap.leads], 10)]
    assert "lead.drafted" in actions
    assert "lead.created" in actions

    wrong_key = UIConfig(api_url=CONFIG.api_url, api_key=API_KEY + "x")
    assert fetch_leads(wrong_key, transport=api_transport).outcome is ApiOutcome.API_KEY_REJECTED


# --- rendered page -----------------------------------------------------------------------------


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Never read the real `.env` or call a server; tests set what the API answers."""
    state = {
        "verify": LoginResult(LoginOutcome.OK, ReviewerIdentity(7, "reviewer:avery")),
        "leads": LeadsSnapshot(ApiOutcome.OK, ITEMS, len(ITEMS)),
        "audit": (ApiOutcome.OK, {}),
        "fetches": 0,
        "tokens": [],
    }
    monkeypatch.setattr(api, "load_ui_config", lambda: CONFIG)

    def verify(config: UIConfig, token: str) -> LoginResult:
        state["tokens"].append(token)
        return state["verify"]

    def leads(config: UIConfig) -> LeadsSnapshot:
        state["fetches"] += 1
        return state["leads"]

    monkeypatch.setattr(api, "verify_reviewer", verify)
    monkeypatch.setattr(api, "fetch_leads", leads)
    monkeypatch.setattr(api, "fetch_audit", lambda config, ids: state["audit"])
    return state


def html(at) -> str:
    return "\n".join(e.proto.body for e in at.get("html"))


def signed_in():
    app_test = pytest.importorskip("streamlit.testing.v1").AppTest  # needs the `ui` group
    at = app_test.from_file(APP)
    at.session_state["fp_reviewer"] = {"id": 7, "name": "reviewer:avery"}
    at.session_state["fp_reviewer_token"] = TOKEN
    return at.run()


def test_dashboard_is_the_first_page_and_shows_api_figures(fake_api: dict) -> None:
    at = signed_in()
    assert not at.exception
    page = html(at)
    assert "<h1>Dashboard</h1>" in page
    assert "Overview of your lead pipeline and workflow activity." in page
    assert "Live data" in page
    assert "<strong>5</strong><span>Total leads</span>" in page
    assert "<strong>2</strong><span>Hot leads</span>" in page
    assert "<strong>2</strong><span>Awaiting review</span>" in page
    assert "Awaiting approval" in page
    assert "Web form" in page
    assert "Sample data" not in page
    assert "Ali Khan" not in page
    assert fake_api["tokens"] == [TOKEN]  # session re-checked before data was shown
    assert TOKEN not in page
    assert API_KEY not in page
    assert at.button(key="fp_nav_dashboard").proto.type == "primary"


def test_revoked_session_returns_to_sign_in_without_data(fake_api: dict) -> None:
    fake_api["verify"] = LoginResult(LoginOutcome.INVALID_TOKEN)
    at = signed_in()
    page = html(at)
    assert "Welcome back" in page
    assert KPIS not in page
    assert fake_api["fetches"] == 0
    assert "not accepted" in at.error[0].value
    for key in ("fp_reviewer", "fp_reviewer_token"):
        assert key not in at.session_state


@pytest.mark.parametrize(
    ("verify", "message"),
    [
        (LoginOutcome.API_UNAVAILABLE, "not reachable"),
        (LoginOutcome.API_KEY_REJECTED, "not authorised"),
    ],
)
def test_unconfirmed_session_shows_no_data(
    fake_api: dict, verify: LoginOutcome, message: str
) -> None:
    fake_api["verify"] = LoginResult(verify)
    at = signed_in()
    assert message in html(at)
    assert KPIS not in html(at)
    assert fake_api["fetches"] == 0
    assert at.button(key="fp_retry")


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (ApiOutcome.API_UNAVAILABLE, "not reachable"),
        (ApiOutcome.API_KEY_REJECTED, "not authorised to read leads"),
        (ApiOutcome.UNEXPECTED, "unexpected response"),
    ],
)
def test_api_errors_show_a_retry_instead_of_figures(
    fake_api: dict, outcome: ApiOutcome, message: str
) -> None:
    fake_api["leads"] = LeadsSnapshot(outcome)
    at = signed_in()
    assert message in html(at)
    assert KPIS not in html(at)
    at.button(key="fp_retry").click().run()
    assert fake_api["fetches"] == 2


def test_malformed_records_are_an_error_not_partial_figures(fake_api: dict) -> None:
    fake_api["leads"] = LeadsSnapshot(ApiOutcome.OK, ({"id": 1},), 1)
    page = html(signed_in())
    assert "unexpected response" in page
    assert KPIS not in page


def test_empty_pipeline(fake_api: dict) -> None:
    fake_api["leads"] = LeadsSnapshot(ApiOutcome.OK, (), 0)
    page = html(signed_in())
    assert "No leads yet" in page
    assert KPIS not in page


def test_activity_failure_does_not_hide_the_figures(fake_api: dict) -> None:
    fake_api["audit"] = (ApiOutcome.API_UNAVAILABLE, {})
    page = html(signed_in())
    assert KPIS in page
    assert "Activity could not be loaded" in page


def test_navigation_and_sign_out(fake_api: dict) -> None:
    at = signed_in()
    at.button(key="fp_nav_leads").click().run()
    assert "<h1>Leads</h1>" in html(at)
    assert at.button(key="fp_nav_leads").proto.type == "primary"
    at.button(key="fp_nav_dashboard").click().run()
    assert "<h1>Dashboard</h1>" in html(at)
    page = html(at)
    assert 'title="Not available yet"' not in page  # every destination is built now
    assert at.button(key="fp_nav_drafts").label == "Draft Responses"
    assert at.button(key="fp_nav_outbox").label == "Outbox (Simulated)"
    assert at.button(key="fp_nav_settings").label == "Settings"
    assert "Human approval required" not in page  # sign-in page only
    assert "No messages sent automatically" not in page
    assert "Hamaz Mubashar" in page
    at.button(key="fp_sign_out").click().run()
    assert "Welcome back" in html(at)
    assert "fp_page" not in at.session_state
    assert "fp_reviewer_token" not in at.session_state
