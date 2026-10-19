"""Aiogram handlers."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, Router, html
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from .config import Settings
from .reports import (
    MAX_MESSAGE_LEN,
    build_anime_season_report,
    build_anime_today_report,
    build_anime_week_report,
    build_cashback_report,
    build_flight_report,
    build_market_section,
)
from .services.bangumi import Season
from .services.cashback import Quarter
from .services.douban import (
    DoubanItem,
    UpcomingMovie,
    fetch_anticipated_movies,
    fetch_trending_books,
    fetch_trending_movies,
)
from .services.stock import fetch_quotes, format_report
from .services.weather import fetch_daily_forecast

log = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Commands</b>\n"
    "/start — greet and show chat id\n"
    "/weather [city] — forecast for the configured city or a given city\n"
    "/stock [TICKER ...] — quotes for default tickers or provided ones\n"
    "/anime [week|season] — highly rated anime airing today, this week or this season\n"
    "/douban [movies|books] — new and upcoming Douban movies, and monthly books\n"
    "/cashback — Chase and Discover 5% categories (this and next quarter)\n"
    "/flight — Alaska fare sales and Global Getaways right now\n"
    "/help — list commands"
)


async def _empty() -> list[DoubanItem]:
    """Placeholder coroutine so gather can skip an unwanted Douban section."""
    return []


def _render_douban(
    movies: list[DoubanItem],
    upcoming: list[UpcomingMovie],
    books: list[DoubanItem],
    month: str,
) -> str:
    """Render Douban movie and book sections, truncating to fit Telegram."""
    lines = ["\U0001f525 <b>Trending on Douban</b>"]
    sections = (
        (f"\U0001f3ac <b>Movies</b> \u00b7 {month}", movies),
        ("\u23f3 <b>Most anticipated</b>", upcoming),
        (f"\U0001f4da <b>Books</b> \u00b7 {month}", books),
    )
    for title, items in sections:
        if not items:
            continue
        block = [f"\n{title}"]
        for item in items:
            candidate = "\n".join([*lines, *block, item.format()])
            if len(candidate) > MAX_MESSAGE_LEN:
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
        choice = (command.args or "").strip().lower()
        if choice not in ("", "week", "season"):
            await message.answer("Usage: /anime [week|season]")
            return
        today = datetime.now(ZoneInfo(settings.timezone)).date()
        try:
            if choice == "week":
                text = await build_anime_week_report(today)
            elif choice == "season":
                text = await build_anime_season_report(Season.containing(today))
            else:
                text = await build_anime_today_report(today)
            await message.answer(
                text,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            log.exception("Bangumi fetch failed")
            await message.answer(
                "Couldn't fetch anime from Bangumi right now. Try again later."
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
            movies, upcoming, books = await asyncio.gather(
                fetch_trending_movies() if want_movies else _empty(),
                fetch_anticipated_movies() if want_movies else _empty(),
                fetch_trending_books() if want_books else _empty(),
            )
            if not movies and not upcoming and not books:
                await message.answer("Couldn't load Douban trending right now.")
                return
            month = datetime.now(ZoneInfo(settings.timezone)).strftime("%B %Y")
            await message.answer(
                _render_douban(movies, upcoming, books, month),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            log.exception("Douban fetch failed")
            await message.answer(
                "Couldn't fetch Douban trending right now. Try again later."
            )

    @router.message(Command("cashback"))
    async def handle_cashback(message: Message) -> None:
        current = Quarter.containing(datetime.now(ZoneInfo(settings.timezone)).date())
        await message.answer(
            await build_cashback_report([current, current.next()]),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    @router.message(Command("flight"))
    async def handle_flight(message: Message) -> None:
        await message.answer(
            await build_flight_report(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    return router


def register_handlers(dp: Dispatcher, settings: Settings) -> None:
    dp.include_router(build_router(settings))
