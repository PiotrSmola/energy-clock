import csv
import json
from datetime import date, datetime
from io import StringIO
from typing import cast

import pytest
import time_machine
from django.http import StreamingHttpResponse
from django.test import Client
from django.urls import reverse

from apps.market.timeutils import WARSAW
from tests.market_fixtures import seed_market_day

pytestmark = pytest.mark.django_db

NOON_SEPT_4 = datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW)
EN_DASH = chr(0x2013)


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def two_days() -> None:
    seed_market_day(date(2026, 9, 4))
    seed_market_day(date(2026, 9, 5))


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_prices_api_returns_a_full_day_with_generation(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:api_prices"), {"date": "2026-09-04"})

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["business_date"] == "2026-09-04"
    assert payload["count"] == 96
    first = payload["intervals"][0]
    assert first["start_local"].startswith("2026-09-04T00:00:00+02:00")
    assert first["start_utc"].startswith("2026-09-03T22:00:00+00:00")
    assert first["price_pln_mwh"] == "717.86"
    assert first["carbon_g_kwh"] is not None
    assert payload["timezone"] == "Europe/Warsaw"


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_prices_api_defaults_to_today_and_validates_dates(client: Client, two_days: None) -> None:
    assert (
        json.loads(client.get(reverse("web:api_prices")).content)["business_date"] == "2026-09-04"
    )
    assert client.get(reverse("web:api_prices"), {"date": "yesterday"}).status_code == 400
    empty = json.loads(client.get(reverse("web:api_prices"), {"date": "2026-09-09"}).content)
    assert empty["count"] == 0 and empty["intervals"] == []


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_windows_api_returns_cheapest_and_start_now(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:api_windows"), {"duration": "120", "power": "2.0"})

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["duration_minutes"] == 120
    assert payload["power_kw"] == "2.0"
    assert payload["cheapest"]["start_local"].startswith("2026-09-05T11:45")
    assert payload["start_now"]["start_local"].startswith("2026-09-04T12:00")
    assert float(payload["cheapest"]["cost_pln"]) < float(payload["start_now"]["cost_pln"])
    assert payload["greenest"]["carbon_g"] is not None
    assert payload["savings_pln"] is not None
    assert payload["horizon_end"].startswith("2026-09-05T22:00:00+00:00")


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_windows_api_honours_constraints_and_validates_input(
    client: Client, two_days: None
) -> None:
    constrained = json.loads(
        client.get(
            reverse("web:api_windows"),
            {"duration": "120", "power": "2", "not_before": "22:00", "finish_by": "07:00"},
        ).content
    )
    assert constrained["cheapest"]["start_local"] >= "2026-09-04T22:00"
    assert constrained["cheapest"]["end_local"] <= "2026-09-05T07:00"

    assert client.get(reverse("web:api_windows"), {"duration": "5"}).status_code == 400
    assert client.get(reverse("web:api_windows"), {"power": "abc"}).status_code == 400
    assert client.get(reverse("web:api_windows"), {"power": "99"}).status_code == 400
    assert client.get(reverse("web:api_windows"), {"finish_by": "25:99"}).status_code == 400


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_windows_api_without_data_returns_nulls(client: Client) -> None:
    payload = json.loads(client.get(reverse("web:api_windows")).content)

    assert payload["cheapest"] is None
    assert payload["start_now"] is None
    assert payload["savings_pln"] is None


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_csv_export_streams_the_requested_range(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:export_csv"), {"start": "2026-09-04", "end": "2026-09-05"})

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert 'filename="energy-clock-2026-09-04-2026-09-05.csv"' in response["Content-Disposition"]
    streaming = cast(StreamingHttpResponse, response)
    rows = list(csv.reader(StringIO(streaming.getvalue().decode())))
    assert rows[0][:6] == [
        "business_date",
        "start_local",
        "end_local",
        "start_utc",
        "end_utc",
        "price_pln_mwh",
    ]
    assert len(rows) == 1 + 192
    assert rows[1][0] == "2026-09-04"
    assert rows[1][1] == "2026-09-04 00:00"
    assert rows[1][5] == "717.86"
    assert rows[1][10] != ""


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_csv_export_validates_ranges(client: Client) -> None:
    assert (
        client.get(
            reverse("web:export_csv"), {"start": "2026-09-05", "end": "2026-09-04"}
        ).status_code
        == 400
    )
    assert (
        client.get(
            reverse("web:export_csv"), {"start": "2024-01-01", "end": "2026-09-04"}
        ).status_code
        == 400
    )
    assert client.get(reverse("web:export_csv"), {"start": "nope"}).status_code == 400
    default = client.get(reverse("web:export_csv"))
    assert default.status_code == 200
    assert "2026-08-29-2026-09-04" in default["Content-Disposition"]


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_widget_page_is_embeddable_and_shows_price_and_window(
    client: Client, two_days: None
) -> None:
    response = client.get(reverse("web:widget"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "X-Frame-Options" not in response
    assert "max-age=300" in response["Cache-Control"]
    assert "Price now" in body
    assert f"11:45{EN_DASH}13:45" in body
    assert "tomorrow" in body
    assert 'class="nav"' not in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_widget_svg_renders_server_side(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:widget_svg"))

    body = response.content.decode()
    assert response.status_code == 200
    assert response["Content-Type"].startswith("image/svg+xml")
    assert body.startswith("<svg")
    assert "PLN/MWh" in body
    assert f"11:45{EN_DASH}13:45" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_widget_without_data_degrades_gracefully(client: Client) -> None:
    assert client.get(reverse("web:widget")).status_code == 200
    svg = client.get(reverse("web:widget_svg")).content.decode()
    assert svg.startswith("<svg")
