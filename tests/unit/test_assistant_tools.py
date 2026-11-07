import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import time_machine

from apps.assistant import tools
from apps.assistant.law import pack
from apps.assistant.models import LawChunk
from apps.assistant.tools import TOOLS, ToolOutcome, local_label, run_tool
from apps.market.models import PricePoint
from apps.market.queries import price_point_at
from apps.market.timeutils import INTERVAL, WARSAW, day_bounds_utc, expected_intervals
from apps.planner.optimizer import Window
from apps.planner.services import PlanRequest, build_plan, summarize_day, tariff_slots
from apps.planner.tariff import builtin_profiles, simulate_tariff
from tests.assistant_fakes import FakeLlm
from tests.market_fixtures import seed_market_day

pytestmark = pytest.mark.django_db

NOON_SEPT_4 = datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW)


@pytest.fixture
def two_days() -> None:
    seed_market_day(date(2026, 9, 4))
    seed_market_day(date(2026, 9, 5))


def call(name: str, arguments: dict[str, Any] | str, client: FakeLlm | None = None) -> ToolOutcome:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return run_tool(name, raw, NOON_SEPT_4, client or FakeLlm())


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def label(moment: datetime) -> str:
    return local_label(moment, NOON_SEPT_4)


def window_dict(window: Window) -> dict[str, str]:
    return {
        "start": label(window.start_utc),
        "end": label(window.end_utc),
        "cost_pln": money(window.cost_pln),
        "average_price_pln_mwh": money(window.avg_price_pln_mwh),
    }


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


def test_every_tool_schema_has_a_matching_handler() -> None:
    names = [tool["function"]["name"] for tool in TOOLS]

    assert names == [
        "find_cheapest_window",
        "get_day_prices",
        "compare_tariff",
        "search_energy_law",
    ]
    for tool in TOOLS:
        assert tool["type"] == "function"
        parameters: Any = tool["function"]["parameters"]
        assert set(parameters.get("required", [])) <= set(parameters["properties"])
        assert "unknown tool" not in str(call(tool["function"]["name"], {}).error)


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_find_cheapest_window_matches_the_planner(two_days: None) -> None:
    request = PlanRequest(120, Decimal("2"), None, None, "Custom appliance")
    plan = build_plan(request, NOON_SEPT_4)
    assert plan.cheapest is not None
    assert plan.start_now is not None
    assert plan.greenest is not None
    assert plan.savings_pln is not None
    assert plan.horizon_end is not None

    result = call("find_cheapest_window", {"duration_minutes": 120, "power_kw": 2}).result

    assert result["duration_minutes"] == 120
    assert Decimal(result["power_kw"]) == Decimal("2")
    assert result["energy_kwh"] == str(plan.energy_kwh)
    assert result["cheapest"] == window_dict(plan.cheapest)
    assert result["start_now"] == window_dict(plan.start_now)
    assert result["greenest"] == window_dict(plan.greenest)
    assert result["saving_vs_start_now_pln"] == money(plan.savings_pln)
    assert result["saving_percent"] == plan.savings_percent
    assert result["prices_published_until"] == label(plan.horizon_end)
    assert result["start_now"]["start"] == "2026-09-04 12:00 (today)"
    assert "Wholesale" in result["note"]


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_find_cheapest_window_applies_the_time_constraints(two_days: None) -> None:
    result = call(
        "find_cheapest_window",
        {"duration_minutes": 60, "power_kw": 1.5, "earliest_start": "18:00", "finish_by": "07:00"},
    ).result

    cheapest = result["cheapest"]
    # "YYYY-MM-DD HH:MM" strings sort chronologically
    assert cheapest["start"][:16] >= "2026-09-04 18:00"
    assert cheapest["end"][:16] <= "2026-09-05 07:00"
    assert cheapest["end"].endswith(("(today)", "(tomorrow)"))


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_find_cheapest_window_labels_tomorrow(two_days: None) -> None:
    result = call(
        "find_cheapest_window",
        {"duration_minutes": 60, "power_kw": 1, "earliest_start": "03:00", "finish_by": "05:00"},
    ).result

    assert result["cheapest"]["start"].endswith("(tomorrow)")
    assert result["cheapest"]["start"].startswith("2026-09-05 ")


@time_machine.travel(NOON_SEPT_4, tick=False)
@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"duration_minutes": 5, "power_kw": 2}, "duration_minutes"),
        ({"duration_minutes": 5000, "power_kw": 2}, "duration_minutes"),
        ({"power_kw": 2}, "duration_minutes"),
        ({"duration_minutes": 60, "power_kw": 0}, "power_kw"),
        ({"duration_minutes": 60, "power_kw": 500}, "power_kw"),
        ({"duration_minutes": 60}, "power_kw"),
        ({"duration_minutes": 60, "power_kw": 2, "earliest_start": "late"}, "earliest_start"),
    ],
)
def test_find_cheapest_window_reports_validation_errors(
    two_days: None, arguments: dict[str, Any], field: str
) -> None:
    outcome = call("find_cheapest_window", arguments)

    assert outcome.error is not None
    assert field in outcome.error
    assert set(outcome.result) == {"error"}


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_find_cheapest_window_without_prices_says_so() -> None:
    outcome = call("find_cheapest_window", {"duration_minutes": 120, "power_kw": 2})

    assert outcome.error == "No published prices cover this cycle and its time constraints."


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_get_day_prices_summarises_today(two_days: None) -> None:
    summary = summarize_day(date(2026, 9, 4), NOON_SEPT_4)
    current = price_point_at(NOON_SEPT_4)
    assert summary.lowest is not None
    assert summary.highest is not None
    assert summary.average is not None
    assert summary.cheapest is not None
    assert summary.most_expensive is not None
    assert current is not None

    result = call("get_day_prices", {}).result

    assert result["date"] == "2026-09-04"
    assert result["unit"] == "PLN/MWh"
    assert result["current_price"] == money(current.price_pln_mwh)
    assert result["lowest"] == {
        "price": money(summary.lowest.price_pln_mwh),
        "interval_start": label(summary.lowest.interval_start_utc),
    }
    assert result["highest"] == {
        "price": money(summary.highest.price_pln_mwh),
        "interval_start": label(summary.highest.interval_start_utc),
    }
    assert result["average"] == money(summary.average)
    assert result["cheapest_2h_window"] == window_dict(summary.cheapest)
    assert result["most_expensive_2h_window"] == window_dict(summary.most_expensive)
    assert Decimal(result["lowest"]["price"]) <= Decimal(result["average"])
    assert Decimal(result["average"]) <= Decimal(result["highest"]["price"])
    assert result["lowest"]["interval_start"].endswith("(today)")


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_get_day_prices_for_tomorrow_has_no_current_price_and_uses_tomorrow_labels(
    two_days: None,
) -> None:
    result = call("get_day_prices", {"date": "2026-09-05"}).result

    assert result["date"] == "2026-09-05"
    assert result["current_price"] is None
    assert result["lowest"]["interval_start"].endswith("(tomorrow)")
    assert result["cheapest_2h_window"]["start"].endswith("(tomorrow)")


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_get_day_prices_without_data_hints_at_the_publication_time_for_the_future(
    two_days: None,
) -> None:
    future = call("get_day_prices", {"date": "2026-09-06"})
    past = call("get_day_prices", {"date": "2026-08-01"})

    assert (
        future.error
        == "No prices published for 2026-09-06. Prices for tomorrow are published around 14:00."
    )
    assert past.error == "No prices published for 2026-08-01."


@time_machine.travel(NOON_SEPT_4, tick=False)
@pytest.mark.parametrize("value", ["tomorrow", "04.09.2026", "2026-13-40"])
def test_get_day_prices_rejects_malformed_dates(value: str) -> None:
    assert call("get_day_prices", {"date": value}).error == "date must be YYYY-MM-DD"


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_compare_tariff_matches_the_simulation() -> None:
    seed_constant_month(2026, 7, Decimal("500"))
    seed_constant_month(2026, 8, Decimal("500"))
    start, end = date(2025, 9, 1), date(2026, 9, 3)
    expected = simulate_tariff(
        tariff_slots(start, end),
        monthly_kwh=Decimal("300"),
        profile=builtin_profiles()["flat"],
        fixed_price_pln_kwh=Decimal("0.62"),
        start=start,
        end=end,
    )

    result = call("compare_tariff", {"monthly_kwh": 300, "fixed_price_pln_kwh": 0.62}).result

    assert result["period"] == "2025-09-01 to 2026-09-03"
    assert result["energy_kwh"] == money(expected.total_energy_kwh)
    assert result["dynamic_tariff_pln"] == money(expected.total_dynamic_pln)
    assert result["fixed_price_pln"] == money(expected.total_fixed_pln)
    assert result["fixed_minus_dynamic_pln"] == money(expected.total_difference_pln)
    assert result["cheaper"] == expected.cheaper
    # two months at a flat 0.50 PLN/kWh against a fixed 0.62 PLN/kWh
    assert result["energy_kwh"] == "600.00"
    assert result["dynamic_tariff_pln"] == "300.00"
    assert result["fixed_price_pln"] == "372.00"
    assert result["fixed_minus_dynamic_pln"] == "72.00"


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_compare_tariff_night_profile_is_validated_and_used() -> None:
    seed_constant_month(2026, 8, Decimal("500"))

    night = call(
        "compare_tariff", {"monthly_kwh": 250, "fixed_price_pln_kwh": 0.62, "profile": "night"}
    )
    invalid = call(
        "compare_tariff", {"monthly_kwh": 250, "fixed_price_pln_kwh": 0.62, "profile": "weekend"}
    )

    assert night.error is None
    assert night.result["dynamic_tariff_pln"] == "125.00"
    assert invalid.error is not None
    assert "profile" in invalid.error


@time_machine.travel(NOON_SEPT_4, tick=False)
@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"monthly_kwh": 1, "fixed_price_pln_kwh": 0.6}, "monthly_kwh"),
        ({"fixed_price_pln_kwh": 0.6}, "monthly_kwh"),
        ({"monthly_kwh": 250, "fixed_price_pln_kwh": 99}, "fixed_price_pln_kwh"),
        ({"monthly_kwh": 250}, "fixed_price_pln_kwh"),
    ],
)
def test_compare_tariff_reports_validation_errors(arguments: dict[str, Any], field: str) -> None:
    outcome = call("compare_tariff", arguments)

    assert outcome.error is not None
    assert field in outcome.error


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_compare_tariff_without_history_says_so() -> None:
    outcome = call("compare_tariff", {"monthly_kwh": 250, "fixed_price_pln_kwh": 0.62})

    assert outcome.error == "No prices in the last twelve months."


def add_chunk(position: int, article: str, vector: list[float], text: str = "") -> LawChunk:
    return LawChunk.objects.create(
        act="DU/2024/266",
        act_title="Prawo energetyczne",
        position=position,
        article=article,
        heading=f"Rozdział 1 > Art. {article}",
        text=text or f"tekst artykułu {article}",
        embedding=pack(vector),
    )


def test_search_energy_law_returns_ranked_articles_and_sources() -> None:
    meters = add_chunk(0, "11", [0.0, 1.0, 0.0])
    tariffs = add_chunk(1, "45", [1.0, 0.0, 0.0])

    outcome = call("search_energy_law", {"query": "taryfa dla gospodarstw"})

    assert outcome.error is None
    assert outcome.result["act"] == "Prawo energetyczne"
    assert [row["article"] for row in outcome.result["results"]] == ["art. 45", "art. 11"]
    assert outcome.result["results"][0]["location"] == "Rozdział 1 > Art. 45"
    assert outcome.result["results"][0]["text"] == "tekst artykułu 45"
    assert outcome.result["results"][0]["similarity"] > outcome.result["results"][1]["similarity"]
    assert outcome.sources == [tariffs, meters]


def test_search_energy_law_sends_the_full_chunk_text_to_the_model() -> None:
    long_text = " ".join(f"zdanie{number}" for number in range(1000))
    assert len(long_text) > 2400
    add_chunk(0, "45", [1.0, 0.0, 0.0], text=long_text)

    outcome = call("search_energy_law", {"query": "taryfa"})

    assert outcome.result["results"][0]["text"] == long_text
    assert not hasattr(tools, "MAX_LAW_TEXT_CHARS")


def test_search_energy_law_with_an_empty_index_points_to_ingest_law() -> None:
    outcome = call("search_energy_law", {"query": "taryfa"})

    assert outcome.error == "The law index is empty; run `python manage.py ingest_law`."
    assert outcome.sources == []


@pytest.mark.parametrize("arguments", [{}, {"query": ""}, {"query": "   "}, {"query": None}])
def test_search_energy_law_rejects_an_empty_query_without_calling_the_model(
    arguments: dict[str, Any],
) -> None:
    client = FakeLlm()

    outcome = call("search_energy_law", arguments, client)

    assert outcome.error == "query must not be empty"
    assert client.embedded == []


def test_invalid_json_arguments_are_an_error_not_an_exception() -> None:
    assert call("get_day_prices", "{not json").error == "arguments are not valid JSON"


@pytest.mark.parametrize("raw", ["[1, 2]", '"text"', "42", "null"])
def test_non_object_arguments_are_rejected(raw: str) -> None:
    assert call("get_day_prices", raw).error == "arguments must be a JSON object"


@time_machine.travel(NOON_SEPT_4, tick=False)
def test_empty_arguments_string_means_no_arguments(two_days: None) -> None:
    assert call("get_day_prices", "").result["date"] == "2026-09-04"


def test_unknown_tool_is_an_error() -> None:
    assert call("delete_everything", {}).error == "unknown tool delete_everything"


def test_local_label_names_today_tomorrow_yesterday_and_dates() -> None:
    now = datetime(2026, 9, 4, 23, 30, tzinfo=WARSAW)

    assert (
        local_label(datetime(2026, 9, 4, 23, 45, tzinfo=WARSAW), now) == "2026-09-04 23:45 (today)"
    )
    assert (
        local_label(datetime(2026, 9, 5, 0, 15, tzinfo=WARSAW), now)
        == "2026-09-05 00:15 (tomorrow)"
    )
    assert (
        local_label(datetime(2026, 9, 3, 8, 0, tzinfo=WARSAW), now)
        == "2026-09-03 08:00 (yesterday)"
    )
    assert (
        local_label(datetime(2026, 9, 9, 8, 0, tzinfo=WARSAW), now)
        == "2026-09-09 08:00 (2026-09-09)"
    )
    # the label uses Warsaw time, not UTC: 22:30 UTC on Sept 4 is already Sept 5 in Poland
    utc_moment = datetime(2026, 9, 4, 22, 30, tzinfo=UTC)
    assert local_label(utc_moment, now) == "2026-09-05 00:30 (tomorrow)"


def test_search_energy_law_turns_an_index_mismatch_into_an_error_result() -> None:
    add_chunk(0, "45", [1.0, 0.0, 0.0])
    client = FakeLlm(vectorize=lambda _text: [1.0, 0.0])

    outcome = call("search_energy_law", {"query": "taryfa"}, client)

    assert outcome.error is not None
    assert "different embedding model" in outcome.error
    assert outcome.sources == []
