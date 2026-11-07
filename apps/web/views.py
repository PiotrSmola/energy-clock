from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

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
from apps.market.timeutils import INTERVAL, day_bounds_utc, to_local
from apps.planner.forms import DEFAULT_HOURLY_SHAPE, HOURS, PlannerForm, TariffForm
from apps.planner.models import Household, appliances_for
from apps.planner.services import (
    REFERENCE_DURATION_MINUTES,
    REFERENCE_POWER_KW,
    DaySummary,
    Plan,
    build_plan,
    reference_plan,
    reference_request,
    summarize_day,
    tariff_slots,
)
from apps.planner.tariff import TariffComparison, simulate_tariff
from apps.web.analysis import (
    PERIODS,
    compare_windows,
    hour_bars,
    negative_bars,
    res_bars,
    stats_bundle,
    summarize_mix,
    weekday_bars,
)
from apps.web.charts import (
    BarsChart,
    ChartModel,
    aggregate_hourly,
    build_bars,
    build_chart,
    build_ring,
)
from apps.web.context_processors import THEME_COOKIE, THEME_COOKIE_AGE, THEMES

DAY_KEYS = ("today", "tomorrow")
RESULT_HORIZON = timedelta(hours=48)
HOUR = timedelta(hours=1)
HOURLY_CHART_WIDTH = 480


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
    hourly_chart: ChartModel | None
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
    hourly_chart = None
    rows: list[IntervalRow] = []
    if summary.points:
        generation = generation_by_interval_end(day)
        chart = build_chart(
            summary.points, generation, thresholds=thresholds, now=now, window=summary.cheapest
        )
        hourly_points, hourly_generation = aggregate_hourly(summary.points, generation)
        hourly_chart = build_chart(
            hourly_points,
            hourly_generation,
            thresholds=thresholds,
            now=now,
            window=summary.cheapest,
            slot=HOUR,
            width=HOURLY_CHART_WIDTH,
        )
        rows = interval_rows(summary, generation, thresholds, now)
    return DayPanel(
        key=key,
        day=day,
        summary=summary,
        chart=chart,
        hourly_chart=hourly_chart,
        rows=rows,
        legend=legend_entries(thresholds),
        freshness=fresh,
        expected_publication=expected_publication(day),
        next_attempt=next_fetch_attempt(now),
        publication_late=publication_is_late(day, now),
    )


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


@require_POST
def set_theme(request: HttpRequest) -> HttpResponse:
    chosen = request.POST.get("theme", "")
    next_url = request.POST.get("next", "/")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/"
    response = HttpResponseRedirect(next_url)
    if chosen in THEMES:
        response.set_cookie(THEME_COOKIE, chosen, max_age=THEME_COOKIE_AGE, samesite="Lax")
    else:
        response.delete_cookie(THEME_COOKIE)
    return response


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
    form = PlannerForm(request.GET or None, user=request.user)
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
        "now_point": price_point_at(now),
        **appliance_context(request),
    }


def appliance_context(request: HttpRequest) -> dict[str, Any]:
    appliances = list(appliances_for(request.user))
    return {
        "system_appliances": [appliance for appliance in appliances if appliance.user_id is None],
        "own_appliances": [appliance for appliance in appliances if appliance.user_id is not None],
        "presets": {
            str(appliance.pk): {
                "duration_minutes": appliance.duration_minutes,
                "power_kw": str(appliance.power_kw),
            }
            for appliance in appliances
        },
    }


def planner(request: HttpRequest) -> HttpResponse:
    return render(request, "web/planner.html", planner_context(request))


def planner_result(request: HttpRequest) -> HttpResponse:
    return render(request, "web/partials/plan_result.html", planner_context(request))


def parse_day(raw: str | None, default: date) -> date:
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return default


def mix(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    today = to_local(now).date()
    day = parse_day(request.GET.get("day"), today)
    summary = summarize_mix(day, now)
    context = {
        "nav": "mix",
        "summary": summary,
        "now": now,
        "today": today,
        "previous_day": day - timedelta(days=1),
        "next_day": day + timedelta(days=1),
        "freshness": freshness(now),
    }
    template = "web/partials/mix_panel.html" if is_htmx(request) else "web/mix.html"
    return render(request, template, context)


def compare_context(request: HttpRequest) -> dict[str, Any]:
    now = timezone.now()
    today = to_local(now).date()
    form = PlannerForm(request.GET or None, user=request.user)
    duration = REFERENCE_DURATION_MINUTES
    power = REFERENCE_POWER_KW
    label = str(reference_request().label)
    if request.GET and form.is_valid():
        plan_request = form.to_request(now)
        duration, power, label = (
            plan_request.duration_minutes,
            plan_request.power_kw,
            plan_request.label,
        )
    raw_day = request.GET.get("day")
    day = parse_day(raw_day, today) if raw_day else None
    comparison = compare_windows(duration, power, now, day=day)
    thresholds = price_thresholds(now)
    chart = None
    if comparison.has_data:
        span = (
            comparison.horizon_start if day is None else day_bounds_utc(day)[0],
            comparison.horizon_end or comparison.horizon_start,
        )
        points = list(
            PricePoint.objects.filter(dtime_utc__gt=span[0], dtime_utc__lte=span[1]).order_by(
                "dtime_utc"
            )
        )
        generation = {
            point.dtime_utc: point
            for point in GenerationPoint.objects.filter(
                dtime_utc__gt=span[0], dtime_utc__lte=span[1]
            )
        }
        if points:
            chart = build_chart(
                points,
                generation,
                thresholds=thresholds,
                now=now,
                window=comparison.best_cheap,
                secondary_window=comparison.best_clean,
                span=(points[0].dtime_utc - INTERVAL, points[-1].dtime_utc),
                compact=True,
            )
    return {
        "nav": "compare",
        "form": form,
        "comparison": comparison,
        "chart": chart,
        "now": now,
        "day": day,
        "duration": duration,
        "power": power,
        "label": label,
        "freshness": freshness(now),
        **appliance_context(request),
    }


def compare(request: HttpRequest) -> HttpResponse:
    return render(request, "web/compare.html", compare_context(request))


def compare_result(request: HttpRequest) -> HttpResponse:
    return render(request, "web/partials/compare_result.html", compare_context(request))


def tariff_context(request: HttpRequest) -> dict[str, Any]:
    now = timezone.now()
    today = to_local(now).date()
    form = TariffForm(request.GET or None)
    comparison: TariffComparison | None = None
    chart = None
    period: tuple[date, date] | None = None
    if not request.GET:
        defaults = {
            name: field.initial for name, field in form.fields.items() if field.initial is not None
        }
        household_price = household_fixed_price(request)
        if household_price is not None:
            defaults["fixed_price_pln_kwh"] = household_price
        form = TariffForm(defaults)
    if form.is_valid():
        period = form.period(today)
        comparison = simulate_tariff(
            tariff_slots(*period),
            monthly_kwh=Decimal(form.cleaned_data["monthly_kwh"]),
            profile=form.to_profile(),
            fixed_price_pln_kwh=Decimal(form.cleaned_data["fixed_price_pln_kwh"]),
            start=period[0],
            end=period[1],
        )
        chart = tariff_bars(comparison)
    return {
        "nav": "tariff",
        "form": form,
        "comparison": comparison,
        "chart": chart,
        "period": period,
        "hourly_levels": form.hourly_levels()
        if form.is_bound and form.is_valid()
        else list(DEFAULT_HOURLY_SHAPE),
        "hours": list(HOURS),
        "now": now,
        "freshness": freshness(now),
    }


def household_fixed_price(request: HttpRequest) -> Decimal | None:
    if not request.user.is_authenticated:
        return None
    price: Decimal | None = (
        Household.objects.filter(user=request.user.pk)
        .values_list("tariff_fixed_pln_kwh", flat=True)
        .first()
    )
    return price


def tariff_bars(comparison: TariffComparison) -> BarsChart | None:
    if not comparison.months:
        return None
    return build_bars(
        [
            (
                f"{month.first_day:%m.%y}",
                month.difference_pln,
                f"{month.difference_pln:+.0f}",
                "b0" if month.difference_pln >= 0 else "b6",
            )
            for month in comparison.months
        ]
    )


def tariff(request: HttpRequest) -> HttpResponse:
    return render(request, "web/tariff.html", tariff_context(request))


def tariff_result(request: HttpRequest) -> HttpResponse:
    return render(request, "web/partials/tariff_result.html", tariff_context(request))


def stats(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    today = to_local(now).date()
    bundle = stats_bundle(request.GET.get("period", "all"), today)
    thresholds = price_thresholds(now)
    context = {
        "nav": "stats",
        "bundle": bundle,
        "now": now,
        "hour_chart": hour_bars(bundle.hours, thresholds) if bundle.hours else None,
        "weekday_chart": weekday_bars(bundle.weekdays, thresholds) if bundle.weekdays else None,
        "res_chart": res_bars(bundle.res_months) if bundle.res_months else None,
        "negative_chart": negative_bars(bundle.negative_months) if bundle.negative_months else None,
        "periods": list(PERIODS),
        "legend": legend_entries(thresholds),
    }
    return render(request, "web/stats.html", context)
