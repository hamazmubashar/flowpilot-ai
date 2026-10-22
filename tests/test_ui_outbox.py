"""M5 Outbox (Simulated): the read-only client against the real API, and the rendered page."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from flowpilot.ui import api
from flowpilot.ui.api import (
    ApiOutcome,
    LeadPageResult,
    LeadsSnapshot,
    LoginOutcome,
    LoginResult,
    ReviewerIdentity,
    UIConfig,
    fetch_outbox,
)
from tests.conftest import API_KEY
from tests.test_review_api import decide, drafted_lead, make_reviewer

CONFIG = UIConfig(api_url="http://127.0.0.1:8000", api_key=API_KEY)
APP = str(Path(__file__).resolve().parent.parent / "src" / "flowpilot" / "ui" / "app.py")
TOKEN = "fpr_" + "0utb0x" * 7


def entry(entry_id: int, status: str = "pending", **extra) -> dict:
    return {
        "id": entry_id, "lead_id": entry_id, "contact_name": f"Person {entry_id}",
        "company_name": f"Company {entry_id}", "draft_id": entry_id, "draft_version": 1,
        "approval_id": entry_id, "decided_by": "reviewer:avery",
        "decided_at": "2026-10-22T10:00:00+00:00", "channel": "simulated",
        "recipient": f"p{entry_id}@company{entry_id}.example",
        "subject": f"Re: your enquiry {entry_id}",
        "body": f"Hello <script>alert({entry_id})</script>,\n\nThanks for reaching out.",
        "status": status, "created_at": "2026-10-22T10:00:00+00:00", "sent_at": None,
        **extra,
    }  # fmt: skip


# --- client -------------------------------------------------------------------------------------


def test_fetch_outbox_against_the_real_api(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path
) -> None:
    t = client._transport
    empty = fetch_outbox(CONFIG, transport=t)
    assert (empty.outcome, empty.items, empty.total) == (ApiOutcome.OK, (), 0)

    headers = {**auth, "X-Reviewer-Token": make_reviewer(db_path)}
    lead_id = drafted_lead(client, auth, lead_payload)
    assert decide(client, lead_id, 1, headers).status_code == 201

    page = fetch_outbox(CONFIG, transport=t)
    assert page.total == 1
    assert page.items[0]["lead_id"] == lead_id
    assert page.items[0]["channel"] == "simulated"
    assert fetch_outbox(CONFIG, status="sent", transport=t).total == 0
    wrong = UIConfig(api_url=CONFIG.api_url, api_key=API_KEY + "x")
    assert fetch_outbox(wrong, transport=t).outcome is ApiOutcome.API_KEY_REJECTED


@pytest.mark.parametrize(
    "body",
    [
        {"items": [entry(1, channel="email")], "total": 1},  # never trusted
        {"items": [{"id": 1}], "total": 1},
        {"items": "nope", "total": 1},
        {"total": 1},
    ],
)
def test_fetch_outbox_rejects_unexpected_responses(body: dict) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    assert fetch_outbox(CONFIG, transport=transport).outcome is ApiOutcome.UNEXPECTED


def test_api_started_before_the_outbox_endpoint_is_reported_as_such(
    client: TestClient,
) -> None:
    """Regression: an API process started before `GET /outbox` existed answered FastAPI's
    default 404, which the page showed as a generic "unexpected response"."""
    from fastapi import FastAPI

    with TestClient(FastAPI()) as older_api:  # no /outbox route, like the stale process
        response = older_api.get("/outbox")
        assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
        result = fetch_outbox(CONFIG, transport=older_api._transport)
    assert result.outcome is ApiOutcome.ENDPOINT_MISSING
    assert fetch_outbox(CONFIG, transport=client._transport).outcome is ApiOutcome.OK  # current


def test_fetch_outbox_failures_and_params() -> None:
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [], "total": 0})

    fetch_outbox(CONFIG, status="pending", offset=25, transport=httpx.MockTransport(ok))
    assert dict(seen[0].url.params) == {"status": "pending", "limit": "25", "offset": "25"}
    assert seen[0].url.path == "/outbox"
    assert seen[0].method == "GET"
    assert "X-Reviewer-Token" not in seen[0].headers

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert fetch_outbox(CONFIG, transport=httpx.MockTransport(refuse)).outcome is (
        ApiOutcome.API_UNAVAILABLE
    )
    down = httpx.MockTransport(lambda r: httpx.Response(503, json={}))
    assert fetch_outbox(CONFIG, transport=down).outcome is ApiOutcome.API_UNAVAILABLE


# --- rendered page -------------------------------------------------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stands in for the API: never reads `.env` or calls a server."""
    state: dict = {
        "verify": LoginResult(LoginOutcome.OK, ReviewerIdentity(7, "reviewer:avery")),
        "entries": [entry(i) for i in range(30, 0, -1)],  # newest first, like the API
        "outcome": ApiOutcome.OK,
        "calls": [],
    }
    monkeypatch.setattr(api, "load_ui_config", lambda: CONFIG)
    monkeypatch.setattr(api, "verify_reviewer", lambda c, t: state["verify"])
    monkeypatch.setattr(api, "fetch_leads", lambda c: LeadsSnapshot(ApiOutcome.OK, (), 0))

    def outbox(config, *, status=None, offset=0, limit=25):
        state["calls"].append({"status": status, "offset": offset, "limit": limit})
        if state["outcome"] is not ApiOutcome.OK:
            return LeadPageResult(state["outcome"])
        items = [e for e in state["entries"] if status is None or e["status"] == status]
        return LeadPageResult(ApiOutcome.OK, tuple(items[offset : offset + limit]), len(items))

    monkeypatch.setattr(api, "fetch_outbox", outbox)
    return state


def html(at) -> str:
    return "\n".join(e.proto.body for e in at.get("html"))


def outbox_page():
    app_test = pytest.importorskip("streamlit.testing.v1").AppTest  # needs the `ui` group
    at = app_test.from_file(APP)
    at.session_state["fp_reviewer"] = {"id": 7, "name": "reviewer:avery"}
    at.session_state["fp_reviewer_token"] = TOKEN
    at.run()
    return at.button(key="fp_nav_outbox").click().run()


def test_entries_and_detail_come_from_the_api(fake: dict) -> None:
    at = outbox_page()
    assert not at.exception
    page = html(at)
    assert "<h1>Outbox (Simulated)</h1>" in page
    assert "Nothing is actually sent" in page
    assert "Nothing here is actually sent" in page
    assert "<strong>30</strong><span>Simulated entries</span>" in page
    assert "<strong>30</strong><span>Pending</span>" in page
    assert len(at.radio(key="fp_ob_sel").options) == 25
    assert "Entry #30" in page  # newest first, selected by default
    assert "p30@company30.example" in page
    assert "Simulated delivery, not sent" in page
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "Showing 1\N{EN DASH}25 of 30 entries" in page
    assert at.button(key="fp_nav_outbox").proto.type == "primary"
    assert TOKEN not in page
    assert API_KEY not in page


def test_no_sending_or_editing_controls(fake: dict) -> None:
    at = outbox_page()
    labels = " ".join(b.label.lower() for b in at.button)
    for word in ("send", "retry", "resend", "edit", "delete"):
        assert word not in labels


def test_selecting_an_entry_shows_its_message(fake: dict) -> None:
    at = outbox_page()
    at.radio(key="fp_ob_sel").set_value(28).run()
    page = html(at)
    assert "Entry #28" in page
    assert "Re: your enquiry 28" in page


def test_status_filter_and_paging_are_server_side(fake: dict) -> None:
    at = outbox_page()
    at.button(key="fp_ob_next").click().run()
    assert fake["calls"][-1]["offset"] == 25
    assert "Entry #5" in html(at)
    assert at.button(key="fp_ob_next").disabled
    at.selectbox(key="fp_ob_status").set_value("sent").run()
    assert fake["calls"][-1] == {"status": "sent", "offset": 0, "limit": 25}
    assert "No entries with this status" in html(at)


def test_empty_outbox(fake: dict) -> None:
    fake["entries"] = []
    page = html(outbox_page())
    assert "No outbox entries" in page
    assert "after a reviewer approves a draft" in page


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (ApiOutcome.API_UNAVAILABLE, "not reachable"),
        (ApiOutcome.API_KEY_REJECTED, "not authorised"),
        (ApiOutcome.UNEXPECTED, "unexpected response"),
        (ApiOutcome.ENDPOINT_MISSING, "Restart it (uv run flowpilot-api)"),
    ],
)
def test_api_errors(fake: dict, outcome: ApiOutcome, message: str) -> None:
    fake["outcome"] = outcome
    at = outbox_page()
    assert message in html(at)
    assert "Entry #" not in html(at)
    assert at.button(key="fp_retry")


def test_revoked_session_shows_no_entries(fake: dict) -> None:
    at = outbox_page()
    fake["verify"] = LoginResult(LoginOutcome.INVALID_TOKEN)
    calls = len(fake["calls"])
    at.run()
    assert "Welcome back" in html(at)
    assert len(fake["calls"]) == calls
    assert "fp_reviewer_token" not in at.session_state
