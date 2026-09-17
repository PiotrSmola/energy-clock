from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import models
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.planner.catalog import TEMPLATE_LABELS


def appliances_for(user: AbstractBaseUser | AnonymousUser | None) -> QuerySet["Appliance"]:
    templates = Q(user__isnull=True)
    if user is not None and user.is_authenticated:
        return Appliance.objects.filter(templates | Q(user=user.pk)).order_by(
            "user_id", "sort_order", "name"
        )
    return Appliance.objects.filter(templates).order_by("sort_order", "name")


class Appliance(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="appliances",
    )
    key = models.SlugField(max_length=40, blank=True, default="")
    name = models.CharField(max_length=80)
    duration_minutes = models.PositiveIntegerField()
    power_kw = models.DecimalField(max_digits=6, decimal_places=2)
    is_interruptible = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")
        constraints = (
            models.UniqueConstraint(
                fields=("key",),
                condition=Q(user__isnull=True),
                name="planner_unique_template_key",
            ),
            models.UniqueConstraint(
                fields=("user", "name"),
                condition=Q(user__isnull=False),
                name="planner_unique_user_appliance_name",
            ),
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.duration_minutes} min, {self.power_kw} kW)"

    @property
    def is_template(self) -> bool:
        return self.user_id is None

    @property
    def display_name(self) -> str:
        if self.is_template and self.key in TEMPLATE_LABELS:
            return str(TEMPLATE_LABELS[self.key])
        return self.name


class Household(models.Model):
    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        POLISH = "pl", "Polski"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="household"
    )
    contracted_power_kw = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal("11.0")
    )
    pv_peak_kw = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    storage_kwh = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    tariff_fixed_pln_kwh = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True
    )
    prefers_green = models.BooleanField(default=False)
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.ENGLISH)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Household of {self.user}"


class Alert(models.Model):
    class Kind(models.TextChoices):
        PRICE_BELOW = "PRICE_BELOW", _("Price below a threshold")
        NEGATIVE_PRICE = "NEGATIVE_PRICE", _("Negative price")
        GREEN_WINDOW = "GREEN_WINDOW", _("Cheap and clean window")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alerts"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    threshold = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    last_fired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("kind", "id")

    def __str__(self) -> str:
        return f"{self.kind} {self.threshold or ''} for {self.user}".strip()


class AlertDelivery(models.Model):
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="deliveries")
    business_date = models.DateField()
    sent_at = models.DateTimeField(default=timezone.now)
    subject = models.CharField(max_length=200)

    class Meta:
        ordering = ("-sent_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("alert", "business_date"), name="planner_alert_once_per_day"
            ),
        )

    def __str__(self) -> str:
        return f"{self.alert_id} {self.business_date} {self.subject}"
