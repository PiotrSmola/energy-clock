from decimal import Decimal

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("planner", "0002_seed_system_templates"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Alert",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("PRICE_BELOW", "Price below a threshold"),
                            ("NEGATIVE_PRICE", "Negative price"),
                            ("GREEN_WINDOW", "Cheap and clean window"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "threshold",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("last_fired_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alerts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("kind", "id"),
            },
        ),
        migrations.CreateModel(
            name="Household",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "contracted_power_kw",
                    models.DecimalField(decimal_places=1, default=Decimal("11.0"), max_digits=5),
                ),
                (
                    "pv_peak_kw",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
                ),
                (
                    "storage_kwh",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
                ),
                (
                    "tariff_fixed_pln_kwh",
                    models.DecimalField(blank=True, decimal_places=4, max_digits=6, null=True),
                ),
                ("prefers_green", models.BooleanField(default=False)),
                (
                    "language",
                    models.CharField(
                        choices=[("en", "English"), ("pl", "Polski")], default="en", max_length=2
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="household",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AlertDelivery",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("business_date", models.DateField()),
                ("sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("subject", models.CharField(max_length=200)),
                (
                    "alert",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="planner.alert",
                    ),
                ),
            ],
            options={
                "ordering": ("-sent_at",),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("alert", "business_date"), name="planner_alert_once_per_day"
                    )
                ],
            },
        ),
    ]
