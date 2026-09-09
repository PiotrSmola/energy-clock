from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django import template
from django.utils import formats
from django.utils.translation import gettext as _

from apps.market.timeutils import to_local

register = template.Library()

GRAMS_PER_KILOGRAM = Decimal(1000)


@register.filter
def money(value: Decimal | int | None, places: int = 2) -> str:
    if value is None:
        return ""
    quantized = Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return formats.number_format(quantized, decimal_pos=places, force_grouping=True)


@register.filter
def local_hm(value: datetime | None) -> str:
    if value is None:
        return ""
    return f"{to_local(value):%H:%M}"


@register.filter
def local_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return f"{to_local(value):%Y-%m-%d %H:%M}"


@register.filter
def local_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    return to_local(value).date()


@register.filter
def duration(minutes: int | timedelta | None) -> str:
    if minutes is None:
        return ""
    total = int(minutes.total_seconds() // 60) if isinstance(minutes, timedelta) else int(minutes)
    hours, rest = divmod(total, 60)
    if hours and rest:
        return _("%(hours)d h %(minutes)d min") % {"hours": hours, "minutes": rest}
    if hours:
        return _("%(hours)d h") % {"hours": hours}
    return _("%(minutes)d min") % {"minutes": rest}


@register.filter
def relative_day(value: datetime | None, now: datetime | None) -> str:
    if value is None or now is None:
        return ""
    target = to_local(value).date()
    today = to_local(now).date()
    if target == today:
        return _("today")
    if target == today + timedelta(days=1):
        return _("tomorrow")
    return formats.date_format(target, "j E")


@register.filter
def grams_to_kg(value: Decimal | None) -> str:
    if value is None:
        return ""
    kilograms = (Decimal(value) / GRAMS_PER_KILOGRAM).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return formats.number_format(kilograms, decimal_pos=2, force_grouping=True)


@register.filter
def bucket_class(bucket: int | None) -> str:
    return "" if bucket is None else f"b{bucket}"


@register.simple_tag
def attr_if(condition: object, attribute: str) -> str:
    return attribute if condition else ""
