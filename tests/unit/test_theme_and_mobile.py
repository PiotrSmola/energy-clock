from datetime import date, datetime
from decimal import Decimal

import pytest
import time_machine
from django.test import Client
from django.urls import reverse

from apps.market.queries import generation_by_interval_end, price_points_for_day
from apps.market.timeutils import WARSAW
from apps.web.charts import aggregate_hourly
from tests.market_fixtures import seed_market_day
from tests.pse_fixtures import generation_rows, price_rows

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Client:
    return Client()


def test_pages_render_auto_theme_without_a_cookie(client: Client) -> None:
    body = client.get(reverse("web:home")).content.decode()

    assert 'data-theme="auto"' in body
    assert '<meta name="color-scheme" content="light dark">' in body
    assert 'class="theme"' in body


def test_theme_cookie_is_applied_server_side(client: Client) -> None:
    response = client.post(reverse("web:set_theme"), {"theme": "dark", "next": "/planner"})

    assert response.status_code == 302
    assert response.headers["Location"] == "/planner"
    assert response.cookies["energy_clock_theme"].value == "dark"
    body = client.get(reverse("web:home")).content.decode()
    assert 'data-theme="dark"' in body
    assert '<meta name="color-scheme" content="dark">' in body
    assert 'value="dark" title="Dark theme" aria-label="Dark theme" aria-current="true"' in body


def test_theme_endpoint_rejects_junk_and_open_redirects(client: Client) -> None:
    junk = client.post(reverse("web:set_theme"), {"theme": "neon", "next": "https://evil.example/"})

    assert junk.status_code == 302
    assert junk.headers["Location"] == "/"
    assert client.get(reverse("web:set_theme")).status_code == 405
    body = client.get(reverse("web:home")).content.decode()
    assert 'data-theme="auto"' in body


@time_machine.travel(datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW), tick=False)
def test_home_renders_both_full_and_hourly_charts(client: Client) -> None:
    seed_market_day(date(2026, 9, 4))

    body = client.get(reverse("web:home")).content.decode()

    assert body.count('<figure class="chart') == 2
    assert 'class="chart chart--hourly"' in body
    assert body.count('class="bar ') == 96 + 24
    assert 'viewBox="0 0 480 260"' in body


@time_machine.travel(datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW), tick=False)
def test_hourly_aggregation_averages_prices_and_res_share() -> None:
    seed_market_day(date(2026, 9, 4))
    points = price_points_for_day(date(2026, 9, 4))
    generation = generation_by_interval_end(date(2026, 9, 4))

    hourly_points, hourly_generation = aggregate_hourly(points, generation)

    assert len(hourly_points) == 24
    first_four = [point.price_pln_mwh for point in points[:4]]
    expected = (sum(first_four, Decimal(0)) / 4).quantize(Decimal("0.01"))
    assert hourly_points[0].price_pln_mwh == expected
    assert hourly_points[0].dtime_utc == points[3].dtime_utc
    assert len(hourly_generation) == 24
    assert hourly_generation[hourly_points[0].dtime_utc].pv_mw is not None


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 3, 29), 23), (date(2025, 10, 26), 25)])
def test_hourly_aggregation_follows_real_hours_on_dst_days(day: date, hours: int) -> None:
    from apps.market.models import PricePoint
    from apps.market.timeutils import parse_pse_datetime

    PricePoint.objects.bulk_create(
        PricePoint(
            dtime_utc=parse_pse_datetime(row["dtime_utc"]),
            business_date=day,
            price_pln_mwh=Decimal(str(row["csdac_pln"])),
        )
        for row in price_rows(day)
    )
    assert len(generation_rows(day)) in (92, 100)

    hourly_points, _generation = aggregate_hourly(price_points_for_day(day), {})

    assert len(hourly_points) == hours
