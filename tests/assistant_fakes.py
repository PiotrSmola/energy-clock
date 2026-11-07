from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openai.types.chat import ChatCompletion

from apps.assistant.llm import LlmClient, LlmError


def completion(
    content: str | None = None,
    *,
    tool_calls: Sequence[tuple[str, str, str]] = (),
    usage: tuple[int, int] | None = None,
) -> ChatCompletion:
    """A chat completion as the API returns it; tool calls are (id, name, raw JSON arguments)."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
            for call_id, name, arguments in tool_calls
        ]
    payload: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": message,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "total_tokens": usage[0] + usage[1],
        }
    return ChatCompletion.model_validate(payload)


def keyword_vector(text: str) -> list[float]:
    """Three-dimensional bag of words: tariff, meter, other."""
    lowered = text.lower()
    return [float("taryf" in lowered), float("licznik" in lowered), 0.1]


@dataclass
class ChatCall:
    messages: list[Any]
    tools: list[Any] | None


class FakeLlm(LlmClient):
    """Scripted stand-in for LlmClient: replies (or errors) are returned in order."""

    def __init__(
        self,
        *replies: ChatCompletion | LlmError,
        vectorize: Callable[[str], list[float]] = keyword_vector,
    ) -> None:
        self.model = "fake-model"
        self.embedding_model = "fake-embedding"
        self.reasoning_effort = "none"
        self.replies: list[ChatCompletion | LlmError] = list(replies)
        self.vectorize = vectorize
        self.chat_calls: list[ChatCall] = []
        self.embedded: list[list[str]] = []

    def chat(self, messages: Sequence[Any], tools: Sequence[Any] | None = None) -> ChatCompletion:
        # the agent keeps appending to its message list, so keep a snapshot of each call
        self.chat_calls.append(ChatCall(list(messages), list(tools) if tools else None))
        if not self.replies:
            raise AssertionError("the fake model has no scripted reply left")
        reply = self.replies.pop(0)
        if isinstance(reply, LlmError):
            raise reply
        return reply

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.append(list(texts))
        return [self.vectorize(text) for text in texts]
