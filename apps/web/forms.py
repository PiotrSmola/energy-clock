from decimal import Decimal
from typing import Any, ClassVar

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrPromise

from apps.planner.models import Alert, Appliance, Household

MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 24 * 60
MIN_POWER_KW = Decimal("0.05")
MAX_POWER_KW = Decimal("50")
DEFAULT_GREEN_SHARE = Decimal("50")


class RegistrationForm(forms.Form):
    email = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )
    password1 = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label=_("Repeat password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_email(self) -> str:
        email = str(self.cleaned_data["email"]).strip().lower()
        if User.objects.filter(username__iexact=email).exists():
            raise ValidationError(_("An account with this email already exists."))
        return email

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        password1 = data.get("password1")
        password2 = data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", _("The two passwords do not match."))
            return data
        email = data.get("email")
        if password1 and email:
            try:
                validate_password(password1, user=User(username=email, email=email))
            except ValidationError as error:
                self.add_error("password1", error)
        return data

    def save(self) -> User:
        email = str(self.cleaned_data["email"])
        user = User.objects.create_user(
            username=email, email=email, password=self.cleaned_data["password1"]
        )
        Household.objects.create(user=user, language=translation.get_language() or "en")
        return user


class EmailAuthenticationForm(AuthenticationForm):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Email")
        self.fields["username"].widget.attrs.update({"autocomplete": "email", "autofocus": True})
        self.fields["password"].widget.attrs.update({"autocomplete": "current-password"})

    def clean(self) -> dict[str, Any]:
        username = self.cleaned_data.get("username")
        if isinstance(username, str):
            self.cleaned_data["username"] = username.strip().lower()
        data: dict[str, Any] = super().clean() or {}
        return data


class HouseholdForm(forms.ModelForm[Household]):
    class Meta:
        model = Household
        fields = (
            "contracted_power_kw",
            "pv_peak_kw",
            "storage_kwh",
            "tariff_fixed_pln_kwh",
            "prefers_green",
            "language",
        )
        labels: ClassVar[dict[str, StrPromise]] = {
            "contracted_power_kw": _("Contracted power (kW)"),
            "pv_peak_kw": _("PV peak power (kWp)"),
            "storage_kwh": _("Battery storage (kWh)"),
            "tariff_fixed_pln_kwh": _("Fixed energy price (PLN/kWh)"),
            "prefers_green": _("Prefer the cleanest window over the cheapest"),
            "language": _("Language of alert emails"),
        }


class UserApplianceForm(forms.ModelForm[Appliance]):
    class Meta:
        model = Appliance
        fields = ("name", "duration_minutes", "power_kw", "is_interruptible")
        labels: ClassVar[dict[str, StrPromise]] = {
            "name": _("Name"),
            "duration_minutes": _("Cycle length (minutes)"),
            "power_kw": _("Power draw (kW)"),
            "is_interruptible": _("Can be paused and resumed"),
        }

    def __init__(self, *args: Any, user: User | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["duration_minutes"].widget.attrs.update(
            {"min": MIN_DURATION_MINUTES, "max": MAX_DURATION_MINUTES, "step": 5}
        )
        self.fields["power_kw"].widget.attrs.update({"min": "0.05", "max": "50", "step": "0.1"})

    def clean_duration_minutes(self) -> int:
        duration = int(self.cleaned_data["duration_minutes"])
        if not MIN_DURATION_MINUTES <= duration <= MAX_DURATION_MINUTES:
            raise ValidationError(
                _("Enter a cycle length between %(low)d and %(high)d minutes.")
                % {"low": MIN_DURATION_MINUTES, "high": MAX_DURATION_MINUTES}
            )
        return duration

    def clean_power_kw(self) -> Decimal:
        power = Decimal(self.cleaned_data["power_kw"])
        if not MIN_POWER_KW <= power <= MAX_POWER_KW:
            raise ValidationError(
                _("Enter a power draw between %(low)s and %(high)s kW.")
                % {"low": MIN_POWER_KW, "high": MAX_POWER_KW}
            )
        return power

    def clean_name(self) -> str:
        name = str(self.cleaned_data["name"]).strip()
        if self.user is not None and Appliance.objects.filter(user=self.user, name=name).exists():
            raise ValidationError(_("You already have an appliance with this name."))
        return name


class AlertForm(forms.ModelForm[Alert]):
    class Meta:
        model = Alert
        fields = ("kind", "threshold")
        labels: ClassVar[dict[str, StrPromise]] = {
            "kind": _("Alert type"),
            "threshold": _("Threshold"),
        }

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        kind = data.get("kind")
        threshold = data.get("threshold")
        if kind == Alert.Kind.PRICE_BELOW and threshold is None:
            self.add_error("threshold", _("Enter the price threshold in PLN/MWh."))
        if kind == Alert.Kind.NEGATIVE_PRICE:
            data["threshold"] = None
        if kind == Alert.Kind.GREEN_WINDOW:
            share = DEFAULT_GREEN_SHARE if threshold is None else Decimal(threshold)
            if not Decimal(1) <= share <= Decimal(100):
                self.add_error("threshold", _("Enter a renewable share between 1 and 100 percent."))
            data["threshold"] = share
        return data
