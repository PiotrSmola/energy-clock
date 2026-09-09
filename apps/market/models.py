from datetime import datetime

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.market.timeutils import INTERVAL


class PricePoint(models.Model):
    dtime_utc = models.DateTimeField(unique=True)
    business_date = models.DateField(db_index=True)
    price_pln_mwh = models.DecimalField(max_digits=10, decimal_places=2)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("dtime_utc",)
        indexes = (
            models.Index(fields=("business_date", "dtime_utc"), name="market_price_day_time_idx"),
        )

    def __str__(self) -> str:
        return f"{self.dtime_utc:%Y-%m-%d %H:%M}Z {self.price_pln_mwh} PLN/MWh"

    @property
    def interval_start_utc(self) -> datetime:
        return self.dtime_utc - INTERVAL


class GenerationPoint(models.Model):
    dtime_utc = models.DateTimeField(unique=True)
    business_date = models.DateField(db_index=True)
    pv_mw = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    wind_mw = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    conventional_mw = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    demand_mw = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    carbon_g_kwh = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("dtime_utc",)
        indexes = (
            models.Index(
                fields=("business_date", "dtime_utc"), name="market_generation_day_time_idx"
            ),
        )

    def __str__(self) -> str:
        return f"{self.dtime_utc:%Y-%m-%d %H:%M}Z pv={self.pv_mw} wind={self.wind_mw}"

    @property
    def interval_start_utc(self) -> datetime:
        return self.dtime_utc - INTERVAL


class IngestRun(models.Model):
    class Endpoint(models.TextChoices):
        PRICES = "csdac-pln", _("Day-ahead prices")
        GENERATION = "pdgobpkd", _("Generation plan")

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        OK = "OK", _("OK")
        FAILED = "FAILED", _("Failed")
        PARTIAL = "PARTIAL", _("Partial")

    endpoint = models.CharField(max_length=32, choices=Endpoint.choices, db_index=True)
    business_date = models.DateField(db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, db_index=True)
    records = models.PositiveIntegerField(default=0)
    expected_records = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-started_at",)
        indexes = (
            models.Index(
                fields=("endpoint", "business_date"), name="market_ingest_endpoint_day_idx"
            ),
        )

    def __str__(self) -> str:
        return f"{self.endpoint} {self.business_date} {self.status} ({self.records})"
