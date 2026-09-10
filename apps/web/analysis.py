from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from apps.market.models import GenerationPoint, PricePoint
from apps.market.queries import price_bucket, res_share_percent
from apps.market.stats import (
    HourStat,
    MonthCount,
    MonthShare,
    PriceOverview,
    WeekdayStat,
    average_price_by_hour,
    average_price_by_weekday,
    highest_price_point,
    lowest_price_point,
    negative_prices_by_month,
    price_overview,
    res_share_by_month,
)
from apps.market.timeutils import INTERVAL, day_bounds_utc, to_local
from apps.planner.optimizer import PriceSlot, Window, ranked_windows
from apps.planner.services import carbon_slots, load_price_slots
from apps.web.charts import BarsChart, MixChart, build_bars, build_mix_chart

PERCENT = Decimal(100)
STATS_CACHE_SECONDS = 6 * 3600
RANKING_LIMIT = 5
GRAMS_PER_KG = Decimal(1000)


@dataclass(frozen=True, slots=True)
class MixRow:
    start_local: datetime
    end_local: datetime
    pv_mw: Decimal | None
    wind_mw: Decimal | None
    conventional_mw: Decimal | None
    demand_mw: Decimal | None
    carbon_g_kwh: Decimal | None
    res_share: int | None


@dataclass(frozen=True, slots=True)
class MixSummary:
    day: date
    points: list[GenerationPoint]
    chart: MixChart | None
    rows: list[MixRow]
    res_avg: int | None
    cleanest: MixRow | None
    dirtiest: MixRow | None
    pv_peak: MixRow | None
    wind_peak: MixRow | None
    demand_peak: MixRow | None
    published_at: datetime | None

    @property
    def available(self) -> bool:
        return bool(self.points)


def mix_row(point: GenerationPoint) -> MixRow:
    return MixRow(
        start_local=to_local(point.dtime_utc - INTERVAL),
        end_local=to_local(point.dtime_utc),
        pv_mw=point.pv_mw,
        wind_mw=point.wind_mw,
        conventional_mw=point.conventional_mw,
        demand_mw=point.demand_mw,
        carbon_g_kwh=point.carbon_g_kwh,
        res_share=res_share_percent(point),
    )


def summarize_mix(day: date, now: datetime) -> MixSummary:
    points = list(GenerationPoint.objects.filter(business_date=day).order_by("dtime_utc"))
    if not points:
        return MixSummary(day, [], None, [], None, None, None, None, None, None, None)
    rows = [mix_row(point) for point in points]
    with_res = [row for row in rows if row.res_share is not None]
    with_carbon = [row for row in rows if row.carbon_g_kwh is not None]
    res_avg = (
        int(
            (Decimal(sum(row.res_share or 0 for row in with_res)) / len(with_res)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        if with_res
        else None
    )
    return MixSummary(
        day=day,
        points=points,
        chart=build_mix_chart(points, now=now),
        rows=rows,
        res_avg=res_avg,
        cleanest=min(with_carbon, key=lambda row: row.carbon_g_kwh or 0, default=None),
        dirtiest=max(with_carbon, key=lambda row: row.carbon_g_kwh or 0, default=None),
        pv_peak=max(rows, key=lambda row: row.pv_mw or Decimal(-1), default=None),
        wind_peak=max(rows, key=lambda row: row.wind_mw or Decimal(-1), default=None),
        demand_peak=max(rows, key=lambda row: row.demand_mw or Decimal(-1), default=None),
        published_at=points[0].published_at,
    )


@dataclass(frozen=True, slots=True)
class Comparison:
    cheapest: list[Window]
    cleanest: list[Window]
    slots: list[PriceSlot]
    horizon_start: datetime
    horizon_end: datetime | None

    @property
    def has_data(self) -> bool:
        return bool(self.slots)

    @property
    def has_carbon(self) -> bool:
        return any(slot.carbon_g_kwh is not None for slot in self.slots)

    @property
    def best_cheap(self) -> Window | None:
        return self.cheapest[0] if self.cheapest else None

    @property
    def best_clean(self) -> Window | None:
        return self.cleanest[0] if self.cleanest else None

    @property
    def same_window(self) -> bool:
        cheap, clean = self.best_cheap, self.best_clean
        return cheap is not None and clean is not None and cheap.start_utc == clean.start_utc

    @property
    def extra_cost_pln(self) -> Decimal | None:
        cheap, clean = self.best_cheap, self.best_clean
        if cheap is None or clean is None:
            return None
        return clean.cost_pln - cheap.cost_pln

    @property
    def extra_cost_percent(self) -> int | None:
        cheap, extra = self.best_cheap, self.extra_cost_pln
        if cheap is None or extra is None or cheap.cost_pln <= 0:
            return None
        return int((extra / cheap.cost_pln * PERCENT).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    @property
    def carbon_saved_g(self) -> Decimal | None:
        cheap, clean = self.best_cheap, self.best_clean
        if cheap is None or clean is None or cheap.carbon_g is None or clean.carbon_g is None:
            return None
        return cheap.carbon_g - clean.carbon_g

    @property
    def carbon_saved_percent(self) -> int | None:
        cheap, saved = self.best_cheap, self.carbon_saved_g
        if cheap is None or saved is None or cheap.carbon_g is None or cheap.carbon_g <= 0:
            return None
        return int((saved / cheap.carbon_g * PERCENT).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def compare_windows(
    duration_minutes: int,
    power_kw: Decimal,
    now: datetime,
    *,
    day: date | None = None,
) -> Comparison:
    if day is None:
        slots = load_price_slots(now)
        not_before: datetime | None = now
        horizon_start = now
    else:
        start_utc, end_utc = day_bounds_utc(day)
        slots = load_price_slots(start_utc, end_utc)
        not_before = None
        horizon_start = start_utc
    generation = carbon_slots(slots)
    return Comparison(
        cheapest=ranked_windows(
            slots, duration_minutes, power_kw, by="cost", limit=RANKING_LIMIT, not_before=not_before
        ),
        cleanest=ranked_windows(
            slots,
            duration_minutes,
            power_kw,
            by="carbon",
            generation=generation,
            limit=RANKING_LIMIT,
            not_before=not_before,
        ),
        slots=slots,
        horizon_start=horizon_start,
        horizon_end=slots[-1].end_utc if slots else None,
    )


@dataclass(frozen=True, slots=True)
class StatsBundle:
    period_key: str
    start: date | None
    overview: PriceOverview
    hours: list[HourStat]
    weekdays: list[WeekdayStat]
    res_months: list[MonthShare]
    negative_months: list[MonthCount]
    lowest: PricePoint | None
    highest: PricePoint | None

    @property
    def cheapest_hour(self) -> HourStat | None:
        return min(self.hours, key=lambda stat: stat.avg_price, default=None)

    @property
    def priciest_hour(self) -> HourStat | None:
        return max(self.hours, key=lambda stat: stat.avg_price, default=None)


PERIODS: dict[str, timedelta | None] = {
    "all": None,
    "12m": timedelta(days=365),
    "30d": timedelta(days=30),
}


def stats_bundle(period_key: str, today: date, *, refresh: bool = False) -> StatsBundle:
    if period_key not in PERIODS:
        period_key = "all"
    span = PERIODS[period_key]
    start = None if span is None else today - span
    cache_key = f"stats:{period_key}:{today.isoformat()}"
    cached: StatsBundle | None = None if refresh else cache.get(cache_key)
    if cached is not None:
        return cached
    bundle = StatsBundle(
        period_key=period_key,
        start=start,
        overview=price_overview(start, None),
        hours=average_price_by_hour(start, None),
        weekdays=average_price_by_weekday(start, None),
        res_months=res_share_by_month(start, None),
        negative_months=negative_prices_by_month(start, None),
        lowest=lowest_price_point(start, None),
        highest=highest_price_point(start, None),
    )
    cache.set(cache_key, bundle, STATS_CACHE_SECONDS)
    return bundle


WEEKDAY_LABELS = (
    _("Mon"),
    _("Tue"),
    _("Wed"),
    _("Thu"),
    _("Fri"),
    _("Sat"),
    _("Sun"),
)


def hour_bars(hours: list[HourStat], thresholds: list[Decimal]) -> BarsChart:
    return build_bars(
        [
            (
                f"{stat.hour:02d}",
                stat.avg_price,
                f"{stat.avg_price:.0f}",
                f"b{price_bucket(stat.avg_price, thresholds)}",
            )
            for stat in hours
        ]
    )


def weekday_bars(weekdays: list[WeekdayStat], thresholds: list[Decimal]) -> BarsChart:
    return build_bars(
        [
            (
                str(WEEKDAY_LABELS[stat.iso_weekday - 1]),
                stat.avg_price,
                f"{stat.avg_price:.0f}",
                f"b{price_bucket(stat.avg_price, thresholds)}",
            )
            for stat in weekdays
        ]
    )


def month_label(month: date) -> str:
    return f"{month:%m.%y}"


def res_bars(months: list[MonthShare]) -> BarsChart:
    return build_bars(
        [
            (
                month_label(share.month),
                share.res_share_percent or Decimal(0),
                f"{share.res_share_percent or 0:.0f}%",
                "neutral",
            )
            for share in months
        ],
        y_max=Decimal(100),
    )


def negative_bars(months: list[MonthCount]) -> BarsChart:
    return build_bars(
        [
            (
                month_label(count.month),
                Decimal(count.negative_intervals),
                str(count.negative_intervals),
                "b0",
            )
            for count in months
        ]
    )
