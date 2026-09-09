from bisect import bisect_right
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from apps.market.timeutils import INTERVAL

COST_PLACES = Decimal("0.0001")
PRICE_PLACES = Decimal("0.01")
CARBON_PLACES = Decimal("0.1")
SECONDS_PER_HOUR = Decimal(3600)
KWH_PER_MWH = Decimal(1000)
ONE_SECOND = timedelta(seconds=1)


@dataclass(frozen=True, slots=True)
class PriceSlot:
    start_utc: datetime
    price_pln_mwh: Decimal
    carbon_g_kwh: Decimal | None = None

    @property
    def end_utc(self) -> datetime:
        return self.start_utc + INTERVAL


@dataclass(frozen=True, slots=True)
class CarbonSlot:
    start_utc: datetime
    carbon_g_kwh: Decimal | None


@dataclass(frozen=True, slots=True)
class Window:
    start_utc: datetime
    end_utc: datetime
    cost_pln: Decimal
    avg_price_pln_mwh: Decimal
    carbon_g: Decimal | None

    @property
    def duration(self) -> timedelta:
        return self.end_utc - self.start_utc


def sorted_slots(prices: Iterable[PriceSlot]) -> list[PriceSlot]:
    return sorted(prices, key=lambda slot: slot.start_utc)


def hours(delta: timedelta) -> Decimal:
    return Decimal(delta // ONE_SECOND) / SECONDS_PER_HOUR


def locate_slot(slots: Sequence[PriceSlot], at: datetime) -> int | None:
    starts = [slot.start_utc for slot in slots]
    index = bisect_right(starts, at) - 1
    if index >= 0 and slots[index].start_utc <= at < slots[index].end_utc:
        return index
    return None


def evaluate_window(
    slots: Sequence[PriceSlot],
    index: int,
    start_at: datetime,
    duration_minutes: int,
    power_kw: Decimal,
) -> Window | None:
    remaining = timedelta(minutes=duration_minutes)
    cursor = start_at
    cost = Decimal(0)
    energy_kwh = Decimal(0)
    carbon_g = Decimal(0)
    carbon_known = True
    while remaining > timedelta(0):
        if index >= len(slots):
            return None
        slot = slots[index]
        if not slot.start_utc <= cursor < slot.end_utc:
            return None
        covered = min(slot.end_utc, cursor + remaining) - cursor
        slot_energy_kwh = power_kw * hours(covered)
        cost += slot.price_pln_mwh * slot_energy_kwh / KWH_PER_MWH
        energy_kwh += slot_energy_kwh
        if slot.carbon_g_kwh is None:
            carbon_known = False
        else:
            carbon_g += slot.carbon_g_kwh * slot_energy_kwh
        cursor += covered
        remaining -= covered
        index += 1
    average = (cost * KWH_PER_MWH / energy_kwh) if energy_kwh else Decimal(0)
    return Window(
        start_utc=start_at,
        end_utc=cursor,
        cost_pln=cost.quantize(COST_PLACES, rounding=ROUND_HALF_UP),
        avg_price_pln_mwh=average.quantize(PRICE_PLACES, rounding=ROUND_HALF_UP),
        carbon_g=carbon_g.quantize(CARBON_PLACES, rounding=ROUND_HALF_UP) if carbon_known else None,
    )


def candidate_windows(
    slots: Sequence[PriceSlot],
    duration_minutes: int,
    power_kw: Decimal,
    not_before: datetime | None,
    not_after: datetime | None,
) -> Iterator[Window]:
    starts: list[tuple[int, datetime]] = []
    if not_before is not None:
        index = locate_slot(slots, not_before)
        if index is not None and slots[index].start_utc != not_before:
            starts.append((index, not_before))
    starts.extend(
        (index, slot.start_utc)
        for index, slot in enumerate(slots)
        if not_before is None or slot.start_utc >= not_before
    )
    for index, start_at in starts:
        window = evaluate_window(slots, index, start_at, duration_minutes, power_kw)
        if window is None:
            continue
        if not_after is not None and window.end_utc > not_after:
            continue
        yield window


def cheapest_window(
    prices: Iterable[PriceSlot],
    duration_minutes: int,
    power_kw: Decimal,
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> Window | None:
    candidates = candidate_windows(
        sorted_slots(prices), duration_minutes, power_kw, not_before, not_after
    )
    return min(candidates, key=lambda window: (window.cost_pln, window.start_utc), default=None)


def most_expensive_window(
    prices: Iterable[PriceSlot],
    duration_minutes: int,
    power_kw: Decimal,
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> Window | None:
    candidates = candidate_windows(
        sorted_slots(prices), duration_minutes, power_kw, not_before, not_after
    )
    return min(candidates, key=lambda window: (-window.cost_pln, window.start_utc), default=None)


def with_carbon(prices: Iterable[PriceSlot], generation: Iterable[CarbonSlot]) -> list[PriceSlot]:
    carbon_by_start = {slot.start_utc: slot.carbon_g_kwh for slot in generation}
    return [
        PriceSlot(
            start_utc=slot.start_utc,
            price_pln_mwh=slot.price_pln_mwh,
            carbon_g_kwh=carbon_by_start.get(slot.start_utc),
        )
        for slot in sorted_slots(prices)
    ]


def greenest_window(
    prices: Iterable[PriceSlot],
    generation: Iterable[CarbonSlot],
    duration_minutes: int,
    power_kw: Decimal,
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> Window | None:
    candidates = candidate_windows(
        with_carbon(prices, generation), duration_minutes, power_kw, not_before, not_after
    )
    with_known_carbon = (window for window in candidates if window.carbon_g is not None)
    return min(
        with_known_carbon,
        key=lambda window: (window.carbon_g, window.start_utc),
        default=None,
    )


def overlaps(first: Window, second: Window) -> bool:
    return first.start_utc < second.end_utc and second.start_utc < first.end_utc


def ranked_windows(
    prices: Iterable[PriceSlot],
    duration_minutes: int,
    power_kw: Decimal,
    *,
    by: Literal["cost", "carbon"],
    generation: Iterable[CarbonSlot] | None = None,
    limit: int = 5,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> list[Window]:
    slots = with_carbon(prices, generation) if generation is not None else sorted_slots(prices)
    candidates = list(candidate_windows(slots, duration_minutes, power_kw, not_before, not_after))
    if by == "carbon":
        candidates = [window for window in candidates if window.carbon_g is not None]
        candidates.sort(key=lambda window: (window.carbon_g, window.start_utc))
    else:
        candidates.sort(key=lambda window: (window.cost_pln, window.start_utc))
    chosen: list[Window] = []
    for window in candidates:
        if any(overlaps(window, picked) for picked in chosen):
            continue
        chosen.append(window)
        if len(chosen) >= limit:
            break
    return chosen


def window_starting_at(
    prices: Iterable[PriceSlot],
    start_at: datetime,
    duration_minutes: int,
    power_kw: Decimal,
) -> Window | None:
    slots = sorted_slots(prices)
    index = locate_slot(slots, start_at)
    if index is None:
        return None
    return evaluate_window(slots, index, start_at, duration_minutes, power_kw)


def cost_if_started_now(
    prices: Iterable[PriceSlot],
    duration_minutes: int,
    power_kw: Decimal,
    now: datetime,
) -> Decimal | None:
    window = window_starting_at(prices, now, duration_minutes, power_kw)
    return None if window is None else window.cost_pln
