import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GenerationPoint",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("dtime_utc", models.DateTimeField(unique=True)),
                ("business_date", models.DateField(db_index=True)),
                (
                    "pv_mw",
                    models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True),
                ),
                (
                    "wind_mw",
                    models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True),
                ),
                (
                    "conventional_mw",
                    models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True),
                ),
                (
                    "demand_mw",
                    models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True),
                ),
                (
                    "carbon_g_kwh",
                    models.DecimalField(blank=True, decimal_places=1, max_digits=7, null=True),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ("dtime_utc",),
                "indexes": [
                    models.Index(
                        fields=["business_date", "dtime_utc"], name="market_generation_day_time_idx"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="IngestRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "endpoint",
                    models.CharField(
                        choices=[
                            ("csdac-pln", "Day-ahead prices"),
                            ("pdgobpkd", "Generation plan"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("business_date", models.DateField(db_index=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("OK", "OK"),
                            ("FAILED", "Failed"),
                            ("PARTIAL", "Partial"),
                        ],
                        db_index=True,
                        max_length=8,
                    ),
                ),
                ("records", models.PositiveIntegerField(default=0)),
                ("expected_records", models.PositiveIntegerField(default=0)),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True, default="")),
            ],
            options={
                "ordering": ("-started_at",),
                "indexes": [
                    models.Index(
                        fields=["endpoint", "business_date"], name="market_ingest_endpoint_day_idx"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PricePoint",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("dtime_utc", models.DateTimeField(unique=True)),
                ("business_date", models.DateField(db_index=True)),
                ("price_pln_mwh", models.DecimalField(decimal_places=2, max_digits=10)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ("dtime_utc",),
                "indexes": [
                    models.Index(
                        fields=["business_date", "dtime_utc"], name="market_price_day_time_idx"
                    )
                ],
            },
        ),
    ]
