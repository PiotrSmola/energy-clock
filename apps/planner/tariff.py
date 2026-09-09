from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Literal

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from apps.market.timeutils import to_local
from apps.planner.optimizer import PriceSlot

INTERVALS_PER_DAY = 96
SLOTS_PER_HOUR = 4
HOURS_PER_DAY = 24
MONEY = Decimal("0.01")
KWH_PER_MWH = Decimal(1000)
DAY_HOURS = range(6, 22)
DAY_PROFILE_DAYTIME_SHARE = Fraction(85, 100)
NIGHT_PROFILE_NIGHT_SHARE = Fraction(60, 100)


@dataclass(frozen=True, slots=True)
class DailyProfile:
    key: str
    label: Promise | str
    weights: tuple[Fraction, ...]


def weights_from_hourly(hourly: Sequence[Fraction]) -> tuple[Fraction, ...]:
    total = sum(hourly, Fraction(0))
    return tuple(
        value / total / SLOTS_PER_HOUR for value in hourly for _slot in range(SLOTS_PER_HOUR)
    )


def hourly_split(daytime_share: Fraction) -> list[Fraction]:
    night_hours = HOURS_PER_DAY - len(DAY_HOURS)
    night_share = 1 - daytime_share
    return [
        daytime_share / len(DAY_HOURS) if hour in DAY_HOURS else night_share / night_hours
        for hour in range(HOURS_PER_DAY)
    ]


def builtin_profiles() -> dict[str, DailyProfile]:
    return {
        "flat": DailyProfile(
            "flat", _("Even, all day"), weights_from_hourly([Fraction(1)] * HOURS_PER_DAY)
        ),
        "day": DailyProfile(
            "day",
            _("Daytime, 06:00 to 22:00"),
            weights_from_hourly(hourly_split(DAY_PROFILE_DAYTIME_SHARE)),
        ),
        "night": DailyProfile(
            "night",
            _("Night, 22:00 to 06:00"),
            weights_from_hourly(hourly_split(1 - NIGHT_PROFILE_NIGHT_SHARE)),
        ),
    }


def custom_profile(hourly_values: Sequence[int | float | Decimal | Fraction]) -> DailyProfile:
    if len(hourly_values) != HOURS_PER_DAY:
        raise ValueError(f"Expected 24 hourly values, got {len(hourly_values)}")
    hourly = [Fraction(str(value)) for value in hourly_values]
    if any(value < 0 for value in hourly):
        raise ValueError("Hourly values must not be negative")
    if sum(hourly, Fraction(0)) <= 0:
        raise ValueError("At least one hourly value must be positive")
    return DailyProfile("custom", _("Custom"), weights_from_hourly(hourly))


@dataclass(frozen=True, slots=True)
class MonthResult:
    year: int
    month: int
    energy_kwh: Decimal
    dynamic_cost_pln: Decimal
    fixed_cost_pln: Decimal
    days_covered: int
    days_in_month: int

    @property
    def difference_pln(self) -> Decimal:
        return self.fixed_cost_pln - self.dynamic_cost_pln

    @property
    def complete(self) -> bool:
        return self.days_covered == self.days_in_month

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def avg_dynamic_price_pln_kwh(self) -> Decimal | None:
        if self.energy_kwh == 0:
            return None
        return (self.dynamic_cost_pln / self.energy_kwh).quantize(Decimal("0.0001"))


@dataclass(frozen=True, slots=True)
class TariffComparison:
    months: list[MonthResult]
    monthly_kwh: Decimal
    fixed_price_pln_kwh: Decimal
    profile: DailyProfile

    @property
    def total_energy_kwh(self) -> Decimal:
        return sum((month.energy_kwh for month in self.months), Decimal(0)).quantize(MONEY)

    @property
    def total_dynamic_pln(self) -> Decimal:
        return sum((month.dynamic_cost_pln for month in self.months), Decimal(0)).quantize(MONEY)

    @property
    def total_fixed_pln(self) -> Decimal:
        return sum((month.fixed_cost_pln for month in self.months), Decimal(0)).quantize(MONEY)

    @property
    def total_difference_pln(self) -> Decimal:
        return self.total_fixed_pln - self.total_dynamic_pln

    @property
    def cheaper(self) -> Literal["dynamic", "fixed", "equal"]:
        difference = self.total_difference_pln
        if difference > 0:
            return "dynamic"
        if difference < 0:
            return "fixed"
        return "equal"

    @property
    def largest_monthly_cost(self) -> Decimal:
        costs = [
            max(month.dynamic_cost_pln, month.fixed_cost_pln, Decimal(0)) for month in self.months
        ]
        return max(costs, default=Decimal(0))


@dataclass(slots=True)
class MonthAccumulator:
    energy_kwh: Decimal = Decimal(0)
    dynamic_cost_pln: Decimal = Decimal(0)
    days: set[date] = field(default_factory=set)


def share_as_decimal(share: Fraction) -> Decimal:
    return Decimal(share.numerator) / Decimal(share.denominator)


def simulate_tariff(
    prices: Iterable[PriceSlot],
    monthly_kwh: Decimal,
    profile: DailyProfile,
    fixed_price_pln_kwh: Decimal,
    start: date,
    end: date,
) -> TariffComparison:
    by_day: dict[date, list[tuple[int, Decimal]]] = defaultdict(list)
    for slot in prices:
        local = to_local(slot.start_utc)
        day = local.date()
        if not start <= day <= end:
            continue
        by_day[day].append((local.hour * SLOTS_PER_HOUR + local.minute // 15, slot.price_pln_mwh))

    months: dict[tuple[int, int], MonthAccumulator] = defaultdict(MonthAccumulator)
    share_cache: dict[tuple[Fraction, int], Decimal] = {}
    for day, entries in by_day.items():
        present_weight = sum((profile.weights[index] for index, _price in entries), Fraction(0))
        if present_weight == 0:
            continue
        daily_budget = monthly_kwh / monthrange(day.year, day.month)[1]
        accumulator = months[(day.year, day.month)]
        accumulator.days.add(day)
        for index, price in entries:
            cache_key = (present_weight, index)
            share = share_cache.get(cache_key)
            if share is None:
                share = share_as_decimal(profile.weights[index] / present_weight)
                share_cache[cache_key] = share
            energy = daily_budget * share
            accumulator.energy_kwh += energy
            accumulator.dynamic_cost_pln += price * energy / KWH_PER_MWH

    results = [
        MonthResult(
            year=year,
            month=month,
            energy_kwh=accumulator.energy_kwh.quantize(MONEY, rounding=ROUND_HALF_UP),
            dynamic_cost_pln=accumulator.dynamic_cost_pln.quantize(MONEY, rounding=ROUND_HALF_UP),
            fixed_cost_pln=(fixed_price_pln_kwh * accumulator.energy_kwh).quantize(
                MONEY, rounding=ROUND_HALF_UP
            ),
            days_covered=len(accumulator.days),
            days_in_month=monthrange(year, month)[1],
        )
        for (year, month), accumulator in sorted(months.items())
    ]
    return TariffComparison(
        months=results,
        monthly_kwh=monthly_kwh,
        fixed_price_pln_kwh=fixed_price_pln_kwh,
        profile=profile,
    )
