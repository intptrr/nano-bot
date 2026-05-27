"""Aiogram handlers."""

from __future__ import annotations

import logging

from aiogram import Dispatcher, Router, html
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from .config import Settings
from .reports import build_market_section
from .services.stock import fetch_quotes, format_report
from .services.weather import fetch_daily_forecast

log = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Commands</b>\n"
    "/start — greet and show chat id\n"
    "/weather [city] — forecast for the configured city or a given city\n"
    "/stock [TICKER ...] — quotes for default tickers or provided ones\n"
    "/help — list commands"
)


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

    @router.message()
    async def echo(message: Message) -> None:
        await message.answer("Unknown command. Try /help.")

    return router


def register_handlers(dp: Dispatcher, settings: Settings) -> None:
    dp.include_router(build_router(settings))
