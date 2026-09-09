import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from apps.market.models import GenerationPoint, PricePoint
from apps.market.timeutils import INTERVAL, parse_pse_datetime
from apps.planner.optimizer import CarbonSlot, PriceSlot

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_market_day(day: date) -> dict[str, Any]:
    with (FIXTURES_DIR / f"market_{day.isoformat()}.json").open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


def optional_decimal(value: str | None) -> Decimal | None:
    if value in (None, "None"):
        return None
    return Decimal(value)


def fixture_price_slots(day: date) -> list[PriceSlot]:
    payload = load_market_day(day)
    carbon_by_end = {
        row["dtime_utc"]: optional_decimal(row["carbon_g_kwh"]) for row in payload["generation"]
    }
    return [
        PriceSlot(
            start_utc=parse_pse_datetime(row["dtime_utc"]) - INTERVAL,
            price_pln_mwh=Decimal(row["price_pln_mwh"]),
            carbon_g_kwh=carbon_by_end.get(row["dtime_utc"]),
        )
        for row in payload["prices"]
    ]


def fixture_carbon_slots(day: date) -> list[CarbonSlot]:
    payload = load_market_day(day)
    return [
        CarbonSlot(
            start_utc=parse_pse_datetime(row["dtime_utc"]) - INTERVAL,
            carbon_g_kwh=optional_decimal(row["carbon_g_kwh"]),
        )
        for row in payload["generation"]
    ]


def seed_market_day(day: date, *, with_generation: bool = True) -> None:
    payload = load_market_day(day)
    PricePoint.objects.bulk_create(
        [
            PricePoint(
                dtime_utc=parse_pse_datetime(row["dtime_utc"]),
                business_date=day,
                price_pln_mwh=Decimal(row["price_pln_mwh"]),
                published_at=parse_pse_datetime("2026-09-03 11:53:17.304"),
            )
            for row in payload["prices"]
        ]
    )
    if not with_generation:
        return
    GenerationPoint.objects.bulk_create(
        [
            GenerationPoint(
                dtime_utc=parse_pse_datetime(row["dtime_utc"]),
                business_date=day,
                pv_mw=optional_decimal(row["pv_mw"]),
                wind_mw=optional_decimal(row["wind_mw"]),
                conventional_mw=optional_decimal(row["conventional_mw"]),
                demand_mw=optional_decimal(row["demand_mw"]),
                carbon_g_kwh=optional_decimal(row["carbon_g_kwh"]),
            )
            for row in payload["generation"]
        ]
    )
