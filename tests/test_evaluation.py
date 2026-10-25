"""M6 synthetic LLM evaluation: aggregation, safety ordering, isolation and privacy.

A scripted fake stands in for Ollama; no test runs real inference or opens a network socket.
"""

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpilot import demo
from flowpilot.api.app import create_app
from flowpilot.db.connection import connect
from flowpilot.db.migrate import migrate
from flowpilot.domain.normalize import email_domain, is_synthetic_domain
from flowpilot.evaluation import RunRecord, guard_names, percentile, report_dict, summarize
from flowpilot.llm.client import LLMTimeout, LLMUnavailable
from flowpilot.settings import LLMConfig, Settings
from tests.conftest import API_KEY
from tests.fakes import FakeLLMClient
from tests.test_assist_api import ASSESSMENT_JSON, DRAFT_JSON
from tests.test_llm_guards import CLEAN_BODY

GUARD_BAD_DRAFT = json.dumps(
    {"subject": "Re: your enquiry", "body": f"{CLEAN_BODY}\n\nDetails: https://example.com/x"}
)


class FakeOllama(FakeLLMClient):
    def __init__(self, *responses, installed: list[str] | Exception | None = None) -> None:
        super().__init__(*responses)
        self.installed = [self.model] if installed is None else installed
        self.listed = 0

    def list_models(self) -> list[str]:
        self.listed += 1
        if isinstance(self.installed, Exception):
            raise self.installed
        return self.installed


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    demo_db = tmp_path / "var" / "demo" / "flowpilot-demo.db"
    normal_db = tmp_path / "var" / "flowpilot.db"
    normal_db.parent.mkdir(parents=True)
    conn = connect(normal_db)
    try:
        migrate(conn)
    finally:
        conn.close()
    monkeypatch.setattr(demo, "DEMO_DB", demo_db)
    return {"demo": demo_db, "normal": normal_db}


def settings_for(normal: Path) -> Settings:
    return Settings(api_key=API_KEY, db_path=normal, backup_dir=normal.parent / "backups")


def count(db: Path, table: str) -> int:
    conn = connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


def scripted() -> FakeOllama:
    """count=2 gives 5 leads, 2 model calls each, with a known mix of outcomes."""
    return FakeOllama(
        ASSESSMENT_JSON, DRAFT_JSON,  # lead 1: both from the model
        ASSESSMENT_JSON, GUARD_BAD_DRAFT,  # lead 2: draft rejected by a guard
        LLMTimeout("slow"), "not json",  # lead 3: timeout, invalid output
        ASSESSMENT_JSON, DRAFT_JSON,  # lead 4
        ASSESSMENT_JSON, DRAFT_JSON,  # lead 5
    )  # fmt: skip


# --- aggregation --------------------------------------------------------------------------------


def test_summarize_rates_reasons_guards_and_latency() -> None:
    records = [
        RunRecord("qualification", "ok", None, None, 9000, warmup=True),
        RunRecord("qualification", "ok", None, None, 100),
        RunRecord("qualification", "fallback", "timeout", "slow", 300),
        RunRecord("draft", "ok", None, None, 200),
        RunRecord("draft", "fallback", "guard_rejected", "url,currency", 50),
        RunRecord("draft", "fallback", "guard_rejected", "url", 60),
        RunRecord("draft", "fallback", "injection_suspected", "ignore_instructions", None),
    ]
    quali, draft = summarize(records, layout_repairs=1)
    assert (quali.calls, quali.ok, quali.fallbacks) == (3, 2, {"timeout": 1})
    assert quali.latency_ok.count == 1  # the warm-up call is excluded
    assert quali.latency_all.max == 300
    assert (draft.calls, draft.ok) == (4, 1)
    assert draft.fallbacks == {"guard_rejected": 2, "injection_suspected": 1}
    assert draft.guard_failures == {"url": 2, "currency": 1}
    assert draft.layout_repairs == 1
    assert round(draft.success_rate, 2) == 0.25


def test_percentile_and_guard_names() -> None:
    assert percentile([5, 1, 3, 2, 4], 0.5) == 3
    assert percentile([5, 1, 3, 2, 4], 0.95) == 5
    assert percentile([7], 0.95) == 7
    assert guard_names("url, currency,") == ["url", "currency"]
    assert guard_names(None) == []


def test_eval_leads_are_deterministic_and_synthetic() -> None:
    leads = demo.eval_leads(4)
    assert leads == demo.eval_leads(4)
    assert len(leads) == 7
    assert [lead["company_name"] for lead in leads[:3]] == [
        s.payload["company_name"] for s in demo.SCENARIOS[:3]
    ]
    for lead in leads:
        domain = email_domain(lead["contact_email"].lower())
        assert is_synthetic_domain(domain)
        assert domain.endswith(".example")


def test_eval_config_enables_the_model_only_for_eval(paths: dict[str, Path]) -> None:
    configured = LLMConfig(mode="off", model="qwen2.5:3b", timeout_s=30)
    forced = demo.eval_llm_config(configured)
    assert (forced.mode, forced.model, forced.timeout_s) == ("ollama", "qwen2.5:3b", 30)
    assert forced.ollama_url == configured.ollama_url  # still loopback-validated
    assert demo.demo_settings(settings_for(paths["normal"])).llm.mode == "off"  # serve and run


# --- end to end with the fake model -------------------------------------------------------------


def test_eval_measures_the_scripted_outcomes(paths: dict[str, Path]) -> None:
    before = paths["normal"].read_bytes()
    fake = scripted()
    report = demo.evaluate(yes=True, count=2, settings=settings_for(paths["normal"]), client=fake)
    quali, draft = report.tasks
    assert (quali.calls, quali.ok, quali.fallbacks) == (5, 4, {"timeout": 1})
    assert (draft.calls, draft.ok) == (5, 3)
    assert draft.fallbacks == {"guard_rejected": 1, "invalid_output": 1}
    assert "url" in draft.guard_failures
    assert len(fake.calls) == 10
    assert fake.listed == 1
    assert report.meta["leads"] == 5
    assert report.warmup_ms is not None
    assert any("new unprinted token" in note for note in report.notes)
    assert count(paths["demo"], "leads") == 5
    assert count(paths["demo"], "approvals") == count(paths["demo"], "outbox") == 0
    assert paths["normal"].read_bytes() == before


def test_eval_runs_in_process_without_any_socket(
    paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(self: socket.socket, address: object) -> None:
        raise AssertionError(f"evaluation opened a socket to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    demo.evaluate(yes=True, count=2, settings=settings_for(paths["normal"]), client=scripted())


def test_report_contains_no_lead_data_or_secrets(paths: dict[str, Path]) -> None:
    report = demo.evaluate(
        yes=True, count=2, settings=settings_for(paths["normal"]), client=scripted()
    )
    text = json.dumps(report_dict(report)) + demo.format_report(report)
    assert "@" not in text  # no emails
    assert API_KEY not in text
    assert "order intake" not in text  # no raw model output
    for lead in demo.eval_leads(2):
        assert lead["message"][:30] not in text
        assert lead["contact_name"] not in text


# --- safety ordering and failure paths ----------------------------------------------------------


@pytest.mark.parametrize(
    "installed", [["some-other-model"], [], LLMUnavailable("connection refused")]
)
def test_failed_preflight_keeps_existing_demo_data(
    paths: dict[str, Path], installed: object
) -> None:
    demo.reset(yes=True, configured_db=paths["normal"])
    settings = demo.demo_settings(settings_for(paths["normal"]))
    with TestClient(create_app(settings)) as client:
        demo.run(API_KEY, transport=client._transport)  # existing demo data: 3 leads
    before = paths["demo"].read_bytes()
    fake = FakeOllama(installed=installed)
    with pytest.raises(demo.DemoError, match="nothing was changed"):
        demo.evaluate(yes=True, count=2, settings=settings_for(paths["normal"]), client=fake)
    assert paths["demo"].read_bytes() == before
    assert fake.calls == []


def test_refusals_happen_before_anything_else(paths: dict[str, Path]) -> None:
    fake = FakeOllama()
    with pytest.raises(demo.DemoError, match="--yes"):
        demo.evaluate(yes=False, settings=settings_for(paths["normal"]), client=fake)
    with pytest.raises(demo.DemoError, match="--count"):
        demo.evaluate(yes=True, count=0, settings=settings_for(paths["normal"]), client=fake)
    with pytest.raises(demo.DemoError, match="configured FLOWPILOT_DB_PATH"):
        demo.evaluate(yes=True, settings=settings_for(paths["demo"]), client=fake)
    assert fake.listed == 0
    assert not paths["demo"].exists()


def test_errors_mid_run_still_verify_the_invariants(paths: dict[str, Path]) -> None:
    def explode() -> str:
        raise RuntimeError("model client crashed")

    fake = FakeOllama(ASSESSMENT_JSON, explode)
    with pytest.raises(RuntimeError, match="crashed"):
        demo.evaluate(yes=True, count=2, settings=settings_for(paths["normal"]), client=fake)
    assert count(paths["demo"], "approvals") == count(paths["demo"], "outbox") == 0


def test_invariant_check_detects_a_decision(paths: dict[str, Path]) -> None:
    token = demo.reset(yes=True, configured_db=paths["normal"])
    settings = demo.demo_settings(settings_for(paths["normal"]))
    auth = {"X-API-Key": API_KEY}
    with TestClient(create_app(settings)) as client:
        demo.run(API_KEY, transport=client._transport)
        assert client.post("/leads/1/drafts", headers=auth).status_code == 201
        approved = client.post(
            "/leads/1/drafts/1/approval",
            json={"decision": "approved"},
            headers={**auth, "X-Reviewer-Token": token},
        )
        assert approved.status_code == 201
    with pytest.raises(demo.DemoError, match="invariant broken: 1 approvals, 1 outbox"):
        demo._assert_no_decisions()


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_eval(paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    assert demo.main(["eval"]) == 2
    assert "--yes" in capsys.readouterr().err

    existing = tmp_path / "report.json"
    existing.write_text("{}")
    assert demo.main(["eval", "--yes", "--json", str(existing)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err

    real_evaluate = demo.evaluate

    def fake_evaluate(*, yes: bool, count: int):
        return real_evaluate(
            yes=yes, count=count, settings=settings_for(paths["normal"]), client=scripted()
        )

    monkeypatch.setattr(demo, "evaluate", fake_evaluate)
    out_file = tmp_path / "new.json"
    assert demo.main(["eval", "--yes", "--count", "2", "--json", str(out_file)]) == 0
    assert "qualification: 4/5 from the model" in capsys.readouterr().out
    assert json.loads(out_file.read_text())["meta"]["leads"] == 5
