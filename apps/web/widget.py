from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt

from apps.market.models import PricePoint
from apps.market.queries import price_bucket, price_point_at, price_thresholds
from apps.planner.optimizer import Window
from apps.planner.services import reference_plan

WIDGET_CACHE_SECONDS = 300
PALETTE = ("#155f7e", "#2b87a1", "#7fb5b8", "#c4beb0", "#e2a45c", "#d26f3d", "#a5301c")


@dataclass(frozen=True, slots=True)
class WidgetData:
    now: datetime
    now_point: PricePoint | None
    bucket: int | None
    color: str
    cheapest: Window | None
    savings_pln: Decimal | None


def widget_data(now: datetime) -> WidgetData:
    point = price_point_at(now)
    bucket = None if point is None else price_bucket(point.price_pln_mwh, price_thresholds(now))
    plan = reference_plan(now)
    return WidgetData(
        now=now,
        now_point=point,
        bucket=bucket,
        color=PALETTE[bucket] if bucket is not None else "#62656b",
        cheapest=plan.cheapest,
        savings_pln=plan.savings_pln,
    )


@xframe_options_exempt
def widget(request: HttpRequest) -> HttpResponse:
    response = render(request, "web/widget.html", {"data": widget_data(timezone.now())})
    response["Cache-Control"] = f"public, max-age={WIDGET_CACHE_SECONDS}"
    return response


def widget_svg(request: HttpRequest) -> HttpResponse:
    body = render_to_string("web/widget.svg", {"data": widget_data(timezone.now())}, request)
    response = HttpResponse(body, content_type="image/svg+xml; charset=utf-8")
    response["Cache-Control"] = f"public, max-age={WIDGET_CACHE_SECONDS}"
    return response
