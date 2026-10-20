"""Scripted stand-in for the Ollama client. Default tests never talk to a real model."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Step = str | Exception | Callable[[], str]


class FakeLLMClient:
    def __init__(self, *responses: Step, model: str = "fake-model:1b") -> None:
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self._responses: list[Step] = list(responses)

    def script(self, *responses: Step) -> FakeLLMClient:
        self._responses.extend(responses)
        return self

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any]) -> str:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self._responses:
            raise AssertionError("FakeLLMClient was called more times than scripted")
        step = self._responses.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step()
        return step
