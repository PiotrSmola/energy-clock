from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
INTERVAL = timedelta(minutes=15)


def local_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=WARSAW)


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = local_midnight(day).astimezone(UTC)
    end = local_midnight(day + timedelta(days=1)).astimezone(UTC)
    return start, end


def expected_intervals(day: date) -> int:
    start, end = day_bounds_utc(day)
    return (end - start) // INTERVAL


def parse_pse_datetime(dtime_utc: str) -> datetime:
    return datetime.fromisoformat(dtime_utc.strip()).replace(tzinfo=UTC)


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(WARSAW)


def local_today() -> date:
    return datetime.now(tz=WARSAW).date()


def interval_start(dtime_utc: datetime) -> datetime:
    return dtime_utc - INTERVAL
