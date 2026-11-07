import time
from typing import Any

import httpx
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.assistant.law import DEFAULT_ACT, LAW_ACTS, LawIndexError, ingest_act
from apps.assistant.llm import LlmClient, LlmError


class Command(BaseCommand):
    help = "Download an act from the Sejm ELI API, chunk it by article and index its embeddings"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--act", choices=sorted(LAW_ACTS), default=DEFAULT_ACT)

    def handle(self, *args: Any, **options: Any) -> None:
        act: str = options["act"]
        client = LlmClient()
        self.stdout.write(f"{LAW_ACTS[act]}: embedding with {client.embedding_model}")
        started = time.monotonic()
        try:
            count = ingest_act(act, client)
        except httpx.HTTPError as exc:
            raise CommandError(f"Could not download {act} from the ELI API: {exc}") from exc
        except LawIndexError as exc:
            raise CommandError(str(exc)) from exc
        except LlmError as exc:
            raise CommandError(f"Embedding model is not reachable: {exc}") from exc
        elapsed = time.monotonic() - started
        self.stdout.write(self.style.SUCCESS(f"Indexed {count} chunks in {elapsed:.1f}s"))
