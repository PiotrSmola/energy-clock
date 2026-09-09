from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from apps.market.client import PseClient
from apps.market.ingest import (
    ingest_generation,
    ingest_prices,
    ingest_range,
    is_day_complete,
)
from apps.market.models import GenerationPoint, IngestRun, PricePoint
from tests.pse_fixtures import generation_rows, price_rows

BASE_URL = "https://api.raporty.pse.pl/api"
PRICES_URL = f"{BASE_URL}/csdac-pln"
GENERATION_URL = f"{BASE_URL}/pdgobpkd"

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Iterator[PseClient]:
    with PseClient(BASE_URL, backoff_seconds=0, sleep=lambda _: None) as pse:
        yield pse


def mock_day(url: str, day: date, rows: list[dict[str, Any]]) -> respx.Route:
    return respx.get(
        url, params__contains={"$filter": f"business_date eq '{day.isoformat()}'"}
    ).mock(return_value=httpx.Response(200, json={"value": rows}))


def mock_range(url: str, start: date, end: date, rows: list[dict[str, Any]]) -> respx.Route:
    filter_expr = f"business_date ge '{start.isoformat()}' and business_date le '{end.isoformat()}'"
    return respx.get(url, params__contains={"$filter": filter_expr}).mock(
        return_value=httpx.Response(200, json={"value": rows})
    )


@respx.mock
def test_ingest_prices_stores_a_full_day(client: PseClient) -> None:
    day = date(2026, 9, 4)
    mock_day(PRICES_URL, day, price_rows(day, price_for=lambda index: 717.86 + index))

    result = ingest_prices(day, client=client)

    assert result.status == IngestRun.Status.OK
    assert result.records == 96
    assert result.expected == 96
    assert PricePoint.objects.filter(business_date=day).count() == 96
    first = PricePoint.objects.order_by("dtime_utc").first()
    assert first is not None
    assert first.dtime_utc == datetime(2026, 9, 3, 22, 15, tzinfo=UTC)
    assert first.price_pln_mwh == Decimal("717.86")
    assert first.published_at == datetime(2026, 9, 3, 11, 53, 17, 304000, tzinfo=UTC)
    run = IngestRun.objects.get()
    assert run.endpoint == IngestRun.Endpoint.PRICES
    assert run.status == IngestRun.Status.OK
    assert run.records == 96
    assert run.expected_records == 96
    assert run.finished_at is not None


@respx.mock
def test_ingest_prices_is_idempotent_and_updates_changed_values(client: PseClient) -> None:
    day = date(2026, 9, 4)
    route = mock_day(PRICES_URL, day, price_rows(day, price_for=lambda index: 500.0))

    ingest_prices(day, client=client)
    route.mock(
        return_value=httpx.Response(
            200, json={"value": price_rows(day, price_for=lambda index: 600.0)}
        )
    )
    second = ingest_prices(day, client=client)

    assert second.status == IngestRun.Status.OK
    assert PricePoint.objects.count() == 96
    assert set(PricePoint.objects.values_list("price_pln_mwh", flat=True)) == {Decimal("600.00")}
    assert IngestRun.objects.count() == 2


@pytest.mark.parametrize(("day", "expected"), [(date(2026, 3, 29), 92), (date(2025, 10, 26), 100)])
@respx.mock
def test_ingest_prices_accepts_dst_transition_days(
    client: PseClient, day: date, expected: int
) -> None:
    mock_day(PRICES_URL, day, price_rows(day))

    result = ingest_prices(day, client=client)

    assert result.status == IngestRun.Status.OK
    assert result.records == expected
    assert PricePoint.objects.filter(business_date=day).count() == expected
    assert (
        PricePoint.objects.filter(business_date=day).values("dtime_utc").distinct().count()
        == expected
    )


@respx.mock
def test_ingest_prices_marks_incomplete_day_as_partial(client: PseClient) -> None:
    day = date(2026, 9, 4)
    mock_day(PRICES_URL, day, price_rows(day, count=90))

    result = ingest_prices(day, client=client)

    assert result.status == IngestRun.Status.PARTIAL
    assert result.records == 90
    assert result.expected == 96
    assert IngestRun.objects.get().status == IngestRun.Status.PARTIAL


@respx.mock
def test_ingest_prices_reports_pending_when_nothing_published_yet(client: PseClient) -> None:
    day = date(2026, 9, 5)
    mock_day(PRICES_URL, day, [])

    result = ingest_prices(day, client=client)

    assert result.status == IngestRun.Status.PENDING
    assert result.records == 0
    assert PricePoint.objects.count() == 0
    assert IngestRun.objects.get().status == IngestRun.Status.PENDING


@respx.mock
def test_ingest_prices_records_api_failure_without_raising(client: PseClient) -> None:
    day = date(2026, 9, 4)
    respx.get(PRICES_URL).mock(return_value=httpx.Response(500))

    result = ingest_prices(day, client=client)

    assert result.status == IngestRun.Status.FAILED
    assert "HTTP 500" in result.error
    run = IngestRun.objects.get()
    assert run.status == IngestRun.Status.FAILED
    assert "HTTP 500" in run.error


@respx.mock
def test_ingest_prices_collapses_duplicate_intervals_in_one_response(client: PseClient) -> None:
    day = date(2026, 9, 4)
    rows = price_rows(day)
    mock_day(PRICES_URL, day, rows + rows[:5])

    result = ingest_prices(day, client=client)

    assert result.status == IngestRun.Status.OK
    assert result.records == 96
    assert PricePoint.objects.count() == 96


@respx.mock
def test_ingest_generation_computes_conventional_sum_and_carbon(client: PseClient) -> None:
    day = date(2026, 9, 4)
    mock_day(
        GENERATION_URL,
        day,
        generation_rows(
            day,
            pv_for=lambda index: 1000.0,
            wind_for=lambda index: 1000.0,
            conventional={"gen_jgw_zak_1": 1500.0, "gen_jgz_zak_2": 500.0, "gen_jga": None},
            demand_for=lambda index: 15650.0,
        ),
    )

    result = ingest_generation(day, client=client)

    assert result.status == IngestRun.Status.OK
    assert result.records == 96
    point = GenerationPoint.objects.order_by("dtime_utc").first()
    assert point is not None
    assert point.pv_mw == Decimal("1000.000")
    assert point.wind_mw == Decimal("1000.000")
    assert point.conventional_mw == Decimal("2000.000")
    assert point.demand_mw == Decimal("15650.000")
    assert point.carbon_g_kwh == Decimal("375.0")
    assert point.published_at == datetime(2026, 9, 3, 20, 54, 41, 687000, tzinfo=UTC)


@respx.mock
def test_ingest_generation_keeps_missing_conventional_as_none(client: PseClient) -> None:
    day = date(2026, 9, 4)
    mock_day(
        GENERATION_URL,
        day,
        generation_rows(
            day,
            pv_for=lambda index: 100.0,
            wind_for=lambda index: None,
            conventional={},
            demand_for=lambda index: None,
        ),
    )

    ingest_generation(day, client=client)

    point = GenerationPoint.objects.order_by("dtime_utc").first()
    assert point is not None
    assert point.wind_mw is None
    assert point.conventional_mw is None
    assert point.demand_mw is None
    assert point.carbon_g_kwh == Decimal("0.0")


@respx.mock
def test_ingest_range_covers_every_day_and_endpoint(client: PseClient) -> None:
    start, end = date(2026, 9, 1), date(2026, 9, 3)
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    mock_range(PRICES_URL, start, end, [row for day in days for row in price_rows(day)])
    mock_range(GENERATION_URL, start, end, [row for day in days for row in generation_rows(day)])

    results = ingest_range(start, end, client=client)

    assert len(results) == 6
    assert {result.status for result in results} == {IngestRun.Status.OK}
    assert PricePoint.objects.count() == 288
    assert GenerationPoint.objects.count() == 288
    assert IngestRun.objects.count() == 6


@respx.mock
def test_ingest_range_skips_complete_days_and_splits_chunks(client: PseClient) -> None:
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    mock_day(PRICES_URL, days[1], price_rows(days[1]))
    ingest_prices(days[1], client=client)
    first_chunk = mock_range(PRICES_URL, days[0], days[0], price_rows(days[0]))
    last_chunk = mock_range(PRICES_URL, days[2], days[2], price_rows(days[2]))

    results = ingest_range(
        days[0], days[2], endpoints=(IngestRun.Endpoint.PRICES,), client=client, skip_complete=True
    )

    assert [result.business_date for result in results] == [days[0], days[2]]
    assert first_chunk.call_count == 1
    assert last_chunk.call_count == 1
    assert PricePoint.objects.count() == 288


@respx.mock
def test_ingest_range_marks_missing_days_pending_and_reports_progress(client: PseClient) -> None:
    start, end = date(2026, 9, 4), date(2026, 9, 5)
    mock_range(PRICES_URL, start, end, price_rows(start))
    seen: list[date] = []

    results = ingest_range(
        start,
        end,
        endpoints=(IngestRun.Endpoint.PRICES,),
        client=client,
        on_result=lambda result: seen.append(result.business_date),
    )

    assert [result.status for result in results] == [IngestRun.Status.OK, IngestRun.Status.PENDING]
    assert seen == [start, end]


@respx.mock
def test_ingest_range_records_failure_for_every_day_in_chunk(client: PseClient) -> None:
    respx.get(PRICES_URL).mock(return_value=httpx.Response(503))

    results = ingest_range(
        date(2026, 9, 1), date(2026, 9, 2), endpoints=(IngestRun.Endpoint.PRICES,), client=client
    )

    assert [result.status for result in results] == [IngestRun.Status.FAILED] * 2
    assert IngestRun.objects.filter(status=IngestRun.Status.FAILED).count() == 2


@respx.mock
def test_is_day_complete_compares_with_expected_intervals(client: PseClient) -> None:
    dst_day = date(2026, 3, 29)
    assert is_day_complete(IngestRun.Endpoint.PRICES, dst_day) is False
    mock_day(PRICES_URL, dst_day, price_rows(dst_day))
    ingest_prices(dst_day, client=client)

    assert is_day_complete(IngestRun.Endpoint.PRICES, dst_day) is True
    assert is_day_complete(IngestRun.Endpoint.GENERATION, dst_day) is False
