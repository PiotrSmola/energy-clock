import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.forms import Form
from openai.types.chat import ChatCompletionToolParam

from apps.assistant import law
from apps.assistant.llm import LlmClient
from apps.assistant.models import LawChunk
from apps.market.queries import price_point_at
from apps.market.timeutils import to_local
from apps.planner.forms import PlannerForm, TariffForm
from apps.planner.optimizer import Window
from apps.planner.services import build_plan, summarize_day, tariff_slots
from apps.planner.tariff import simulate_tariff

MAX_LAW_TEXT_CHARS = 1800

TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "find_cheapest_window",
            "description": (
                "Find the cheapest start time for an appliance cycle in the published day-ahead "
                "prices, with its cost, the cost of starting now and the saving."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Cycle length in minutes, 15 to 1440",
                    },
                    "power_kw": {
                        "type": "number",
                        "description": "Average power draw in kW, 0.05 to 50",
                    },
                    "earliest_start": {
                        "type": "string",
                        "description": (
                            "Optional local time HH:MM before which the cycle cannot start"
                        ),
                    },
                    "finish_by": {
                        "type": "string",
                        "description": "Optional local time HH:MM by which the cycle must end",
                    },
                },
                "required": ["duration_minutes", "power_kw"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_day_prices",
            "description": (
                "Summary of day-ahead electricity prices for one day: current price, lowest, "
                "highest, average and the cheapest and most expensive two-hour windows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Day as YYYY-MM-DD; omit for today",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_tariff",
            "description": (
                "Replay the last twelve months of prices to compare a dynamic tariff with a fixed "
                "energy price for a household."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_kwh": {
                        "type": "number",
                        "description": "Monthly consumption in kWh",
                    },
                    "fixed_price_pln_kwh": {
                        "type": "number",
                        "description": "Fixed energy price in PLN/kWh, energy component only",
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["flat", "day", "night"],
                        "description": "When the energy is used: evenly, mostly by day or at night",
                    },
                },
                "required": ["monthly_kwh", "fixed_price_pln_kwh"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_energy_law",
            "description": (
                "Search the Polish Energy Law (Prawo energetyczne) for provisions about contracts, "
                "dynamic prices, suppliers, smart meters and consumer rights. Returns articles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for, in Polish",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


@dataclass
class ToolOutcome:
    result: dict[str, Any]
    sources: list[LawChunk] = field(default_factory=list)

    @property
    def error(self) -> str | None:
        error = self.result.get("error")
        return str(error) if error is not None else None


def failure(message: str) -> ToolOutcome:
    return ToolOutcome({"error": message})


def form_errors(form: Form) -> str:
    return "; ".join(
        f"{name}: {' '.join(str(message) for message in messages)}"
        for name, messages in form.errors.items()
    )


def local_label(moment: datetime, now: datetime) -> str:
    local = to_local(moment)
    days = (local.date() - to_local(now).date()).days
    relative = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(days, local.date().isoformat())
    return f"{local:%Y-%m-%d %H:%M} ({relative})"


def money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def window_payload(window: Window | None, now: datetime) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "start": local_label(window.start_utc, now),
        "end": local_label(window.end_utc, now),
        "cost_pln": money(window.cost_pln),
        "average_price_pln_mwh": money(window.avg_price_pln_mwh),
    }


def find_cheapest_window(arguments: dict[str, Any], now: datetime) -> ToolOutcome:
    form = PlannerForm(
        {
            "duration_minutes": arguments.get("duration_minutes"),
            "power_kw": arguments.get("power_kw"),
            "earliest_start": arguments.get("earliest_start") or "",
            "finish_by": arguments.get("finish_by") or "",
        }
    )
    if not form.is_valid():
        return failure(form_errors(form))
    plan = build_plan(form.to_request(now), now)
    if plan.cheapest is None:
        return failure("No published prices cover this cycle and its time constraints.")
    return ToolOutcome(
        {
            "duration_minutes": plan.request.duration_minutes,
            "power_kw": str(plan.request.power_kw),
            "energy_kwh": str(plan.energy_kwh),
            "cheapest": window_payload(plan.cheapest, now),
            "start_now": window_payload(plan.start_now, now),
            "saving_vs_start_now_pln": money(plan.savings_pln),
            "saving_percent": plan.savings_percent,
            "greenest": window_payload(plan.greenest, now),
            "prices_published_until": (
                None if plan.horizon_end is None else local_label(plan.horizon_end, now)
            ),
            "note": "Wholesale day-ahead prices, constant power draw assumed.",
        }
    )


def get_day_prices(arguments: dict[str, Any], now: datetime) -> ToolOutcome:
    today = to_local(now).date()
    raw = arguments.get("date")
    try:
        day = date.fromisoformat(str(raw)) if raw else today
    except ValueError:
        return failure("date must be YYYY-MM-DD")
    summary = summarize_day(day, now)
    if not summary.points or summary.lowest is None or summary.highest is None:
        hint = " Prices for tomorrow are published around 14:00." if day > today else ""
        return failure(f"No prices published for {day.isoformat()}.{hint}")
    current = price_point_at(now) if day == today else None
    return ToolOutcome(
        {
            "date": day.isoformat(),
            "unit": "PLN/MWh",
            "current_price": None if current is None else money(current.price_pln_mwh),
            "lowest": {
                "price": money(summary.lowest.price_pln_mwh),
                "interval_start": local_label(summary.lowest.interval_start_utc, now),
            },
            "highest": {
                "price": money(summary.highest.price_pln_mwh),
                "interval_start": local_label(summary.highest.interval_start_utc, now),
            },
            "average": money(summary.average),
            "cheapest_2h_window": window_payload(summary.cheapest, now),
            "most_expensive_2h_window": window_payload(summary.most_expensive, now),
            "note": "Wholesale day-ahead prices before margin, fees and taxes.",
        }
    )


def compare_tariff(arguments: dict[str, Any], now: datetime) -> ToolOutcome:
    form = TariffForm(
        {
            "monthly_kwh": arguments.get("monthly_kwh"),
            "fixed_price_pln_kwh": arguments.get("fixed_price_pln_kwh"),
            "profile": arguments.get("profile") or "flat",
        }
    )
    if not form.is_valid():
        return failure(form_errors(form))
    start, end = form.period(to_local(now).date())
    comparison = simulate_tariff(
        tariff_slots(start, end),
        monthly_kwh=Decimal(form.cleaned_data["monthly_kwh"]),
        profile=form.to_profile(),
        fixed_price_pln_kwh=Decimal(form.cleaned_data["fixed_price_pln_kwh"]),
        start=start,
        end=end,
    )
    if not comparison.months:
        return failure("No prices in the last twelve months.")
    return ToolOutcome(
        {
            "period": f"{start.isoformat()} to {end.isoformat()}",
            "energy_kwh": money(comparison.total_energy_kwh),
            "dynamic_tariff_pln": money(comparison.total_dynamic_pln),
            "fixed_price_pln": money(comparison.total_fixed_pln),
            "fixed_minus_dynamic_pln": money(comparison.total_difference_pln),
            "cheaper": comparison.cheaper,
            "note": (
                "Energy component only, wholesale prices; distribution fees and taxes excluded."
            ),
        }
    )


def search_energy_law(arguments: dict[str, Any], client: LlmClient) -> ToolOutcome:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return failure("query must not be empty")
    try:
        results = law.search(query, client)
    except law.LawIndexError as exc:
        return failure(str(exc))
    if not results:
        return failure("The law index is empty; run `python manage.py ingest_law`.")
    return ToolOutcome(
        {
            "act": results[0][0].act_title,
            "results": [
                {
                    "article": f"art. {chunk.article}",
                    "location": chunk.heading,
                    "text": chunk.text[:MAX_LAW_TEXT_CHARS],
                    "similarity": round(score, 3),
                }
                for chunk, score in results
            ],
        },
        sources=[chunk for chunk, _score in results],
    )


def run_tool(name: str, raw_arguments: str, now: datetime, client: LlmClient) -> ToolOutcome:
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return failure("arguments are not valid JSON")
    if not isinstance(arguments, dict):
        return failure("arguments must be a JSON object")
    if name == "find_cheapest_window":
        return find_cheapest_window(arguments, now)
    if name == "get_day_prices":
        return get_day_prices(arguments, now)
    if name == "compare_tariff":
        return compare_tariff(arguments, now)
    if name == "search_energy_law":
        return search_energy_law(arguments, client)
    return failure(f"unknown tool {name}")
