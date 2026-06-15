"""Aiogram handlers."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, Router, html
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from .config import Settings
from .reports import build_market_section
from .services.anilist import AiringEpisode, fetch_airing_today, fetch_airing_week
from .services.douban import DoubanItem, fetch_trending_books, fetch_trending_movies
from .services.stock import fetch_quotes, format_report
from .services.weather import fetch_daily_forecast

log = logging.getLogger(__name__)

# Telegram caps a single message at 4096 characters.
_MAX_MESSAGE_LEN = 4096

HELP_TEXT = (
    "<b>Commands</b>\n"
    "/start — greet and show chat id\n"
    "/weather [city] — forecast for the configured city or a given city\n"
    "/stock [TICKER ...] — quotes for default tickers or provided ones\n"
    "/anime [week] — highly-rated anime airing today (or this week)\n"
    "/douban [movies|books] — trending Douban movies and monthly books\n"
    "/help — list commands"
)


def _render_airing(episodes: list[AiringEpisode]) -> str:
    """Render today's airing episodes, truncating to stay under Telegram's limit."""
    header = "\U0001f4fa <b>Airing today</b>"
    lines = [header]
    for index, episode in enumerate(episodes):
        candidate = "\n".join([*lines, episode.format()])
        remaining = len(episodes) - index
        footer = f"\n<i>…and {remaining} more</i>"
        if len(candidate) + len(footer) > _MAX_MESSAGE_LEN:
            lines.append(f"<i>…and {remaining} more</i>")
            break
        lines.append(episode.format())
    return "\n".join(lines)


def _render_week(episodes: list[AiringEpisode]) -> str:
    """Render a week of airing episodes grouped by day, each day score-sorted."""
    by_day: dict[date, list[AiringEpisode]] = defaultdict(list)
    for episode in episodes:
        by_day[episode.airing_at.date()].append(episode)

    lines = ["\U0001f4fa <b>Airing this week</b>"]
    rendered = 0
    total = len(episodes)
    for day in sorted(by_day):
        day_episodes = sorted(by_day[day], key=lambda e: (-e.score, e.title.lower()))
        block = [f"\n<b>{day.strftime('%a, %b %-d')}</b>"]
        for episode in day_episodes:
            line = episode.format()
            remaining = total - rendered
            footer = f"\n<i>…and {remaining} more</i>"
            projected = "\n".join([*lines, *block, line])
            if len(projected) + len(footer) > _MAX_MESSAGE_LEN:
                lines.extend(block)
                lines.append(f"<i>…and {remaining} more</i>")
                return "\n".join(lines)
            block.append(line)
            rendered += 1
        lines.extend(block)
    return "\n".join(lines)


async def _empty() -> list[DoubanItem]:
    """Placeholder coroutine so gather can skip an unwanted Douban section."""
    return []


def _render_douban(
    movies: list[DoubanItem], books: list[DoubanItem], month: str
) -> str:
    """Render Douban movie and book sections, truncating to fit Telegram."""
    lines = ["\U0001f525 <b>Trending on Douban</b>"]
    sections = (
        ("\U0001f3ac <b>Movies</b>", movies),
        ("\U0001f4da <b>Books</b>", books),
    )
    for title, items in sections:
        if not items:
            continue
        block = [f"\n{title} \u00b7 {month}"]
        for item in items:
            candidate = "\n".join([*lines, *block, item.format()])
            if len(candidate) > _MAX_MESSAGE_LEN:
                lines.extend(block)
                return "\n".join(lines)
            block.append(item.format())
        lines.extend(block)
    return "\n".join(lines)


def build_router(settings: Settings) -> Router:
    router = Router(name="main")

    @router.message(CommandStart())
    async def handle_start(message: Message) -> None:
        await message.answer(
            f"Hello, {html.bold(message.from_user.full_name)}!\n"
            f"Your chat id is <code>{message.chat.id}</code>.\n"
            f"Use /help to see all commands."
        )

    @router.message(Command("help"))
    async def handle_help(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @router.message(Command("weather"))
    async def handle_weather(message: Message, command: CommandObject) -> None:
        city = (command.args or "").strip() or settings.location_name
        try:
            if not city:
                await message.answer(
                    "No city configured. Set LOCATION_NAME in .env or pass a city name."
                )
                return
            forecast = await fetch_daily_forecast(city)
            await message.answer(forecast.format())
        except Exception:
            log.exception("Forecast fetch failed")
            await message.answer(
                "Couldn't fetch the forecast right now. Try again later."
            )

    @router.message(Command("stock"))
    async def handle_stock(message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if raw:
            tickers = list(dict.fromkeys(t.upper() for t in raw.split() if t))
        else:
            tickers = list(settings.tickers)
        if not tickers:
            await message.answer(
                "No tickers configured. Set TICKERS in .env or pass tickers as arguments."
            )
            return
        try:
            if raw:
                quotes = await fetch_quotes(tickers)
                await message.answer(format_report(quotes))
            else:
                await message.answer(await build_market_section(settings))
        except Exception:
            log.exception("Stocks fetch failed")
            await message.answer(
                "Couldn't fetch market data right now. Try again later."
            )

    @router.message(Command("anime"))
    async def handle_anime(message: Message, command: CommandObject) -> None:
        weekly = (command.args or "").strip().lower() == "week"
        try:
            if weekly:
                episodes = await fetch_airing_week(settings.timezone)
                if not episodes:
                    await message.answer("No highly-rated anime airing this week.")
                    return
                text = _render_week(episodes)
            else:
                episodes = await fetch_airing_today(settings.timezone)
                if not episodes:
                    await message.answer("No highly-rated anime airing today.")
                    return
                text = _render_airing(episodes)
            await message.answer(
                text,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            log.exception("Airing schedule fetch failed")
            await message.answer(
                "Couldn't fetch the airing schedule right now. Try again later."
            )

    @router.message(Command("douban"))
    async def handle_douban(message: Message, command: CommandObject) -> None:
        choice = (command.args or "").strip().lower()
        want_movies = choice in ("", "movies")
        want_books = choice in ("", "books")
        if not want_movies and not want_books:
            await message.answer("Usage: /douban [movies|books]")
            return
        try:
            movies, books = await asyncio.gather(
                fetch_trending_movies() if want_movies else _empty(),
                fetch_trending_books() if want_books else _empty(),
            )
            if not movies and not books:
                await message.answer("Couldn't load Douban trending right now.")
                return
            month = datetime.now(ZoneInfo(settings.timezone)).strftime("%B %Y")
            await message.answer(
                _render_douban(movies, books, month),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            log.exception("Douban fetch failed")
            await message.answer(
                "Couldn't fetch Douban trending right now. Try again later."
            )

    return router


def register_handlers(dp: Dispatcher, settings: Settings) -> None:
    dp.include_router(build_router(settings))
