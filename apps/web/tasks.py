import logging

from celery import shared_task

from apps.market.timeutils import local_today
from apps.web.analysis import PERIODS, stats_bundle

logger = logging.getLogger(__name__)


@shared_task
def warm_statistics() -> list[str]:
    today = local_today()
    warmed = []
    for period_key in PERIODS:
        stats_bundle(period_key, today, refresh=True)
        warmed.append(period_key)
    logger.info("Statistics cache warmed for %s", ", ".join(warmed))
    return warmed
