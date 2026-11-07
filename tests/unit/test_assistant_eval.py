from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.assistant.law import DEFAULT_ACT, pack
from apps.assistant.llm import LlmError
from apps.assistant.management.commands import eval_law_search
from apps.assistant.models import LawChunk
from tests.assistant_fakes import FakeLlm

pytestmark = pytest.mark.django_db

CASES = (
    ("taryfa", "phrase A"),
    ("taryfa", "phrase C"),
    ("nic", "absent phrase"),
)
SHIPPED_CASES = eval_law_search.CASES


def add_chunk(position: int, text: str, vector: list[float]) -> LawChunk:
    return LawChunk.objects.create(
        act=DEFAULT_ACT,
        act_title="Prawo energetyczne",
        position=position,
        article=str(position),
        heading=f"Art. {position}",
        text=text,
        embedding=pack(vector),
    )


@pytest.fixture
def small_eval(monkeypatch: pytest.MonkeyPatch) -> FakeLlm:
    fake = FakeLlm()
    monkeypatch.setattr(eval_law_search, "LlmClient", lambda: fake)
    monkeypatch.setattr(eval_law_search, "CASES", CASES)
    return fake


@pytest.fixture
def index() -> None:
    add_chunk(0, "phrase A", [1.0, 0.0, 0.1])
    add_chunk(1, "phrase C", [0.6, 0.0, 0.1])
    add_chunk(2, "other", [0.0, 0.0, 1.0])


def run_eval(*args: str) -> list[str]:
    out = StringIO()
    call_command("eval_law_search", *args, stdout=out)
    return out.getvalue().splitlines()


@pytest.mark.usefixtures("small_eval", "index")
def test_eval_prints_the_rank_of_each_case_and_dashes_for_misses() -> None:
    lines = run_eval()

    assert lines[:3] == ["  1  taryfa", "  2  taryfa", "  -  nic"]


@pytest.mark.usefixtures("small_eval", "index")
def test_eval_reports_recall_and_mrr_for_the_default_k() -> None:
    assert run_eval()[3] == "recall@4: 2/3   MRR@20: 0.50"


@pytest.mark.usefixtures("small_eval", "index")
def test_eval_k_option_changes_the_recall_cutoff() -> None:
    assert run_eval("--k", "1")[3] == "recall@1: 1/3   MRR@20: 0.50"
    assert run_eval("--k", "2")[3] == "recall@2: 2/3   MRR@20: 0.50"


@pytest.mark.usefixtures("small_eval")
def test_eval_with_an_empty_index_is_a_command_error() -> None:
    with pytest.raises(CommandError, match="law index is empty"):
        call_command("eval_law_search")


@pytest.mark.usefixtures("index")
def test_eval_turns_llm_errors_into_command_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(FakeLlm):
        def embed(self, texts: object) -> list[list[float]]:
            raise LlmError("embedding server down")

    monkeypatch.setattr(eval_law_search, "LlmClient", lambda: Broken())
    monkeypatch.setattr(eval_law_search, "CASES", CASES)

    with pytest.raises(CommandError, match="embedding server down"):
        call_command("eval_law_search")


@pytest.mark.usefixtures("index")
def test_eval_turns_an_index_mismatch_into_a_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_law_search, "LlmClient", lambda: FakeLlm(vectorize=lambda _t: [1.0]))
    monkeypatch.setattr(eval_law_search, "CASES", CASES)

    with pytest.raises(CommandError, match="different embedding model"):
        call_command("eval_law_search")


def test_the_shipped_cases_are_well_formed() -> None:
    assert len(SHIPPED_CASES) >= 5
    assert all(query.strip() and phrase.strip() for query, phrase in SHIPPED_CASES)
