"""Compose the morning, cash back, flight, anime and tennis reports from services."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .services.alaska import PROMOTION_SOURCES, promotion_status
from .services.bangumi import Anime, Season, fetch_calendar, fetch_season
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
from .services.tennis import (
    PACIFIC,
    UNRECOGNIZED,
    CenterStatus,
    Opening,
    check_centers,
)
from .services.weather import fetch_daily_forecast

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096

_AIRING_TODAY = "\U0001f4fa <b>Airing today</b>"
_AIRING_THIS_WEEK = "\U0001f4fa <b>Airing this week</b>"

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


async def build_flight_report() -> str:
    statuses = await asyncio.gather(
        *(promotion_status(source) for source in PROMOTION_SOURCES)
    )
    return "\n\n".join(status.message for status in statuses)


def _render_anime(header: str, anime: list[Anime], empty: str) -> str:
    if not anime:
        return f"{header}\n{empty}"
    lines = [header]
    for index, show in enumerate(anime):
        candidate = "\n".join([*lines, show.format()])
        remaining = len(anime) - index
        footer = f"\n<i>…and {remaining} more</i>"
        if len(candidate) + len(footer) > MAX_MESSAGE_LEN:
            lines.append(f"<i>…and {remaining} more</i>")
            break
        lines.append(show.format())
    return "\n".join(lines)


def _render_anime_week(calendar: dict[int, list[Anime]], today: date) -> str:
    days = [today + timedelta(days=offset) for offset in range(7)]
    total = sum(len(calendar.get(day.isoweekday(), [])) for day in days)
    if not total:
        return f"{_AIRING_THIS_WEEK}\nNo highly rated anime this week."
    lines = [_AIRING_THIS_WEEK]
    rendered = 0
    for day in days:
        shows = calendar.get(day.isoweekday(), [])
        if not shows:
            continue
        block = [f"\n<b>{day.strftime('%a, %b %-d')}</b>"]
        for show in shows:
            line = show.format()
            remaining = total - rendered
            footer = f"\n<i>…and {remaining} more</i>"
            projected = "\n".join([*lines, *block, line])
            if len(projected) + len(footer) > MAX_MESSAGE_LEN:
                lines.extend(block)
                lines.append(f"<i>…and {remaining} more</i>")
                return "\n".join(lines)
            block.append(line)
            rendered += 1
        lines.extend(block)
    return "\n".join(lines)


async def build_anime_today_report(today: date) -> str:
    calendar = await fetch_calendar()
    return _render_anime(
        _AIRING_TODAY,
        calendar.get(today.isoweekday(), []),
        "No highly rated anime airing today.",
    )


async def build_anime_week_report(today: date) -> str:
    return _render_anime_week(await fetch_calendar(), today)


async def build_anime_season_report(season: Season) -> str:
    return _render_anime(
        f"\U0001f4fa <b>{season.label} season</b>",
        await fetch_season(season),
        "No highly rated anime airing this season yet.",
    )


def _tennis_link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'


def _clock(moment: datetime) -> str:
    return moment.strftime("%I:%M %p").lstrip("0")


def _tennis_line(verb: str, opening: Opening) -> str:
    return (
        f"{verb} {opening.opens_at:%Y/%m/%d %a} {_clock(opening.opens_at)} \u00b7 "
        f"{html.escape(opening.term)}"
    )


def _tennis_center_block(status: CenterStatus, now: datetime) -> str:
    center = status.center
    title = _tennis_link(center.page_url, center.name)
    if status.problem:
        return f"{title}\nCouldn't read the schedule right now."
    opened = [opening for opening in status.openings if opening.opens_at <= now]
    current = [
        opening
        for opening in opened
        if opening.opens_at.date() == opened[-1].opens_at.date()
    ]
    upcoming = [opening for opening in status.openings if opening.opens_at > now]
    lines = [title, *(_tennis_line("Opened", opening) for opening in current)]
    lines.extend(
        [_tennis_line("Opens", opening) for opening in upcoming]
        or ["Next dates not posted yet."]
    )
    return "\n".join(lines)


async def build_tennis_report() -> str:
    now = datetime.now(PACIFIC)
    statuses = await check_centers(now.date())
    return "\n\n".join(
        [
            "\U0001f3be <b>Tennis registration</b>",
            *(_tennis_center_block(status, now) for status in statuses),
        ]
    )


def build_tennis_heads_up(openings: list[Opening]) -> str:
    lines = ["\U0001f3be <b>Tennis registration opens tomorrow</b>"]
    lines.extend(
        f"{_clock(opening.opens_at)} \u00b7 {_tennis_link(opening.url, opening.center)}"
        f" \u00b7 {html.escape(opening.term)}"
        for opening in sorted(openings, key=lambda opening: opening.opens_at)
    )
    return "\n".join(lines)


def build_tennis_alert(opening: Opening) -> str:
    return (
        "\U0001f3be <b>Tennis registration is open</b>\n"
        f"{_tennis_link(opening.url, opening.center)} \u00b7 "
        f"{html.escape(opening.term)}"
    )


def build_tennis_warning(status: CenterStatus) -> str:
    if status.problem == UNRECOGNIZED:
        detail = (
            "The registration schedule changed and couldn't be read, so its "
            "alerts are paused. Check the page for dates."
        )
    else:
        detail = (
            "Couldn't reach the website to check registration dates. I'll keep "
            "trying; check the page in the meantime."
        )
    center = status.center
    return f"\u26a0\ufe0f {_tennis_link(center.page_url, center.name)}\n{detail}"
