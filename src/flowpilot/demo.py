"""`flowpilot-demo`: an isolated, resettable portfolio demo environment.

The demo always uses the fixed database `var/demo/flowpilot-demo.db`; no environment variable or
`.env` value can redirect it, and every command refuses to run if that path is the configured
(normal) database. `serve` starts the API on 127.0.0.1:8000 against the demo database with LLM
mode off, and marks every response with `X-FlowPilot-Environment: demo`. `run` checks that
marker before it writes anything, then submits five fixed synthetic scenarios.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from flowpilot.api.app import DEMO_HEADER
from flowpilot.db.connection import connect
from flowpilot.db.migrate import migrate
from flowpilot.services.reviewers import add_reviewer
from flowpilot.settings import (
    LLMConfig,
    Settings,
    SettingsError,
    load_settings,
    load_storage_paths,
)

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


# --- CLI -----------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flowpilot-demo", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    reset_cmd = sub.add_parser("reset", help="delete and recreate the demo database")
    reset_cmd.add_argument("--yes", action="store_true", help="confirm the reset")
    sub.add_parser("serve", help="serve the API on 127.0.0.1:8000 against the demo database")
    sub.add_parser("run", help="submit the five demo scenarios to the demo API")
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
        outcomes = run(load_settings().api_key)
        print(format_outcomes(outcomes))
        return 0 if all(o.ok for o in outcomes) else 1
    except (DemoError, SettingsError) as exc:
        print(f"flowpilot-demo: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
