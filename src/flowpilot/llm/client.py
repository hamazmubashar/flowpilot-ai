"""Ollama chat client with strict timeouts and a single-flight concurrency limit.

Only loopback URLs are accepted (enforced in settings), proxy environment variables are ignored,
redirects are not followed, and every failure maps to a typed error with a fallback reason.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

import httpx

CONNECT_TIMEOUT_S = 2.0
BUSY_TIMEOUT_S = 5.0
KEEP_ALIVE = "10m"
DEFAULT_OPTIONS: dict[str, Any] = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 4096,
    "num_predict": 600,
}


class LLMError(Exception):
    reason = "unavailable"


class LLMUnavailable(LLMError):
    reason = "unavailable"


class LLMTimeout(LLMError):
    reason = "timeout"


class LLMModelMissing(LLMError):
    reason = "model_missing"


class LLMBusy(LLMError):
    reason = "busy"


class LLMInvalidOutput(LLMError):
    reason = "invalid_output"


class LLMClient(Protocol):
    model: str

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any]) -> str:
        """Return the raw JSON text produced by the model, or raise an LLMError."""
        ...


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
        busy_timeout_s: float = BUSY_TIMEOUT_S,
    ) -> None:
        self.model = model
        self._busy_timeout_s = busy_timeout_s
        self._slot = threading.Semaphore(1)  # one generation at a time on a 16 GB laptop
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S),
            transport=transport,
            trust_env=False,  # never route through HTTP(S)_PROXY or read .netrc
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.ConnectTimeout as exc:
            raise LLMUnavailable(f"connect timeout: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"{type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code == 404 and "not found" in response.text.lower():
            raise LLMModelMissing(f"model {self.model!r} is not installed")
        if response.status_code >= 400:
            raise LLMUnavailable(f"HTTP {response.status_code}")
        return response

    def list_models(self) -> list[str]:
        """Installed model names via /api/tags. Performs no inference."""
        try:
            payload = self._request("GET", "/api/tags").json()
            return sorted(m["name"] for m in payload.get("models", []))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LLMUnavailable(f"unexpected /api/tags response: {exc}") from exc

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any]) -> str:
        if not self._slot.acquire(timeout=self._busy_timeout_s):
            raise LLMBusy("another generation is in progress")
        try:
            response = self._request(
                "POST",
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": schema,
                    "keep_alive": KEEP_ALIVE,
                    "options": DEFAULT_OPTIONS,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
        finally:
            self._slot.release()
        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMInvalidOutput(f"unexpected /api/chat response: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMInvalidOutput("empty model response")
        return content
