from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from apps.market.timeutils import INTERVAL, day_bounds_utc, expected_intervals, to_local

PSE_TIMESTAMP = "%Y-%m-%d %H:%M:%S"

CONVENTIONAL_FIELDS = (
    "gen_jgw_zak_1",
    "gen_jgw_zak_2",
    "gen_jgz_zak_1",
    "gen_jgz_zak_2",
    "gen_jgz_zak_3",
    "gen_jga",
    "gen_jgo",
    "gen_jgm_zak_1",
    "gen_jgm_zak_2",
)


def interval_ends_utc(day: date, count: int | None = None) -> list[datetime]:
    start, _ = day_bounds_utc(day)
    total = expected_intervals(day) if count is None else count
    return [start + (index + 1) * INTERVAL for index in range(total)]


def period_label(end_utc: datetime) -> str:
    local_end = to_local(end_utc)
    local_start = to_local(end_utc - INTERVAL)
    return f"{local_start:%H:%M} - {local_end:%H:%M}"


def price_rows(
    day: date,
    *,
    count: int | None = None,
    price_for: Callable[[int], float] = lambda index: 500.0 + index,
    published: str = "2026-09-03 11:53:17.304",
) -> list[dict[str, Any]]:
    return [
        {
            "business_date": day.isoformat(),
            "dtime": f"{to_local(end_utc):%Y-%m-%d %H:%M}",
            "dtime_utc": end_utc.strftime(PSE_TIMESTAMP),
            "period": period_label(end_utc),
            "csdac_pln": price_for(index),
            "publication_ts": "2026-09-03 13:53:17.304",
            "publication_ts_utc": published,
        }
        for index, end_utc in enumerate(interval_ends_utc(day, count))
    ]


def generation_rows(
    day: date,
    *,
    count: int | None = None,
    pv_for: Callable[[int], float | None] = lambda index: float(index * 10),
    wind_for: Callable[[int], float | None] = lambda index: 5000.0,
    conventional: dict[str, float | None] | None = None,
    demand_for: Callable[[int], float | None] = lambda index: 15000.0,
) -> list[dict[str, Any]]:
    conventional_values: dict[str, float | None] = {field: None for field in CONVENTIONAL_FIELDS}
    conventional_values.update(
        conventional
        if conventional is not None
        else {"gen_jgw_zak_1": 9000.0, "gen_jgw_zak_2": 90.0, "gen_jgz_zak_2": 25.0}
    )
    return [
        {
            "business_date": day.isoformat(),
            "dtime": f"{to_local(end_utc):%Y-%m-%d %H:%M:%S}",
            "dtime_utc": end_utc.strftime(PSE_TIMESTAMP),
            "period": period_label(end_utc),
            "gen_fv": pv_for(index),
            "gen_wi": wind_for(index),
            "kse_pow_dem": demand_for(index),
            "publication_ts_utc": "2026-09-03 20:54:41.687",
            **conventional_values,
        }
        for index, end_utc in enumerate(interval_ends_utc(day, count))
    ]
