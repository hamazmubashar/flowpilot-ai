"""`flowpilot-demo`: an isolated, resettable portfolio demo environment.

The demo always uses the fixed database `var/demo/flowpilot-demo.db`; no environment variable or
`.env` value can redirect it, and every command refuses to run if that path is the configured
(normal) database. `serve` starts the API on 127.0.0.1:8000 against the demo database with LLM
mode off, and marks every response with `X-FlowPilot-Environment: demo`. `run` checks that
marker before it writes anything, then submits five fixed synthetic scenarios.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from flowpilot import __version__
from flowpilot.api.app import DEMO_HEADER, create_app
from flowpilot.db import repository as repo
from flowpilot.db.connection import connect
from flowpilot.db.migrate import migrate
from flowpilot.evaluation import Report, RunRecord, format_report, report_dict, summarize
from flowpilot.llm.client import LLMClient, LLMError, OllamaClient
from flowpilot.llm.prompts import PROMPT_VERSION
from flowpilot.services.reviewers import add_reviewer
from flowpilot.settings import (
    LLMConfig,
    Settings,
    SettingsError,
    load_llm_config,
    load_settings,
    load_storage_paths,
)
from flowpilot.synthetic import generate_leads

DEMO_DB = Path("var/demo/flowpilot-demo.db")
DEMO_REVIEWER = "demo:reviewer"
DEMO_URL = "http://127.0.0.1:8000"  # n8n's api-gateway reaches the host API on port 8000
ACTOR = "demo:scenario-runner"


class DemoError(RuntimeError):
    """A demo safety check failed; nothing was changed."""


def _check_isolated(demo_db: Path, configured_db: Path) -> None:
    if demo_db.name != "flowpilot-demo.db" or demo_db.parent.name != "demo":
        raise DemoError(f"refusing to use {demo_db}: not the fixed demo database path")
    if demo_db.resolve() == configured_db.resolve():
        raise DemoError(
            f"refusing to use {demo_db}: it is the configured FLOWPILOT_DB_PATH, not an "
            "isolated demo database"
        )


def reset(*, yes: bool, configured_db: Path | None = None) -> str:
    """Delete and recreate only the demo database; returns the new demo reviewer's token."""
    if not yes:
        raise DemoError("reset deletes the demo database; re-run with --yes to confirm")
    _check_isolated(DEMO_DB, configured_db or load_storage_paths().db_path)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{DEMO_DB}{suffix}").unlink(missing_ok=True)
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(DEMO_DB)
    try:
        migrate(conn)
        return add_reviewer(conn, DEMO_REVIEWER)[1]
    finally:
        conn.close()


def demo_settings(base: Settings | None = None) -> Settings:
    """The API settings for the demo: demo database, LLM off, loopback port 8000, marked."""
    base = base or load_settings()
    _check_isolated(DEMO_DB, base.db_path)
    return replace(base, db_path=DEMO_DB, llm=LLMConfig(), host="127.0.0.1", port=8000, demo=True)


# --- scenarios -----------------------------------------------------------------------------------

_MESSAGE = "We re-key orders from email into three systems and want to automate it."
_HOT = {
    "company_name": "Harbor Crest Logistics",
    "contact_name": "Morgan Reyes",
    "contact_email": "morgan.reyes@harborcrest.demo.example",
    "company_size": "201-1000",
    "industry": "logistics",
    "service_interest": "process_automation",
    "budget_band": "over_100k",
    "timeline": "immediate",
    "source": "referral",
    "message": _MESSAGE,
}


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    payload: dict
    status: int  # expected HTTP status
    tier: str | None = None
    route: str | None = None
    score: int | None = None
    duplicate_of: str | None = None  # key of the scenario whose lead must be returned
    invalid_field: str | None = None


# Expected scores, tiers and routes come from the real rules (docs/scoring-rules.md); the tests
# assert them against `score_lead`, so a rule change fails loudly instead of drifting.
SCENARIOS = (
    Scenario("hot", "High-priority lead", _HOT, 201, "hot", "partner_review", 98),
    Scenario(
        "warm", "Mid-priority lead",
        {"company_name": "Bluefin Analytics", "contact_name": "Casey Lin",
         "contact_email": "casey.lin@bluefin.demo.example", "company_size": "11-50",
         "industry": "saas", "service_interest": "data_analytics", "budget_band": "10k_25k",
         "timeline": "3_6_months", "source": "web_form",
         "message": "We want one dashboard for sales data that now lives in five spreadsheets."},
        201, "warm", "sales_queue", 60,
    ),
    Scenario(
        "cold", "Low-priority lead",
        {"company_name": "Pebble Corner Bakery", "contact_name": "Jordan Ames",
         "contact_email": "jordan.ames@pebblecorner.demo.example", "company_size": "1-10",
         "industry": "retail_ecommerce", "service_interest": "other", "budget_band": "under_10k",
         "timeline": "exploring", "source": "event",
         "message": "Just curious what automation could do for a small bakery someday."},
        201, "cold", "nurture", 22,
    ),
    Scenario(
        "duplicate", "Same lead submitted again",
        {**_HOT, "company_name": "  harbor crest LOGISTICS ",
         "contact_email": "Morgan.Reyes@HarborCrest.demo.example"},
        200, duplicate_of="hot",
    ),
    Scenario(
        "invalid", "Invalid input (non-synthetic email)",
        {**_HOT, "contact_email": "morgan.reyes@gmail.com"},
        422, invalid_field="contact_email",
    ),
)  # fmt: skip


@dataclass(frozen=True)
class Outcome:
    scenario: Scenario
    status: int
    lead_id: int | None
    detail: str
    ok: bool


def _check(scenario: Scenario, response: httpx.Response, ids: dict[str, int]) -> Outcome:
    body = response.json()
    if response.status_code != scenario.status:
        return Outcome(scenario, response.status_code, None, "unexpected status", False)
    if scenario.invalid_field is not None:
        fields = [e.get("loc", [])[-1] for e in body.get("detail", []) if isinstance(e, dict)]
        ok = scenario.invalid_field in fields
        return Outcome(scenario, 422, None, f"rejected: {', '.join(map(str, fields))}", ok)
    lead = body["lead"]
    lead_id = int(lead["id"])
    if scenario.duplicate_of is not None:
        ok = body["duplicate"] is True and lead_id == ids.get(scenario.duplicate_of)
        return Outcome(scenario, 200, lead_id, f"duplicate of lead #{lead_id}", ok)
    score = lead["score"]
    ids[scenario.key] = lead_id
    ok = (score["tier"], score["route"], score["total"]) == (
        scenario.tier,
        scenario.route,
        scenario.score,
    )
    detail = f"{score['tier']} {score['total']} {score['route']}"
    return Outcome(scenario, 201, lead_id, detail, ok)


def run(
    api_key: str, *, url: str = DEMO_URL, transport: httpx.BaseTransport | None = None
) -> list[Outcome]:
    """Verify the demo marker, then submit every scenario. Nothing is written without it."""
    with httpx.Client(
        base_url=url, headers={"X-API-Key": api_key}, timeout=10.0, trust_env=False,
        follow_redirects=False, transport=transport,
    ) as client:  # fmt: skip
        try:
            marker = client.get("/health").headers.get(DEMO_HEADER)
        except httpx.HTTPError as exc:
            raise DemoError(
                f"no API answered at {url}; start it with `flowpilot-demo serve`"
            ) from exc
        if marker != "demo":
            raise DemoError(
                f"the API at {url} is not the demo server (missing {DEMO_HEADER}: demo); "
                "nothing was submitted. Stop it and start `flowpilot-demo serve`."
            )
        ids: dict[str, int] = {}
        outcomes = []
        for scenario in SCENARIOS:
            response = client.post(
                "/leads",
                json=scenario.payload,
                headers={"Idempotency-Key": f"demo-{scenario.key}", "X-Actor": ACTOR},
            )
            outcomes.append(_check(scenario, response, ids))
        return outcomes


def format_outcomes(outcomes: list[Outcome]) -> str:
    lines = []
    for o in outcomes:
        lead = f"lead #{o.lead_id}" if o.lead_id is not None else "no lead"
        mark = "OK " if o.ok else "MISMATCH"
        lines.append(f"{mark}  {o.scenario.title:<38} {o.status}  {lead:<9} {o.detail}")
    return "\n".join(lines)


# --- evaluation (M6): the only path that enables the model -------------------------------------

EVAL_SEED = 5150
EVAL_MAX_COUNT = 50


def eval_llm_config(base: LLMConfig | None = None) -> LLMConfig:
    """The configured model and timeout with mode forced to `ollama`; the URL is still validated
    as loopback by `LLMConfig`. Used only by `eval`; `serve` and `run` keep the model off."""
    base = base or load_llm_config()
    return LLMConfig(
        mode="ollama", ollama_url=base.ollama_url, model=base.model, timeout_s=base.timeout_s
    )


def preflight(client: LLMClient) -> None:
    """No inference: Ollama must answer `/api/tags` and list the configured model."""
    try:
        installed = client.list_models()  # type: ignore[attr-defined]
    except LLMError as exc:
        raise DemoError(f"Ollama is not reachable ({exc}); nothing was changed") from exc
    if client.model not in installed:
        raise DemoError(
            f"model {client.model!r} is not installed (installed: {', '.join(installed) or 'none'})"
            "; nothing was changed"
        )


def eval_leads(count: int) -> list[dict]:
    """The three valid demo scenarios plus `count` deterministic synthetic leads."""
    fixed = [s.payload for s in SCENARIOS if s.tier is not None]
    return fixed + generate_leads(count, EVAL_SEED)


def _assert_no_decisions() -> None:
    conn = connect(DEMO_DB)
    try:
        approvals = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        outbox = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    finally:
        conn.close()
    if approvals or outbox:
        raise DemoError(f"invariant broken: {approvals} approvals, {outbox} outbox rows")


def _exercise(api, api_key: str, leads: list[dict]) -> tuple[list[int], int]:
    """Intake, qualify and draft each lead in turn. Returns lead ids and layout repairs."""
    headers = {"X-API-Key": api_key, "X-Actor": "demo:evaluation"}
    ids, repairs = [], 0
    for i, payload in enumerate(leads):
        created = api.post(
            "/leads", json=payload, headers={**headers, "Idempotency-Key": f"eval-{i}"}
        )
        if created.status_code != 201:
            raise DemoError(f"lead {i}: intake returned {created.status_code}")
        lead_id = int(created.json()["lead"]["id"])
        score = created.json()["lead"]["score"]
        for step in ("qualification", "drafts"):
            response = api.post(
                f"/leads/{lead_id}/{step}",
                headers={**headers, "Idempotency-Key": f"eval-{i}-{step}"},
            )
            if response.status_code != 201:
                raise DemoError(f"lead {i}: {step} returned {response.status_code}")
        after = api.get(f"/leads/{lead_id}", headers=headers).json()["score"]
        if (after["total"], after["tier"], after["route"]) != (
            score["total"], score["tier"], score["route"],
        ):  # fmt: skip
            raise DemoError(f"lead {i}: the model changed the score; advisory invariant broken")
        audit = api.get(f"/leads/{lead_id}/audit", headers=headers).json()
        repairs += sum(
            1
            for e in audit
            if e["action"] == "lead.drafted" and (e.get("after") or {}).get("layout_normalized")
        )
        ids.append(lead_id)
    return ids, repairs


def _records(lead_ids: list[int]) -> tuple[list[RunRecord], int | None]:
    conn = connect(DEMO_DB)
    try:
        rows = [row for lead_id in lead_ids for row in repo.list_llm_runs(conn, lead_id)]
    finally:
        conn.close()
    rows.sort(key=lambda r: r["id"])
    first_model_call = next(
        (r["id"] for r in rows if r["fallback_reason"] != "injection_suspected"), None
    )
    records = [
        RunRecord(
            task=r["task"],
            outcome=r["outcome"],
            fallback_reason=r["fallback_reason"],
            error=r["error"],
            latency_ms=r["latency_ms"],
            warmup=r["id"] == first_model_call,
        )
        for r in rows
    ]
    warmup = next((r.latency_ms for r in records if r.warmup), None)
    return records, warmup


def evaluate(
    *,
    yes: bool,
    count: int = 10,
    settings: Settings | None = None,
    client: LLMClient | None = None,
) -> Report:
    """Measure the model on synthetic leads in the isolated demo database, in-process.

    Requests go to an app object built from demo settings, never over the network, so the
    normal API cannot receive them. Order: path checks, model preflight (no inference), then
    reset; a failed preflight leaves the existing demo data untouched.
    """
    if not yes:
        raise DemoError("eval resets the demo database; re-run with --yes to confirm")
    if not 1 <= count <= EVAL_MAX_COUNT:
        raise DemoError(f"--count must be between 1 and {EVAL_MAX_COUNT}")
    base = settings or load_settings()
    _check_isolated(DEMO_DB, base.db_path)
    owns_client = client is None
    if client is None:
        config = eval_llm_config()
        client = OllamaClient(
            base_url=config.ollama_url, model=config.model, timeout_s=config.timeout_s
        )
    leads = eval_leads(count)
    reset_done = False
    try:
        preflight(client)
        reset(yes=True, configured_db=base.db_path)
        reset_done = True
        app_settings = replace(
            base, db_path=DEMO_DB, llm=LLMConfig(), host="127.0.0.1", port=8000, demo=True
        )
        from fastapi.testclient import TestClient  # in-process: no socket, no server

        with TestClient(create_app(app_settings, llm=client)) as api:
            if api.get("/health").headers.get(DEMO_HEADER) != "demo":
                raise DemoError("in-process app is not marked as demo; nothing was submitted")
            lead_ids, repairs = _exercise(api, base.api_key, leads)
        records, warmup = _records(lead_ids)
    except Exception:
        if reset_done:
            _assert_no_decisions()  # a broken invariant is reported over the original error
        raise
    finally:
        if owns_client and isinstance(client, OllamaClient):
            client.close()
    _assert_no_decisions()
    meta = {
        "model": client.model,
        "prompt_version": PROMPT_VERSION,
        "flowpilot": __version__,
        "leads": len(leads),
        "seed": EVAL_SEED,
    }
    notes = (
        "synthetic leads only; demo database only; approvals and outbox verified empty",
        "results describe this machine, model and Ollama version only",
        "the demo database was reset, so demo:reviewer has a new unprinted token; see the "
        "runbook's 'Synthetic LLM evaluation' for getting a console sign-in token",
    )
    return Report(meta, summarize(records, repairs), warmup, notes)


# --- CLI -----------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flowpilot-demo", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    reset_cmd = sub.add_parser("reset", help="delete and recreate the demo database")
    reset_cmd.add_argument("--yes", action="store_true", help="confirm the reset")
    sub.add_parser("serve", help="serve the API on 127.0.0.1:8000 against the demo database")
    sub.add_parser("run", help="submit the five demo scenarios to the demo API")
    eval_cmd = sub.add_parser("eval", help="measure the local model on synthetic demo leads")
    eval_cmd.add_argument("--yes", action="store_true", help="confirm resetting the demo database")
    eval_cmd.add_argument(
        "--count", type=int, default=10, help="generated leads (plus 3 scenarios)"
    )
    eval_cmd.add_argument(
        "--json", type=Path, help="also write the report as JSON to this new file"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "reset":
            token = reset(yes=args.yes)
            print(f"demo database reset: {DEMO_DB}")
            print(f"demo reviewer {DEMO_REVIEWER} token (shown once, sign in with it): {token}")
            return 0
        if args.command == "serve":
            settings = demo_settings()
            if not DEMO_DB.is_file():
                raise DemoError(f"{DEMO_DB} does not exist; run `flowpilot-demo reset --yes`")
            from flowpilot.api.__main__ import serve

            print(f"flowpilot-demo: serving {DEMO_DB} on {DEMO_URL} (LLM off)", file=sys.stderr)
            return serve(settings, prog="flowpilot-demo")
        if args.command == "eval":
            if args.json is not None and args.json.exists():
                raise DemoError(f"refusing to overwrite {args.json}")
            report = evaluate(yes=args.yes, count=args.count)
            print(format_report(report))
            if args.json is not None:
                args.json.write_text(json.dumps(report_dict(report), indent=2) + "\n")
            return 0
        outcomes = run(load_settings().api_key)
        print(format_outcomes(outcomes))
        return 0 if all(o.ok for o in outcomes) else 1
    except (DemoError, SettingsError) as exc:
        print(f"flowpilot-demo: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
