import logging
from datetime import date
from typing import Any

from celery import shared_task
from django.utils import timezone

from apps.planner.alerts import run_alerts

logger = logging.getLogger(__name__)


@shared_task
def check_alerts(business_date: str) -> list[dict[str, Any]]:
    day = date.fromisoformat(business_date)
    deliveries = run_alerts(day, timezone.now())
    logger.info("Alerts for %s: %d emails sent", day, len(deliveries))
    return [
        {
            "alert": delivery.alert_id,
            "email": delivery.alert.user.email,
            "subject": delivery.subject,
        }
        for delivery in deliveries
    ]
