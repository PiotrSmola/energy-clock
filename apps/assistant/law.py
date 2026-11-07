import math
import re
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx
from django.conf import settings
from django.db import transaction

from apps.assistant.llm import LlmClient
from apps.assistant.models import LawChunk

USER_AGENT = "energy-clock/1.0"
FETCH_TIMEOUT_SECONDS = 60.0
MAX_CHUNK_CHARS = 2400
SEARCH_RESULTS = 4
HEADING_SEPARATOR = " > "

# acts the assistant can search; texts are downloaded from the Sejm ELI API, never committed
LAW_ACTS = {
    "DU/2024/266": "Prawo energetyczne, tekst jednolity Dz.U. 2024 poz. 266",
}
DEFAULT_ACT = "DU/2024/266"

SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
REPEALED = re.compile(
    r"^\(?\s*(uchylon[ya]|uchylone|pominięt[ya]|pominięte|utracił moc)\s*\)?\.?$", re.IGNORECASE
)
SPACES = re.compile(r"\s+")
CHAPTER_NUMBER = re.compile(r"^(Rozdział \S+) ")


@dataclass
class Article:
    label: str
    chapter: str
    segments: list[str] = field(default_factory=lambda: [""])

    @property
    def body(self) -> str:
        text = "\n".join(clean(segment) for segment in self.segments if clean(segment))
        heading = f"Art. {self.label}."
        return text.removeprefix(heading).strip()


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    article: str
    heading: str
    text: str


def clean(text: str) -> str:
    return SPACES.sub(" ", text).strip()


def article_label(data_id: str) -> str:
    # "arti_5a_1" is art. 5a¹ in the printed act
    number, *upper = data_id.removeprefix("arti_").split("_")
    return number + "".join(part.translate(SUPERSCRIPT) for part in upper)


class ActParser(HTMLParser):
    """Collects the articles of an act from the ELI HTML.

    Skipped: provisions of other acts quoted in the announcement (cite boxes) and editorial
    footnotes, which the HTML inlines as tooltips in the middle of the text.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articles: list[Article] = []
        self._divs: list[str] = []
        self._chapter = ""
        self._chapter_parts: list[str] = []
        self._in_heading = False
        self._in_sup = False
        self._in_footnote = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "div":
            kind = self._kind(classes)
            if kind == "article" and not self._in_cite:
                self.articles.append(
                    Article(article_label(attributes.get("data-id") or ""), self._chapter)
                )
            elif kind == "chapter" and not self._in_cite:
                self._chapter_parts = []
            elif kind == "passage" and self._article is not None:
                self._article.segments.append("")
            self._divs.append(kind)
        elif tag == "h3" and self._innermost_unit == "chapter":
            self._in_heading = True
        elif tag == "sup":
            self._in_sup = True
        elif tag == "a" and "gloss-link" in classes:
            self._in_footnote = True
        elif tag in {"p", "br"}:
            self._append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._divs:
            self._divs.pop()
        elif tag == "h3" and self._in_heading:
            self._in_heading = False
            self._chapter = CHAPTER_NUMBER.sub(r"\1. ", clean(" ".join(self._chapter_parts)))
        elif tag == "sup":
            self._in_sup = False
        elif tag == "a":
            self._in_footnote = False

    def handle_data(self, data: str) -> None:
        if self._in_cite or self._in_footnote:
            return
        text = data.translate(SUPERSCRIPT) if self._in_sup else data
        if self._in_heading:
            self._chapter_parts.append(text)
        else:
            self._append(text)

    def _append(self, text: str) -> None:
        article = self._article
        if article is not None and not self._in_cite:
            article.segments[-1] += text

    @staticmethod
    def _kind(classes: list[str]) -> str:
        if "cite-box" in classes or "pro-cite-text" in classes:
            return "cite"
        if "unit_arti" in classes:
            return "article"
        if "unit_chpt" in classes:
            return "chapter"
        if "unit_pass" in classes or "unit_pint" in classes:
            return "passage"
        return "other"

    @property
    def _in_cite(self) -> bool:
        return "cite" in self._divs

    @property
    def _innermost_unit(self) -> str | None:
        for kind in reversed(self._divs):
            if kind in {"article", "chapter"}:
                return kind
        return None

    @property
    def _article(self) -> Article | None:
        if "article" not in self._divs or not self.articles:
            return None
        return self.articles[-1]


def parse_articles(html: str) -> list[Article]:
    parser = ActParser()
    parser.feed(html)
    parser.close()
    return [article for article in parser.articles if not REPEALED.match(article.body)]


def chunk_article(article: Article) -> list[ChunkDraft]:
    """One chunk per article; long ones are split between passages (ust.) or points (pkt)."""
    heading = f"Art. {article.label}"
    if article.chapter:
        heading = f"{article.chapter}{HEADING_SEPARATOR}{heading}"
    passages = [clean(segment) for segment in article.segments if clean(segment)]
    if passages:
        passages[0] = passages[0].removeprefix(f"Art. {article.label}.").strip()
    passages = [passage for passage in passages if passage]
    chunks: list[ChunkDraft] = []
    current: list[str] = []
    for passage in passages:
        for piece in split_long(passage):
            if current and len("\n".join([*current, piece])) > MAX_CHUNK_CHARS:
                chunks.append(ChunkDraft(article.label, heading, "\n".join(current)))
                current = []
            current.append(piece)
    if current:
        chunks.append(ChunkDraft(article.label, heading, "\n".join(current)))
    return chunks


def split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    pieces: list[str] = []
    while len(text) > MAX_CHUNK_CHARS:
        cut = text.rfind(" ", 0, MAX_CHUNK_CHARS)
        cut = cut if cut > 0 else MAX_CHUNK_CHARS
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


def embedding_text(act_title: str, draft: ChunkDraft) -> str:
    # the heading travels with the text, so a passage like "ust. 2" still says what it belongs to
    return f"{act_title}\n{draft.heading}\n{draft.text}"


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def pack(vector: Sequence[float]) -> bytes:
    return array("f", normalize(vector)).tobytes()


def unpack(blob: bytes | memoryview) -> array[float]:
    vector = array("f")
    vector.frombytes(bytes(blob))
    return vector


def fetch_act_html(act: str) -> str:
    url = f"{str(settings.ELI_API_BASE).rstrip('/')}/acts/{act}/text.html"
    response = httpx.get(url, timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.text


def ingest_act(act: str, client: LlmClient, html: str | None = None) -> int:
    act_title = LAW_ACTS[act]
    drafts = [
        draft
        for article in parse_articles(html or fetch_act_html(act))
        for draft in chunk_article(article)
    ]
    vectors = client.embed([embedding_text(act_title, draft) for draft in drafts])
    chunks = [
        LawChunk(
            act=act,
            act_title=act_title,
            position=position,
            article=draft.article,
            heading=draft.heading[:300],
            text=draft.text,
            embedding=pack(vector),
        )
        for position, (draft, vector) in enumerate(zip(drafts, vectors, strict=True))
    ]
    with transaction.atomic():
        LawChunk.objects.filter(act=act).delete()
        LawChunk.objects.bulk_create(chunks, batch_size=200)
    return len(chunks)


def search(
    query: str, client: LlmClient, limit: int = SEARCH_RESULTS
) -> list[tuple[LawChunk, float]]:
    """Brute-force cosine similarity: a few hundred chunks do not need a vector database."""
    stored = list(LawChunk.objects.values_list("id", "embedding"))
    if not stored:
        return []
    query_vector = normalize(client.embed([query])[0])
    scored = sorted(
        (
            (sum(a * b for a, b in zip(query_vector, unpack(blob), strict=True)), chunk_id)
            for chunk_id, blob in stored
        ),
        reverse=True,
    )[:limit]
    chunks = LawChunk.objects.in_bulk([chunk_id for _score, chunk_id in scored])
    return [(chunks[chunk_id], score) for score, chunk_id in scored]
