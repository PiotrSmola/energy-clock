import random
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from apps.market.timeutils import INTERVAL, WARSAW, day_bounds_utc, expected_intervals, to_local
from apps.planner.optimizer import (
    CarbonSlot,
    PriceSlot,
    Window,
    cheapest_window,
    cost_if_started_now,
    greenest_window,
    most_expensive_window,
    window_starting_at,
)
from tests.market_fixtures import fixture_carbon_slots, fixture_price_slots

TWO_KW = Decimal("2")
TWO_HOURS = 120


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=WARSAW)


def slots_from(
    start: datetime,
    prices: Iterable[str | int],
    carbon: Iterable[str | int | None] | None = None,
) -> list[PriceSlot]:
    start_utc = start.astimezone(UTC)
    carbon_values = list(carbon) if carbon is not None else None
    return [
        PriceSlot(
            start_utc=start_utc + index * INTERVAL,
            price_pln_mwh=Decimal(str(price)),
            carbon_g_kwh=(
                None
                if carbon_values is None or carbon_values[index] is None
                else Decimal(str(carbon_values[index]))
            ),
        )
        for index, price in enumerate(prices)
    ]


def flat(count: int, price: int = 500) -> list[int]:
    return [price] * count


def with_dip(prices: list[int], start_index: int, length: int, price: int) -> list[int]:
    result = list(prices)
    for index in range(start_index, start_index + length):
        result[index] = price
    return result


def day_slots(day: date, price_for: Callable[[int], int]) -> list[PriceSlot]:
    start_utc, _ = day_bounds_utc(day)
    return [
        PriceSlot(start_utc=start_utc + index * INTERVAL, price_pln_mwh=Decimal(price_for(index)))
        for index in range(expected_intervals(day))
    ]


def test_cheapest_window_covers_the_price_dip() -> None:
    prices = with_dip(flat(48), 20, 8, 100)
    slots = slots_from(local(2026, 9, 4, 0), prices)

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 5)
    assert to_local(window.end_utc) == local(2026, 9, 4, 7)
    assert window.cost_pln == Decimal("0.4000")
    assert window.avg_price_pln_mwh == Decimal("100.00")


def test_cost_formula_uses_quarter_hours_times_power() -> None:
    slots = slots_from(local(2026, 9, 4, 10), flat(8, 500))

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window == Window(
        start_utc=local(2026, 9, 4, 10).astimezone(UTC),
        end_utc=local(2026, 9, 4, 12).astimezone(UTC),
        cost_pln=Decimal("2.0000"),
        avg_price_pln_mwh=Decimal("500.00"),
        carbon_g=None,
    )


def test_window_exactly_equal_to_available_data() -> None:
    slots = slots_from(local(2026, 9, 4, 10), [300, 200, 100, 400, 500, 600, 700, 800])

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert window.start_utc == slots[0].start_utc
    assert window.end_utc == slots[-1].end_utc
    assert window.avg_price_pln_mwh == Decimal("450.00")


def test_window_longer_than_available_data_returns_none() -> None:
    slots = slots_from(local(2026, 9, 4, 10), flat(7))

    assert cheapest_window(slots, TWO_HOURS, TWO_KW) is None


def test_window_crossing_midnight() -> None:
    prices = with_dip(flat(16), 6, 8, 50)
    slots = slots_from(local(2026, 9, 4, 22), prices)

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 23, 30)
    assert to_local(window.end_utc) == local(2026, 9, 5, 1, 30)
    assert to_local(window.end_utc).date() == date(2026, 9, 5)


def test_constraints_excluding_everything_return_none() -> None:
    slots = slots_from(local(2026, 9, 4, 0), flat(48))

    assert cheapest_window(slots, TWO_HOURS, TWO_KW, not_before=local(2026, 9, 4, 11)) is None
    assert cheapest_window(slots, TWO_HOURS, TWO_KW, not_after=local(2026, 9, 4, 1)) is None
    assert (
        cheapest_window(
            slots,
            TWO_HOURS,
            TWO_KW,
            not_before=local(2026, 9, 4, 6),
            not_after=local(2026, 9, 4, 7),
        )
        is None
    )


def test_not_after_acts_as_finish_deadline() -> None:
    prices = with_dip(flat(48), 32, 8, 100)
    slots = slots_from(local(2026, 9, 4, 0), prices)

    window = cheapest_window(slots, TWO_HOURS, TWO_KW, not_after=local(2026, 9, 4, 7))

    assert window is not None
    assert window.end_utc <= local(2026, 9, 4, 7)
    assert to_local(window.start_utc) == local(2026, 9, 4, 0)
    assert window.avg_price_pln_mwh == Decimal("500.00")


def test_not_before_shifts_the_search_start() -> None:
    prices = with_dip(flat(48), 32, 8, 100)
    slots = slots_from(local(2026, 9, 4, 0), prices)

    window = cheapest_window(slots, TWO_HOURS, TWO_KW, not_before=local(2026, 9, 4, 9))

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 9)
    assert window.avg_price_pln_mwh == Decimal("300.00")


def test_negative_prices_produce_negative_cost() -> None:
    prices = with_dip(flat(24), 8, 8, -50)
    slots = slots_from(local(2026, 9, 4, 10), prices)

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert window.cost_pln == Decimal("-0.2000")
    assert window.avg_price_pln_mwh == Decimal("-50.00")


def test_spring_dst_day_has_92_slots_and_real_two_hour_windows() -> None:
    dst_day = date(2026, 3, 29)
    slots = day_slots(dst_day, lambda index: 100 if 6 <= index < 14 else 500)
    assert len(slots) == 92

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert window.end_utc - window.start_utc == timedelta(hours=2)
    assert to_local(window.start_utc) == datetime(2026, 3, 29, 1, 30, tzinfo=WARSAW)
    assert to_local(window.end_utc) == datetime(2026, 3, 29, 4, 30, tzinfo=WARSAW)


def test_autumn_dst_day_distinguishes_the_repeated_local_hour() -> None:
    dst_day = date(2025, 10, 26)
    slots = day_slots(dst_day, lambda index: 100 if 12 <= index < 20 else 500)
    assert len(slots) == 100

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert window.start_utc == datetime(2025, 10, 26, 1, 0, tzinfo=UTC)
    assert to_local(window.start_utc).hour == 2
    assert to_local(window.start_utc).utcoffset() == timedelta(hours=1)
    assert window.end_utc - window.start_utc == timedelta(hours=2)


def test_gap_in_data_cannot_be_jumped_over() -> None:
    prices = with_dip(with_dip(flat(24), 4, 4, 100), 9, 4, 100)
    slots = slots_from(local(2026, 9, 4, 0), prices)
    del slots[8]

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 0)
    assert window.avg_price_pln_mwh == Decimal("300.00")


def test_tie_prefers_the_earlier_window() -> None:
    prices = with_dip(with_dip(flat(48), 8, 8, 100), 32, 8, 100)
    slots = slots_from(local(2026, 9, 4, 0), prices)

    window = cheapest_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 2)


def test_unsorted_input_gives_the_same_answer() -> None:
    prices = with_dip(flat(48), 20, 8, 100)
    ordered = slots_from(local(2026, 9, 4, 0), prices)
    shuffled = list(ordered)
    random.Random(7).shuffle(shuffled)

    assert cheapest_window(shuffled, TWO_HOURS, TWO_KW) == cheapest_window(
        ordered, TWO_HOURS, TWO_KW
    )


def test_duration_not_divisible_by_fifteen_weights_the_last_slot() -> None:
    slots = slots_from(local(2026, 9, 4, 10), flat(8, 500))

    window = cheapest_window(slots, 100, TWO_KW)

    assert window is not None
    assert window.end_utc - window.start_utc == timedelta(minutes=100)
    assert window.cost_pln == Decimal("1.6667")
    assert window.avg_price_pln_mwh == Decimal("500.00")


def test_most_expensive_window_covers_the_peak() -> None:
    prices = with_dip(flat(48), 28, 8, 1200)
    slots = slots_from(local(2026, 9, 4, 0), prices)

    window = most_expensive_window(slots, TWO_HOURS, TWO_KW)

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 7)
    assert window.avg_price_pln_mwh == Decimal("1200.00")


def test_greenest_window_differs_from_cheapest_window() -> None:
    prices = [300 if index < 24 else 600 for index in range(96)]
    carbon = [700 if index < 24 else (100 if 44 <= index < 56 else 500) for index in range(96)]
    slots = slots_from(local(2026, 9, 4, 0), prices, carbon)
    generation = [
        CarbonSlot(start_utc=slot.start_utc, carbon_g_kwh=slot.carbon_g_kwh) for slot in slots
    ]

    cheapest = cheapest_window(slots, TWO_HOURS, TWO_KW)
    greenest = greenest_window(slots, generation, TWO_HOURS, TWO_KW)

    assert cheapest is not None and greenest is not None
    assert to_local(cheapest.start_utc) == local(2026, 9, 4, 0)
    assert to_local(greenest.start_utc) == local(2026, 9, 4, 11)
    assert greenest.carbon_g == Decimal("400.0")
    assert greenest.cost_pln == Decimal("2.4000")
    assert cheapest.carbon_g == Decimal("2800.0")


def test_greenest_window_skips_slots_without_carbon_data() -> None:
    slots = slots_from(local(2026, 9, 4, 0), flat(16))
    generation = [
        CarbonSlot(start_utc=slot.start_utc, carbon_g_kwh=None if index < 8 else Decimal(200))
        for index, slot in enumerate(slots)
    ]

    window = greenest_window(slots, generation, TWO_HOURS, TWO_KW)

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 2)
    assert window.carbon_g == Decimal("800.0")


def test_greenest_window_returns_none_without_any_carbon_data() -> None:
    slots = slots_from(local(2026, 9, 4, 0), flat(16))

    assert greenest_window(slots, [], TWO_HOURS, TWO_KW) is None


def test_cost_if_started_now_weights_the_partial_first_slot() -> None:
    slots = slots_from(local(2026, 9, 4, 10), [400, 800, 800, 800])

    cost = cost_if_started_now(slots, 30, Decimal("3"), now=local(2026, 9, 4, 10, 5))

    assert cost == Decimal("1.0000")


def test_cost_if_started_now_returns_none_when_data_runs_out() -> None:
    slots = slots_from(local(2026, 9, 4, 10), [400, 800])

    assert cost_if_started_now(slots, 60, TWO_KW, now=local(2026, 9, 4, 10, 5)) is None
    assert cost_if_started_now(slots, 15, TWO_KW, now=local(2026, 9, 4, 9, 0)) is None


def test_window_starting_at_reports_real_end_time() -> None:
    slots = slots_from(local(2026, 9, 4, 10), flat(8, 500))

    window = window_starting_at(slots, local(2026, 9, 4, 10, 5), 45, TWO_KW)

    assert window is not None
    assert to_local(window.start_utc) == local(2026, 9, 4, 10, 5)
    assert to_local(window.end_utc) == local(2026, 9, 4, 10, 50)
    assert window.cost_pln == Decimal("0.7500")


def test_real_day_2026_09_04_cheapest_two_hours_is_in_the_afternoon() -> None:
    slots = fixture_price_slots(date(2026, 9, 4))

    cheapest = cheapest_window(slots, TWO_HOURS, TWO_KW)
    expensive = most_expensive_window(slots, TWO_HOURS, TWO_KW)

    assert cheapest is not None and expensive is not None
    assert local(2026, 9, 4, 14, 30) <= to_local(cheapest.start_utc) <= local(2026, 9, 4, 17, 0)
    assert local(2026, 9, 4, 6, 0) <= to_local(expensive.start_utc) <= local(2026, 9, 4, 7, 30)
    assert min(slots, key=lambda slot: slot.price_pln_mwh).price_pln_mwh == Decimal("453.67")
    assert cheapest.cost_pln < expensive.cost_pln


def test_real_day_2026_09_04_greenest_window_has_lower_carbon_than_cheapest() -> None:
    slots = fixture_price_slots(date(2026, 9, 4))
    generation = fixture_carbon_slots(date(2026, 9, 4))

    cheapest = cheapest_window(slots, TWO_HOURS, TWO_KW)
    greenest = greenest_window(slots, generation, TWO_HOURS, TWO_KW)

    assert cheapest is not None and greenest is not None
    assert greenest.carbon_g is not None and cheapest.carbon_g is not None
    assert greenest.carbon_g <= cheapest.carbon_g
    assert greenest.cost_pln >= cheapest.cost_pln


@pytest.mark.parametrize("duration", [15, 60, 360])
def test_any_window_never_exceeds_available_data(duration: int) -> None:
    slots = fixture_price_slots(date(2026, 9, 4))

    window = cheapest_window(slots, duration, Decimal("11"))

    assert window is not None
    assert window.start_utc >= slots[0].start_utc
    assert window.end_utc <= slots[-1].end_utc
    assert window.end_utc - window.start_utc == timedelta(minutes=duration)
