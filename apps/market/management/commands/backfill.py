from collections import Counter
from datetime import date, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from tqdm import tqdm

from apps.market.constants import MARKET_DATA_START
from apps.market.ingest import ALL_ENDPOINTS, IngestResult, ingest_range, stored_count
from apps.market.models import IngestRun
from apps.market.timeutils import local_today

ENDPOINT_CHOICES: dict[str, tuple[str, ...]] = {
    "prices": (IngestRun.Endpoint.PRICES,),
    "generation": (IngestRun.Endpoint.GENERATION,),
    "all": ALL_ENDPOINTS,
}
MAX_LISTED_ISSUES = 40


class Command(BaseCommand):
    help = "Backfill PSE day-ahead prices and generation plan into the local database"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--start", type=date.fromisoformat, default=MARKET_DATA_START)
        parser.add_argument("--end", type=date.fromisoformat, default=None)
        parser.add_argument("--endpoint", choices=sorted(ENDPOINT_CHOICES), default="all")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--delay", type=float, default=0.2)

    def handle(self, *args: Any, **options: Any) -> None:
        start: date = options["start"]
        end: date = options["end"] or local_today() + timedelta(days=1)
        if start < MARKET_DATA_START:
            self.stdout.write(
                self.style.WARNING(
                    f"PSE data starts on {MARKET_DATA_START}, clamping start from {start}"
                )
            )
            start = MARKET_DATA_START
        if end < start:
            raise CommandError(f"--end {end} is before --start {start}")
        endpoints = ENDPOINT_CHOICES[options["endpoint"]]
        day_count = (end - start).days + 1
        statuses: Counter[str] = Counter()
        fetched_records: Counter[str] = Counter()
        issues: list[IngestResult] = []

        self.stdout.write(f"Backfill {start}..{end} ({day_count} days) for {', '.join(endpoints)}")
        with tqdm(total=day_count * len(endpoints), unit="day", dynamic_ncols=True) as progress:

            def on_result(result: IngestResult) -> None:
                progress.update(1)
                statuses[f"{result.endpoint}:{result.status}"] += 1
                fetched_records[result.endpoint] += result.records
                if result.status != IngestRun.Status.OK:
                    issues.append(result)

            results = ingest_range(
                start,
                end,
                endpoints=endpoints,
                skip_complete=not options["force"],
                pause_seconds=options["delay"],
                on_result=on_result,
            )
            progress.update(progress.total - progress.n)

        processed = Counter(result.endpoint for result in results)
        self.stdout.write("")
        for endpoint in endpoints:
            skipped = day_count - processed[endpoint]
            stored = sum(
                stored_count(endpoint, start + timedelta(days=offset))
                for offset in range(day_count)
            )
            self.stdout.write(self.style.MIGRATE_HEADING(endpoint))
            self.stdout.write(f"  days requested      {day_count}")
            self.stdout.write(f"  days skipped        {skipped} (already complete)")
            self.stdout.write(f"  days fetched        {processed[endpoint]}")
            for status in IngestRun.Status:
                count = statuses[f"{endpoint}:{status}"]
                if count:
                    self.stdout.write(f"    {status:<8}          {count}")
            self.stdout.write(f"  records fetched     {fetched_records[endpoint]}")
            self.stdout.write(f"  records in database {stored}")
        if issues:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"Days needing attention: {len(issues)}"))
            for result in issues[:MAX_LISTED_ISSUES]:
                detail = f" {result.error}" if result.error else ""
                self.stdout.write(
                    f"  {result.endpoint} {result.business_date} {result.status}"
                    f" {result.records}/{result.expected}{detail}"
                )
            if len(issues) > MAX_LISTED_ISSUES:
                self.stdout.write(f"  ... and {len(issues) - MAX_LISTED_ISSUES} more")
        self.stdout.write(self.style.SUCCESS("Backfill finished"))
