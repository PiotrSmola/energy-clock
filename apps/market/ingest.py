import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.market.carbon import carbon_intensity
from apps.market.client import PseClient
from apps.market.models import GenerationPoint, IngestRun, PricePoint
from apps.market.timeutils import expected_intervals, parse_pse_datetime

logger = logging.getLogger(__name__)

PRICE_PLACES = Decimal("0.01")
POWER_PLACES = Decimal("0.001")
MAX_DAYS_PER_REQUEST = 7
BULK_BATCH_SIZE = 500

CONVENTIONAL_FIELDS = (
    "gen_jgw_zak_1",
    "gen_jgw_zak_2",
    "gen_jgz_zak_1",
    "gen_jgz_zak_2",
    "gen_jgz_zak_3",
    "gen_jga",
    "gen_jgo",
    "gen_jgm_zak_1",
    "gen_jgm_zak_2",
)

ALL_ENDPOINTS: tuple[str, ...] = (IngestRun.Endpoint.PRICES, IngestRun.Endpoint.GENERATION)


@dataclass(frozen=True, slots=True)
class IngestResult:
    endpoint: str
    business_date: date
    status: str
    records: int
    expected: int
    error: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status == IngestRun.Status.OK


def to_decimal(value: Any, places: Decimal) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(places)


def optional_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    return parse_pse_datetime(str(value))


def price_point_from_row(row: dict[str, Any]) -> PricePoint:
    price = to_decimal(row["csdac_pln"], PRICE_PLACES)
    if price is None:
        raise ValueError(f"Missing price in row {row.get('dtime_utc')}")
    return PricePoint(
        dtime_utc=parse_pse_datetime(row["dtime_utc"]),
        business_date=date.fromisoformat(row["business_date"]),
        price_pln_mwh=price,
        published_at=optional_timestamp(row.get("publication_ts_utc")),
    )


def conventional_generation(row: dict[str, Any]) -> Decimal | None:
    present = [
        value
        for value in (to_decimal(row.get(field), POWER_PLACES) for field in CONVENTIONAL_FIELDS)
        if value is not None
    ]
    if not present:
        return None
    return sum(present, Decimal(0)).quantize(POWER_PLACES)


def generation_point_from_row(row: dict[str, Any]) -> GenerationPoint:
    pv_mw = to_decimal(row.get("gen_fv"), POWER_PLACES)
    wind_mw = to_decimal(row.get("gen_wi"), POWER_PLACES)
    conventional_mw = conventional_generation(row)
    return GenerationPoint(
        dtime_utc=parse_pse_datetime(row["dtime_utc"]),
        business_date=date.fromisoformat(row["business_date"]),
        pv_mw=pv_mw,
        wind_mw=wind_mw,
        conventional_mw=conventional_mw,
        demand_mw=to_decimal(row.get("kse_pow_dem"), POWER_PLACES),
        carbon_g_kwh=carbon_intensity(pv_mw, wind_mw, conventional_mw),
        published_at=optional_timestamp(row.get("publication_ts_utc")),
    )


def store_prices(rows: list[dict[str, Any]]) -> int:
    points = {point.dtime_utc: point for point in map(price_point_from_row, rows)}
    PricePoint.objects.bulk_create(
        list(points.values()),
        update_conflicts=True,
        unique_fields=["dtime_utc"],
        update_fields=["business_date", "price_pln_mwh", "published_at"],
        batch_size=BULK_BATCH_SIZE,
    )
    return len(points)


def store_generation(rows: list[dict[str, Any]]) -> int:
    points = {point.dtime_utc: point for point in map(generation_point_from_row, rows)}
    GenerationPoint.objects.bulk_create(
        list(points.values()),
        update_conflicts=True,
        unique_fields=["dtime_utc"],
        update_fields=[
            "business_date",
            "pv_mw",
            "wind_mw",
            "conventional_mw",
            "demand_mw",
            "carbon_g_kwh",
            "published_at",
        ],
        batch_size=BULK_BATCH_SIZE,
    )
    return len(points)


STORERS: dict[str, Callable[[list[dict[str, Any]]], int]] = {
    IngestRun.Endpoint.PRICES: store_prices,
    IngestRun.Endpoint.GENERATION: store_generation,
}


def stored_count(endpoint: str, day: date) -> int:
    if endpoint == IngestRun.Endpoint.PRICES:
        return PricePoint.objects.filter(business_date=day).count()
    return GenerationPoint.objects.filter(business_date=day).count()


def is_day_complete(endpoint: str, day: date) -> bool:
    return stored_count(endpoint, day) == expected_intervals(day)


def record_run(
    endpoint: str,
    day: date,
    status: str,
    records: int,
    expected: int,
    started_at: datetime,
    error: str = "",
) -> IngestResult:
    IngestRun.objects.create(
        endpoint=endpoint,
        business_date=day,
        status=status,
        records=records,
        expected_records=expected,
        started_at=started_at,
        finished_at=timezone.now(),
        error=error,
    )
    return IngestResult(endpoint, day, status, records, expected, error)


def status_for(records: int, expected: int) -> str:
    if records == 0:
        return IngestRun.Status.PENDING
    if records == expected:
        return IngestRun.Status.OK
    return IngestRun.Status.PARTIAL


def store_day(
    endpoint: str, day: date, rows: list[dict[str, Any]], started_at: datetime
) -> IngestResult:
    expected = expected_intervals(day)
    try:
        records = STORERS[endpoint](rows)
    except Exception as exc:
        logger.exception("Failed to store %s for %s", endpoint, day)
        return record_run(endpoint, day, IngestRun.Status.FAILED, 0, expected, started_at, str(exc))
    status = status_for(records, expected)
    if status != IngestRun.Status.OK:
        logger.warning(
            "%s for %s stored %d of %d intervals (%s)", endpoint, day, records, expected, status
        )
    return record_run(endpoint, day, status, records, expected, started_at)


def resolve_client(client: PseClient | None) -> AbstractContextManager[PseClient]:
    if client is not None:
        return nullcontext(client)
    return PseClient()


def ingest_day(endpoint: str, day: date, client: PseClient | None = None) -> IngestResult:
    started_at = timezone.now()
    with resolve_client(client) as pse:
        try:
            rows = pse.fetch_day(endpoint, day)
        except Exception as exc:
            logger.exception("Failed to fetch %s for %s", endpoint, day)
            return record_run(
                endpoint,
                day,
                IngestRun.Status.FAILED,
                0,
                expected_intervals(day),
                started_at,
                str(exc),
            )
    return store_day(endpoint, day, rows, started_at)


def ingest_prices(day: date, client: PseClient | None = None) -> IngestResult:
    return ingest_day(IngestRun.Endpoint.PRICES, day, client)


def ingest_generation(day: date, client: PseClient | None = None) -> IngestResult:
    return ingest_day(IngestRun.Endpoint.GENERATION, day, client)


def iter_days(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def consecutive_chunks(days: list[date], max_length: int) -> Iterator[list[date]]:
    chunk: list[date] = []
    for day in days:
        starts_new_chunk = bool(chunk) and (
            day - chunk[-1] != timedelta(days=1) or len(chunk) >= max_length
        )
        if starts_new_chunk:
            yield chunk
            chunk = []
        chunk.append(day)
    if chunk:
        yield chunk


def ingest_chunk(endpoint: str, days: list[date], client: PseClient) -> list[IngestResult]:
    started_at = timezone.now()
    try:
        rows = list(client.fetch_range(endpoint, days[0], days[-1]))
    except Exception as exc:
        logger.exception("Failed to fetch %s for %s..%s", endpoint, days[0], days[-1])
        return [
            record_run(
                endpoint,
                day,
                IngestRun.Status.FAILED,
                0,
                expected_intervals(day),
                started_at,
                str(exc),
            )
            for day in days
        ]
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[date.fromisoformat(row["business_date"])].append(row)
    return [store_day(endpoint, day, grouped.get(day, []), started_at) for day in days]


def ingest_range(
    start: date,
    end: date,
    *,
    endpoints: Iterable[str] = ALL_ENDPOINTS,
    client: PseClient | None = None,
    skip_complete: bool = False,
    pause_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    on_result: Callable[[IngestResult], None] | None = None,
) -> list[IngestResult]:
    results: list[IngestResult] = []
    with resolve_client(client) as pse:
        for endpoint in endpoints:
            days = [
                day
                for day in iter_days(start, end)
                if not (skip_complete and is_day_complete(endpoint, day))
            ]
            for chunk in consecutive_chunks(days, MAX_DAYS_PER_REQUEST):
                for result in ingest_chunk(endpoint, chunk, pse):
                    results.append(result)
                    if on_result is not None:
                        on_result(result)
                if pause_seconds > 0:
                    sleep(pause_seconds)
    return results
