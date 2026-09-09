from collections.abc import Callable
from datetime import date
from io import StringIO
from typing import Any

import httpx
import pytest
import respx
from django.conf import settings
from django.core.management import call_command

from apps.market.models import GenerationPoint, IngestRun, PricePoint
from tests.pse_fixtures import generation_rows, price_rows

pytestmark = pytest.mark.django_db

PRICES_URL = f"{settings.PSE_API_BASE}/csdac-pln"
GENERATION_URL = f"{settings.PSE_API_BASE}/pdgobpkd"

type RowsFactory = Callable[[date], list[dict[str, Any]]]


def run_backfill(*args: str) -> str:
    output = StringIO()
    call_command("backfill", *args, "--delay", "0", stdout=output, stderr=StringIO())
    return output.getvalue()


def mock_all_days(url: str, days: list[date], rows_for: RowsFactory) -> respx.Route:
    rows = [row for day in days for row in rows_for(day)]
    return respx.get(url).mock(return_value=httpx.Response(200, json={"value": rows}))


@respx.mock
def test_backfill_range_stores_prices_and_generation() -> None:
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    mock_all_days(PRICES_URL, days, price_rows)
    mock_all_days(GENERATION_URL, days, generation_rows)

    output = run_backfill("--start", "2026-09-01", "--end", "2026-09-03")

    assert PricePoint.objects.count() == 288
    assert GenerationPoint.objects.count() == 288
    assert "records in database 288" in output
    assert "Backfill finished" in output


@respx.mock
def test_backfill_is_idempotent_and_skips_complete_days() -> None:
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    prices = mock_all_days(PRICES_URL, days, price_rows)

    run_backfill("--start", "2026-09-01", "--end", "2026-09-03", "--endpoint", "prices")
    output = run_backfill("--start", "2026-09-01", "--end", "2026-09-03", "--endpoint", "prices")

    assert PricePoint.objects.count() == 288
    assert prices.call_count == 1
    assert "days skipped        3" in output
    assert "records in database 288" in output


@respx.mock
def test_backfill_force_refetches_complete_days() -> None:
    days = [date(2026, 9, 1)]
    prices = mock_all_days(PRICES_URL, days, price_rows)

    run_backfill("--start", "2026-09-01", "--end", "2026-09-01", "--endpoint", "prices")
    run_backfill("--start", "2026-09-01", "--end", "2026-09-01", "--endpoint", "prices", "--force")

    assert prices.call_count == 2
    assert PricePoint.objects.count() == 96


@respx.mock
def test_backfill_handles_dst_days_with_92_and_100_intervals() -> None:
    spring = [date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30)]
    mock_all_days(PRICES_URL, spring, price_rows)

    output = run_backfill("--start", "2026-03-28", "--end", "2026-03-30", "--endpoint", "prices")

    assert PricePoint.objects.count() == 284
    assert "records in database 284" in output
    assert IngestRun.objects.filter(status=IngestRun.Status.OK).count() == 3


@respx.mock
def test_backfill_clamps_start_to_market_data_start() -> None:
    respx.get(PRICES_URL).mock(return_value=httpx.Response(200, json={"value": []}))

    output = run_backfill("--start", "2024-06-01", "--end", "2024-06-14", "--endpoint", "prices")

    assert "clamping start" in output
    assert "days requested      1" in output


@respx.mock
def test_backfill_lists_days_needing_attention() -> None:
    respx.get(PRICES_URL).mock(return_value=httpx.Response(200, json={"value": []}))

    output = run_backfill("--start", "2026-09-05", "--end", "2026-09-05", "--endpoint", "prices")

    assert "Days needing attention: 1" in output
    assert "csdac-pln 2026-09-05 PENDING 0/96" in output


def test_backfill_rejects_reversed_range() -> None:
    with pytest.raises(Exception, match="is before"):
        run_backfill("--start", "2026-09-05", "--end", "2026-09-01")
