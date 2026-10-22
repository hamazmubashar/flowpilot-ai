"""Portfolio demo environment: isolation, the demo marker, and deterministic scenarios.

Every test redirects the fixed demo path into pytest's tmp_path, and uses a stand-in "normal"
database whose bytes must never change.
"""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from flowpilot import demo
from flowpilot.api.app import DEMO_HEADER, create_app
from flowpilot.db.connection import connect
from flowpilot.db.migrate import current_version, migrate
from flowpilot.domain.models import LeadIn
from flowpilot.domain.normalize import email_domain, is_synthetic_domain
from flowpilot.domain.scoring import score_lead
from flowpilot.settings import LLMConfig, Settings
from tests.conftest import API_KEY


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


def counts(db: Path) -> dict[str, int]:
    conn = connect(db)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("leads", "reviewers", "drafts", "approvals", "outbox", "audit_log")
        } | {"schema": current_version(conn)}
    finally:
        conn.close()


def base_settings(normal: Path, **changes) -> Settings:
    return Settings(
        api_key=API_KEY, db_path=normal, backup_dir=normal.parent / "backups", **changes
    )


def demo_run(paths: dict[str, Path]) -> list[tuple]:
    settings = demo.demo_settings(base_settings(paths["normal"]))
    with TestClient(create_app(settings)) as client:
        outcomes = demo.run(API_KEY, transport=client._transport)
        trails = {
            o.lead_id: [e["action"] for e in client.get(
                f"/leads/{o.lead_id}/audit", headers={"X-API-Key": API_KEY}).json()]
            for o in outcomes if o.lead_id is not None
        }  # fmt: skip
    return [(o.scenario.key, o.status, o.lead_id, o.detail, o.ok) for o in outcomes] + [trails]


# --- isolation -----------------------------------------------------------------------------------


def test_reset_requires_confirmation(paths: dict[str, Path]) -> None:
    with pytest.raises(demo.DemoError, match="--yes"):
        demo.reset(yes=False, configured_db=paths["normal"])
    assert not paths["demo"].exists()


def test_reset_refuses_the_configured_database(
    paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = paths["normal"].read_bytes()
    with pytest.raises(demo.DemoError, match="configured FLOWPILOT_DB_PATH"):
        demo.reset(yes=True, configured_db=paths["demo"])  # demo path == configured path
    impostor = paths["normal"]  # a demo path that is really the normal database
    monkeypatch.setattr(demo, "DEMO_DB", impostor)
    with pytest.raises(demo.DemoError, match="not the fixed demo database path"):
        demo.reset(yes=True, configured_db=paths["normal"])
    assert paths["normal"].read_bytes() == before


def test_reset_creates_a_fresh_demo_database_every_time(paths: dict[str, Path]) -> None:
    before = paths["normal"].read_bytes()
    first = demo.reset(yes=True, configured_db=paths["normal"])
    expected = {"leads": 0, "reviewers": 1, "drafts": 0, "approvals": 0, "outbox": 0}
    assert {k: v for k, v in counts(paths["demo"]).items() if k in expected} == expected
    demo_run(paths)  # leaves leads behind
    second = demo.reset(yes=True, configured_db=paths["normal"])
    assert counts(paths["demo"])["leads"] == 0
    assert counts(paths["demo"])["reviewers"] == 1
    assert first != second
    assert first.startswith("fpr_")
    assert paths["normal"].read_bytes() == before


def test_demo_settings_ignore_environment_choices(paths: dict[str, Path]) -> None:
    base = base_settings(paths["normal"], host="localhost", port=9001, llm=LLMConfig(mode="ollama"))
    settings = demo.demo_settings(base)
    assert settings.db_path == paths["demo"]
    assert settings.llm.mode == "off"
    assert (settings.host, settings.port, settings.demo) == ("127.0.0.1", 8000, True)
    assert settings.api_key == API_KEY
    with pytest.raises(demo.DemoError):
        demo.demo_settings(base_settings(paths["demo"]))  # configured path is the demo path


def test_marker_header_only_in_demo_mode(paths: dict[str, Path]) -> None:
    demo.reset(yes=True, configured_db=paths["normal"])
    with TestClient(create_app(base_settings(paths["normal"]))) as normal:
        assert DEMO_HEADER not in normal.get("/health").headers
    demo_settings = demo.demo_settings(base_settings(paths["normal"]))
    with TestClient(create_app(demo_settings)) as served:
        assert served.get("/health").headers[DEMO_HEADER] == "demo"
        assert served.get("/leads", headers={"X-API-Key": API_KEY}).headers[DEMO_HEADER] == "demo"


def test_run_refuses_without_the_marker_and_writes_nothing(paths: dict[str, Path]) -> None:
    seen: list[str] = []

    def not_demo(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={"status": "ok", "database": "ok"})

    with pytest.raises(demo.DemoError, match="not the demo server"):
        demo.run(API_KEY, transport=httpx.MockTransport(not_demo))
    assert seen == ["GET"]

    before = paths["normal"].read_bytes()
    unmarked = create_app(base_settings(paths["normal"]))  # a real API without the marker
    with TestClient(unmarked) as normal, pytest.raises(demo.DemoError):
        demo.run(API_KEY, transport=normal._transport)
    assert counts(paths["normal"])["leads"] == 0
    assert paths["normal"].read_bytes() == before


def test_run_reports_a_missing_api() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(demo.DemoError, match="flowpilot-demo serve"):
        demo.run(API_KEY, transport=httpx.MockTransport(refuse))


# --- scenarios -----------------------------------------------------------------------------------


def test_scenario_expectations_match_the_real_rules() -> None:
    keys = [s.key for s in demo.SCENARIOS]
    assert keys == ["hot", "warm", "cold", "duplicate", "invalid"]
    for s in demo.SCENARIOS:
        if s.tier is None:
            continue
        score = score_lead(LeadIn(**s.payload))
        assert (score.total, score.tier, score.route) == (s.score, s.tier, s.route), s.key


def test_scenarios_are_synthetic() -> None:
    for s in demo.SCENARIOS:
        domain = email_domain(s.payload["contact_email"].strip().lower())
        if s.key == "invalid":
            assert not is_synthetic_domain(domain)  # deliberately rejected by the API
            with pytest.raises(ValueError, match="synthetic"):
                LeadIn(**s.payload)
        else:
            assert domain.endswith(".demo.example")


def test_scenarios_are_deterministic_across_resets(paths: dict[str, Path]) -> None:
    before = paths["normal"].read_bytes()
    demo.reset(yes=True, configured_db=paths["normal"])
    first = demo_run(paths)
    demo.reset(yes=True, configured_db=paths["normal"])
    second = demo_run(paths)
    assert first == second
    assert first[:-1] == [
        ("hot", 201, 1, "hot 98 partner_review", True),
        ("warm", 201, 2, "warm 60 sales_queue", True),
        ("cold", 201, 3, "cold 22 nurture", True),
        ("duplicate", 200, 1, "duplicate of lead #1", True),
        ("invalid", 422, None, "rejected: contact_email", True),
    ]
    assert first[-1][1] == ["lead.created", "lead.scored", "lead.duplicate_submission"]
    assert counts(paths["demo"])["leads"] == 3
    assert paths["normal"].read_bytes() == before


def test_running_twice_without_reset_creates_nothing_new(paths: dict[str, Path]) -> None:
    demo.reset(yes=True, configured_db=paths["normal"])
    demo_run(paths)
    again = demo_run(paths)
    assert all(row[-1] for row in again[:-1])  # every scenario still matches
    assert counts(paths["demo"])["leads"] == 3


def test_format_outcomes(paths: dict[str, Path]) -> None:
    demo.reset(yes=True, configured_db=paths["normal"])
    settings = demo.demo_settings(base_settings(paths["normal"]))
    with TestClient(create_app(settings)) as client:
        text = demo.format_outcomes(demo.run(API_KEY, transport=client._transport))
    assert text.count("OK ") == 5
    assert "MISMATCH" not in text


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_reset(paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    assert demo.main(["reset"]) == 2
    assert "--yes" in capsys.readouterr().err
    monkeypatch.setattr(
        demo, "load_storage_paths", lambda: type("P", (), {"db_path": paths["normal"]})()
    )
    assert demo.main(["reset", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "demo reviewer demo:reviewer token" in out
    assert paths["demo"].is_file()
