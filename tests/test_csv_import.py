"""CSV import through n8n (M7 C): file checks, n8n configuration and per-row outcomes."""

from __future__ import annotations

import json

import httpx
import pytest

from flowpilot.ui import csv_import as ci

TOKEN = "t" * 32
HEADER = ",".join(ci.COLUMNS)
ROW = (
    "Harbor Crest,Morgan Reyes,morgan@harbor.demo.example,51-200,logistics,"
    "process_automation,50k_100k,1_3_months,Automate order intake please,referral"
)


def config(**kw) -> ci.N8nConfig:
    return ci.N8nConfig(**{"url": "http://127.0.0.1:5678", "token": TOKEN, **kw})


# --- parsing -------------------------------------------------------------------------------------


def test_template_is_a_valid_one_row_file() -> None:
    parsed = ci.parse_csv(ci.TEMPLATE_CSV.encode())
    assert parsed.problems == ()
    assert len(parsed.rows) == 1
    assert parsed.rows[0]["message"].startswith("We need to automate")


def test_rows_are_trimmed_blank_optional_cells_dropped_and_bom_accepted() -> None:
    data = ("﻿" + HEADER + "\n" + ROW.replace(",referral", ",") + "\n\n").encode()
    parsed = ci.parse_csv(data)
    assert parsed.problems == ()
    assert "source" not in parsed.rows[0]
    assert len(parsed.digest) == 16


def test_formulas_stay_inert_text() -> None:
    parsed = ci.parse_csv((HEADER + "\n" + ROW.replace("Harbor Crest", "=1+1")).encode())
    assert parsed.rows[0]["company_name"] == "=1+1"


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        (b"", "empty"),
        (b"\xff\xfe\x00bad", "not UTF-8"),
        (HEADER.encode(), "no rows"),
        (b"company_name,contact_name\nA,B", "Missing columns"),
        ((HEADER + ",extra\n" + ROW + ",x").encode(), "Unknown columns"),
        ((HEADER + ",message\n" + ROW + ",x").encode(), "Repeated columns"),
        ((HEADER + "\n" + ROW + ",surplus").encode(), "more cells"),
        ((HEADER + "\n" + (ROW + "\n") * (ci.MAX_ROWS + 1)).encode(), "limit"),
        (b"x" * (ci.MAX_FILE_BYTES + 1), "larger"),
    ],
)
def test_bad_files_are_refused_with_a_reason(data: bytes, fragment: str) -> None:
    parsed = ci.parse_csv(data)
    assert parsed.rows == ()
    assert any(fragment in p for p in parsed.problems)


def test_overlong_cells_are_refused() -> None:
    parsed = ci.parse_csv((HEADER + "\n" + ROW.replace("Automate", "x" * 5001)).encode())
    assert "too long" in parsed.problems[0]


# --- configuration -------------------------------------------------------------------------------


def test_import_is_off_without_a_url() -> None:
    assert ci.load_n8n_config({}, env_file=None) is None


def test_config_is_read_and_the_token_hidden() -> None:
    env = {"FLOWPILOT_N8N_URL": "http://127.0.0.1:5678/", "FLOWPILOT_N8N_WEBHOOK_TOKEN": TOKEN}
    cfg = ci.load_n8n_config(env, env_file=None)
    assert cfg is not None
    assert cfg.url == "http://127.0.0.1:5678"
    assert TOKEN not in repr(cfg)


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1:5678", "http://n8n:5678", "http://evil.example:5678",
     "http://127.0.0.1:5678/webhook", "http://u:p@127.0.0.1:5678"],
)  # fmt: skip
def test_only_private_n8n_urls_are_accepted(url: str) -> None:
    with pytest.raises(ci.ImportConfigError):
        config(url=url)


def test_the_compose_service_name_is_allowed_only_in_the_public_demo() -> None:
    assert config(url="http://n8n:5678", public_demo=True).url == "http://n8n:5678"


def test_a_short_token_is_refused() -> None:
    with pytest.raises(ci.ImportConfigError):
        config(token="short")


# --- submitting ----------------------------------------------------------------------------------


def _transport(status: int, body: object, seen: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


ROWDICT = {"company_name": "Harbor Crest", "contact_email": "m@h.demo.example"}


def test_a_created_row_reports_its_lead_and_tier_and_sends_the_right_headers() -> None:
    seen: list[httpx.Request] = []
    body = {"result": "created", "lead_id": 7, "tier": "hot"}
    result = ci.submit_row(
        config(), ROWDICT, line=2, digest="abc", transport=_transport(201, body, seen)
    )
    assert (result.outcome, result.lead_id, result.tier) == ("created", 7, "hot")
    request = seen[0]
    assert request.url.path == ci.WEBHOOK_PATH
    assert request.headers["X-Webhook-Token"] == TOKEN
    assert request.headers["Idempotency-Key"] == "csv-abc-2"  # same file + row, same key
    assert json.loads(request.content) == ROWDICT


@pytest.mark.parametrize(
    ("status", "body", "outcome", "detail"),
    [
        (200, {"result": "duplicate", "lead_id": 3}, "duplicate", "Already exists"),
        (422, {"result": "invalid", "detail": [{"loc": ["body", "contact_email"]}]},
         "invalid", "Invalid: contact_email"),
        (422, {"result": "invalid"}, "invalid", "Rejected by FlowPilot"),
        (403, {}, "error", "webhook token"),
        (404, {}, "error", "not active"),
        (503, {"result": "error"}, "error", "HTTP 503"),
        (200, ["not", "a", "dict"], "error", "HTTP 200"),
    ],
)  # fmt: skip
def test_other_outcomes(status: int, body: object, outcome: str, detail: str) -> None:
    transport = _transport(status, body)
    result = ci.submit_row(config(), ROWDICT, line=3, digest="d", transport=transport)
    assert result.outcome == outcome
    assert detail in result.detail


def test_non_json_answers_and_network_errors_are_errors() -> None:
    text = httpx.MockTransport(lambda r: httpx.Response(500, text="oops"))
    assert ci.submit_row(config(), ROWDICT, line=2, digest="d", transport=text).outcome == "error"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    down = ci.submit_row(config(), ROWDICT, line=2, digest="d", transport=httpx.MockTransport(boom))
    assert (down.outcome, down.detail) == ("error", "n8n is not reachable")


def test_summary_counts_every_outcome() -> None:
    results = [ci.RowResult(2, "a", "created"), ci.RowResult(3, "b", "invalid")]
    assert ci.summarize(results) == {"created": 1, "duplicate": 0, "invalid": 1, "error": 0}
