from datetime import datetime

from django.contrib import admin
from django.db.models import Model
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.market.models import GenerationPoint, IngestRun, PricePoint
from apps.market.timeutils import to_local


def format_local(value: datetime) -> str:
    return f"{to_local(value):%Y-%m-%d %H:%M}"


class ReadOnlyAdmin[ModelType: Model](admin.ModelAdmin[ModelType]):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: ModelType | None = None) -> bool:
        return False


@admin.register(IngestRun)
class IngestRunAdmin(ReadOnlyAdmin[IngestRun]):
    list_display = (
        "started_at",
        "endpoint",
        "business_date",
        "status",
        "records",
        "expected_records",
        "duration_seconds",
        "short_error",
    )
    list_filter = ("status", "endpoint")
    date_hierarchy = "business_date"
    ordering = ("-started_at",)
    search_fields = ("error",)
    readonly_fields = (
        "endpoint",
        "business_date",
        "status",
        "records",
        "expected_records",
        "started_at",
        "finished_at",
        "error",
    )
    list_per_page = 100

    @admin.display(description=_("Duration (s)"))
    def duration_seconds(self, obj: IngestRun) -> str:
        if obj.finished_at is None:
            return "-"
        return f"{(obj.finished_at - obj.started_at).total_seconds():.1f}"

    @admin.display(description=_("Error"))
    def short_error(self, obj: IngestRun) -> str:
        return obj.error[:80]


@admin.register(PricePoint)
class PricePointAdmin(ReadOnlyAdmin[PricePoint]):
    list_display = (
        "dtime_utc",
        "local_interval_end",
        "business_date",
        "price_pln_mwh",
        "published_at",
    )
    list_filter = (("business_date", admin.DateFieldListFilter),)
    date_hierarchy = "business_date"
    ordering = ("-dtime_utc",)
    readonly_fields = ("dtime_utc", "business_date", "price_pln_mwh", "published_at")
    list_per_page = 96

    @admin.display(description=_("Local interval end"))
    def local_interval_end(self, obj: PricePoint) -> str:
        return format_local(obj.dtime_utc)


@admin.register(GenerationPoint)
class GenerationPointAdmin(ReadOnlyAdmin[GenerationPoint]):
    list_display = (
        "dtime_utc",
        "local_interval_end",
        "business_date",
        "pv_mw",
        "wind_mw",
        "conventional_mw",
        "demand_mw",
        "carbon_g_kwh",
    )
    list_filter = (("business_date", admin.DateFieldListFilter),)
    date_hierarchy = "business_date"
    ordering = ("-dtime_utc",)
    readonly_fields = (
        "dtime_utc",
        "business_date",
        "pv_mw",
        "wind_mw",
        "conventional_mw",
        "demand_mw",
        "carbon_g_kwh",
        "published_at",
    )
    list_per_page = 96

    @admin.display(description=_("Local interval end"))
    def local_interval_end(self, obj: GenerationPoint) -> str:
        return format_local(obj.dtime_utc)
