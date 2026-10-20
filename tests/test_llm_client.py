"""OllamaClient over httpx.MockTransport: no sockets, no real model."""

import json
from collections.abc import Callable

import httpx
import pytest

from flowpilot.llm.client import (
    LLMBusy,
    LLMInvalidOutput,
    LLMModelMissing,
    LLMTimeout,
    LLMUnavailable,
    OllamaClient,
)

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def make(handler: Callable[[httpx.Request], httpx.Response], **kw) -> OllamaClient:
    return OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:3b",
        timeout_s=30,
        transport=httpx.MockTransport(handler),
        **kw,
    )


def chat(client: OllamaClient) -> str:
    return client.chat_json(system="sys", user="usr", schema=SCHEMA)


def test_chat_sends_constrained_deterministic_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": '{"a":"b"}'}})

    client = make(handler)
    assert chat(client) == '{"a":"b"}'
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/api/chat"
    body = json.loads(request.content)
    assert body["model"] == "qwen2.5:3b"
    assert body["stream"] is False
    assert body["format"] == SCHEMA
    assert body["options"]["temperature"] == 0
    assert body["options"]["seed"] == 42
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "sys"
    client.close()


def test_client_ignores_proxy_env_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.9:3128")
    client = make(lambda r: httpx.Response(200))
    assert client._http.trust_env is False
    assert client._http.follow_redirects is False


def _raiser(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize(
    ("handler", "error", "reason"),
    [
        (_raiser(httpx.ConnectError("refused")), LLMUnavailable, "unavailable"),
        (_raiser(httpx.ConnectTimeout("slow connect")), LLMUnavailable, "unavailable"),
        (_raiser(httpx.ReadTimeout("slow model")), LLMTimeout, "timeout"),
        (
            lambda r: httpx.Response(404, json={"error": "model 'qwen2.5:3b' not found"}),
            LLMModelMissing,
            "model_missing",
        ),
        (lambda r: httpx.Response(500, text="boom"), LLMUnavailable, "unavailable"),
        (lambda r: httpx.Response(404, text="no route"), LLMUnavailable, "unavailable"),
        (lambda r: httpx.Response(200, text="not json"), LLMInvalidOutput, "invalid_output"),
        (lambda r: httpx.Response(200, json={"done": True}), LLMInvalidOutput, "invalid_output"),
        (
            lambda r: httpx.Response(200, json={"message": {"content": "   "}}),
            LLMInvalidOutput,
            "invalid_output",
        ),
        (
            lambda r: httpx.Response(200, json={"message": {"content": 42}}),
            LLMInvalidOutput,
            "invalid_output",
        ),
    ],
)
def test_failures_map_to_typed_errors(handler, error: type, reason: str) -> None:
    with pytest.raises(error) as info:
        chat(make(handler))
    assert info.value.reason == reason


def test_semaphore_releases_after_failure() -> None:
    client = make(_raiser(httpx.ConnectError("refused")))
    with pytest.raises(LLMUnavailable):
        chat(client)
    assert client._slot.acquire(blocking=False)  # slot was released


def test_busy_when_generation_in_progress() -> None:
    client = make(lambda r: httpx.Response(200), busy_timeout_s=0.01)
    assert client._slot.acquire(blocking=False)
    with pytest.raises(LLMBusy) as info:
        chat(client)
    assert info.value.reason == "busy"


def test_list_models() -> None:
    client = make(
        lambda r: httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}, {"name": "a:1"}]})
    )
    assert client.list_models() == ["a:1", "qwen2.5:3b"]


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="nope"), httpx.Response(200, json={"models": [{"id": 1}]})],
)
def test_list_models_rejects_bad_payload(response: httpx.Response) -> None:
    with pytest.raises(LLMUnavailable, match="unexpected"):
        make(lambda r: response).list_models()
