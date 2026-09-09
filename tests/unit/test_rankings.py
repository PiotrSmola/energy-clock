from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise

from apps.market.timeutils import INTERVAL, WARSAW, to_local
from apps.planner.optimizer import CarbonSlot, PriceSlot, Window, ranked_windows
from tests.market_fixtures import fixture_carbon_slots, fixture_price_slots


def carbon_of(window: Window) -> Decimal:
    assert window.carbon_g is not None
    return window.carbon_g


def slots_with(prices: list[int], carbon: list[int]) -> list[PriceSlot]:
    start = datetime(2026, 9, 4, 0, 0, tzinfo=WARSAW).astimezone(UTC)
    return [
        PriceSlot(start + index * INTERVAL, Decimal(price), Decimal(grams))
        for index, (price, grams) in enumerate(zip(prices, carbon, strict=True))
    ]


def test_ranked_windows_are_non_overlapping_and_ordered_by_cost() -> None:
    prices = [500] * 96
    for index in range(8, 16):
        prices[index] = 100
    for index in range(40, 48):
        prices[index] = 200
    for index in range(70, 78):
        prices[index] = 300
    slots = slots_with(prices, [400] * 96)

    ranking = ranked_windows(slots, 120, Decimal("2"), by="cost", limit=3)

    assert [to_local(window.start_utc).strftime("%H:%M") for window in ranking] == [
        "02:00",
        "10:00",
        "17:30",
    ]
    assert ranking[0].cost_pln < ranking[1].cost_pln < ranking[2].cost_pln
    for earlier, later in pairwise(ranking):
        assert earlier.end_utc <= later.start_utc or later.end_utc <= earlier.start_utc


def test_ranked_windows_by_carbon_uses_generation_data() -> None:
    carbon = [700] * 96
    for index in range(44, 52):
        carbon[index] = 100
    slots = slots_with([500] * 96, carbon)
    generation = [CarbonSlot(slot.start_utc, slot.carbon_g_kwh) for slot in slots]

    ranking = ranked_windows(slots, 120, Decimal("2"), by="carbon", generation=generation, limit=2)

    assert to_local(ranking[0].start_utc).strftime("%H:%M") == "11:00"
    assert ranking[0].carbon_g == Decimal("400.0")
    assert carbon_of(ranking[0]) < carbon_of(ranking[1])


def test_ranked_windows_limit_and_empty_data() -> None:
    slots = slots_with([500] * 16, [400] * 16)

    assert len(ranked_windows(slots, 120, Decimal("2"), by="cost", limit=5)) == 2
    assert ranked_windows([], 120, Decimal("2"), by="cost", limit=5) == []


def test_real_day_cheapest_and_cleanest_windows_diverge() -> None:
    day = date(2026, 9, 4)
    slots = fixture_price_slots(day)
    generation = fixture_carbon_slots(day)

    by_cost = ranked_windows(slots, 120, Decimal("2"), by="cost", limit=3)
    by_carbon = ranked_windows(
        slots, 120, Decimal("2"), by="carbon", generation=generation, limit=3
    )

    assert by_cost[0].start_utc != by_carbon[0].start_utc
    assert carbon_of(by_carbon[0]) < carbon_of(by_cost[0])
    assert by_carbon[0].cost_pln > by_cost[0].cost_pln
