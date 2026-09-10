import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import time_machine
from django.test import Client
from django.urls import reverse

from apps.market.models import PricePoint
from apps.market.timeutils import INTERVAL, WARSAW, day_bounds_utc, expected_intervals
from tests.market_fixtures import seed_market_day

pytestmark = pytest.mark.django_db

NOON_SEPT_4 = datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW)


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def two_days() -> None:
    seed_market_day(date(2026, 9, 4))
    seed_market_day(date(2026, 9, 5))


def seed_constant_month(year: int, month: int, price: Decimal) -> None:
    day = date(year, month, 1)
    points: list[PricePoint] = []
    while day.month == month:
        start_utc, _ = day_bounds_utc(day)
        points.extend(
            PricePoint(
                dtime_utc=start_utc + (index + 1) * INTERVAL,
                business_date=day,
                price_pln_mwh=price,
            )
            for index in range(expected_intervals(day))
        )
        day += timedelta(days=1)
    PricePoint.objects.bulk_create(points, batch_size=500)


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_mix_page_renders_layers_and_stats(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:mix"))

    body = response.content.decode()
    assert response.status_code == 200
    assert 'class="layer layer--pv"' in body
    assert 'class="layer layer--wind"' in body
    assert 'class="layer layer--conventional"' in body
    assert 'class="carbon-line"' in body
    assert "RES share, day average" in body
    assert "Friday, 4 September 2026" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_mix_page_for_a_day_without_plan_shows_message(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:mix"), {"day": "2026-09-07"})

    body = response.content.decode()
    assert response.status_code == 200
    assert "No generation plan for this day." in body
    assert "layer--pv" not in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_mix_htmx_request_returns_partial_and_invalid_day_falls_back(
    client: Client, two_days: None
) -> None:
    response = client.get(reverse("web:mix"), {"day": "not-a-date"}, HTTP_HX_REQUEST="true")

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body
    assert "4 September 2026" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_compare_shows_diverging_rankings_for_2026_09_04(client: Client, two_days: None) -> None:
    response = client.get(
        reverse("web:compare"),
        {"appliance": "", "duration_minutes": "120", "power_kw": "2.0", "day": "2026-09-04"},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "Cheapest" in body and "Cleanest" in body
    assert "The cleaner window costs" in body
    assert 'class="win win--clean"' in body
    assert body.count('<ol class="rank">') == 2
    cheapest_start = body.split("<h2>Cheapest</h2>")[1].split('class="value num">')[1][:5]
    cleanest_start = body.split("<h2>Cleanest</h2>")[1].split('class="value num">')[1][:5]
    assert cheapest_start != cleanest_start
    assert cheapest_start == "14:45"
    assert cleanest_start == "12:45"


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_compare_result_partial_uses_upcoming_horizon(client: Client, two_days: None) -> None:
    response = client.get(
        reverse("web:compare_result"), {"appliance": "", "duration_minutes": "60", "power_kw": "3"}
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body
    assert "Cheapest" in body
    assert "1 h" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_compare_without_generation_data_explains_missing_clean_ranking(client: Client) -> None:
    seed_market_day(date(2026, 9, 4), with_generation=False)

    body = client.get(reverse("web:compare")).content.decode()

    assert "clean ranking is empty" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_tariff_default_view_computes_last_twelve_months(client: Client) -> None:
    seed_constant_month(2026, 7, Decimal("500"))
    seed_constant_month(2026, 8, Decimal("300"))

    response = client.get(reverse("web:tariff"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "July 2026" in body and "August 2026" in body
    assert "A dynamic tariff would have been" in body
    assert 'class="bars' in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_tariff_result_respects_profile_and_fixed_price(client: Client) -> None:
    seed_constant_month(2026, 8, Decimal("400"))

    response = client.get(
        reverse("web:tariff_result"),
        {
            "monthly_kwh": "300",
            "fixed_price_pln_kwh": "0.30",
            "profile": "night",
            "start": "2026-08-01",
            "end": "2026-08-31",
        },
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body
    assert "Your fixed price would have been" in body
    assert "120.00" in body
    assert "90.00" in body
    assert "Night, 22:00 to 06:00" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_tariff_custom_profile_uses_hourly_levels(client: Client) -> None:
    seed_constant_month(2026, 8, Decimal("400"))
    params = {
        "monthly_kwh": "100",
        "fixed_price_pln_kwh": "1",
        "profile": "custom",
        "start": "2026-08-01",
        "end": "2026-08-31",
    }
    params.update({f"h{hour}": "0" for hour in range(24)})

    body = client.get(reverse("web:tariff_result"), params).content.decode()

    assert "Set at least one hour above zero." in body or "Check the highlighted fields." in body

    params["h3"] = "5"
    body = client.get(reverse("web:tariff_result"), params).content.decode()

    assert "40.00" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_tariff_full_year_runs_under_two_seconds(client: Client) -> None:
    for month in range(1, 13):
        seed_constant_month(2025, month, Decimal("450"))

    started = time.perf_counter()
    response = client.get(
        reverse("web:tariff_result"),
        {
            "monthly_kwh": "250",
            "fixed_price_pln_kwh": "0.9",
            "profile": "day",
            "start": "2025-01-01",
            "end": "2025-12-31",
        },
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert response.content.decode().count("<tr") == 13
    assert elapsed < 2.0


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_stats_page_renders_sections(client: Client, two_days: None) -> None:
    PricePoint.objects.filter(dtime_utc=datetime(2026, 9, 4, 10, 15, tzinfo=UTC)).update(
        price_pln_mwh=Decimal("-5.00")
    )

    response = client.get(reverse("web:stats"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "Average price by hour of day" in body
    assert "Average price by day of week" in body
    assert "Renewable share of planned generation by month" in body
    assert "Intervals with a negative price by month" in body
    assert "Cheapest hour on average" in body
    assert body.count('<figure class="bars') == 4


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_stats_page_without_data_and_with_period_filter(client: Client) -> None:
    assert "No price data yet." in client.get(reverse("web:stats")).content.decode()
    seed_market_day(date(2026, 9, 4))

    body = client.get(reverse("web:stats"), {"period": "30d"}).content.decode()

    assert "Last 30 days" in body
    assert 'aria-current="page"' in body
