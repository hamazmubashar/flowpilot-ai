"""Structured-output contracts for the model. Strict: unknown fields and values are rejected.

The JSON schema of each model is sent to Ollama as `format`, and the reply is validated again
here, so a server that ignores `format` still cannot smuggle unexpected data through.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from flowpilot.domain.models import ServiceLine, Urgency


class LLMFlag(StrEnum):
    """Flags the model may raise. `possible_prompt_injection` is deliberately absent: only the
    deterministic pre-screen may set it."""

    REQUESTS_PRICING = "requests_pricing"
    OFF_TOPIC = "off_topic"
    UNCLEAR_NEED = "unclear_need"


class AssessmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: Annotated[str, StringConstraints(min_length=10, max_length=300)]
    service_line: ServiceLine
    urgency: Urgency
    flags: Annotated[list[LLMFlag], Field(max_length=3)]


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject: Annotated[str, StringConstraints(min_length=5, max_length=120)]
    body: Annotated[str, StringConstraints(min_length=80, max_length=1500)]


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
