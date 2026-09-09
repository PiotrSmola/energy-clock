from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import translation

from apps.planner.catalog import SYSTEM_TEMPLATES
from apps.planner.models import Appliance

pytestmark = pytest.mark.django_db


def test_system_templates_are_seeded_by_migrations() -> None:
    templates = {appliance.key: appliance for appliance in Appliance.objects.filter(user=None)}

    assert set(templates) == {template.key for template in SYSTEM_TEMPLATES}
    assert templates["washing_machine"].duration_minutes == 120
    assert templates["washing_machine"].power_kw == Decimal("2.0")
    assert templates["dishwasher"].duration_minutes == 150
    assert templates["dishwasher"].power_kw == Decimal("1.8")
    assert templates["dryer"].duration_minutes == 90
    assert templates["dryer"].power_kw == Decimal("2.5")
    assert templates["ev_charging"].duration_minutes == 360
    assert templates["ev_charging"].power_kw == Decimal("11.0")
    assert templates["heat_pump"].duration_minutes == 240
    assert templates["heat_pump"].power_kw == Decimal("3.0")
    assert templates["water_heater"].duration_minutes == 180
    assert templates["water_heater"].power_kw == Decimal("2.0")


def test_templates_keep_catalog_order() -> None:
    keys = list(Appliance.objects.filter(user=None).values_list("key", flat=True))

    assert keys == [template.key for template in SYSTEM_TEMPLATES]


def test_template_display_name_is_translated() -> None:
    dryer = Appliance.objects.get(user=None, key="dryer")

    with translation.override("en"):
        assert dryer.display_name == "Tumble dryer"
    with translation.override("pl"):
        assert dryer.display_name == "Suszarka"


def test_user_appliance_keeps_its_own_name() -> None:
    user = User.objects.create_user("anna", password="pass-12345")
    sauna = Appliance.objects.create(
        user=user, name="Sauna", duration_minutes=60, power_kw=Decimal("6.0")
    )

    assert sauna.is_template is False
    with translation.override("pl"):
        assert sauna.display_name == "Sauna"
    assert Appliance.objects.filter(user=user).count() == 1
