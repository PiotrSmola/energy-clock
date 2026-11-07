from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.assistant.law import SEARCH_RESULTS, LawIndexError, search
from apps.assistant.llm import LlmClient, LlmError

# (query, a phrase the right chunk must contain); everyday wording on purpose, the way
# the model phrases its searches, next to the wording of the act
CASES = (
    ("obowiązek oferowania taryfy dynamicznej przez dostawcę energii", "co najmniej 200 000"),
    ("oferta umowy z ceną dynamiczną prąd", "co najmniej 200 000"),
    ("czy sprzedawca musi oferować umowę z ceną dynamiczną", "co najmniej 200 000"),
    ("dynamic electricity price contract obligation supplier", "co najmniej 200 000"),
    ("zmiana sprzedawcy termin", "zmianę sprzedawcy"),
    ("ile trwa zmiana dostawcy prądu", "zmianę sprzedawcy"),
    ("licznik zdalnego odczytu umowa z ceną dynamiczną", "licznik zdalnego odczytu"),
    ("rozliczenie po zmianie sprzedawcy 42 dni", "42 dni"),
)
DEPTH = 20


class Command(BaseCommand):
    help = "Measure law retrieval against fixed questions: rank of the right chunk, recall, MRR"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--k", type=int, default=SEARCH_RESULTS)

    def handle(self, *args: Any, **options: Any) -> None:
        k: int = options["k"]
        client = LlmClient()
        ranks: list[int | None] = []
        for query, phrase in CASES:
            try:
                results = search(query, client, limit=DEPTH)
            except (LlmError, LawIndexError) as exc:
                raise CommandError(str(exc)) from exc
            if not results:
                raise CommandError("The law index is empty; run ingest_law first")
            rank = next(
                (
                    position
                    for position, (chunk, _s) in enumerate(results, 1)
                    if phrase in chunk.text
                ),
                None,
            )
            ranks.append(rank)
            shown = "-" if rank is None else str(rank)
            self.stdout.write(f"{shown:>3}  {query}")
        found = sum(1 for rank in ranks if rank is not None and rank <= k)
        mrr = sum(1 / rank for rank in ranks if rank is not None) / len(ranks)
        self.stdout.write(
            self.style.SUCCESS(f"recall@{k}: {found}/{len(ranks)}   MRR@{DEPTH}: {mrr:.2f}")
        )
