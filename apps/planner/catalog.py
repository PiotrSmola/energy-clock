from dataclasses import dataclass
from decimal import Decimal

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True, slots=True)
class ApplianceTemplate:
    key: str
    name: str
    duration_minutes: int
    power_kw: Decimal
    is_interruptible: bool


SYSTEM_TEMPLATES: tuple[ApplianceTemplate, ...] = (
    ApplianceTemplate("washing_machine", "Washing machine", 120, Decimal("2.0"), False),
    ApplianceTemplate("dishwasher", "Dishwasher", 150, Decimal("1.8"), False),
    ApplianceTemplate("dryer", "Tumble dryer", 90, Decimal("2.5"), False),
    ApplianceTemplate("ev_charging", "EV charging", 360, Decimal("11.0"), True),
    ApplianceTemplate("heat_pump", "Heat pump", 240, Decimal("3.0"), True),
    ApplianceTemplate("water_heater", "Water heater", 180, Decimal("2.0"), True),
)

TEMPLATE_LABELS: dict[str, Promise] = {
    "washing_machine": _("Washing machine"),
    "dishwasher": _("Dishwasher"),
    "dryer": _("Tumble dryer"),
    "ev_charging": _("EV charging"),
    "heat_pump": _("Heat pump"),
    "water_heater": _("Water heater"),
}
