"""Compose the morning and cash back reports from individual services."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .services.cashback import (
    CHASE,
    DISCOVER,
    Calendar,
    Card,
    Quarter,
    fetch_chase_calendar,
    fetch_discover_calendar,
)
from .services.stock import fetch_quotes, format_report
from .services.weather import fetch_daily_forecast

log = logging.getLogger(__name__)

# Process-wide counters for observability. Reset on process restart.
_metrics: dict[str, int] = {
    "failed_sections_weather": 0,
    "failed_sections_market": 0,
    "reports_built": 0,
}


def report_metrics() -> dict[str, int]:
    """Snapshot of cumulative report failure metrics."""
    return dict(_metrics)


def _greeting(settings: Settings) -> str:
    today = datetime.now(ZoneInfo(settings.timezone)).strftime("%b %-d, %Y (%a)")
    return f"<b>Good morning!</b>\nToday is {today}."


async def build_weather_section(settings: Settings) -> str:
    forecast = await fetch_daily_forecast(settings.location_name)
    return forecast.format()


async def build_market_section(settings: Settings) -> str:
    if not settings.tickers:
        return ""
    quotes = await fetch_quotes(settings.tickers)
    return format_report(quotes)


async def build_morning_report(settings: Settings) -> str | None:
    """Return the combined morning text, or None if every section failed."""
    sections: list[str] = [_greeting(settings)]

    weather_ok = False
    try:
        sections.append(await build_weather_section(settings))
        weather_ok = True
    except Exception:
        log.exception("Weather section failed")
        _metrics["failed_sections_weather"] += 1
        sections.append(
            "\U0001f324\ufe0f <b>Weather</b>\nWeather unavailable this morning."
        )

    is_weekend = datetime.now(ZoneInfo(settings.timezone)).weekday() >= 5
    market_ok = False
    if settings.tickers and not is_weekend:
        try:
            market = await build_market_section(settings)
            if market:
                sections.append(market)
                market_ok = True
        except Exception:
            log.exception("Market section failed")
            _metrics["failed_sections_market"] += 1
            sections.append(
                "\U0001f4c8 <b>Markets</b>\nMarket data unavailable this morning."
            )

    if not weather_ok and (is_weekend or not market_ok):
        # Everything we attempted failed; still send greeting + placeholders
        # so users know the bot is alive.
        log.warning(
            "All sections failed; sending greeting + placeholders "
            "(weather_fail_total=%d, market_fail_total=%d)",
            _metrics["failed_sections_weather"],
            _metrics["failed_sections_market"],
        )
    _metrics["reports_built"] += 1
    return "\n\n".join(sections)


async def _load_calendar(
    card: Card, fetch: Callable[[], Awaitable[Calendar]]
) -> Calendar | None:
    try:
        return await fetch()
    except Exception:
        log.exception("%s calendar failed", card.name)
        return None


def _card_section(card: Card, calendar: Calendar | None, quarter: Quarter) -> str:
    if calendar is None:
        return (
            f"<b>{card.name}</b>\n"
            f"Categories unavailable right now. {card.link('Check here')}"
        )
    bonus = calendar.get(quarter)
    if bonus is None:
        return f"<b>{card.name}</b>\nNot announced yet."
    return bonus.format()


async def build_cashback_report(quarters: list[Quarter]) -> str:
    chase, discover = await asyncio.gather(
        _load_calendar(CHASE, fetch_chase_calendar),
        _load_calendar(DISCOVER, fetch_discover_calendar),
    )
    blocks = [
        "\n\n".join(
            [
                f"\U0001f4b3 <b>Bonus categories \u00b7 {quarter.label}</b>\n{quarter.span}",
                _card_section(CHASE, chase, quarter),
                _card_section(DISCOVER, discover, quarter),
            ]
        )
        for quarter in quarters
    ]
    return "\n\n".join(blocks)
