from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.planner.catalog import TEMPLATE_LABELS


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
