from datetime import date, datetime
from decimal import Decimal

import pytest
import time_machine
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.market.timeutils import WARSAW
from apps.planner.models import Alert, Appliance, Household
from tests.market_fixtures import seed_market_day

pytestmark = pytest.mark.django_db

EMAIL = "Anna@Example.com"
PASSWORD = "correct-horse-battery"


@pytest.fixture
def client() -> Client:
    return Client()


def register(client: Client) -> None:
    client.post(
        reverse("web:register"),
        {"email": EMAIL, "password1": PASSWORD, "password2": PASSWORD},
    )


def test_registration_creates_user_household_and_signs_in(client: Client) -> None:
    response = client.post(
        reverse("web:register"),
        {"email": EMAIL, "password1": PASSWORD, "password2": PASSWORD},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("web:profile")
    user = User.objects.get(username="anna@example.com")
    assert user.email == "anna@example.com"
    assert Household.objects.filter(user=user).exists()
    profile = client.get(reverse("web:profile"))
    assert profile.status_code == 200
    assert "anna@example.com" in profile.content.decode()
    assert "Your account is ready." in profile.content.decode()


def test_registration_rejects_duplicates_and_mismatched_passwords(client: Client) -> None:
    register(client)
    client.post(reverse("web:logout"))

    duplicate = client.post(
        reverse("web:register"),
        {"email": "anna@example.com", "password1": PASSWORD, "password2": PASSWORD},
    )
    mismatch = client.post(
        reverse("web:register"),
        {"email": "new@example.com", "password1": PASSWORD, "password2": "other-password-1"},
    )
    weak = client.post(
        reverse("web:register"),
        {"email": "weak@example.com", "password1": "12345678", "password2": "12345678"},
    )

    assert "An account with this email already exists." in duplicate.content.decode()
    assert "The two passwords do not match." in mismatch.content.decode()
    assert weak.status_code == 200
    assert User.objects.count() == 1


def test_login_and_logout_flow(client: Client) -> None:
    register(client)
    client.post(reverse("web:logout"))

    wrong = client.post(
        reverse("web:login"), {"username": "anna@example.com", "password": "nope-nope-nope"}
    )
    assert wrong.status_code == 200
    assert client.get(reverse("web:profile")).status_code == 302

    right = client.post(reverse("web:login"), {"username": EMAIL, "password": PASSWORD})
    assert right.status_code == 302
    assert right.headers["Location"] == reverse("web:profile")
    assert client.get(reverse("web:profile")).status_code == 200

    logout = client.post(reverse("web:logout"))
    assert logout.status_code == 302
    redirected = client.get(reverse("web:profile"))
    assert redirected.status_code == 302
    assert redirected.headers["Location"].startswith(reverse("web:login"))


def test_logout_requires_post(client: Client) -> None:
    register(client)

    assert client.get(reverse("web:logout")).status_code == 405
    assert client.get(reverse("web:profile")).status_code == 200


@time_machine.travel(datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW), tick=False)
def test_planner_works_for_anonymous_users(client: Client) -> None:
    seed_market_day(date(2026, 9, 4))

    response = client.get(
        reverse("web:planner"), {"appliance": "", "duration_minutes": "120", "power_kw": "2"}
    )

    assert response.status_code == 200
    assert "Start at" in response.content.decode()
    assert "Your appliances" not in response.content.decode()


def test_forms_carry_csrf_tokens_and_reject_posts_without_them(client: Client) -> None:
    for name in ("web:login", "web:register"):
        body = client.get(reverse(name)).content.decode()
        assert "csrfmiddlewaretoken" in body
    register(client)
    assert client.get(reverse("web:profile")).content.decode().count("csrfmiddlewaretoken") >= 3

    strict = Client(enforce_csrf_checks=True)
    forbidden = strict.post(reverse("web:login"), {"username": EMAIL, "password": PASSWORD})

    assert forbidden.status_code == 403


def test_household_form_saves_values(client: Client) -> None:
    register(client)

    response = client.post(
        reverse("web:profile"),
        {
            "form": "household",
            "contracted_power_kw": "14.0",
            "pv_peak_kw": "6.5",
            "storage_kwh": "",
            "tariff_fixed_pln_kwh": "0.8500",
            "prefers_green": "on",
            "language": "pl",
        },
    )

    assert response.status_code == 302
    household = Household.objects.get(user__username="anna@example.com")
    assert household.contracted_power_kw == Decimal("14.0")
    assert household.pv_peak_kw == Decimal("6.50")
    assert household.storage_kwh is None
    assert household.tariff_fixed_pln_kwh == Decimal("0.8500")
    assert household.prefers_green is True
    assert household.language == "pl"


def test_household_fixed_price_prefills_the_tariff_calculator(client: Client) -> None:
    register(client)
    Household.objects.filter(user__username="anna@example.com").update(
        tariff_fixed_pln_kwh=Decimal("0.7700")
    )

    body = client.get(reverse("web:tariff")).content.decode()

    assert 'value="0.7700"' in body


@time_machine.travel(datetime(2026, 9, 4, 12, 0, tzinfo=WARSAW), tick=False)
def test_own_appliances_show_up_only_for_their_owner(client: Client) -> None:
    seed_market_day(date(2026, 9, 4))
    register(client)

    added = client.post(
        reverse("web:profile"),
        {
            "form": "appliance",
            "name": "Sauna",
            "duration_minutes": "60",
            "power_kw": "6.0",
            "is_interruptible": "on",
        },
    )
    assert added.status_code == 302
    sauna = Appliance.objects.get(name="Sauna")
    assert sauna.user is not None and sauna.user.username == "anna@example.com"

    owner_view = client.get(reverse("web:planner")).content.decode()
    assert "Your appliances" in owner_view
    assert "Sauna" in owner_view
    result = client.get(
        reverse("web:planner_result"),
        {"appliance": str(sauna.pk), "duration_minutes": "5", "power_kw": "1"},
    ).content.decode()
    assert "Sauna" in result and "6.00 kW" in result

    stranger = Client()
    assert "Sauna" not in stranger.get(reverse("web:planner")).content.decode()
    rejected = stranger.get(
        reverse("web:planner_result"),
        {"appliance": str(sauna.pk), "duration_minutes": "5", "power_kw": "1"},
    ).content.decode()
    assert "Sauna" not in rejected

    duplicate = client.post(
        reverse("web:profile"),
        {"form": "appliance", "name": "Sauna", "duration_minutes": "60", "power_kw": "6.0"},
    )
    assert "You already have an appliance with this name." in duplicate.content.decode()

    removed = client.post(reverse("web:delete_appliance", args=[sauna.pk]))
    assert removed.status_code == 302
    assert not Appliance.objects.filter(pk=sauna.pk).exists()


def test_alert_management(client: Client) -> None:
    register(client)

    client.post(
        reverse("web:profile"), {"form": "alert", "kind": "PRICE_BELOW", "threshold": "500"}
    )
    client.post(
        reverse("web:profile"), {"form": "alert", "kind": "NEGATIVE_PRICE", "threshold": "7"}
    )
    client.post(reverse("web:profile"), {"form": "alert", "kind": "GREEN_WINDOW", "threshold": ""})
    invalid = client.post(
        reverse("web:profile"), {"form": "alert", "kind": "PRICE_BELOW", "threshold": ""}
    )

    alerts = {
        alert.kind: alert for alert in Alert.objects.filter(user__username="anna@example.com")
    }
    assert set(alerts) == {"PRICE_BELOW", "NEGATIVE_PRICE", "GREEN_WINDOW"}
    assert alerts["PRICE_BELOW"].threshold == Decimal("500")
    assert alerts["NEGATIVE_PRICE"].threshold is None
    assert alerts["GREEN_WINDOW"].threshold == Decimal("50")
    assert "Enter the price threshold in PLN/MWh." in invalid.content.decode()

    toggled = client.post(reverse("web:toggle_alert", args=[alerts["PRICE_BELOW"].pk]))
    assert toggled.status_code == 302
    assert Alert.objects.get(pk=alerts["PRICE_BELOW"].pk).is_active is False

    deleted = client.post(reverse("web:delete_alert", args=[alerts["NEGATIVE_PRICE"].pk]))
    assert deleted.status_code == 302
    assert Alert.objects.filter(user__username="anna@example.com").count() == 2

    body = client.get(reverse("web:profile")).content.decode()
    assert "paused" in body
    assert "Price below a threshold" in body


def test_other_users_cannot_touch_my_alerts_or_appliances(client: Client) -> None:
    register(client)
    client.post(reverse("web:profile"), {"form": "alert", "kind": "NEGATIVE_PRICE"})
    alert = Alert.objects.get()
    client.post(reverse("web:logout"))

    other = Client()
    other.post(
        reverse("web:register"),
        {"email": "other@example.com", "password1": PASSWORD, "password2": PASSWORD},
    )

    assert other.post(reverse("web:delete_alert", args=[alert.pk])).status_code == 404
    assert Alert.objects.filter(pk=alert.pk).exists()
