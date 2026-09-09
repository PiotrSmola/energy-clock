import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from apps.market.timeutils import INTERVAL, WARSAW, day_bounds_utc, expected_intervals, to_local
from apps.planner.optimizer import PriceSlot
from apps.planner.tariff import (
    INTERVALS_PER_DAY,
    DailyProfile,
    MonthResult,
    TariffComparison,
    builtin_profiles,
    custom_profile,
    simulate_tariff,
)


def month_slots(year: int, month: int, price_for: Callable[[datetime], Decimal]) -> list[PriceSlot]:
    first = date(year, month, 1)
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    slots: list[PriceSlot] = []
    day = first
    while day < next_month:
        start_utc, _ = day_bounds_utc(day)
        for index in range(expected_intervals(day)):
            slot_start = start_utc + index * INTERVAL
            slots.append(PriceSlot(start_utc=slot_start, price_pln_mwh=price_for(slot_start)))
        day += timedelta(days=1)
    return slots


def constant(price: str) -> Callable[[datetime], Decimal]:
    return lambda moment: Decimal(price)


def local_hour(moment: datetime) -> int:
    return moment.astimezone(WARSAW).hour


@pytest.mark.parametrize("key", ["flat", "day", "night"])
def test_builtin_profile_weights_sum_to_exactly_one(key: str) -> None:
    profile = builtin_profiles()[key]

    assert len(profile.weights) == INTERVALS_PER_DAY
    assert sum(profile.weights, Fraction(0)) == Fraction(1)
    assert all(weight > 0 for weight in profile.weights)


def test_custom_profile_normalizes_hourly_values_to_exactly_one() -> None:
    profile = custom_profile([1] * 6 + [3] * 12 + [2] * 6)

    assert len(profile.weights) == INTERVALS_PER_DAY
    assert sum(profile.weights, Fraction(0)) == Fraction(1)
    assert profile.weights[0] * 4 * 6 + profile.weights[24] * 4 * 12 + profile.weights[
        72
    ] * 4 * 6 == Fraction(1)


def test_custom_profile_rejects_all_zero_or_wrong_length() -> None:
    with pytest.raises(ValueError, match="24"):
        custom_profile([1, 2, 3])
    with pytest.raises(ValueError, match="positive"):
        custom_profile([0] * 24)
    with pytest.raises(ValueError, match="negative"):
        custom_profile([1] * 23 + [-1])


def test_day_profile_places_most_energy_between_six_and_twenty_two() -> None:
    profile = builtin_profiles()["day"]
    daytime = sum(profile.weights[6 * 4 : 22 * 4], Fraction(0))

    assert daytime == Fraction(85, 100)


def test_night_profile_places_most_energy_at_night() -> None:
    profile = builtin_profiles()["night"]
    night = sum(profile.weights[: 6 * 4], Fraction(0)) + sum(profile.weights[22 * 4 :], Fraction(0))

    assert night == Fraction(60, 100)


def test_flat_profile_on_constant_prices_matches_hand_calculation() -> None:
    slots = month_slots(2026, 4, constant("500"))

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("300"),
        profile=builtin_profiles()["flat"],
        fixed_price_pln_kwh=Decimal("0.90"),
        start=date(2026, 4, 1),
        end=date(2026, 4, 30),
    )

    assert isinstance(result, TariffComparison)
    assert len(result.months) == 1
    month = result.months[0]
    assert isinstance(month, MonthResult)
    assert (month.year, month.month) == (2026, 4)
    assert month.energy_kwh == Decimal("300.00")
    assert month.dynamic_cost_pln == Decimal("150.00")
    assert month.fixed_cost_pln == Decimal("270.00")
    assert month.difference_pln == Decimal("120.00")
    assert month.days_covered == 30
    assert month.complete is True
    assert result.total_dynamic_pln == Decimal("150.00")
    assert result.total_fixed_pln == Decimal("270.00")


def test_day_profile_weights_dynamic_cost_by_time_of_day() -> None:
    slots = month_slots(
        2026, 4, lambda moment: Decimal("1000") if 6 <= local_hour(moment) < 22 else Decimal("0")
    )

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("300"),
        profile=builtin_profiles()["day"],
        fixed_price_pln_kwh=Decimal("1"),
        start=date(2026, 4, 1),
        end=date(2026, 4, 30),
    )

    assert result.months[0].dynamic_cost_pln == Decimal("255.00")


def test_spring_dst_month_keeps_monthly_energy_exact() -> None:
    slots = month_slots(2026, 3, constant("500"))
    assert len(slots) == 31 * 96 - 4

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("310"),
        profile=builtin_profiles()["day"],
        fixed_price_pln_kwh=Decimal("1"),
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
    )

    month = result.months[0]
    assert month.energy_kwh == Decimal("310.00")
    assert month.dynamic_cost_pln == Decimal("155.00")
    assert month.days_covered == 31


def test_autumn_dst_month_keeps_monthly_energy_exact() -> None:
    slots = month_slots(2025, 10, constant("400"))
    assert len(slots) == 31 * 96 + 4

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("310"),
        profile=builtin_profiles()["night"],
        fixed_price_pln_kwh=Decimal("1"),
        start=date(2025, 10, 1),
        end=date(2025, 10, 31),
    )

    month = result.months[0]
    assert month.energy_kwh == Decimal("310.00")
    assert month.dynamic_cost_pln == Decimal("124.00")


def test_partial_month_scales_energy_and_is_marked_incomplete() -> None:
    slots = [
        slot for slot in month_slots(2026, 5, constant("500")) if to_local(slot.start_utc).day <= 10
    ]

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("310"),
        profile=builtin_profiles()["flat"],
        fixed_price_pln_kwh=Decimal("1"),
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
    )

    month = result.months[0]
    assert month.complete is False
    assert month.days_covered == 10
    assert month.energy_kwh == Decimal("100.00")
    assert month.dynamic_cost_pln == Decimal("50.00")


def test_range_spanning_months_returns_ordered_month_results() -> None:
    slots = month_slots(2026, 1, constant("600")) + month_slots(2026, 2, constant("400"))

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("100"),
        profile=builtin_profiles()["flat"],
        fixed_price_pln_kwh=Decimal("0.5"),
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
    )

    assert [(month.year, month.month) for month in result.months] == [(2026, 1), (2026, 2)]
    assert [month.dynamic_cost_pln for month in result.months] == [
        Decimal("60.00"),
        Decimal("40.00"),
    ]
    assert result.total_difference_pln == Decimal("0.00")
    assert result.cheaper == "dynamic" or result.cheaper == "equal"


def test_negative_prices_reduce_the_dynamic_cost() -> None:
    slots = month_slots(2026, 4, lambda moment: Decimal("-100"))

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("300"),
        profile=builtin_profiles()["flat"],
        fixed_price_pln_kwh=Decimal("1"),
        start=date(2026, 4, 1),
        end=date(2026, 4, 30),
    )

    assert result.months[0].dynamic_cost_pln == Decimal("-30.00")
    assert result.cheaper == "dynamic"


def test_slots_outside_the_range_are_ignored() -> None:
    slots = month_slots(2026, 4, constant("500")) + month_slots(2026, 5, constant("500"))

    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("300"),
        profile=builtin_profiles()["flat"],
        fixed_price_pln_kwh=Decimal("1"),
        start=date(2026, 4, 1),
        end=date(2026, 4, 30),
    )

    assert [(month.year, month.month) for month in result.months] == [(2026, 4)]


def test_full_year_simulation_runs_under_two_seconds() -> None:
    slots = [
        slot for month in range(1, 13) for slot in month_slots(2025, month, constant("450.50"))
    ]
    assert len(slots) == 365 * 96

    started = time.perf_counter()
    result = simulate_tariff(
        slots,
        monthly_kwh=Decimal("250"),
        profile=builtin_profiles()["day"],
        fixed_price_pln_kwh=Decimal("0.95"),
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
    )
    elapsed = time.perf_counter() - started

    assert len(result.months) == 12
    assert result.total_energy_kwh == Decimal("3000.00")
    assert elapsed < 2.0


def test_profile_from_key_and_custom_have_labels() -> None:
    profiles = builtin_profiles()

    assert set(profiles) == {"flat", "day", "night"}
    assert all(isinstance(profile, DailyProfile) for profile in profiles.values())
    assert all(str(profile.label) for profile in profiles.values())
