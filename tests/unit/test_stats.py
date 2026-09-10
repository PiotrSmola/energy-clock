from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.market.models import PricePoint
from apps.market.stats import (
    average_price_by_hour,
    average_price_by_weekday,
    negative_prices_by_month,
    price_overview,
    res_share_by_month,
)
from tests.market_fixtures import load_market_day, seed_market_day

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_days() -> None:
    seed_market_day(date(2026, 9, 4))
    seed_market_day(date(2026, 9, 5))


def fixture_prices() -> list[tuple[datetime, Decimal]]:
    return [
        (
            datetime.fromisoformat(row["dtime_utc"]).replace(tzinfo=UTC),
            Decimal(row["price_pln_mwh"]),
        )
        for day in (date(2026, 9, 4), date(2026, 9, 5))
        for row in load_market_day(day)["prices"]
    ]


def fixture_hour_average(hour: int) -> Decimal:
    prices = [
        price
        for end_utc, price in fixture_prices()
        if (end_utc.hour + 2 - (1 if end_utc.minute == 0 else 0)) % 24 == hour
    ]
    return (sum(prices, Decimal(0)) / len(prices)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fixture_negative_count() -> int:
    return sum(1 for _end_utc, price in fixture_prices() if price < 0)


def test_hourly_averages_are_grouped_by_local_start_hour(two_days: None) -> None:
    with CaptureQueriesContext(connection) as queries:
        hours = average_price_by_hour()

    assert len(hours) == 24
    assert all(stat.intervals == 8 for stat in hours)
    assert hours[16].avg_price == fixture_hour_average(16)
    assert hours[0].avg_price == fixture_hour_average(0)
    assert hours[23].avg_price == fixture_hour_average(23)
    assert len(queries) == 1
    assert "GROUP BY" in queries[0]["sql"]


def test_weekday_averages_use_business_date(two_days: None) -> None:
    with CaptureQueriesContext(connection) as queries:
        weekdays = average_price_by_weekday()

    assert [stat.iso_weekday for stat in weekdays] == [5, 6]
    assert all(stat.intervals == 96 for stat in weekdays)
    assert len(queries) == 1
    assert "GROUP BY" in queries[0]["sql"]


def test_res_share_by_month_is_a_single_grouped_query(two_days: None) -> None:
    with CaptureQueriesContext(connection) as queries:
        months = res_share_by_month()

    assert len(months) == 1
    assert months[0].month == date(2026, 9, 1)
    assert months[0].intervals == 192
    assert months[0].res_share_percent is not None
    assert Decimal("0") < months[0].res_share_percent < Decimal("100")
    assert len(queries) == 1
    assert "GROUP BY" in queries[0]["sql"]


def test_negative_price_intervals_are_counted_per_month(two_days: None) -> None:
    PricePoint.objects.filter(dtime_utc=datetime(2026, 9, 4, 10, 15, tzinfo=UTC)).update(
        price_pln_mwh=Decimal("-1250.00")
    )
    expected_negative = fixture_negative_count() + 1

    with CaptureQueriesContext(connection) as queries:
        months = negative_prices_by_month()
    overview = price_overview()

    assert expected_negative == 11
    assert months[0].negative_intervals == expected_negative
    assert months[0].intervals == 192
    assert overview.negative_intervals == expected_negative
    assert overview.min_price == Decimal("-1250.00")
    assert overview.intervals == 192
    assert (overview.first_day, overview.last_day) == (date(2026, 9, 4), date(2026, 9, 5))
    assert len(queries) == 1


def test_period_filter_limits_the_scope(two_days: None) -> None:
    hours = average_price_by_hour(start=date(2026, 9, 5))

    assert all(stat.intervals == 4 for stat in hours)
    assert price_overview(start=date(2026, 9, 5)).intervals == 96


def test_empty_database_gives_empty_stats() -> None:
    assert average_price_by_hour() == []
    assert average_price_by_weekday() == []
    assert res_share_by_month() == []
    assert negative_prices_by_month() == []
    assert price_overview().intervals == 0
