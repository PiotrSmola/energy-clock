import json
from datetime import date, datetime

import pytest
import time_machine

from apps.assistant import agent
from apps.assistant.agent import (
    MAX_QUESTION_CHARS,
    MAX_TOOL_ROUNDS,
    REWRITE_PROMPT,
    ask,
    foreign_letters,
)
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


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Zażółć gęślą jaźń, ŁÓDŹ ćńśż",
        "Café naïve über straße",
        "Koszt 12,50 zł, 2 kWh, 100%",
        "CO₂ m² m¹ ≈ 0,5 \u2013 1 € 20 °C",
        "1ª 2º",
        '- lista:\n1. punkt; (nawias) "cytat" ... —',
    ],
)
def test_foreign_letters_allows_latin_text_digits_and_symbols(text: str) -> None:
    assert foreign_letters(text) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Cześć \u043f\u0440\u0438\u0432\u0435\u0442",
            "".join(sorted(set("\u043f\u0440\u0438\u0432\u0435\u0442"))),
        ),
        (
            "\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac",
            "".join(sorted(set("\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac"))),
        ),
        ("\u96fb\u6c17\u6599\u91d1", "".join(sorted(set("\u96fb\u6c17\u6599\u91d1")))),
        (
            "\u043f\u0440\u043e\u0446\u0435\u0434\u0443rę",
            "".join(sorted(set("\u043f\u0440\u043e\u0446\u0435\u0434\u0443"))),
        ),
        ("Włącz \u043e 02:00", "\u043e"),
    ],
)
def test_foreign_letters_detects_other_scripts_sorted_and_deduplicated(
    text: str, expected: str
) -> None:
    result = foreign_letters(text)

    assert result == expected
    assert result == "".join(sorted(set(result)))
    assert result != ""


def test_clean_answer_needs_no_extra_call() -> None:
    client = FakeLlm(completion("Włącz pralkę o 02:00."))

    reply = ask("Pralka?", client=client, now=NOON_SEPT_4)

    assert len(client.chat_calls) == 1
    assert (reply.rewritten, reply.rejected) == (False, False)
    assert AssistantQuery.objects.get().rewritten is False


def test_contaminated_answer_is_rewritten_with_exactly_one_extra_call() -> None:
    client = FakeLlm(
        completion(
            "Włącz pralkę o \u043f\u0440\u043e\u0446\u0435\u0434\u0443rę 02:00.", usage=(10, 5)
        ),
        completion("Włącz pralkę o 02:00.", usage=(20, 6)),
    )

    reply = ask("Kiedy włączyć pralkę?", client=client, now=NOON_SEPT_4)

    assert reply.answer == "Włącz pralkę o 02:00."
    assert (reply.rewritten, reply.rejected, reply.error) == (True, False, None)
    assert len(client.chat_calls) == 2
    rewrite_call = client.chat_calls[1]
    assert rewrite_call.tools is None
    assert rewrite_call.messages[-2] == {
        "role": "assistant",
        "content": "Włącz pralkę o \u043f\u0440\u043e\u0446\u0435\u0434\u0443rę 02:00.",
    }
    assert rewrite_call.messages[-1] == {
        "role": "user",
        "content": REWRITE_PROMPT.format(
            letters=foreign_letters("\u043f\u0440\u043e\u0446\u0435\u0434\u0443rę")
        ),
    }
    assert rewrite_call.messages[:2] == client.chat_calls[0].messages[:2]
    assert (reply.prompt_tokens, reply.completion_tokens) == (30, 11)
    row = AssistantQuery.objects.get()
    assert (row.answer, row.rewritten, row.error) == ("Włącz pralkę o 02:00.", True, "")


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_rewrite_after_a_tool_round_keeps_the_tool_messages() -> None:
    client = FakeLlm(
        completion(tool_calls=[("a", "get_day_prices", '{"date": "2020-01-01"}')]),
        completion("Brak danych, \u0441\u043f\u0430\u0441\u0438\u0431\u043e."),
        completion("Brak danych."),
    )

    reply = ask("Ceny?", client=client)

    assert reply.answer == "Brak danych."
    roles = [message["role"] for message in client.chat_calls[2].messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert client.chat_calls[2].tools is None


@pytest.mark.parametrize(
    "second", ["Nadal \u043f\u043e-\u0440\u0443\u0441\u0441\u043a\u0438", "", "   "]
)
def test_bad_rewrite_rejects_the_answer(second: str) -> None:
    client = FakeLlm(completion("Włącz \u043e 02:00."), completion(second))

    reply = ask("Pralka?", client=client, now=NOON_SEPT_4)

    assert len(client.chat_calls) == 2
    assert reply.answer == ""
    assert (reply.rejected, reply.rewritten, reply.error) == (True, True, None)
    row = AssistantQuery.objects.get()
    assert row.answer == ""
    assert row.rewritten is True
    assert row.error == "Rejected: non-Latin letters after a rewrite"


def test_missing_rewrite_content_is_rejected() -> None:
    client = FakeLlm(completion("Włącz \u043e 02:00."), completion(None))

    reply = ask("Pralka?", client=client, now=NOON_SEPT_4)

    assert reply.rejected is True
    assert reply.answer == ""


def test_llm_error_during_the_rewrite_is_an_error_not_a_rejection() -> None:
    client = FakeLlm(completion("Włącz \u043e 02:00."), LlmError("timeout"))

    reply = ask("Pralka?", client=client, now=NOON_SEPT_4)

    assert (reply.error, reply.rejected, reply.answer) == ("timeout", False, "")
    assert AssistantQuery.objects.get().error == "timeout"
