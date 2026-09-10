from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import (
    CharField,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Func,
    Max,
    Min,
    Q,
    QuerySet,
    Sum,
    Value,
)
from django.db.models.functions import Cast, ExtractHour, ExtractWeekDay, TruncMonth

from apps.market.models import GenerationPoint, PricePoint
from apps.market.timeutils import WARSAW

MONEY = Decimal("0.01")
PERCENT_PLACES = Decimal("0.1")
HOURS_PER_DAY = 24
ISO_WEEKDAYS = (1, 2, 3, 4, 5, 6, 7)


@dataclass(frozen=True, slots=True)
class HourStat:
    hour: int
    avg_price: Decimal
    intervals: int


@dataclass(frozen=True, slots=True)
class WeekdayStat:
    iso_weekday: int
    avg_price: Decimal
    intervals: int


@dataclass(frozen=True, slots=True)
class MonthShare:
    month: date
    res_share_percent: Decimal | None
    intervals: int


@dataclass(frozen=True, slots=True)
class MonthCount:
    month: date
    negative_intervals: int
    intervals: int


@dataclass(frozen=True, slots=True)
class PriceOverview:
    first_day: date | None
    last_day: date | None
    intervals: int
    min_price: Decimal | None
    max_price: Decimal | None
    avg_price: Decimal | None
    negative_intervals: int


def price_scope(start: date | None, end: date | None) -> QuerySet[PricePoint]:
    queryset = PricePoint.objects.all()
    if start is not None:
        queryset = queryset.filter(business_date__gte=start)
    if end is not None:
        queryset = queryset.filter(business_date__lte=end)
    return queryset


def generation_scope(start: date | None, end: date | None) -> QuerySet[GenerationPoint]:
    queryset = GenerationPoint.objects.all()
    if start is not None:
        queryset = queryset.filter(business_date__gte=start)
    if end is not None:
        queryset = queryset.filter(business_date__lte=end)
    return queryset


def start_hour_of_interval_end(end_hour: int, end_minute: int) -> int:
    if end_minute == 0:
        return (end_hour - 1) % HOURS_PER_DAY
    return end_hour


def average_price_by_hour(start: date | None = None, end: date | None = None) -> list[HourStat]:
    rows = (
        price_scope(start, end)
        .annotate(
            end_hour=ExtractHour("dtime_utc", tzinfo=WARSAW),
            end_minute=Func(
                Value("%M"), F("dtime_utc"), function="strftime", output_field=CharField()
            ),
        )
        .values("end_hour", "end_minute")
        .annotate(total=Sum("price_pln_mwh"), intervals=Count("id"))
        .order_by("end_hour", "end_minute")
    )
    totals: dict[int, Decimal] = defaultdict(Decimal)
    counts: dict[int, int] = defaultdict(int)
    for row in rows:
        hour = start_hour_of_interval_end(row["end_hour"], int(row["end_minute"]))
        totals[hour] += row["total"]
        counts[hour] += row["intervals"]
    return [
        HourStat(
            hour=hour,
            avg_price=(totals[hour] / counts[hour]).quantize(MONEY, rounding=ROUND_HALF_UP),
            intervals=counts[hour],
        )
        for hour in range(HOURS_PER_DAY)
        if counts[hour]
    ]


def average_price_by_weekday(
    start: date | None = None, end: date | None = None
) -> list[WeekdayStat]:
    rows = (
        price_scope(start, end)
        .annotate(weekday=ExtractWeekDay("business_date"))
        .values("weekday")
        .annotate(total=Sum("price_pln_mwh"), intervals=Count("id"))
    )
    by_iso: dict[int, tuple[Decimal, int]] = {}
    for row in rows:
        iso_weekday = 7 if row["weekday"] == 1 else row["weekday"] - 1
        by_iso[iso_weekday] = (row["total"], row["intervals"])
    return [
        WeekdayStat(
            iso_weekday=iso_weekday,
            avg_price=(by_iso[iso_weekday][0] / by_iso[iso_weekday][1]).quantize(
                MONEY, rounding=ROUND_HALF_UP
            ),
            intervals=by_iso[iso_weekday][1],
        )
        for iso_weekday in ISO_WEEKDAYS
        if iso_weekday in by_iso
    ]


def res_share_by_month(start: date | None = None, end: date | None = None) -> list[MonthShare]:
    renewable = Cast(Sum("pv_mw") + Sum("wind_mw"), FloatField())
    total = Cast(Sum("pv_mw") + Sum("wind_mw") + Sum("conventional_mw"), FloatField())
    rows = (
        generation_scope(start, end)
        .annotate(month=TruncMonth("business_date"))
        .values("month")
        .annotate(
            res_share=ExpressionWrapper(renewable * 100.0 / total, output_field=FloatField()),
            intervals=Count("id"),
        )
        .order_by("month")
    )
    return [
        MonthShare(
            month=row["month"],
            res_share_percent=(
                None
                if row["res_share"] is None
                else Decimal(str(row["res_share"])).quantize(PERCENT_PLACES, rounding=ROUND_HALF_UP)
            ),
            intervals=row["intervals"],
        )
        for row in rows
    ]


def negative_prices_by_month(
    start: date | None = None, end: date | None = None
) -> list[MonthCount]:
    rows = (
        price_scope(start, end)
        .annotate(month=TruncMonth("business_date"))
        .values("month")
        .annotate(
            negative=Count("id", filter=Q(price_pln_mwh__lt=0)),
            intervals=Count("id"),
        )
        .order_by("month")
    )
    return [
        MonthCount(
            month=row["month"], negative_intervals=row["negative"], intervals=row["intervals"]
        )
        for row in rows
    ]


def price_overview(start: date | None = None, end: date | None = None) -> PriceOverview:
    summary = price_scope(start, end).aggregate(
        first_day=Min("business_date"),
        last_day=Max("business_date"),
        intervals=Count("id"),
        min_price=Min("price_pln_mwh"),
        max_price=Max("price_pln_mwh"),
        total=Sum("price_pln_mwh"),
        negative=Count("id", filter=Q(price_pln_mwh__lt=0)),
    )
    intervals = summary["intervals"]
    return PriceOverview(
        first_day=summary["first_day"],
        last_day=summary["last_day"],
        intervals=intervals,
        min_price=summary["min_price"],
        max_price=summary["max_price"],
        avg_price=(
            (summary["total"] / intervals).quantize(MONEY, rounding=ROUND_HALF_UP)
            if intervals
            else None
        ),
        negative_intervals=summary["negative"],
    )


def lowest_price_point(start: date | None = None, end: date | None = None) -> PricePoint | None:
    return price_scope(start, end).order_by("price_pln_mwh", "dtime_utc").first()


def highest_price_point(start: date | None = None, end: date | None = None) -> PricePoint | None:
    return price_scope(start, end).order_by("-price_pln_mwh", "dtime_utc").first()


def generation_peaks(start: date | None = None, end: date | None = None) -> dict[str, object]:
    return generation_scope(start, end).aggregate(
        pv_peak=Max("pv_mw"), wind_peak=Max("wind_mw"), demand_peak=Max(F("demand_mw"))
    )
