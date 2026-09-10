from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.market.timeutils import WARSAW, to_local
from apps.planner.models import Appliance
from apps.planner.services import PlanRequest
from apps.planner.tariff import DailyProfile, builtin_profiles, custom_profile

MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 24 * 60
MIN_POWER_KW = Decimal("0.05")
MAX_POWER_KW = Decimal("50")


class TemplateChoiceField(forms.ModelChoiceField[Appliance]):
    def label_from_instance(self, obj: Appliance) -> str:
        return f"{obj.display_name} · {obj.duration_minutes} min · {obj.power_kw} kW"


class PlannerForm(forms.Form):
    appliance = TemplateChoiceField(
        queryset=Appliance.objects.filter(user=None),
        required=False,
        empty_label=str(_("Custom appliance")),
        label=_("Appliance"),
    )
    duration_minutes = forms.IntegerField(
        required=False,
        initial=120,
        label=_("Cycle length (minutes)"),
    )
    power_kw = forms.DecimalField(
        required=False,
        decimal_places=2,
        max_digits=6,
        initial=Decimal("2.0"),
        label=_("Power draw (kW)"),
    )
    earliest_start = forms.TimeField(
        required=False,
        label=_("Earliest start"),
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
    )
    finish_by = forms.TimeField(
        required=False,
        label=_("Must finish by"),
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
    )

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        appliance = data.get("appliance")
        if isinstance(appliance, Appliance):
            data["duration_minutes"] = appliance.duration_minutes
            data["power_kw"] = appliance.power_kw
            data["label"] = appliance.display_name
            return data
        data["label"] = str(_("Custom appliance"))
        duration = data.get("duration_minutes")
        power = data.get("power_kw")
        if duration is None or not MIN_DURATION_MINUTES <= duration <= MAX_DURATION_MINUTES:
            self.add_error(
                "duration_minutes",
                _("Enter a cycle length between %(low)d and %(high)d minutes.")
                % {"low": MIN_DURATION_MINUTES, "high": MAX_DURATION_MINUTES},
            )
        if power is None or not MIN_POWER_KW <= power <= MAX_POWER_KW:
            self.add_error(
                "power_kw",
                _("Enter a power draw between %(low)s and %(high)s kW.")
                % {"low": MIN_POWER_KW, "high": MAX_POWER_KW},
            )
        return data

    def to_request(self, now: datetime) -> PlanRequest:
        data = self.cleaned_data
        duration = int(data["duration_minutes"])
        earliest = data.get("earliest_start")
        deadline = data.get("finish_by")
        not_before = next_local_occurrence(earliest, now) if isinstance(earliest, time) else None
        not_after = None
        if isinstance(deadline, time):
            not_after = next_local_occurrence(deadline, not_before or now)
        return PlanRequest(
            duration_minutes=duration,
            power_kw=Decimal(data["power_kw"]),
            not_before=not_before,
            not_after=not_after,
            label=str(data["label"]),
        )


HOURS = range(24)
DEFAULT_HOURLY_SHAPE = (2, 1, 1, 1, 1, 2, 4, 6, 5, 4, 4, 4, 4, 4, 4, 5, 6, 8, 9, 8, 7, 6, 4, 3)
MIN_MONTHLY_KWH = Decimal("10")
MAX_MONTHLY_KWH = Decimal("100000")
MIN_FIXED_PRICE = Decimal("0.01")
MAX_FIXED_PRICE = Decimal("10")
MAX_HOURLY_LEVEL = 10


class TariffForm(forms.Form):
    monthly_kwh = forms.DecimalField(
        min_value=MIN_MONTHLY_KWH,
        max_value=MAX_MONTHLY_KWH,
        decimal_places=1,
        max_digits=8,
        initial=Decimal("250"),
        label=_("Monthly consumption (kWh)"),
    )
    fixed_price_pln_kwh = forms.DecimalField(
        min_value=MIN_FIXED_PRICE,
        max_value=MAX_FIXED_PRICE,
        decimal_places=4,
        max_digits=6,
        initial=Decimal("0.90"),
        label=_("Your fixed energy price (PLN/kWh)"),
    )
    profile = forms.ChoiceField(
        choices=(
            ("flat", _("Even, all day")),
            ("day", _("Daytime, 06:00 to 22:00")),
            ("night", _("Night, 22:00 to 06:00")),
            ("custom", _("Custom, hour by hour")),
        ),
        initial="flat",
        label=_("Daily profile"),
    )
    start = forms.DateField(required=False, label=_("From"))
    end = forms.DateField(required=False, label=_("To"))

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for hour in HOURS:
            self.fields[f"h{hour}"] = forms.IntegerField(
                required=False,
                min_value=0,
                max_value=MAX_HOURLY_LEVEL,
                initial=DEFAULT_HOURLY_SHAPE[hour],
            )

    def hourly_levels(self) -> list[int]:
        levels: list[int] = []
        for hour in HOURS:
            value = self.cleaned_data.get(f"h{hour}") if self.is_bound else None
            levels.append(DEFAULT_HOURLY_SHAPE[hour] if value is None else int(value))
        return levels

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        start = data.get("start")
        end = data.get("end")
        if start and end and end < start:
            self.add_error("end", _("The end date must not be before the start date."))
        if data.get("profile") == "custom" and sum(self.hourly_levels()) <= 0:
            self.add_error("profile", _("Set at least one hour above zero."))
        return data

    def to_profile(self) -> DailyProfile:
        key = self.cleaned_data.get("profile") or "flat"
        if key == "custom":
            return custom_profile(self.hourly_levels())
        return builtin_profiles()[key]

    def period(self, today: date) -> tuple[date, date]:
        end = self.cleaned_data.get("end") or today - timedelta(days=1)
        start = self.cleaned_data.get("start") or (
            end.replace(day=1) - timedelta(days=365)
        ).replace(day=1)
        return start, end


def next_local_occurrence(clock: time, after: datetime) -> datetime:
    local_after = to_local(after)
    candidate = datetime.combine(local_after.date(), clock, tzinfo=WARSAW)
    while candidate < local_after:
        candidate = datetime.combine(candidate.date() + timedelta(days=1), clock, tzinfo=WARSAW)
    return candidate
