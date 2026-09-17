from datetime import date, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core import mail

from apps.market.timeutils import WARSAW, day_bounds_utc
from apps.planner.alerts import evaluate_alert, run_alerts
from apps.planner.models import Alert, AlertDelivery, Household
from apps.planner.optimizer import PriceSlot
from apps.planner.services import load_price_slots
from tests.market_fixtures import seed_market_day

pytestmark = pytest.mark.django_db

SEPT_4 = date(2026, 9, 4)
SEPT_5 = date(2026, 9, 5)
NOW = datetime(2026, 9, 3, 14, 20, tzinfo=WARSAW)


@pytest.fixture
def anna() -> User:
    return User.objects.create_user("anna@example.com", "anna@example.com", "pass-12345")


@pytest.fixture
def two_days() -> None:
    seed_market_day(SEPT_4)
    seed_market_day(SEPT_5)


def slots_for(day: date) -> list[PriceSlot]:
    start, end = day_bounds_utc(day)
    return load_price_slots(start, end)


def test_price_below_fires_when_minimum_is_under_threshold(anna: User, two_days: None) -> None:
    alert = Alert.objects.create(user=anna, kind=Alert.Kind.PRICE_BELOW, threshold=Decimal("500"))

    trigger = evaluate_alert(alert, SEPT_4, slots_for(SEPT_4))

    assert trigger is not None
    assert trigger.business_date == SEPT_4
    assert trigger.context["lowest_price"] == Decimal("453.67")
    assert trigger.context["intervals_below"] > 0
    assert "500" in trigger.subject


def test_price_below_stays_quiet_when_threshold_is_lower_than_the_minimum(
    anna: User, two_days: None
) -> None:
    alert = Alert.objects.create(user=anna, kind=Alert.Kind.PRICE_BELOW, threshold=Decimal("400"))

    assert evaluate_alert(alert, SEPT_4, slots_for(SEPT_4)) is None


def test_negative_price_alert_only_fires_on_days_with_negative_prices(
    anna: User, two_days: None
) -> None:
    alert = Alert.objects.create(user=anna, kind=Alert.Kind.NEGATIVE_PRICE)

    assert evaluate_alert(alert, SEPT_4, slots_for(SEPT_4)) is None
    trigger = evaluate_alert(alert, SEPT_5, slots_for(SEPT_5))

    assert trigger is not None
    assert trigger.context["intervals_below"] == 10
    assert trigger.context["lowest_price"] < 0


def test_green_window_alert_uses_renewable_share_threshold(anna: User, two_days: None) -> None:
    relaxed = Alert.objects.create(user=anna, kind=Alert.Kind.GREEN_WINDOW, threshold=Decimal("50"))
    strict = Alert.objects.create(user=anna, kind=Alert.Kind.GREEN_WINDOW, threshold=Decimal("95"))

    trigger = evaluate_alert(relaxed, SEPT_4, slots_for(SEPT_4))

    assert trigger is not None
    assert trigger.context["res_share"] >= 50
    assert trigger.context["window_start"].hour in range(9, 17)
    assert evaluate_alert(strict, SEPT_4, slots_for(SEPT_4)) is None


def test_run_alerts_sends_one_email_and_never_twice_for_the_same_day(
    anna: User, two_days: None
) -> None:
    Alert.objects.create(user=anna, kind=Alert.Kind.PRICE_BELOW, threshold=Decimal("500"))

    first = run_alerts(SEPT_4, NOW)
    second = run_alerts(SEPT_4, NOW)

    assert len(first) == 1
    assert second == []
    assert AlertDelivery.objects.count() == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["anna@example.com"]
    assert "453.67" in mail.outbox[0].body
    assert Alert.objects.get().last_fired_at is not None


def test_run_alerts_fires_again_for_another_day(anna: User, two_days: None) -> None:
    Alert.objects.create(user=anna, kind=Alert.Kind.NEGATIVE_PRICE)

    assert run_alerts(SEPT_4, NOW) == []
    assert len(run_alerts(SEPT_5, NOW)) == 1
    assert len(run_alerts(SEPT_5, NOW)) == 0
    assert AlertDelivery.objects.filter(business_date=SEPT_5).count() == 1


def test_inactive_alerts_and_users_without_email_are_skipped(anna: User, two_days: None) -> None:
    Alert.objects.create(
        user=anna, kind=Alert.Kind.PRICE_BELOW, threshold=Decimal("500"), is_active=False
    )
    silent = User.objects.create_user("noemail", "", "pass-12345")
    Alert.objects.create(user=silent, kind=Alert.Kind.PRICE_BELOW, threshold=Decimal("500"))

    assert run_alerts(SEPT_4, NOW) == []
    assert mail.outbox == []


def test_email_language_follows_the_household_setting(anna: User, two_days: None) -> None:
    Household.objects.create(user=anna, language=Household.Language.POLISH)
    Alert.objects.create(user=anna, kind=Alert.Kind.PRICE_BELOW, threshold=Decimal("500"))

    run_alerts(SEPT_4, NOW)

    assert "PLN/MWh" in mail.outbox[0].body
    assert "poniżej" in mail.outbox[0].subject
    assert "453,67" in mail.outbox[0].body


def test_delivery_is_unique_per_alert_and_day(anna: User) -> None:
    alert = Alert.objects.create(user=anna, kind=Alert.Kind.NEGATIVE_PRICE)
    AlertDelivery.objects.create(alert=alert, business_date=SEPT_4, subject="x")

    with pytest.raises(Exception, match=r"UNIQUE|unique"):
        AlertDelivery.objects.create(alert=alert, business_date=SEPT_4, subject="y")
