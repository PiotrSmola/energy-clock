import json
import re
from html import unescape
from io import StringIO
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from apps.assistant import agent, views
from apps.assistant.agent import MAX_QUESTION_CHARS, AssistantReply, ToolCallRecord
from apps.assistant.law import DEFAULT_ACT, pack
from apps.assistant.llm import LlmError
from apps.assistant.management.commands import ingest_law
from apps.assistant.models import AssistantQuery, LawChunk
from tests.assistant_fakes import FakeLlm, completion

pytestmark = pytest.mark.django_db

HTML_ACT = """
<div class="unit unit_chpt" data-id="chpt_1"><h3><P>Rozdział&nbsp;1</P><P>Ogólne</P></h3>
<div class="unit-inner"><div class="unit unit_arti" data-id="arti_1"><h3>Art.&nbsp;1.</h3>
<div class="unit-inner"><div data-template="xText">Pierwszy przepis.</div></div></div></div></div>
"""


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def source_chunk() -> LawChunk:
    return LawChunk.objects.create(
        act=DEFAULT_ACT,
        act_title="Prawo energetyczne",
        position=0,
        article="5a",
        heading="Rozdział 1 > Art. 5a",
        text="Sprzedawca <b>oferuje</b> umowę.",
        embedding=pack([1.0, 0.0]),
    )


def result_area(html: str) -> str:
    """Text content of the result section, without the progress bar."""
    inner = html.split('id="assistant-result"', 1)[1].split("</section>", 1)[0]
    inner = inner.split(">", 1)[1].replace(
        '<div class="panel-progress" aria-hidden="true"></div>', ""
    )
    return inner.strip()


def make_reply(**overrides: Any) -> AssistantReply:
    reply = AssistantReply(
        question="Kiedy włączyć pralkę?",
        answer="Włącz o **02:00**.",
        tool_calls=[
            ToolCallRecord("find_cheapest_window", '{"duration_minutes": 120}'),
            ToolCallRecord("get_day_prices", '{"date": "x"}', error="date must be YYYY-MM-DD"),
        ],
        duration_ms=234,
        prompt_tokens=100,
        completion_tokens=42,
    )
    for name, value in overrides.items():
        setattr(reply, name, value)
    return reply


class AskSpy:
    def __init__(self, reply: AssistantReply) -> None:
        self.reply = reply
        self.questions: list[str] = []

    def __call__(self, question: str) -> AssistantReply:
        self.questions.append(question)
        return self.reply


def patch_ask(monkeypatch: pytest.MonkeyPatch, reply: AssistantReply | None = None) -> AskSpy:
    spy = AskSpy(reply or make_reply())
    monkeypatch.setattr(views, "ask", spy)
    return spy


def test_page_without_a_question_does_not_call_the_model(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = patch_ask(monkeypatch)

    response = client.get(reverse("assistant:assistant"))

    assert response.status_code == 200
    assert spy.questions == []
    html = response.content.decode()
    assert "<html" in html
    assert 'name="q"' in html
    assert f'maxlength="{MAX_QUESTION_CHARS}"' in html
    assert '<a class="example" href="/assistant?q=' in html
    assert "washing machine" in html
    assert result_area(html) == ""


def test_blank_question_does_not_call_the_model(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = patch_ask(monkeypatch)

    client.get(reverse("assistant:assistant"), {"q": "   "})

    assert spy.questions == []


def test_page_with_a_question_renders_answer_and_sources_but_no_internals(
    client: Client, monkeypatch: pytest.MonkeyPatch, source_chunk: LawChunk
) -> None:
    spy = patch_ask(monkeypatch, make_reply(sources=[source_chunk]))

    response = client.get(reverse("assistant:assistant"), {"q": "  Kiedy włączyć pralkę?  "})

    assert spy.questions == ["Kiedy włączyć pralkę?"]
    html = response.content.decode()
    assert "<strong>02:00</strong>" in html
    assert "Calculations used" not in html
    assert "find_cheapest_window" not in html
    assert "date must be YYYY-MM-DD" not in html
    assert "234 ms" not in html
    assert "tokens" not in html
    assert "Articles found" in html
    assert html.count('<details class="source">') == 1
    assert "<summary><strong>art. 5a</strong>" in html
    assert "Rozdział 1 &gt; Art. 5a" in html
    assert "Sprzedawca &lt;b&gt;oferuje&lt;/b&gt; umowę." in html
    assert "not legal advice" in html


def test_question_longer_than_the_limit_is_cut_before_asking(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = patch_ask(monkeypatch)

    client.get(reverse("assistant:assistant"), {"q": "a" * (MAX_QUESTION_CHARS + 50)})

    assert spy.questions == ["a" * MAX_QUESTION_CHARS]


def test_page_shows_the_unreachable_model_message_on_error(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_ask(monkeypatch, make_reply(answer="", error="APIConnectionError"))

    response = client.get(reverse("assistant:assistant"), {"q": "Hej"})

    html = response.content.decode()
    assert "The language model is not reachable." in html
    assert "Calculations used" not in html


def test_empty_model_answer_shows_a_placeholder(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_ask(monkeypatch, make_reply(answer="", tool_calls=[]))

    html = client.get(reverse("assistant:assistant"), {"q": "Hej"}).content.decode()

    assert "The model returned an empty answer." in html
    assert "Calculations used" not in html


def test_answer_partial_has_no_page_chrome(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_ask(monkeypatch)

    response = client.get(reverse("assistant:answer"), {"q": "Hej"}, headers={"HX-Request": "true"})

    html = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in html
    assert "<nav" not in html
    assert "<strong>02:00</strong>" in html


def test_answer_partial_without_a_question_is_empty(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = patch_ask(monkeypatch)

    html = client.get(reverse("assistant:answer")).content.decode()

    assert spy.questions == []
    assert "<article" not in html
    assert "could not be shown" not in html
    assert html.replace('<div class="panel-progress" aria-hidden="true"></div>', "").strip() == ""


def test_api_requires_a_question(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = patch_ask(monkeypatch)

    assert client.get(reverse("assistant:api_ask")).status_code == 400
    assert client.get(reverse("assistant:api_ask"), {"q": "  "}).status_code == 400
    assert spy.questions == []


def test_api_returns_the_reply_as_json(
    client: Client, monkeypatch: pytest.MonkeyPatch, source_chunk: LawChunk
) -> None:
    patch_ask(monkeypatch, make_reply(sources=[source_chunk]))

    response = client.get(reverse("assistant:api_ask"), {"q": "Kiedy włączyć pralkę?"})

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "włączyć" in response.content.decode()  # not \u-escaped
    assert response.json() == {
        "question": "Kiedy włączyć pralkę?",
        "answer": "Włącz o **02:00**.",
        "error": None,
        "rejected": False,
        "rewritten": False,
        "tool_calls": [
            {
                "name": "find_cheapest_window",
                "arguments": '{"duration_minutes": 120}',
                "error": None,
            },
            {
                "name": "get_day_prices",
                "arguments": '{"date": "x"}',
                "error": "date must be YYYY-MM-DD",
            },
        ],
        "sources": [
            {"act": "Prawo energetyczne", "article": "5a", "heading": "Rozdział 1 > Art. 5a"}
        ],
        "duration_ms": 234,
        "prompt_tokens": 100,
        "completion_tokens": 42,
    }


def test_api_answers_503_when_the_model_is_unreachable(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_ask(monkeypatch, make_reply(answer="", error="APIConnectionError: Connection error."))

    response = client.get(reverse("assistant:api_ask"), {"q": "Hej"})

    assert response.status_code == 503
    assert response.json()["error"] == "APIConnectionError: Connection error."


def test_api_runs_the_whole_agent_end_to_end(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLlm(completion("Cześć!", usage=(7, 3)))
    monkeypatch.setattr(agent, "LlmClient", lambda: fake)

    response = client.get(reverse("assistant:api_ask"), {"q": "Hej"})

    assert response.status_code == 200
    assert response.json()["answer"] == "Cześć!"
    assert response.json()["prompt_tokens"] == 7
    assert AssistantQuery.objects.get().question == "Hej"


def test_api_end_to_end_with_an_unreachable_model_logs_the_error(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLlm(LlmError("APIConnectionError: Connection error."))
    monkeypatch.setattr(agent, "LlmClient", lambda: fake)

    response = client.get(reverse("assistant:api_ask"), {"q": "Hej"})

    assert response.status_code == 503
    assert AssistantQuery.objects.get().error == "APIConnectionError: Connection error."


def test_navigation_links_to_the_assistant_in_english_and_polish(client: Client) -> None:
    english = client.get(reverse("assistant:assistant")).content.decode()
    client.cookies["energy_clock_language"] = "pl"
    polish = client.get(reverse("assistant:assistant")).content.decode()

    link = f'href="{reverse("assistant:assistant")}"'
    assert link in english
    assert ">Assistant</a>" in english
    assert ">Asystent</a>" in polish
    assert ">Assistant</a>" not in polish
    assert "Zapytaj" in polish or "Pytanie" in polish


def test_navigation_link_is_marked_current_on_the_assistant_page(client: Client) -> None:
    html = client.get(reverse("assistant:assistant")).content.decode()

    assert f'<a href="{reverse("assistant:assistant")}" aria-current="page">Assistant</a>' in html
    other = client.get(reverse("web:home")).content.decode()
    assert 'aria-current="page">Assistant' not in other


def render_answer(text: str) -> str:
    return Template("{% load assistant_text %}{{ text|answer_html }}").render(
        Context({"text": text})
    )


def test_answer_html_escapes_markup_from_the_model() -> None:
    html = render_answer("<script>alert(1)</script> & <b>x</b>")

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;x&lt;/b&gt;" in html
    assert html.startswith("<p>")


def test_answer_html_escapes_inside_bold_and_lists() -> None:
    html = render_answer("**<img src=x onerror=1>**\n\n- <i>a</i>\n- b")

    assert "<img" not in html
    assert "<i>" not in html
    assert "<strong>&lt;img src=x onerror=1&gt;</strong>" in html


def test_answer_html_renders_paragraphs_bold_and_line_breaks() -> None:
    html = render_answer("Pierwszy **ważny** akapit.\nDruga linia.\n\n\nDrugi akapit.")

    assert html == (
        "<p>Pierwszy <strong>ważny</strong> akapit.<br>Druga linia.</p><p>Drugi akapit.</p>"
    )


@pytest.mark.parametrize(
    "text",
    ["- jeden\n- dwa", "* jeden\n* dwa", "• jeden\n• dwa", "1. jeden\n2. dwa", "1) jeden\n2) dwa"],
)
def test_answer_html_renders_every_bullet_style_as_a_list(text: str) -> None:
    assert render_answer(text) == "<ul><li>jeden</li><li>dwa</li></ul>"


def test_answer_html_mixed_paragraph_and_list_stay_separate_blocks() -> None:
    html = render_answer("Wybierz:\n\n- **A**\n- B\n\nKoniec.")

    assert html == "<p>Wybierz:</p><ul><li><strong>A</strong></li><li>B</li></ul><p>Koniec.</p>"


def test_answer_html_of_blank_text_is_empty() -> None:
    assert render_answer("  \n\n  ") == ""


def test_ingest_law_command_reports_the_indexed_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeLlm()
    monkeypatch.setattr(ingest_law, "LlmClient", lambda: fake)
    monkeypatch.setattr("apps.assistant.law.fetch_act_html", lambda act: HTML_ACT)
    out = StringIO()

    call_command("ingest_law", stdout=out)

    text = out.getvalue()
    assert "Prawo energetyczne" in text
    assert "fake-embedding" in text
    assert "Indexed 1 chunks in" in text
    assert LawChunk.objects.filter(act=DEFAULT_ACT).count() == 1


def test_ingest_law_command_turns_llm_errors_into_command_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken(FakeLlm):
        def embed(self, texts: Any) -> list[list[float]]:
            raise LlmError("APIConnectionError: Connection error.")

    monkeypatch.setattr(ingest_law, "LlmClient", lambda: Broken())
    monkeypatch.setattr("apps.assistant.law.fetch_act_html", lambda act: HTML_ACT)

    with pytest.raises(CommandError, match="Embedding model is not reachable: APIConnectionError"):
        call_command("ingest_law")

    assert LawChunk.objects.count() == 0


def test_ingest_law_command_rejects_unknown_acts() -> None:
    with pytest.raises(CommandError, match="invalid choice"):
        call_command("ingest_law", "--act", "DU/1/1")


def test_json_dump_of_payload_is_serialisable() -> None:
    assert json.dumps(views.reply_payload(make_reply()), ensure_ascii=False)


def test_rejected_answer_is_not_shown_on_the_page(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_ask(monkeypatch, make_reply(answer="", rejected=True, rewritten=True))

    html = client.get(reverse("assistant:assistant"), {"q": "Hej"}).content.decode()

    assert "The answer could not be shown." in html
    assert "<article" not in html
    assert "The model returned an empty answer." not in html


def test_rejected_answer_is_not_shown_in_the_partial(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_ask(monkeypatch, make_reply(answer="", rejected=True))

    html = client.get(reverse("assistant:answer"), {"q": "Hej"}).content.decode()

    assert "The answer could not be shown." in html
    assert "<html" not in html


def test_api_answers_502_with_rejected_when_the_answer_is_unusable(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_ask(monkeypatch, make_reply(answer="", rejected=True, rewritten=True))

    response = client.get(reverse("assistant:api_ask"), {"q": "Hej"})

    assert response.status_code == 502
    body = response.json()
    assert (body["rejected"], body["rewritten"], body["error"], body["answer"]) == (
        True,
        True,
        None,
        "",
    )


CYRILLIC_ANSWER = "Włącz pralkę o \u043f\u0440\u043e\u0446\u0435\u0434\u0443rę 02:00."


def contaminated_twice_client() -> FakeLlm:
    return FakeLlm(completion(CYRILLIC_ANSWER), completion(CYRILLIC_ANSWER))


def test_page_end_to_end_shows_the_rewrite_when_the_first_answer_has_foreign_letters(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLlm(completion(CYRILLIC_ANSWER), completion("Włącz pralkę o 02:00."))
    monkeypatch.setattr(agent, "LlmClient", lambda: fake)

    html = client.get(reverse("assistant:assistant"), {"q": "Hej"}).content.decode()

    assert "Włącz pralkę o 02:00." in html
    assert "\u043f\u0440\u043e\u0446\u0435\u0434\u0443rę" not in html
    assert AssistantQuery.objects.get().rewritten is True


def test_page_end_to_end_hides_an_answer_that_is_still_foreign_after_the_rewrite(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = contaminated_twice_client()
    monkeypatch.setattr(agent, "LlmClient", lambda: fake)

    html = client.get(reverse("assistant:assistant"), {"q": "Hej"}).content.decode()

    assert "The answer could not be shown." in html
    assert "\u043f\u0440\u043e\u0446\u0435\u0434\u0443rę" not in html


def test_api_end_to_end_answers_502_after_a_failed_rewrite(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = contaminated_twice_client()
    monkeypatch.setattr(agent, "LlmClient", lambda: fake)

    response = client.get(reverse("assistant:api_ask"), {"q": "Hej"})

    assert response.status_code == 502
    body = response.json()
    assert body["rejected"] is True
    assert body["rewritten"] is True
    assert body["answer"] == ""
    assert "\u043f\u0440\u043e\u0446\u0435\u0434\u0443rę" not in response.content.decode()
    assert len(fake.chat_calls) == 2


def test_ingest_law_command_turns_download_errors_into_command_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_download(act: str) -> str:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(ingest_law, "LlmClient", lambda: FakeLlm())
    monkeypatch.setattr("apps.assistant.law.fetch_act_html", failing_download)

    with pytest.raises(CommandError, match="Could not download DU/2024/266 from the ELI API"):
        call_command("ingest_law")


def test_ingest_law_command_turns_an_empty_parse_into_a_command_error_and_keeps_the_index(
    monkeypatch: pytest.MonkeyPatch, source_chunk: LawChunk
) -> None:
    monkeypatch.setattr(ingest_law, "LlmClient", lambda: FakeLlm())
    monkeypatch.setattr("apps.assistant.law.fetch_act_html", lambda act: "<html>changed</html>")

    with pytest.raises(CommandError, match="No articles found"):
        call_command("ingest_law")

    assert LawChunk.objects.filter(pk=source_chunk.pk).exists()


def example_links(html: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for match in re.finditer(r'<a class="example"(.*?)>(.*?)</a>', html, re.DOTALL):
        attributes = {
            name: unescape(value)
            for name, value in re.findall(r'([\w:-]+)="(.*?)"', match.group(1), re.DOTALL)
        }
        attributes["text"] = unescape(match.group(2))
        links.append(attributes)
    return links


def thinking_block(html: str) -> str:
    match = re.search(
        r'<div class="thinking" role="status">(.*?)</div>\s*<section', html, re.DOTALL
    )
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "The assistant is preparing an answer…"),
        ("pl", "Asystent przygotowuje odpowiedź…"),
    ],
)
def test_page_has_the_preparing_indicator_with_bar_and_text(
    client: Client, language: str, text: str
) -> None:
    client.cookies["energy_clock_language"] = language

    html = client.get(reverse("assistant:assistant")).content.decode()

    assert '<div id="assistant-output" class="assistant-output">' in html
    block = thinking_block(html)
    assert '<div class="panel-progress" aria-hidden="true"></div>' in block
    assert text in block
    assert "model" not in block.lower()
    assert "modelu" not in block.lower()


def test_indicator_is_outside_the_result_section_and_the_partial_has_none(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_ask(monkeypatch)
    page = client.get(reverse("assistant:assistant"), {"q": "Hej"}).content.decode()
    partial = client.get(reverse("assistant:answer"), {"q": "Hej"}).content.decode()

    assert page.index('class="thinking"') < page.index('id="assistant-result"')
    result_section = page.split('id="assistant-result"', 1)[1].split("</section>", 1)[0]
    assert "thinking" not in result_section
    assert "preparing an answer" not in result_section
    assert "thinking" not in partial
    assert "preparing an answer" not in partial
    assert 'role="status"' not in partial


def test_form_is_wired_to_the_indicator_and_disables_the_button(client: Client) -> None:
    html = client.get(reverse("assistant:assistant")).content.decode()

    form = re.search(r'<form class="assistant-form"(.*?)>', html, re.DOTALL)
    assert form is not None
    attributes = dict(re.findall(r'([\w:-]+)="(.*?)"', form.group(1), re.DOTALL))
    assert attributes["hx-get"] == reverse("assistant:answer")
    assert attributes["hx-target"] == "#assistant-result"
    assert attributes["hx-indicator"] == "#assistant-output"
    assert attributes["hx-sync"] == "#assistant-output:replace"
    assert attributes["hx-disabled-elt"] == "find button"
    assert "scrollIntoView" in attributes["hx-on::before-request"]
    assert "assistant-output" in attributes["hx-on::before-request"]


def test_example_links_keep_a_plain_href_and_use_htmx_with_the_same_question(
    client: Client,
) -> None:
    html = client.get(reverse("assistant:assistant")).content.decode()

    links = example_links(html)

    assert len(links) == len(views.EXAMPLE_QUESTIONS) == 4
    for link in links:
        href_query = parse_qs(urlsplit(link["href"]).query)["q"][0]
        get_query = parse_qs(urlsplit(link["hx-get"]).query)["q"][0]
        assert urlsplit(link["href"]).path == reverse("assistant:assistant")
        assert urlsplit(link["hx-get"]).path == reverse("assistant:answer")
        assert href_query == get_query == link["data-question"] == link["text"]
        assert link["hx-target"] == "#assistant-result"
        assert link["hx-indicator"] == "#assistant-output"
        assert link["hx-sync"] == "#assistant-output:replace"
        assert link["hx-disabled-elt"] == ".assistant-form button"
        assert "id_q" in link["hx-on::before-request"]
        assert "dataset.question" in link["hx-on::before-request"]
        assert "scrollIntoView" in link["hx-on::before-request"]


def test_polish_examples_carry_the_polish_question_in_every_attribute(client: Client) -> None:
    client.cookies["energy_clock_language"] = "pl"

    links = example_links(client.get(reverse("assistant:assistant")).content.decode())

    assert len(links) == 4
    for link in links:
        assert parse_qs(urlsplit(link["hx-get"]).query)["q"][0] == link["data-question"]
        assert link["data-question"] == link["text"]
    assert any("pralk" in link["text"].lower() for link in links)
