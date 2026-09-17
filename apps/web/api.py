import csv
from collections.abc import Iterator
from datetime import date, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
    StreamingHttpResponse,
)
from django.http.response import HttpResponseBase
from django.utils import timezone

from apps.market.models import GenerationPoint, PricePoint
from apps.market.queries import generation_by_interval_end, price_points_for_day
from apps.market.timeutils import INTERVAL, to_local
from apps.planner.forms import next_local_occurrence
from apps.planner.optimizer import Window
from apps.planner.services import PlanRequest, build_plan

MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 24 * 60
MIN_POWER_KW = Decimal("0.05")
MAX_POWER_KW = Decimal("50")
MAX_EXPORT_DAYS = 400
DEFAULT_EXPORT_DAYS = 7
CSV_HEADER = (
    "business_date",
    "start_local",
    "end_local",
    "start_utc",
    "end_utc",
    "price_pln_mwh",
    "pv_mw",
    "wind_mw",
    "conventional_mw",
    "demand_mw",
    "carbon_g_kwh",
)


def parse_date_param(raw: str | None, default: date) -> date:
    if not raw:
        return default
    return date.fromisoformat(raw)


def parse_clock(raw: str | None) -> time | None:
    if not raw:
        return None
    return time.fromisoformat(raw)


def interval_payload(point: PricePoint, generation: GenerationPoint | None) -> dict[str, Any]:
    start_utc = point.dtime_utc - INTERVAL
    return {
        "start_utc": start_utc.isoformat(),
        "end_utc": point.dtime_utc.isoformat(),
        "start_local": to_local(start_utc).isoformat(),
        "end_local": to_local(point.dtime_utc).isoformat(),
        "price_pln_mwh": str(point.price_pln_mwh),
        "pv_mw": None if generation is None else decimal_or_none(generation.pv_mw),
        "wind_mw": None if generation is None else decimal_or_none(generation.wind_mw),
        "conventional_mw": (
            None if generation is None else decimal_or_none(generation.conventional_mw)
        ),
        "carbon_g_kwh": None if generation is None else decimal_or_none(generation.carbon_g_kwh),
    }


def decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def window_payload(window: Window | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "start_utc": window.start_utc.isoformat(),
        "end_utc": window.end_utc.isoformat(),
        "start_local": to_local(window.start_utc).isoformat(),
        "end_local": to_local(window.end_utc).isoformat(),
        "cost_pln": str(window.cost_pln),
        "avg_price_pln_mwh": str(window.avg_price_pln_mwh),
        "carbon_g": decimal_or_none(window.carbon_g),
    }


def prices(request: HttpRequest) -> HttpResponse:
    today = to_local(timezone.now()).date()
    try:
        day = parse_date_param(request.GET.get("date"), today)
    except ValueError:
        return HttpResponseBadRequest("date must be YYYY-MM-DD")
    points = price_points_for_day(day)
    generation = generation_by_interval_end(day)
    payload = {
        "business_date": day.isoformat(),
        "timezone": "Europe/Warsaw",
        "unit": "PLN/MWh",
        "source": "PSE day-ahead market (csdac-pln), generation plan (pdgobpkd)",
        "count": len(points),
        "intervals": [interval_payload(point, generation.get(point.dtime_utc)) for point in points],
    }
    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})


def windows(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    try:
        duration = int(request.GET.get("duration", "120"))
        power = Decimal(request.GET.get("power", "2.0"))
        not_before_clock = parse_clock(request.GET.get("not_before"))
        finish_by_clock = parse_clock(request.GET.get("finish_by"))
    except (ValueError, InvalidOperation):
        return HttpResponseBadRequest("duration must be an integer, power a decimal, times HH:MM")
    if not MIN_DURATION_MINUTES <= duration <= MAX_DURATION_MINUTES:
        return HttpResponseBadRequest("duration must be between 15 and 1440 minutes")
    if not MIN_POWER_KW <= power <= MAX_POWER_KW:
        return HttpResponseBadRequest("power must be between 0.05 and 50 kW")
    not_before = next_local_occurrence(not_before_clock, now) if not_before_clock else None
    not_after = (
        next_local_occurrence(finish_by_clock, not_before or now) if finish_by_clock else None
    )
    plan = build_plan(
        PlanRequest(duration, power, not_before, not_after, label="api"),
        now,
    )
    payload = {
        "generated_at": now.isoformat(),
        "duration_minutes": duration,
        "power_kw": str(power),
        "not_before": None if not_before is None else not_before.isoformat(),
        "not_after": None if not_after is None else not_after.isoformat(),
        "horizon_end": None if plan.horizon_end is None else plan.horizon_end.isoformat(),
        "cheapest": window_payload(plan.cheapest),
        "greenest": window_payload(plan.greenest),
        "most_expensive": window_payload(plan.most_expensive),
        "start_now": window_payload(plan.start_now),
        "savings_pln": decimal_or_none(plan.savings_pln),
        "note": (
            "Wholesale day-ahead prices; costs assume constant power draw; carbon is approximate."
        ),
    }
    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})


class Echo:
    def write(self, value: str) -> str:
        return value


def csv_rows(start: date, end: date) -> Iterator[str]:
    writer = csv.writer(Echo())
    yield writer.writerow(CSV_HEADER)
    generation = {
        point.dtime_utc: point
        for point in GenerationPoint.objects.filter(business_date__range=(start, end))
    }
    points = PricePoint.objects.filter(business_date__range=(start, end)).order_by("dtime_utc")
    for point in points.iterator(chunk_size=2000):
        plan = generation.get(point.dtime_utc)
        start_utc = point.dtime_utc - INTERVAL
        yield writer.writerow(
            (
                point.business_date.isoformat(),
                to_local(start_utc).strftime("%Y-%m-%d %H:%M"),
                to_local(point.dtime_utc).strftime("%Y-%m-%d %H:%M"),
                start_utc.strftime("%Y-%m-%d %H:%M"),
                point.dtime_utc.strftime("%Y-%m-%d %H:%M"),
                point.price_pln_mwh,
                "" if plan is None or plan.pv_mw is None else plan.pv_mw,
                "" if plan is None or plan.wind_mw is None else plan.wind_mw,
                "" if plan is None or plan.conventional_mw is None else plan.conventional_mw,
                "" if plan is None or plan.demand_mw is None else plan.demand_mw,
                "" if plan is None or plan.carbon_g_kwh is None else plan.carbon_g_kwh,
            )
        )


def export_csv(request: HttpRequest) -> HttpResponseBase:
    today = to_local(timezone.now()).date()
    try:
        end = parse_date_param(request.GET.get("end"), today)
        start = parse_date_param(
            request.GET.get("start"), end - timedelta(days=DEFAULT_EXPORT_DAYS - 1)
        )
    except ValueError:
        return HttpResponseBadRequest("start and end must be YYYY-MM-DD")
    if end < start:
        return HttpResponseBadRequest("end must not be before start")
    if (end - start).days + 1 > MAX_EXPORT_DAYS:
        return HttpResponseBadRequest(f"range must not exceed {MAX_EXPORT_DAYS} days")
    response = StreamingHttpResponse(csv_rows(start, end), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="energy-clock-{start.isoformat()}-{end.isoformat()}.csv"'
    )
    return response
