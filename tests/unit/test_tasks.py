from datetime import date

import pytest

from apps.market import tasks
from apps.market.ingest import IngestResult
from apps.market.models import IngestRun

pytestmark = pytest.mark.django_db


def result_with(status: str, records: int = 0, day: date = date(2026, 9, 5)) -> IngestResult:
    return IngestResult(IngestRun.Endpoint.PRICES, day, status, records, 96)


def test_fetch_tomorrow_prices_returns_payload_when_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[date] = []
    alert_checks: list[date] = []

    def fake_ingest(day: date) -> IngestResult:
        calls.append(day)
        return result_with(IngestRun.Status.OK, 96, day)

    monkeypatch.setattr(tasks, "ingest_prices", fake_ingest)
    monkeypatch.setattr(tasks, "local_today", lambda: date(2026, 9, 4))
    monkeypatch.setattr(tasks, "schedule_alert_check", alert_checks.append)

    outcome = tasks.fetch_tomorrow_prices.apply()

    assert outcome.successful()
    assert outcome.result["status"] == "OK"
    assert outcome.result["business_date"] == "2026-09-05"
    assert calls == [date(2026, 9, 5)]
    assert alert_checks == [date(2026, 9, 5)]


def test_alert_check_is_not_scheduled_for_partial_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    alert_checks: list[date] = []
    monkeypatch.setattr(
        tasks, "ingest_prices", lambda day: result_with(IngestRun.Status.PARTIAL, 90)
    )
    monkeypatch.setattr(tasks, "schedule_alert_check", alert_checks.append)

    outcome = tasks.fetch_tomorrow_prices.apply()

    assert outcome.successful()
    assert alert_checks == []


def test_check_alerts_task_returns_deliveries() -> None:
    from apps.planner.tasks import check_alerts

    outcome = check_alerts.apply(args=["2026-09-04"])

    assert outcome.successful()
    assert outcome.result == []


def test_fetch_tomorrow_prices_retries_while_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[date] = []

    def fake_ingest(day: date) -> IngestResult:
        attempts.append(day)
        return result_with(IngestRun.Status.PENDING)

    monkeypatch.setattr(tasks, "ingest_prices", fake_ingest)

    outcome = tasks.fetch_tomorrow_prices.apply()

    assert outcome.failed()
    assert len(attempts) == tasks.MAX_PRICE_RETRIES + 1


def test_fetch_tomorrow_prices_succeeds_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = iter([IngestRun.Status.PENDING, IngestRun.Status.FAILED, IngestRun.Status.OK])

    monkeypatch.setattr(tasks, "ingest_prices", lambda day: result_with(next(statuses), 96))

    outcome = tasks.fetch_tomorrow_prices.apply()

    assert outcome.successful()
    assert outcome.result["status"] == "OK"


def test_retry_delay_backs_off_exponentially_with_a_cap() -> None:
    assert [tasks.retry_delay_seconds(n) for n in range(7)] == [
        300,
        600,
        1200,
        2400,
        3600,
        3600,
        3600,
    ]


def test_fetch_today_generation_covers_today_and_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[date] = []

    def fake_ingest(day: date) -> IngestResult:
        seen.append(day)
        return IngestResult(IngestRun.Endpoint.GENERATION, day, IngestRun.Status.OK, 96, 96)

    monkeypatch.setattr(tasks, "ingest_generation", fake_ingest)
    monkeypatch.setattr(tasks, "local_today", lambda: date(2026, 9, 4))

    outcome = tasks.fetch_today_generation.apply()

    assert outcome.successful()
    assert seen == [date(2026, 9, 4), date(2026, 9, 5)]
    assert [entry["business_date"] for entry in outcome.result] == ["2026-09-04", "2026-09-05"]


def test_fill_gaps_scans_recent_days_including_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_range(start: date, end: date, **kwargs: object) -> list[IngestResult]:
        captured.update({"start": start, "end": end, **kwargs})
        return []

    monkeypatch.setattr(tasks, "ingest_range", fake_range)
    monkeypatch.setattr(tasks, "local_today", lambda: date(2026, 9, 9))

    outcome = tasks.fill_gaps.apply(kwargs={"days": 7})

    assert outcome.successful()
    assert captured["start"] == date(2026, 9, 3)
    assert captured["end"] == date(2026, 9, 10)
    assert captured["skip_complete"] is True
