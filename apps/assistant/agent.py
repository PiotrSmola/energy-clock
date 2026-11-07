import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.utils import timezone
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from apps.assistant.law import DEFAULT_ACT, LAW_ACTS
from apps.assistant.llm import LlmClient, LlmError
from apps.assistant.models import AssistantQuery, LawChunk
from apps.assistant.tools import TOOLS, run_tool
from apps.market.timeutils import to_local

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
MAX_QUESTION_CHARS = 500

SYSTEM_PROMPT = """You are the assistant of Energy Clock, an application that shows Polish \
day-ahead electricity prices (PSE) and plans when to run household appliances.
Now: {now} (Europe/Warsaw).

Rules:
- Reply in the language of the question (Polish or English), briefly, in plain text.
- Every price, cost, saving or time you mention must come from a tool result. Never estimate or \
invent numbers. If a tool returns an error or no data, say so plainly.
- When to run an appliance: find_cheapest_window. Prices of a day: get_day_prices. Dynamic vs \
fixed tariff: compare_tariff. Contracts, dynamic prices, suppliers, meters, consumer rights: \
search_energy_law.
- For legal answers cite the article numbers from the search results and mention that they come \
from {act}. If the results do not answer the question, say that you do not know.
- Prices are wholesale market prices, not the final bill: supplier margin, distribution fees \
and taxes come on top.
- Answer only questions about electricity prices, appliances, tariffs and energy law."""


@dataclass
class ToolCallRecord:
    name: str
    arguments: str
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments, "error": self.error}


@dataclass
class AssistantReply:
    question: str
    answer: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    sources: list[LawChunk] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add_usage(self, completion: ChatCompletion) -> None:
        if completion.usage is not None:
            self.prompt_tokens += completion.usage.prompt_tokens
            self.completion_tokens += completion.usage.completion_tokens


def system_prompt(now: datetime) -> str:
    return SYSTEM_PROMPT.format(now=f"{to_local(now):%A %Y-%m-%d %H:%M}", act=LAW_ACTS[DEFAULT_ACT])


def ask(
    question: str, *, client: LlmClient | None = None, now: datetime | None = None
) -> AssistantReply:
    client = client or LlmClient()
    now = now or timezone.now()
    question = question.strip()[:MAX_QUESTION_CHARS]
    reply = AssistantReply(question=question)
    started = time.monotonic()
    try:
        reply.answer = run_agent(question, client, now, reply)
    except LlmError as exc:
        logger.warning("Assistant could not reach the language model: %s", exc)
        reply.error = str(exc)
    reply.duration_ms = int((time.monotonic() - started) * 1000)
    AssistantQuery.objects.create(
        question=question,
        answer=reply.answer,
        tool_calls=[call.as_json() for call in reply.tool_calls],
        model=client.model,
        duration_ms=reply.duration_ms,
        prompt_tokens=reply.prompt_tokens or None,
        completion_tokens=reply.completion_tokens or None,
        error=reply.error or "",
    )
    return reply


def run_agent(question: str, client: LlmClient, now: datetime, reply: AssistantReply) -> str:
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt(now)},
        {"role": "user", "content": question},
    ]
    for _round in range(MAX_TOOL_ROUNDS):
        completion = client.chat(messages, TOOLS)
        reply.add_usage(completion)
        message = completion.choices[0].message
        calls = [call for call in message.tool_calls or [] if call.type == "function"]
        if not calls:
            return (message.content or "").strip()
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in calls
                ],
            }
        )
        for call in calls:
            outcome = run_tool(call.function.name, call.function.arguments, now, client)
            reply.tool_calls.append(
                ToolCallRecord(call.function.name, call.function.arguments, outcome.error)
            )
            reply.sources.extend(chunk for chunk in outcome.sources if chunk not in reply.sources)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(outcome.result, ensure_ascii=False),
                }
            )
    # the model kept calling tools; ask for an answer from what it has
    completion = client.chat(messages)
    reply.add_usage(completion)
    return (completion.choices[0].message.content or "").strip()
