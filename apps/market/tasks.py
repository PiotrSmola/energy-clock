import logging
from datetime import timedelta
from typing import Any

from celery import Task, shared_task

from apps.market.ingest import IngestResult, ingest_generation, ingest_prices, ingest_range
from apps.market.models import IngestRun
from apps.market.timeutils import local_today

logger = logging.getLogger(__name__)

RETRY_BASE_SECONDS = 5 * 60
RETRY_MAX_SECONDS = 60 * 60
MAX_PRICE_RETRIES = 6
GAP_FILL_PAUSE_SECONDS = 0.2
RETRY_STATUSES = frozenset({IngestRun.Status.PENDING, IngestRun.Status.FAILED})


def result_payload(result: IngestResult) -> dict[str, Any]:
    return {
        "endpoint": result.endpoint,
        "business_date": result.business_date.isoformat(),
        "status": result.status,
        "records": result.records,
        "expected": result.expected,
        "error": result.error,
    }


def retry_delay_seconds(retries: int) -> int:
    return min(RETRY_BASE_SECONDS * (1 << retries), RETRY_MAX_SECONDS)


@shared_task(bind=True, max_retries=MAX_PRICE_RETRIES)
def fetch_tomorrow_prices(self: Task) -> dict[str, Any]:
    tomorrow = local_today() + timedelta(days=1)
    result = ingest_prices(tomorrow)
    if result.status in RETRY_STATUSES:
        delay = retry_delay_seconds(self.request.retries)
        logger.info(
            "Prices for %s not available (%s), retrying in %d s", tomorrow, result.status, delay
        )
        raise self.retry(countdown=delay)
    logger.info("Prices for %s ingested: %d records (%s)", tomorrow, result.records, result.status)
    return result_payload(result)


@shared_task
def fetch_today_generation() -> list[dict[str, Any]]:
    today = local_today()
    results = [ingest_generation(today), ingest_generation(today + timedelta(days=1))]
    for result in results:
        logger.info(
            "Generation for %s: %d records (%s)",
            result.business_date,
            result.records,
            result.status,
        )
    return [result_payload(result) for result in results]


@shared_task
def fill_gaps(days: int = 7) -> list[dict[str, Any]]:
    today = local_today()
    results = ingest_range(
        today - timedelta(days=days - 1),
        today + timedelta(days=1),
        skip_complete=True,
        pause_seconds=GAP_FILL_PAUSE_SECONDS,
    )
    logger.info("Gap fill re-ingested %d day/endpoint pairs", len(results))
    return [result_payload(result) for result in results]
