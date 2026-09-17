from django.contrib import admin

from apps.planner.models import Alert, AlertDelivery, Appliance, Household


@admin.register(Appliance)
class ApplianceAdmin(admin.ModelAdmin[Appliance]):
    list_display = ("name", "user", "key", "duration_minutes", "power_kw", "is_interruptible")
    list_filter = ("is_interruptible",)
    search_fields = ("name", "key", "user__email")


@admin.register(Household)
class HouseholdAdmin(admin.ModelAdmin[Household]):
    list_display = (
        "user",
        "contracted_power_kw",
        "pv_peak_kw",
        "storage_kwh",
        "tariff_fixed_pln_kwh",
        "prefers_green",
        "language",
    )
    search_fields = ("user__email",)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin[Alert]):
    list_display = ("user", "kind", "threshold", "is_active", "last_fired_at", "created_at")
    list_filter = ("kind", "is_active")
    search_fields = ("user__email",)


@admin.register(AlertDelivery)
class AlertDeliveryAdmin(admin.ModelAdmin[AlertDelivery]):
    list_display = ("sent_at", "alert", "business_date", "subject")
    list_filter = ("business_date",)
    readonly_fields = ("alert", "business_date", "sent_at", "subject")
    ordering = ("-sent_at",)
