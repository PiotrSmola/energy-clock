from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.market.models import GenerationPoint, PricePoint
from apps.market.queries import (
    Freshness,
    expected_publication,
    freshness,
    generation_by_interval_end,
    next_fetch_attempt,
    price_bucket,
    price_point_at,
    price_points_for_day,
    price_thresholds,
    publication_is_late,
    res_share_percent,
)
from apps.market.timeutils import INTERVAL, to_local
from apps.planner.forms import PlannerForm
from apps.planner.models import Appliance
from apps.planner.services import DaySummary, Plan, build_plan, reference_plan, summarize_day
from apps.web.charts import ChartModel, build_chart, build_ring

DAY_KEYS = ("today", "tomorrow")
RESULT_HORIZON = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class LegendEntry:
    bucket: int
    lower: Decimal | None
    upper: Decimal | None


@dataclass(frozen=True, slots=True)
class IntervalRow:
    start_local: datetime
    end_local: datetime
    price: Decimal
    bucket: int
    res_share: int | None
    is_now: bool
    in_window: bool


@dataclass(frozen=True, slots=True)
class DayPanel:
    key: str
    day: date
    summary: DaySummary
    chart: ChartModel | None
    rows: list[IntervalRow]
    legend: list[LegendEntry]
    freshness: Freshness
    expected_publication: datetime
    next_attempt: datetime | None
    publication_late: bool

    @property
    def available(self) -> bool:
        return bool(self.summary.points)


def legend_entries(thresholds: list[Decimal]) -> list[LegendEntry]:
    return [
        LegendEntry(
            bucket=bucket,
            lower=thresholds[bucket - 1] if bucket > 0 else None,
            upper=thresholds[bucket] if bucket < len(thresholds) else None,
        )
        for bucket in range(len(thresholds) + 1)
    ]


def interval_rows(
    summary: DaySummary,
    generation: dict[datetime, GenerationPoint],
    thresholds: list[Decimal],
    now: datetime,
) -> list[IntervalRow]:
    window = summary.cheapest
    rows: list[IntervalRow] = []
    for point in summary.points:
        start_utc = point.dtime_utc - INTERVAL
        rows.append(
            IntervalRow(
                start_local=to_local(start_utc),
                end_local=to_local(point.dtime_utc),
                price=point.price_pln_mwh,
                bucket=price_bucket(point.price_pln_mwh, thresholds),
                res_share=res_share_percent(generation.get(point.dtime_utc)),
                is_now=start_utc <= now < point.dtime_utc,
                in_window=window is not None
                and start_utc < window.end_utc
                and point.dtime_utc > window.start_utc,
            )
        )
    return rows


def build_day_panel(
    day: date, key: str, now: datetime, thresholds: list[Decimal], fresh: Freshness
) -> DayPanel:
    summary = summarize_day(day, now)
    chart = None
    rows: list[IntervalRow] = []
    if summary.points:
        generation = generation_by_interval_end(day)
        chart = build_chart(
            summary.points, generation, thresholds=thresholds, now=now, window=summary.cheapest
        )
        rows = interval_rows(summary, generation, thresholds, now)
    return DayPanel(
        key=key,
        day=day,
        summary=summary,
        chart=chart,
        rows=rows,
        legend=legend_entries(thresholds),
        freshness=fresh,
        expected_publication=expected_publication(day),
        next_attempt=next_fetch_attempt(now),
        publication_late=publication_is_late(day, now),
    )


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def home(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    key = request.GET.get("day", "today")
    if key not in DAY_KEYS:
        key = "today"
    today = to_local(now).date()
    day = today if key == "today" else today + timedelta(days=1)
    thresholds = price_thresholds(now)
    fresh = freshness(now)
    panel = build_day_panel(day, key, now, thresholds, fresh)
    if is_htmx(request):
        return render(request, "web/partials/day_panel.html", {"panel": panel, "now": now})
    today_points = panel.summary.points if key == "today" else price_points_for_day(today)
    context = {
        "nav": "home",
        "panel": panel,
        "plan": reference_plan(now),
        "now": now,
        "now_point": price_point_at(now),
        "ring": build_ring(today_points, thresholds, now),
    }
    return render(request, "web/home.html", context)


def result_chart(plan: Plan, thresholds: list[Decimal]) -> ChartModel | None:
    if not plan.has_data:
        return None
    horizon_end = plan.now + RESULT_HORIZON
    points = list(
        PricePoint.objects.filter(dtime_utc__gt=plan.now, dtime_utc__lte=horizon_end).order_by(
            "dtime_utc"
        )
    )
    if not points:
        return None
    generation = {
        point.dtime_utc: point
        for point in GenerationPoint.objects.filter(
            dtime_utc__gt=plan.now, dtime_utc__lte=horizon_end
        )
    }
    span = (points[0].dtime_utc - INTERVAL, points[-1].dtime_utc)
    return build_chart(
        points,
        generation,
        thresholds=thresholds,
        now=plan.now,
        window=plan.cheapest,
        span=span,
        compact=True,
    )


def planner_context(request: HttpRequest) -> dict[str, Any]:
    now = timezone.now()
    submitted = bool(request.GET)
    form = PlannerForm(request.GET or None)
    plan: Plan | None = None
    chart: ChartModel | None = None
    if submitted and form.is_valid():
        plan = build_plan(form.to_request(now), now)
        chart = result_chart(plan, price_thresholds(now))
    return {
        "nav": "planner",
        "form": form,
        "plan": plan,
        "chart": chart,
        "now": now,
        "submitted": submitted,
        "freshness": freshness(now),
        "presets": appliance_presets(),
        "appliances": list(Appliance.objects.filter(user=None)),
        "now_point": price_point_at(now),
    }


def appliance_presets() -> dict[str, dict[str, str | int]]:
    return {
        str(appliance.pk): {
            "duration_minutes": appliance.duration_minutes,
            "power_kw": str(appliance.power_kw),
        }
        for appliance in Appliance.objects.filter(user=None)
    }


def planner(request: HttpRequest) -> HttpResponse:
    return render(request, "web/planner.html", planner_context(request))


def planner_result(request: HttpRequest) -> HttpResponse:
    return render(request, "web/partials/plan_result.html", planner_context(request))
