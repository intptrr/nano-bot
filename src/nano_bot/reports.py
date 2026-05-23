"""Compose the morning report from individual services."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .services.stocks import fetch_quotes, format_report
from .services.weather import fetch_daily_forecast

log = logging.getLogger(__name__)


def _greeting(settings: Settings) -> str:
    today = datetime.now(ZoneInfo(settings.timezone)).strftime("%A, %B %d")
    return f"Good morning! Today is {today}."


async def build_weather_section(settings: Settings) -> str:
    forecast = await fetch_daily_forecast(
        settings.latitude,
        settings.longitude,
        settings.timezone,
        settings.location_name,
    )
    return forecast.format()


async def build_market_section(settings: Settings) -> str:
    if not settings.tickers:
        return ""
    quotes = await fetch_quotes(settings.tickers)
    return format_report(quotes)


async def build_morning_report(settings: Settings) -> str | None:
    """Return the combined morning text, or None if every section failed."""
    sections: list[str] = [_greeting(settings)]

    try:
        sections.append(await build_weather_section(settings))
    except Exception:
        log.exception("Weather section failed")

    is_weekend = datetime.now(ZoneInfo(settings.timezone)).weekday() >= 5
    if settings.tickers and not is_weekend:
        try:
            market = await build_market_section(settings)
            if market:
                sections.append(market)
        except Exception:
            log.exception("Market section failed")

    if len(sections) == 1:
        return None
    return "\n\n".join(sections)
