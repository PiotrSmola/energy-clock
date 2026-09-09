from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.db.models import Max

from apps.market.models import GenerationPoint, IngestRun, PricePoint
from apps.market.timeutils import INTERVAL, WARSAW, to_local

STALE_AFTER = timedelta(hours=26)
PUBLICATION_TIME = time(14, 0)
PUBLICATION_LATE_AFTER = timedelta(hours=2, minutes=30)
FETCH_ATTEMPT_TIMES = (time(14, 15), time(15, 15), time(16, 15))
THRESHOLD_PERCENTILES = (5, 20, 40, 60, 80, 95)
THRESHOLD_WINDOW_DAYS = 30
THRESHOLD_CACHE_KEY = "market:price-thresholds:v1"
THRESHOLD_CACHE_SECONDS = 3600
DEFAULT_THRESHOLDS = tuple(Decimal(value) for value in (150, 350, 450, 550, 700, 900))
PERCENT = Decimal(100)


def price_points_for_day(day: date) -> list[PricePoint]:
    return list(PricePoint.objects.filter(business_date=day).order_by("dtime_utc"))


def generation_by_interval_end(day: date) -> dict[datetime, GenerationPoint]:
    return {point.dtime_utc: point for point in GenerationPoint.objects.filter(business_date=day)}


def has_prices_for(day: date) -> bool:
    return PricePoint.objects.filter(business_date=day).exists()


def price_point_at(moment: datetime) -> PricePoint | None:
    return PricePoint.objects.filter(dtime_utc__gt=moment, dtime_utc__lte=moment + INTERVAL).first()


def latest_interval_end() -> datetime | None:
    latest: datetime | None = PricePoint.objects.aggregate(latest=Max("dtime_utc"))["latest"]
    return latest


def expected_publication(day: date) -> datetime:
    return datetime.combine(day - timedelta(days=1), PUBLICATION_TIME, tzinfo=WARSAW)


def next_fetch_attempt(now: datetime) -> datetime | None:
    local_now = to_local(now)
    for attempt in FETCH_ATTEMPT_TIMES:
        candidate = datetime.combine(local_now.date(), attempt, tzinfo=WARSAW)
        if candidate > local_now:
            return candidate
    return None


def publication_is_late(day: date, now: datetime) -> bool:
    return now > expected_publication(day) + PUBLICATION_LATE_AFTER


@dataclass(frozen=True, slots=True)
class Freshness:
    now: datetime
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_attempt_status: str | None
    last_attempt_error: str
    latest_business_date: date | None

    @property
    def is_stale(self) -> bool:
        if self.last_success_at is None:
            return True
        return self.now - self.last_success_at > STALE_AFTER

    @property
    def last_attempt_failed(self) -> bool:
        return self.last_attempt_status == IngestRun.Status.FAILED

    @property
    def age(self) -> timedelta | None:
        if self.last_success_at is None:
            return None
        return self.now - self.last_success_at


def freshness(now: datetime) -> Freshness:
    price_runs = IngestRun.objects.filter(endpoint=IngestRun.Endpoint.PRICES)
    last_success = price_runs.filter(status=IngestRun.Status.OK).order_by("-finished_at").first()
    last_attempt = price_runs.order_by("-started_at").first()
    latest_day: date | None = PricePoint.objects.aggregate(latest=Max("business_date"))["latest"]
    return Freshness(
        now=now,
        last_success_at=last_success.finished_at if last_success else None,
        last_attempt_at=last_attempt.started_at if last_attempt else None,
        last_attempt_status=last_attempt.status if last_attempt else None,
        last_attempt_error=last_attempt.error if last_attempt else "",
        latest_business_date=latest_day,
    )


def percentile(sorted_values: list[Decimal], percent: int) -> Decimal:
    rank = max(0, min(len(sorted_values) - 1, round(len(sorted_values) * percent / 100) - 1))
    return sorted_values[rank]


def compute_price_thresholds(now: datetime) -> list[Decimal]:
    since = to_local(now).date() - timedelta(days=THRESHOLD_WINDOW_DAYS)
    values = sorted(
        PricePoint.objects.filter(business_date__gte=since).values_list("price_pln_mwh", flat=True)
    )
    if len(values) < 96:
        return list(DEFAULT_THRESHOLDS)
    return [percentile(values, percent) for percent in THRESHOLD_PERCENTILES]


def price_thresholds(now: datetime) -> list[Decimal]:
    cached: list[Decimal] | None = cache.get(THRESHOLD_CACHE_KEY)
    if cached is not None:
        return cached
    thresholds = compute_price_thresholds(now)
    cache.set(THRESHOLD_CACHE_KEY, thresholds, THRESHOLD_CACHE_SECONDS)
    return thresholds


def price_bucket(price: Decimal, thresholds: list[Decimal]) -> int:
    return sum(1 for threshold in thresholds if price >= threshold)


def res_share_percent(point: GenerationPoint | None) -> int | None:
    if point is None:
        return None
    renewable = (point.pv_mw or Decimal(0)) + (point.wind_mw or Decimal(0))
    total = renewable + (point.conventional_mw or Decimal(0))
    if total <= 0:
        return None
    return int((renewable / total * PERCENT).quantize(Decimal(1), rounding=ROUND_HALF_UP))
