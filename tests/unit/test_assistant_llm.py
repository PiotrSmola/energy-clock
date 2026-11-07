import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from django.test import override_settings
from openai import OpenAI

from apps.assistant.llm import CHAT_TEMPERATURE, EMBEDDING_BATCH_SIZE, LlmClient, LlmError

BASE_URL = "http://llm.test/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"
EMBEDDINGS_URL = f"{BASE_URL}/embeddings"

CHAT_RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1_700_000_000,
    "model": "m",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Cześć"}}
    ],
}

TOOL: Any = {
    "type": "function",
    "function": {"name": "get_day_prices", "parameters": {"type": "object", "properties": {}}},
}


def body_of(request: httpx2.Request) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(request.content)
    return payload


class FakeServer:
    """Plays the OpenAI-compatible server behind the SDK (which talks httpx2, not httpx)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.requests: list[httpx2.Request] = []
        self.handlers: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}
        real_openai = OpenAI

        def openai_with_fake_transport(**kwargs: Any) -> OpenAI:
            transport = httpx2.MockTransport(self.handle)
            return real_openai(**kwargs, http_client=httpx2.Client(transport=transport))

        monkeypatch.setattr("apps.assistant.llm.OpenAI", openai_with_fake_transport)

    def on(self, url: str, handler: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.handlers[url] = handler

    def reply(self, url: str, status: int, payload: dict[str, Any]) -> None:
        self.on(url, lambda _request: httpx2.Response(status, json=payload))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.handlers[str(request.url)](request)

    def bodies(self) -> list[dict[str, Any]]:
        return [body_of(request) for request in self.requests]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    return FakeServer(monkeypatch)


def make_client(**overrides: Any) -> LlmClient:
    options: dict[str, Any] = {
        "base_url": BASE_URL,
        "api_key": "secret",
        "model": "m",
        "embedding_model": "e",
        "reasoning_effort": "low",
        "timeout": 5.0,
    }
    return LlmClient(**{**options, **overrides})


def embedding_response(request: httpx2.Request) -> httpx2.Response:
    texts: list[str] = body_of(request)["input"]
    # a server is free to answer out of order; the client must sort by index
    data = [
        {"object": "embedding", "index": index, "embedding": [float(len(text)), float(index)]}
        for index, text in enumerate(texts)
    ]
    return httpx2.Response(
        200,
        json={
            "object": "list",
            "model": "e",
            "data": list(reversed(data)),
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )


def test_chat_sends_model_temperature_reasoning_effort_and_tools(server: FakeServer) -> None:
    server.reply(CHAT_URL, 200, CHAT_RESPONSE)
    messages: Any = [{"role": "user", "content": "Hej"}]

    result = make_client().chat(messages, [TOOL])

    assert result.choices[0].message.content == "Cześć"
    body = server.bodies()[-1]
    assert body["model"] == "m"
    assert body["messages"] == messages
    assert body["temperature"] == CHAT_TEMPERATURE == 0.2
    assert body["reasoning_effort"] == "low"
    assert body["tools"] == [TOOL]
    assert server.requests[-1].headers["authorization"] == "Bearer secret"


def test_chat_omits_tools_when_there_are_none(server: FakeServer) -> None:
    server.reply(CHAT_URL, 200, CHAT_RESPONSE)
    client = make_client()

    client.chat([{"role": "user", "content": "Hej"}])
    client.chat([{"role": "user", "content": "Hej"}], [])

    assert len(server.requests) == 2
    assert all("tools" not in body for body in server.bodies())


def test_reasoning_effort_omit_leaves_the_field_out(server: FakeServer) -> None:
    server.reply(CHAT_URL, 200, CHAT_RESPONSE)

    make_client(reasoning_effort="omit").chat([{"role": "user", "content": "Hej"}])

    assert "reasoning_effort" not in server.bodies()[-1]


def test_chat_maps_http_errors_to_llm_error_after_one_retry(server: FakeServer) -> None:
    server.reply(CHAT_URL, 500, {"error": "boom"})

    with pytest.raises(LlmError, match="InternalServerError"):
        make_client().chat([{"role": "user", "content": "Hej"}])

    assert len(server.requests) == 2


def test_chat_maps_client_errors_to_llm_error_without_retrying(server: FakeServer) -> None:
    server.reply(CHAT_URL, 401, {"error": "no"})

    with pytest.raises(LlmError, match="AuthenticationError"):
        make_client().chat([{"role": "user", "content": "Hej"}])

    assert len(server.requests) == 1


def test_chat_maps_connection_errors_to_llm_error(server: FakeServer) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    server.on(CHAT_URL, refuse)

    with pytest.raises(LlmError, match="APIConnectionError"):
        make_client().chat([{"role": "user", "content": "Hej"}])


def test_embed_batches_sixteen_texts_per_request_and_keeps_order(server: FakeServer) -> None:
    server.on(EMBEDDINGS_URL, embedding_response)
    texts = ["x" * (index + 1) for index in range(EMBEDDING_BATCH_SIZE + 4)]

    vectors = make_client().embed(texts)

    assert EMBEDDING_BATCH_SIZE == 16
    assert [len(body["input"]) for body in server.bodies()] == [16, 4]
    assert server.bodies()[0]["model"] == "e"
    assert [vector[0] for vector in vectors] == [float(len(text)) for text in texts]
    assert [vector[1] for vector in vectors[:3]] == [0.0, 1.0, 2.0]


def test_embed_without_texts_makes_no_request(server: FakeServer) -> None:
    server.on(EMBEDDINGS_URL, embedding_response)

    assert make_client().embed([]) == []
    assert server.requests == []


def test_embed_maps_errors_to_llm_error(server: FakeServer) -> None:
    server.reply(EMBEDDINGS_URL, 400, {"error": "bad"})

    with pytest.raises(LlmError, match="BadRequestError"):
        make_client().embed(["a"])


def test_client_defaults_come_from_settings() -> None:
    with override_settings(
        LLM_BASE_URL="http://configured.test/v1",
        LLM_API_KEY="k",
        LLM_MODEL="configured-model",
        LLM_EMBEDDING_MODEL="configured-embedding",
        LLM_REASONING_EFFORT="high",
        LLM_TIMEOUT_SECONDS=12,
    ):
        client = LlmClient()

    assert client.model == "configured-model"
    assert client.embedding_model == "configured-embedding"
    assert client.reasoning_effort == "high"
