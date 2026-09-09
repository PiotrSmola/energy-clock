from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.utils.translation import gettext_lazy as _

from apps.market.models import GenerationPoint, PricePoint
from apps.market.timeutils import INTERVAL, day_bounds_utc
from apps.planner.optimizer import (
    CarbonSlot,
    PriceSlot,
    Window,
    cheapest_window,
    greenest_window,
    most_expensive_window,
    window_starting_at,
)

REFERENCE_DURATION_MINUTES = 120
REFERENCE_POWER_KW = Decimal("2")
PERCENT = Decimal(100)


@dataclass(frozen=True, slots=True)
class PlanRequest:
    duration_minutes: int
    power_kw: Decimal
    not_before: datetime | None
    not_after: datetime | None
    label: str


@dataclass(frozen=True, slots=True)
class Plan:
    request: PlanRequest
    now: datetime
    slots: list[PriceSlot]
    cheapest: Window | None
    greenest: Window | None
    most_expensive: Window | None
    start_now: Window | None

    @property
    def has_data(self) -> bool:
        return bool(self.slots)

    @property
    def horizon_end(self) -> datetime | None:
        return self.slots[-1].end_utc if self.slots else None

    @property
    def savings_pln(self) -> Decimal | None:
        if self.cheapest is None or self.start_now is None:
            return None
        return self.start_now.cost_pln - self.cheapest.cost_pln

    @property
    def extra_cost_pln(self) -> Decimal | None:
        savings = self.savings_pln
        if savings is None or savings >= 0:
            return None
        return -savings

    @property
    def savings_percent(self) -> int | None:
        savings = self.savings_pln
        if savings is None or self.start_now is None or self.start_now.cost_pln <= 0:
            return None
        ratio = savings / self.start_now.cost_pln * PERCENT
        return int(ratio.quantize(Decimal(1), rounding=ROUND_HALF_UP))

    @property
    def energy_kwh(self) -> Decimal:
        return (
            self.request.power_kw * Decimal(self.request.duration_minutes) / Decimal(60)
        ).quantize(Decimal("0.01"))


def load_price_slots(start_utc: datetime, end_utc: datetime | None = None) -> list[PriceSlot]:
    prices = PricePoint.objects.filter(dtime_utc__gt=start_utc)
    generation = GenerationPoint.objects.filter(dtime_utc__gt=start_utc)
    if end_utc is not None:
        prices = prices.filter(dtime_utc__lte=end_utc)
        generation = generation.filter(dtime_utc__lte=end_utc)
    carbon_by_end = dict(generation.values_list("dtime_utc", "carbon_g_kwh"))
    return [
        PriceSlot(
            start_utc=point.dtime_utc - INTERVAL,
            price_pln_mwh=point.price_pln_mwh,
            carbon_g_kwh=carbon_by_end.get(point.dtime_utc),
        )
        for point in prices.order_by("dtime_utc")
    ]


def carbon_slots(slots: list[PriceSlot]) -> list[CarbonSlot]:
    return [CarbonSlot(start_utc=slot.start_utc, carbon_g_kwh=slot.carbon_g_kwh) for slot in slots]


def build_plan(request: PlanRequest, now: datetime) -> Plan:
    slots = load_price_slots(now)
    not_before = now
    if request.not_before is not None and request.not_before > now:
        not_before = request.not_before
    constraints = {"not_before": not_before, "not_after": request.not_after}
    return Plan(
        request=request,
        now=now,
        slots=slots,
        cheapest=cheapest_window(slots, request.duration_minutes, request.power_kw, **constraints),
        greenest=greenest_window(
            slots, carbon_slots(slots), request.duration_minutes, request.power_kw, **constraints
        ),
        most_expensive=most_expensive_window(
            slots, request.duration_minutes, request.power_kw, **constraints
        ),
        start_now=window_starting_at(slots, now, request.duration_minutes, request.power_kw),
    )


def reference_request() -> PlanRequest:
    return PlanRequest(
        duration_minutes=REFERENCE_DURATION_MINUTES,
        power_kw=REFERENCE_POWER_KW,
        not_before=None,
        not_after=None,
        label=str(_("2 kW appliance")),
    )


def reference_plan(now: datetime) -> Plan:
    return build_plan(reference_request(), now)


@dataclass(frozen=True, slots=True)
class DaySummary:
    day: date
    points: list[PricePoint]
    lowest: PricePoint | None
    highest: PricePoint | None
    average: Decimal | None
    cheapest: Window | None
    most_expensive: Window | None

    @property
    def spread_ratio(self) -> Decimal | None:
        if self.lowest is None or self.highest is None or self.lowest.price_pln_mwh <= 0:
            return None
        return (self.highest.price_pln_mwh / self.lowest.price_pln_mwh).quantize(Decimal("0.1"))

    @property
    def published_at(self) -> datetime | None:
        return self.points[0].published_at if self.points else None


def summarize_day(
    day: date,
    now: datetime,
    *,
    duration_minutes: int = REFERENCE_DURATION_MINUTES,
    power_kw: Decimal = REFERENCE_POWER_KW,
) -> DaySummary:
    start_utc, end_utc = day_bounds_utc(day)
    slots = load_price_slots(start_utc, end_utc)
    points = list(PricePoint.objects.filter(business_date=day).order_by("dtime_utc"))
    if not points:
        return DaySummary(day, [], None, None, None, None, None)
    not_before = now if start_utc <= now < end_utc else None
    prices = [point.price_pln_mwh for point in points]
    return DaySummary(
        day=day,
        points=points,
        lowest=min(points, key=lambda point: point.price_pln_mwh),
        highest=max(points, key=lambda point: point.price_pln_mwh),
        average=(sum(prices, Decimal(0)) / len(prices)).quantize(Decimal("0.01")),
        cheapest=cheapest_window(slots, duration_minutes, power_kw, not_before=not_before),
        most_expensive=most_expensive_window(
            slots, duration_minutes, power_kw, not_before=not_before
        ),
    )
