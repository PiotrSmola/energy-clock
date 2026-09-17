import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import formats, translation
from django.utils.translation import gettext as _

from apps.market.carbon import EMISSION_FACTORS_G_KWH
from apps.market.timeutils import day_bounds_utc, to_local
from apps.planner.models import Alert, AlertDelivery, Household
from apps.planner.optimizer import PriceSlot, cheapest_window, greenest_window
from apps.planner.services import carbon_slots, load_price_slots

logger = logging.getLogger(__name__)

REFERENCE_DURATION_MINUTES = 120
REFERENCE_POWER_KW = Decimal("2")
GREEN_WINDOW_MINUTES = 60
GREEN_WINDOW_POWER_KW = Decimal("1")
DEFAULT_GREEN_SHARE_PERCENT = Decimal("50")
CONVENTIONAL_G_KWH = Decimal(EMISSION_FACTORS_G_KWH["conventional"])
PERCENT = Decimal(100)
SLOTS_PER_HOUR = Decimal(4)


@dataclass(frozen=True, slots=True)
class Trigger:
    alert: Alert
    business_date: date
    subject: str
    context: dict[str, Any]


def language_for(user: User) -> str:
    language = Household.objects.filter(user=user).values_list("language", flat=True).first()
    return language or str(settings.LANGUAGE_CODE)


def day_label(day: date) -> str:
    return formats.date_format(day, "l, j E")


def average_price(slots: list[PriceSlot]) -> Decimal:
    total = sum((slot.price_pln_mwh for slot in slots), Decimal(0))
    return (total / len(slots)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def base_context(alert: Alert, day: date, slots: list[PriceSlot]) -> dict[str, Any]:
    return {
        "alert": alert,
        "day": day,
        "day_average": average_price(slots),
        "reference_window": cheapest_window(slots, REFERENCE_DURATION_MINUTES, REFERENCE_POWER_KW),
        "site_url": settings.SITE_URL,
    }


def evaluate_price_below(alert: Alert, day: date, slots: list[PriceSlot]) -> Trigger | None:
    if alert.threshold is None:
        return None
    below = [slot for slot in slots if slot.price_pln_mwh < alert.threshold]
    if not below:
        return None
    lowest = min(below, key=lambda slot: (slot.price_pln_mwh, slot.start_utc))
    subject = _("Energy Clock: price below %(threshold)s PLN/MWh on %(day)s") % {
        "threshold": formats.number_format(alert.threshold, 0),
        "day": day_label(day),
    }
    context = base_context(alert, day, slots) | {
        "threshold": alert.threshold,
        "lowest_price": lowest.price_pln_mwh,
        "lowest_at": to_local(lowest.start_utc),
        "intervals_below": len(below),
        "hours_below": (Decimal(len(below)) / SLOTS_PER_HOUR).quantize(Decimal("0.25")),
    }
    return Trigger(alert, day, subject, context)


def evaluate_negative_price(alert: Alert, day: date, slots: list[PriceSlot]) -> Trigger | None:
    below = [slot for slot in slots if slot.price_pln_mwh < 0]
    if not below:
        return None
    lowest = min(below, key=lambda slot: (slot.price_pln_mwh, slot.start_utc))
    subject = _("Energy Clock: negative prices on %(day)s (%(count)d intervals)") % {
        "day": day_label(day),
        "count": len(below),
    }
    context = base_context(alert, day, slots) | {
        "threshold": Decimal(0),
        "lowest_price": lowest.price_pln_mwh,
        "lowest_at": to_local(lowest.start_utc),
        "intervals_below": len(below),
        "hours_below": (Decimal(len(below)) / SLOTS_PER_HOUR).quantize(Decimal("0.25")),
    }
    return Trigger(alert, day, subject, context)


def evaluate_green_window(alert: Alert, day: date, slots: list[PriceSlot]) -> Trigger | None:
    share = alert.threshold if alert.threshold is not None else DEFAULT_GREEN_SHARE_PERCENT
    max_carbon = CONVENTIONAL_G_KWH * (1 - share / PERCENT)
    window = greenest_window(
        slots, carbon_slots(slots), GREEN_WINDOW_MINUTES, GREEN_WINDOW_POWER_KW
    )
    if window is None or window.carbon_g is None or window.carbon_g > max_carbon:
        return None
    day_average = average_price(slots)
    if window.avg_price_pln_mwh > day_average:
        return None
    res_share = int(
        ((1 - window.carbon_g / CONVENTIONAL_G_KWH) * PERCENT).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    subject = _("Energy Clock: cheap and clean window %(start)s-%(end)s on %(day)s") % {
        "start": f"{to_local(window.start_utc):%H:%M}",
        "end": f"{to_local(window.end_utc):%H:%M}",
        "day": day_label(day),
    }
    context = base_context(alert, day, slots) | {
        "threshold": share,
        "window_start": to_local(window.start_utc),
        "window_end": to_local(window.end_utc),
        "window_price": window.avg_price_pln_mwh,
        "window_carbon": window.carbon_g,
        "res_share": res_share,
    }
    return Trigger(alert, day, subject, context)


EVALUATORS: dict[str, Callable[[Alert, date, list[PriceSlot]], Trigger | None]] = {
    Alert.Kind.PRICE_BELOW: evaluate_price_below,
    Alert.Kind.NEGATIVE_PRICE: evaluate_negative_price,
    Alert.Kind.GREEN_WINDOW: evaluate_green_window,
}


def evaluate_alert(alert: Alert, day: date, slots: list[PriceSlot]) -> Trigger | None:
    evaluator = EVALUATORS.get(alert.kind)
    if evaluator is None or not slots:
        return None
    with translation.override(language_for(alert.user)):
        return evaluator(alert, day, slots)


def deliver(trigger: Trigger, now: datetime) -> AlertDelivery | None:
    user = trigger.alert.user
    if not user.email:
        return None
    try:
        with transaction.atomic():
            delivery = AlertDelivery.objects.create(
                alert=trigger.alert,
                business_date=trigger.business_date,
                subject=trigger.subject,
                sent_at=now,
            )
    except IntegrityError:
        logger.info("Alert %s already delivered for %s", trigger.alert.pk, trigger.business_date)
        return None
    context = trigger.context | {"user": user, "kind": trigger.alert.kind}
    with translation.override(language_for(user)):
        text_body = render_to_string("email/alert.txt", context)
        html_body = render_to_string("email/alert.html", context)
    try:
        send_mail(
            trigger.subject,
            text_body,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            html_message=html_body,
        )
    except Exception:
        logger.exception("Failed to send alert %s to %s", trigger.alert.pk, user.email)
        delivery.delete()
        return None
    Alert.objects.filter(pk=trigger.alert.pk).update(last_fired_at=now)
    logger.info("Alert %s sent to %s: %s", trigger.alert.pk, user.email, trigger.subject)
    return delivery


def run_alerts(day: date, now: datetime) -> list[AlertDelivery]:
    start_utc, end_utc = day_bounds_utc(day)
    slots = load_price_slots(start_utc, end_utc)
    if not slots:
        return []
    deliveries: list[AlertDelivery] = []
    alerts = Alert.objects.filter(is_active=True).select_related("user").order_by("id")
    for alert in alerts:
        if not alert.user.email:
            continue
        if AlertDelivery.objects.filter(alert=alert, business_date=day).exists():
            continue
        trigger = evaluate_alert(alert, day, slots)
        if trigger is None:
            continue
        delivery = deliver(trigger, now)
        if delivery is not None:
            deliveries.append(delivery)
    return deliveries
