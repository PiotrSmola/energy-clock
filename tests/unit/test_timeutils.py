from datetime import UTC, date, datetime, timedelta

import pytest

from apps.market.timeutils import (
    INTERVAL,
    WARSAW,
    day_bounds_utc,
    expected_intervals,
    interval_start,
    parse_pse_datetime,
    to_local,
)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 3, 29), 92),
        (date(2025, 10, 26), 100),
        (date(2026, 9, 4), 96),
        (date(2025, 3, 30), 92),
        (date(2024, 10, 27), 100),
        (date(2024, 3, 31), 92),
        (date(2026, 10, 25), 100),
    ],
)
def test_expected_intervals_on_known_days(day: date, expected: int) -> None:
    assert expected_intervals(day) == expected


@pytest.mark.parametrize(
    "day",
    [
        date(2024, 6, 14),
        date(2025, 1, 1),
        date(2025, 7, 15),
        date(2026, 2, 28),
        date(2025, 3, 29),
        date(2025, 10, 25),
        date(2026, 12, 31),
    ],
)
def test_expected_intervals_on_ordinary_days(day: date) -> None:
    assert expected_intervals(day) == 96


def test_expected_intervals_over_a_full_year_sums_to_96_per_day() -> None:
    days = (date(2025, 12, 31) - date(2025, 1, 1)).days + 1
    total = sum(expected_intervals(date(2025, 1, 1) + timedelta(days=i)) for i in range(days))
    assert total == days * 96


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-03 22:15:00", datetime(2026, 9, 3, 22, 15, tzinfo=UTC)),
        ("2024-06-13 22:15", datetime(2024, 6, 13, 22, 15, tzinfo=UTC)),
        ("2026-09-03 11:53:17.304", datetime(2026, 9, 3, 11, 53, 17, 304000, tzinfo=UTC)),
        ("2026-09-03 11:53:17.304000", datetime(2026, 9, 3, 11, 53, 17, 304000, tzinfo=UTC)),
    ],
)
def test_parse_pse_datetime_returns_aware_utc(raw: str, expected: datetime) -> None:
    parsed = parse_pse_datetime(raw)
    assert parsed == expected
    assert parsed.tzinfo is UTC


def test_parse_pse_datetime_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Invalid isoformat"):
        parse_pse_datetime("not a date")


def test_to_local_converts_to_warsaw_summer_time() -> None:
    local = to_local(datetime(2026, 9, 3, 22, 15, tzinfo=UTC))
    assert local == datetime(2026, 9, 4, 0, 15, tzinfo=WARSAW)
    assert local.utcoffset() == timedelta(hours=2)


def test_to_local_converts_to_warsaw_winter_time() -> None:
    local = to_local(datetime(2026, 1, 15, 23, 0, tzinfo=UTC))
    assert local == datetime(2026, 1, 16, 0, 0, tzinfo=WARSAW)
    assert local.utcoffset() == timedelta(hours=1)


@pytest.mark.parametrize(
    ("day", "start", "end"),
    [
        (
            date(2026, 9, 4),
            datetime(2026, 9, 3, 22, 0, tzinfo=UTC),
            datetime(2026, 9, 4, 22, 0, tzinfo=UTC),
        ),
        (
            date(2026, 1, 15),
            datetime(2026, 1, 14, 23, 0, tzinfo=UTC),
            datetime(2026, 1, 15, 23, 0, tzinfo=UTC),
        ),
        (
            date(2026, 3, 29),
            datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 22, 0, tzinfo=UTC),
        ),
        (
            date(2025, 10, 26),
            datetime(2025, 10, 25, 22, 0, tzinfo=UTC),
            datetime(2025, 10, 26, 23, 0, tzinfo=UTC),
        ),
    ],
)
def test_day_bounds_utc(day: date, start: datetime, end: datetime) -> None:
    assert day_bounds_utc(day) == (start, end)


def test_day_bounds_span_matches_expected_intervals() -> None:
    for day in (date(2026, 3, 29), date(2025, 10, 26), date(2026, 9, 4)):
        start, end = day_bounds_utc(day)
        assert (end - start) == expected_intervals(day) * INTERVAL


def test_interval_start_is_fifteen_minutes_before_pse_dtime() -> None:
    dtime_utc = parse_pse_datetime("2026-09-03 22:15:00")
    assert interval_start(dtime_utc) == datetime(2026, 9, 3, 22, 0, tzinfo=UTC)
