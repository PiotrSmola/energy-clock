from collections.abc import Sequence
from typing import Any

from django.conf import settings
from openai import OpenAI, OpenAIError, omit
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

CHAT_TEMPERATURE = 0.2
EMBEDDING_BATCH_SIZE = 16


class LlmError(Exception):
    pass


class LlmClient:
    """Chat and embeddings over any OpenAI-compatible API (LM Studio, OpenAI, vLLM, ...)."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        reasoning_effort: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.model = model or str(settings.LLM_MODEL)
        self.embedding_model = embedding_model or str(settings.LLM_EMBEDDING_MODEL)
        self.reasoning_effort = reasoning_effort or str(settings.LLM_REASONING_EFFORT)
        self._client = OpenAI(
            base_url=base_url or str(settings.LLM_BASE_URL),
            api_key=api_key or str(settings.LLM_API_KEY),
            timeout=timeout or float(settings.LLM_TIMEOUT_SECONDS),
            max_retries=1,
        )

    def chat(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolParam] | None = None,
    ) -> ChatCompletion:
        extra_body: dict[str, Any] = {}
        if self.reasoning_effort != "omit":
            extra_body["reasoning_effort"] = self.reasoning_effort
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                tools=list(tools) if tools else omit,
                temperature=CHAT_TEMPERATURE,
                extra_body=extra_body or None,
            )
        except OpenAIError as exc:
            raise LlmError(f"{type(exc).__name__}: {exc}") from exc
        if not completion.choices:
            raise LlmError("The model returned no choices")
        return completion

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = list(texts[start : start + EMBEDDING_BATCH_SIZE])
            try:
                response = self._client.embeddings.create(model=self.embedding_model, input=batch)
            except OpenAIError as exc:
                raise LlmError(f"{type(exc).__name__}: {exc}") from exc
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors
