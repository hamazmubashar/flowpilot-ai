"""Opt-in live checks against a local Ollama server. Never part of the default suite.

Run explicitly (performs local inference):
    FLOWPILOT_TEST_OLLAMA=1 uv run pytest -m ollama -s
Uses FLOWPILOT_OLLAMA_URL / FLOWPILOT_LLM_MODEL (defaults: http://127.0.0.1:11434, qwen2.5:3b).
"""

from __future__ import annotations

import json
import os
import time

import pytest

from flowpilot.llm import prompts
from flowpilot.llm.client import OllamaClient
from flowpilot.llm.guards import check_draft, check_summary
from flowpilot.llm.schemas import AssessmentOutput, DraftOutput, json_schema
from flowpilot.settings import load_llm_config

pytestmark = [
    pytest.mark.ollama,
    pytest.mark.skipif(
        os.environ.get("FLOWPILOT_TEST_OLLAMA") != "1",
        reason="live Ollama tests are opt-in: set FLOWPILOT_TEST_OLLAMA=1",
    ),
]

LEAD = json.loads(
    """{"company_name": "Glasswing Logistics", "contact_name": "Rowan Hollis",
    "company_size": "51-200", "industry": "logistics", "service_interest": "process_automation",
    "budget_band": "50k_100k", "timeline": "1_3_months",
    "message": "Our dispatch team re-keys every order from email into three systems."}"""
)


@pytest.fixture(scope="module")
def live() -> OllamaClient:
    cfg = load_llm_config()
    client = OllamaClient(base_url=cfg.ollama_url, model=cfg.model, timeout_s=cfg.timeout_s)
    yield client
    client.close()


def test_model_installed(live: OllamaClient) -> None:
    assert live.model in live.list_models()


def test_live_assessment_is_schema_valid(live: OllamaClient) -> None:
    started = time.monotonic()
    raw = live.chat_json(
        system=prompts.ASSESSMENT_SYSTEM,
        user=prompts.user_message(prompts.lead_data(LEAD)),
        schema=json_schema(AssessmentOutput),
    )
    print(f"\nassessment latency: {time.monotonic() - started:.1f}s\n{raw}")
    out = AssessmentOutput.model_validate_json(raw)
    print(f"summary guard violations: {check_summary(out.summary)}")


def test_live_draft_is_schema_valid(live: OllamaClient) -> None:
    started = time.monotonic()
    raw = live.chat_json(
        system=prompts.DRAFT_SYSTEM,
        user=prompts.user_message(prompts.lead_data(LEAD, route="partner_review")),
        schema=json_schema(DraftOutput),
    )
    print(f"\ndraft latency: {time.monotonic() - started:.1f}s\n{raw}")
    out = DraftOutput.model_validate_json(raw)
    print(f"draft guard violations: {check_draft(out.subject, out.body)}")
