import json
from datetime import date, datetime

import pytest
import time_machine

from apps.assistant import agent
from apps.assistant.agent import MAX_QUESTION_CHARS, MAX_TOOL_ROUNDS, ask
from apps.assistant.law import pack
from apps.assistant.llm import LlmError
from apps.assistant.models import AssistantQuery, LawChunk
from apps.assistant.tools import TOOLS
from apps.market.timeutils import WARSAW
from tests.assistant_fakes import FakeLlm, completion
from tests.market_fixtures import seed_market_day

pytestmark = pytest.mark.django_db

NOON_SEPT_4 = datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW)


def add_chunk(position: int, article: str, vector: list[float]) -> LawChunk:
    return LawChunk.objects.create(
        act="DU/2024/266",
        act_title="Prawo energetyczne",
        position=position,
        article=article,
        heading=f"Art. {article}",
        text=f"tekst {article}",
        embedding=pack(vector),
    )


def test_direct_answer_makes_one_call_with_tools_and_logs_the_query() -> None:
    client = FakeLlm(completion("  Dzień dobry!  ", usage=(11, 4)))

    reply = ask("Cześć", client=client, now=NOON_SEPT_4)

    assert reply.answer == "Dzień dobry!"
    assert reply.error is None
    assert reply.tool_calls == []
    assert len(client.chat_calls) == 1
    first = client.chat_calls[0]
    assert first.tools == TOOLS
    assert [message["role"] for message in first.messages] == ["system", "user"]
    assert first.messages[1]["content"] == "Cześć"
    assert "Friday 2026-09-04 12:00" in first.messages[0]["content"]
    assert "Prawo energetyczne" in first.messages[0]["content"]
    row = AssistantQuery.objects.get()
    assert (row.question, row.answer, row.model, row.error) == (
        "Cześć",
        "Dzień dobry!",
        "fake-model",
        "",
    )
    assert (row.prompt_tokens, row.completion_tokens) == (11, 4)
    assert row.tool_calls == []


def test_missing_content_gives_an_empty_answer() -> None:
    reply = ask("Hej", client=FakeLlm(completion(None)), now=NOON_SEPT_4)

    assert reply.answer == ""
    assert reply.error is None


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_one_tool_round_sends_the_result_back_with_the_call_id() -> None:
    seed_market_day(date(2026, 9, 4))
    seed_market_day(date(2026, 9, 5))
    client = FakeLlm(
        completion(tool_calls=[("call_77", "get_day_prices", "{}")], usage=(100, 10)),
        completion("Najtaniej jest w nocy.", usage=(150, 20)),
    )

    reply = ask("Kiedy jest najtaniej?", client=client)

    assert reply.answer == "Najtaniej jest w nocy."
    assert [(call.name, call.arguments, call.error) for call in reply.tool_calls] == [
        ("get_day_prices", "{}", None)
    ]
    assert len(client.chat_calls) == 2
    second = client.chat_calls[1].messages
    assert [message["role"] for message in second] == ["system", "user", "assistant", "tool"]
    assistant_message = second[2]
    assert assistant_message["tool_calls"] == [
        {
            "id": "call_77",
            "type": "function",
            "function": {"name": "get_day_prices", "arguments": "{}"},
        }
    ]
    tool_message = second[3]
    assert tool_message["tool_call_id"] == "call_77"
    payload = json.loads(tool_message["content"])
    assert payload["date"] == "2026-09-04"
    assert "lowest" in payload
    assert client.chat_calls[1].tools == TOOLS
    row = AssistantQuery.objects.get()
    assert row.tool_calls == [{"name": "get_day_prices", "arguments": "{}", "error": None}]
    assert (row.prompt_tokens, row.completion_tokens) == (250, 30)


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_parallel_tool_calls_are_answered_one_by_one_and_errors_are_recorded() -> None:
    client = FakeLlm(
        completion(
            tool_calls=[
                ("a", "get_day_prices", '{"date": "oops"}'),
                ("b", "no_such_tool", "{}"),
            ]
        ),
        completion("Nie mam danych."),
    )

    reply = ask("Ceny?", client=client)

    second = client.chat_calls[1].messages
    assert [(m["role"], m.get("tool_call_id")) for m in second[3:]] == [
        ("tool", "a"),
        ("tool", "b"),
    ]
    assert json.loads(second[3]["content"]) == {"error": "date must be YYYY-MM-DD"}
    assert [call.error for call in reply.tool_calls] == [
        "date must be YYYY-MM-DD",
        "unknown tool no_such_tool",
    ]
    assert AssistantQuery.objects.get().tool_calls[1]["error"] == "unknown tool no_such_tool"


def test_hitting_the_round_limit_forces_a_final_call_without_tools() -> None:
    looping = [
        completion(
            tool_calls=[(f"c{n}", "get_day_prices", '{"date": "2020-01-01"}')], usage=(10, 1)
        )
        for n in range(MAX_TOOL_ROUNDS)
    ]
    client = FakeLlm(*looping, completion("Podsumowanie.", usage=(10, 2)))

    reply = ask("Ceny?", client=client, now=NOON_SEPT_4)

    assert MAX_TOOL_ROUNDS == 4
    assert len(client.chat_calls) == MAX_TOOL_ROUNDS + 1
    assert all(call.tools == TOOLS for call in client.chat_calls[:-1])
    assert client.chat_calls[-1].tools is None
    assert reply.answer == "Podsumowanie."
    assert len(reply.tool_calls) == MAX_TOOL_ROUNDS
    assert (reply.prompt_tokens, reply.completion_tokens) == (50, 6)


def test_llm_error_becomes_reply_error_and_is_logged() -> None:
    client = FakeLlm(LlmError("APIConnectionError: Connection error."))

    reply = ask("Hej", client=client, now=NOON_SEPT_4)

    assert reply.error == "APIConnectionError: Connection error."
    assert reply.answer == ""
    row = AssistantQuery.objects.get()
    assert row.error == "APIConnectionError: Connection error."
    assert row.answer == ""
    assert (row.prompt_tokens, row.completion_tokens) == (None, None)


def test_error_in_a_later_round_keeps_the_tool_calls_made_so_far() -> None:
    client = FakeLlm(
        completion(tool_calls=[("a", "get_day_prices", '{"date": "2020-01-01"}')], usage=(5, 1)),
        LlmError("timeout"),
    )

    reply = ask("Ceny?", client=client, now=NOON_SEPT_4)

    assert reply.error == "timeout"
    assert [call.name for call in reply.tool_calls] == ["get_day_prices"]
    row = AssistantQuery.objects.get()
    assert row.error == "timeout"
    assert len(row.tool_calls) == 1
    assert row.prompt_tokens == 5


def test_question_is_trimmed_and_cut_to_the_maximum() -> None:
    client = FakeLlm(completion("ok"))

    reply = ask("   " + "a" * (MAX_QUESTION_CHARS + 100) + "   ", client=client, now=NOON_SEPT_4)

    assert reply.question == "a" * MAX_QUESTION_CHARS
    assert client.chat_calls[0].messages[1]["content"] == "a" * MAX_QUESTION_CHARS
    assert AssistantQuery.objects.get().question == "a" * MAX_QUESTION_CHARS


def test_law_sources_are_deduplicated_across_searches() -> None:
    first = add_chunk(0, "45", [1.0, 0.0, 0.0])
    second = add_chunk(1, "11", [0.0, 1.0, 0.0])
    client = FakeLlm(
        completion(tool_calls=[("a", "search_energy_law", '{"query": "taryfa"}')]),
        completion(tool_calls=[("b", "search_energy_law", '{"query": "licznik taryfa"}')]),
        completion("Art. 45 i 11."),
    )

    reply = ask("Taryfa?", client=client, now=NOON_SEPT_4)

    assert reply.sources == [first, second]
    assert len(reply.tool_calls) == 2
    assert client.embedded == [["taryfa"], ["licznik taryfa"]]


def test_search_without_an_index_is_reported_to_the_model_not_raised() -> None:
    client = FakeLlm(
        completion(tool_calls=[("a", "search_energy_law", '{"query": "taryfa"}')]),
        completion("Nie wiem."),
    )

    reply = ask("Taryfa?", client=client, now=NOON_SEPT_4)

    assert reply.answer == "Nie wiem."
    assert reply.sources == []
    assert "ingest_law" in str(reply.tool_calls[0].error)


def test_ask_builds_a_default_client_and_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeLlm(completion("ok"))
    monkeypatch.setattr(agent, "LlmClient", lambda: client)

    reply = ask("Hej")

    assert reply.answer == "ok"
    assert reply.duration_ms >= 0
