import httpx
import pytest
import respx
from django.test import override_settings

from apps.assistant import law
from apps.assistant.law import (
    MAX_CHUNK_CHARS,
    Article,
    article_label,
    chunk_article,
    ingest_act,
    pack,
    parse_articles,
    search,
    split_long,
    unpack,
)
from apps.assistant.llm import LlmError
from apps.assistant.models import LawChunk
from tests.assistant_fakes import FakeLlm

pytestmark = pytest.mark.django_db

ACT = law.DEFAULT_ACT

FOOTNOTE = (
    '<A class="gloss-link tooltip" href="#gloss-0:6:"><sup>6)</sup><span class="tooltip-text">'
    '<span class="pro-gloss-inner">W tym brzmieniu obowiązuje do 2030 r.</span></span></A>'
)

CHAPTER_TWO = """
<div class="unit unit_chpt pro-text with-title" data-id="chpt_2">
  <h3>
    <P ALIGN="center">Rozdział&nbsp;2</P>
    <P ALIGN="center">
    <SPAN CLASS="pro-title-unit">Taryfy</SPAN>
    </P>
    </h3>
  <div class="unit-inner">
    <div class="unit unit_arti pro-text false" data-id="arti_9">
      <h3 CLASS="pro-none"><B CLASS="b">Art.&nbsp;9.</B></h3>
      <div class="unit-inner">
        <div data-template="xText" CLASS="pro-text">Taryfa podlega zatwierdzeniu.</div>
        </div>
    </div>
  </div>
</div>
"""

CHAPTER_ONE = f"""
<div class="unit unit_chpt pro-text with-title" data-id="chpt_1">
  <h3>
    <P ALIGN="center">Rozdział&nbsp;1</P>
    <P ALIGN="center">
    <B>
    <SPAN CLASS="pro-title-unit">Przepisy ogólne</SPAN>
    </B>
    </P>
    </h3>
  <div class="unit-inner">
    <div class="unit unit_arti pro-text false" data-id="arti_1">
      <h3 CLASS="pro-none"><B CLASS="b">Art.&nbsp;1.</B></h3>
      <div class="unit-inner">
        <div class="unit unit_pass pro-text false" data-id="pass_1">
          <h3>1.</h3>
          <div class="unit-inner">
          <div data-template="xText" CLASS="pro-text">Ustawa   określa
            zasady   polityki energetycznej.{FOOTNOTE}</div></div></div>
        <div class="unit unit_pass pro-text false" data-id="pass_2">
          <h3>2.</h3>
          <div class="unit-inner">
          <div data-template="xText" CLASS="pro-text">
            Powierzchnia 5 m<sup>2</sup> jest objęta.</div>
          <div class="unit unit_pint pro-text false" data-id="pint_1">
            <h3>1)</h3>
            <div class="unit-inner">
            <div data-template="xText" CLASS="pro-text">pierwszy punkt;</div></div></div>
        </div></div>
      </div>
    </div>
    <div class="unit unit_arti pro-text false" data-id="arti_5a_1">
      <h3 CLASS="pro-none"><B CLASS="b">Art.&nbsp;5a<sup>1</sup>.</B>{FOOTNOTE}</h3>
      <div class="unit-inner">
        <div data-template="xText" CLASS="pro-text">
          Sprzedawca oferuje umowę z ceną dynamiczną.</div>
        <DIV CLASS="cite-box pro-cite-text"><div class="cite-body">
          <div class="unit unit_arti pro-cite-text false" data-id="arti_28"><h3>Art.&nbsp;28.</h3>
            <div data-template="xText">Cytowany przepis innej ustawy.</div></div>
        </div></DIV>
      </div>
    </div>
    <div class="unit unit_arti pro-text false" data-id="arti_7">
      <h3 CLASS="pro-none"><B CLASS="b">Art.&nbsp;7.</B></h3>
      <div class="unit-inner"><div data-template="xText" CLASS="pro-text">(uchylony)</div></div>
    </div>
    <div class="unit unit_arti pro-text false" data-id="arti_8">
      <h3 CLASS="pro-none"><B CLASS="b">Art.&nbsp;8.</B></h3>
      <div class="unit-inner"><div data-template="xText" CLASS="pro-text">(utracił moc)</div></div>
    </div>
  </div>
</div>
"""

ACT_HTML = f"<html><body>{CHAPTER_ONE}{CHAPTER_TWO}</body></html>"


def passages(label: str, *texts: str) -> Article:
    return Article(label, "Rozdział 1. Przepisy ogólne", [f"Art. {label}.", *texts])


def test_article_label_renders_superscripts() -> None:
    assert article_label("arti_5") == "5"
    assert article_label("arti_5a") == "5a"
    assert article_label("arti_5a_1") == "5a¹"
    assert article_label("arti_11_2_3") == "11²³"
    assert article_label("arti_3_10") == "3¹⁰"


def test_parse_articles_reads_labels_chapters_and_text() -> None:
    articles = parse_articles(ACT_HTML)

    assert [(a.label, a.chapter) for a in articles] == [
        ("1", "Rozdział 1. Przepisy ogólne"),
        ("5a¹", "Rozdział 1. Przepisy ogólne"),
        ("9", "Rozdział 2. Taryfy"),
    ]
    assert articles[0].body == (
        "1. Ustawa określa zasady polityki energetycznej.\n"
        "2. Powierzchnia 5 m² jest objęta.\n"
        "1) pierwszy punkt;"
    )


def test_parse_articles_drops_footnotes_cite_boxes_and_repealed_articles() -> None:
    articles = parse_articles(ACT_HTML)
    everything = "\n".join(article.body for article in articles)

    assert "W tym brzmieniu" not in everything
    assert "6)" not in everything
    assert "Cytowany przepis" not in everything
    assert {"28", "7", "8"}.isdisjoint(article.label for article in articles)
    assert articles[1].body == "Sprzedawca oferuje umowę z ceną dynamiczną."


def test_parse_articles_collapses_whitespace() -> None:
    body = parse_articles(ACT_HTML)[0].body

    assert "  " not in body
    assert "\xa0" not in body
    assert "Ustawa określa zasady" in body


def test_short_article_is_one_chunk_without_the_article_prefix() -> None:
    article = parse_articles(ACT_HTML)[1]

    chunks = chunk_article(article)

    assert len(chunks) == 1
    assert chunks[0].article == "5a¹"
    assert chunks[0].heading == "Rozdział 1. Przepisy ogólne > Art. 5a¹"
    assert chunks[0].text == "Sprzedawca oferuje umowę z ceną dynamiczną."


def test_heading_has_no_chapter_part_when_the_article_has_no_chapter() -> None:
    chunks = chunk_article(Article("3", "", ["Art. 3.", "1. tekst"]))

    assert chunks[0].heading == "Art. 3"


def test_first_passage_loses_the_article_prefix_even_inside_its_own_segment() -> None:
    chunks = chunk_article(Article("4", "", ["Art. 4. 1. Pierwszy ustęp.", "2. Drugi."]))

    assert chunks[0].text == "1. Pierwszy ustęp.\n2. Drugi."


def test_long_article_splits_between_passages_and_never_inside_one() -> None:
    texts = [f"{number}. " + "x" * 1000 for number in range(1, 5)]

    chunks = chunk_article(passages("12", *texts))

    assert len(chunks) == 2
    assert [chunk.text for chunk in chunks] == ["\n".join(texts[:2]), "\n".join(texts[2:])]
    assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert {chunk.heading for chunk in chunks} == {"Rozdział 1. Przepisy ogólne > Art. 12"}


def test_passage_over_the_limit_is_split_at_whitespace_and_keeps_every_word() -> None:
    words = [f"slowo{number}" for number in range(1200)]
    huge = "1. " + " ".join(words)
    assert len(huge) > 2 * MAX_CHUNK_CHARS

    chunks = chunk_article(passages("13", "0. krótki", huge))

    assert len(chunks) >= 3
    assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert chunks[0].text == "0. krótki"
    rejoined = " ".join(" ".join(chunk.text.split()) for chunk in chunks)
    assert rejoined == "0. krótki " + huge


def test_split_long_without_spaces_cuts_at_the_limit() -> None:
    pieces = split_long("a" * (MAX_CHUNK_CHARS * 2 + 10))

    assert [len(piece) for piece in pieces] == [MAX_CHUNK_CHARS, MAX_CHUNK_CHARS, 10]
    assert split_long("short") == ["short"]


def test_pack_stores_normalised_float32_vectors() -> None:
    vector = unpack(pack([3.0, 4.0]))

    assert list(vector) == pytest.approx([0.6, 0.8])
    assert sum(value * value for value in vector) == pytest.approx(1.0)
    assert list(unpack(memoryview(pack([0.0, 0.0])))) == [0.0, 0.0]


def add_chunk(position: int, article: str, vector: list[float], act: str = ACT) -> LawChunk:
    return LawChunk.objects.create(
        act=act,
        act_title="Prawo energetyczne",
        position=position,
        article=article,
        heading=f"Art. {article}",
        text=f"tekst {article}",
        embedding=pack(vector),
    )


def test_search_orders_chunks_by_cosine_similarity_and_applies_the_limit() -> None:
    add_chunk(0, "1", [0.0, 1.0, 0.0])
    add_chunk(1, "2", [1.0, 0.0, 0.0])
    add_chunk(2, "3", [1.0, 0.5, 0.0])
    client = FakeLlm(vectorize=lambda _text: [1.0, 0.0, 0.0])

    results = search("taryfa", client)

    assert [chunk.article for chunk, _score in results] == ["2", "3", "1"]
    scores = [score for _chunk, score in results]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(1.0, abs=0.001)
    assert scores[2] == pytest.approx(0.0, abs=0.001)
    assert [chunk.article for chunk, _score in search("taryfa", client, limit=1)] == ["2"]
    assert client.embedded[0] == ["taryfa"]


def test_search_on_an_empty_index_returns_nothing_without_calling_the_model() -> None:
    client = FakeLlm()

    assert search("cokolwiek", client) == []
    assert client.embedded == []


def test_ingest_act_indexes_chunks_in_document_order() -> None:
    client = FakeLlm()

    count = ingest_act(ACT, client, html=ACT_HTML)

    chunks = list(LawChunk.objects.filter(act=ACT))
    assert count == len(chunks) == 3
    assert [(chunk.position, chunk.article) for chunk in chunks] == [(0, "1"), (1, "5a¹"), (2, "9")]
    assert all(chunk.act_title == law.LAW_ACTS[ACT] for chunk in chunks)
    assert len(unpack(chunks[0].embedding)) == 3
    assert client.embedded[0][1] == (
        f"{law.LAW_ACTS[ACT]}\nRozdział 1. Przepisy ogólne > Art. 5a¹\n"
        "Sprzedawca oferuje umowę z ceną dynamiczną."
    )


def test_ingest_act_replaces_previous_chunks_of_the_act_only() -> None:
    other = add_chunk(0, "99", [1.0, 0.0, 0.0], act="DU/2000/1")
    ingest_act(ACT, FakeLlm(), html=ACT_HTML)

    count = ingest_act(ACT, FakeLlm(), html=f"<html><body>{CHAPTER_ONE}</body></html>")

    assert count == 2
    assert list(LawChunk.objects.filter(act=ACT).values_list("article", flat=True)) == ["1", "5a¹"]
    assert LawChunk.objects.filter(pk=other.pk).exists()


def test_failed_embedding_keeps_the_old_index() -> None:
    ingest_act(ACT, FakeLlm(), html=ACT_HTML)

    class Broken(FakeLlm):
        def embed(self, texts: object) -> list[list[float]]:
            raise LlmError("down")

    with pytest.raises(LlmError):
        ingest_act(ACT, Broken(), html=ACT_HTML)

    assert LawChunk.objects.filter(act=ACT).count() == 3


@respx.mock
def test_fetch_act_html_downloads_the_text_from_the_eli_api() -> None:
    route = respx.get("https://eli.test/acts/DU/2024/266/text.html").mock(
        return_value=httpx.Response(200, text="<html>ustawa</html>")
    )

    with override_settings(ELI_API_BASE="https://eli.test/"):
        assert law.fetch_act_html(ACT) == "<html>ustawa</html>"

    assert route.calls.last.request.headers["user-agent"] == law.USER_AGENT


@respx.mock
def test_fetch_act_html_raises_on_http_errors() -> None:
    respx.get("https://eli.test/acts/DU/2024/266/text.html").mock(return_value=httpx.Response(404))

    with override_settings(ELI_API_BASE="https://eli.test"), pytest.raises(httpx.HTTPStatusError):
        law.fetch_act_html(ACT)
