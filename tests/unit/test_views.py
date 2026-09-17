from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import time_machine
from django.test import Client
from django.urls import reverse

from apps.market.models import IngestRun
from apps.market.timeutils import WARSAW
from apps.planner.models import Appliance
from tests.market_fixtures import seed_market_day

pytestmark = pytest.mark.django_db

NOON_SEPT_4 = datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW)
EVENING_SEPT_4 = datetime(2026, 9, 4, 20, 0, tzinfo=WARSAW)


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def two_days() -> None:
    seed_market_day(date(2026, 9, 4))
    seed_market_day(date(2026, 9, 5))
    IngestRun.objects.create(
        endpoint=IngestRun.Endpoint.PRICES,
        business_date=date(2026, 9, 5),
        status=IngestRun.Status.OK,
        records=96,
        expected_records=96,
        started_at=datetime(2026, 9, 4, 12, 15, tzinfo=UTC),
        finished_at=datetime(2026, 9, 4, 12, 15, 5, tzinfo=UTC),
    )


@pytest.fixture
def today_only() -> None:
    seed_market_day(date(2026, 9, 4))


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_home_shows_price_now_and_windows(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:home"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "Price now" in body
    assert "Cheapest 2 h window" in body
    assert "Most expensive 2 h window" in body
    assert 'class="bar' in body
    assert body.count('class="bar ') == 96 + 24
    assert "wholesale day-ahead market prices" in body
    assert "<html" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_home_cheapest_window_for_2026_09_04_is_in_the_afternoon(
    client: Client, today_only: None
) -> None:
    response = client.get(reverse("web:home"))

    body = response.content.decode()
    start = body.split('class="figure">')[1].split('class="value num">')[1][:5]
    hour = int(start[:2])
    assert 14 <= hour <= 17
    assert "18:30" not in start


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_home_tomorrow_without_data_shows_publication_message(
    client: Client, today_only: None
) -> None:
    response = client.get(reverse("web:home"), {"day": "tomorrow"})

    body = response.content.decode()
    assert response.status_code == 200
    assert "prices are not published yet." in body
    assert "around 14:00" in body
    assert "next automatic check runs at 14:15" in body
    assert 'class="bar ' not in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_home_tomorrow_with_data_renders_chart(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:home"), {"day": "tomorrow"})

    body = response.content.decode()
    assert body.count('class="bar ') == 96 + 24
    assert "Saturday, 5 September 2026" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_home_htmx_request_returns_only_the_panel(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:home"), {"day": "tomorrow"}, HTTP_HX_REQUEST="true")

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body
    assert 'class="daytabs"' in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_home_without_any_data_is_not_a_500(client: Client) -> None:
    response = client.get(reverse("web:home"))

    assert response.status_code == 200
    assert "No price data for this day." in response.content.decode()


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_home_reports_stale_data(client: Client, today_only: None) -> None:
    IngestRun.objects.create(
        endpoint=IngestRun.Endpoint.PRICES,
        business_date=date(2026, 9, 4),
        status=IngestRun.Status.OK,
        records=96,
        expected_records=96,
        started_at=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
    )

    body = client.get(reverse("web:home")).content.decode()

    assert "Data may be stale" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_planner_page_renders_form_and_presets(client: Client, two_days: None) -> None:
    response = client.get(reverse("web:planner"))

    body = response.content.decode()
    assert response.status_code == 200
    assert 'name="appliance"' in body
    assert "Washing machine" in body
    assert 'id="appliance-presets"' in body
    assert "Find the cheapest window" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_planner_plain_get_submit_returns_full_page_with_result(
    client: Client, two_days: None
) -> None:
    response = client.get(
        reverse("web:planner"), {"appliance": "", "duration_minutes": "120", "power_kw": "2.0"}
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" in body
    assert "Start at" in body
    assert "You save" in body
    assert "Cost if started now" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_planner_result_partial_uses_selected_appliance(client: Client, two_days: None) -> None:
    dryer_id = str(Appliance.objects.get(user=None, key="dryer").pk)
    response = client.get(
        reverse("web:planner_result"),
        {"appliance": dryer_id, "duration_minutes": "5", "power_kw": "0.1"},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body
    assert "Tumble dryer" in body
    assert "1 h 30 min" in body
    assert "2.50 kW" in body


@time_machine.travel(EVENING_SEPT_4, tick=False)
def test_planner_finish_by_seven_never_returns_a_window_after_seven(
    client: Client, two_days: None
) -> None:
    response = client.get(
        reverse("web:planner_result"),
        {"appliance": "", "duration_minutes": "120", "power_kw": "2.0", "finish_by": "07:00"},
    )

    body = response.content.decode()
    assert "Start at" in body
    done_by = body.split("done by")[1].split('class="num">')[1][:5]
    start = body.split('class="value num">')[1][:5]
    assert done_by <= "07:00" or start >= "20:00"
    assert "finish by 07:00 (tomorrow)" in body


@time_machine.travel(datetime(2026, 9, 5, 6, 30, tzinfo=WARSAW), tick=False)
def test_planner_finish_by_seven_returns_no_window_when_impossible(
    client: Client, two_days: None
) -> None:
    response = client.get(
        reverse("web:planner_result"),
        {"appliance": "", "duration_minutes": "120", "power_kw": "2.0", "finish_by": "07:00"},
    )

    body = response.content.decode()
    assert "Start at" not in body
    assert "No window fits these constraints" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_planner_reports_missing_data(client: Client) -> None:
    response = client.get(
        reverse("web:planner_result"), {"appliance": "", "duration_minutes": "120", "power_kw": "2"}
    )

    assert response.status_code == 200
    assert "No price data available" in response.content.decode()


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_planner_validates_input(client: Client, two_days: None) -> None:
    response = client.get(
        reverse("web:planner"), {"appliance": "", "duration_minutes": "3", "power_kw": "-1"}
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "Check the highlighted fields." in body


def test_default_language_is_english_even_for_polish_browsers(client: Client) -> None:
    response = client.get(reverse("web:home"), HTTP_ACCEPT_LANGUAGE="pl-PL,pl;q=0.9")

    assert response.status_code == 200
    assert response.headers["Content-Language"] == "en"
    assert 'lang="en"' in response.content.decode()


def test_language_switch_sets_cookie_and_translates(client: Client) -> None:
    response = client.post(reverse("set_language"), {"language": "pl", "next": "/"})

    assert response.status_code == 302
    home = client.get(reverse("web:home"))
    body = home.content.decode()
    assert home.headers["Content-Language"] == "pl"
    assert 'lang="pl"' in body
    assert "Zegar cen" in body
    assert "Planer" in body


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_polish_home_uses_polish_dates_and_numbers(client: Client, two_days: None) -> None:
    client.cookies["energy_clock_language"] = "pl"

    body = client.get(reverse("web:home")).content.decode()

    assert "Piątek, 4 września 2026" in body
    assert "Cena teraz" in body
    assert "453,67" in body


def test_reference_price_is_decimal_formatted_with_two_places() -> None:
    from apps.web.templatetags.energy import money

    assert money(Decimal("453.667")) == "453.67"
    assert money(Decimal("1022.5")) == "1,022.50"
    assert money(None) == ""
