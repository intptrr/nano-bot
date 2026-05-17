"""Aiogram handlers."""
from __future__ import annotations

import logging

from aiogram import Dispatcher, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from .config import Settings
from .reports import build_market_section, build_weather_section

log = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Commands</b>\n"
    "/start — greet and show chat id\n"
    "/weather — today's forecast\n"
    "/stocks — ticker quotes\n"
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
    async def handle_weather(message: Message) -> None:
        try:
            await message.answer(await build_weather_section(settings))
        except Exception:
            log.exception("Forecast fetch failed")
            await message.answer("Couldn't fetch the forecast right now. Try again later.")

    @router.message(Command("stocks"))
    async def handle_stocks(message: Message) -> None:
        if not settings.tickers:
            await message.answer("No tickers configured. Set TICKERS in .env.")
            return
        try:
            await message.answer(await build_market_section(settings))
        except Exception:
            log.exception("Stocks fetch failed")
            await message.answer("Couldn't fetch market data right now. Try again later.")

    @router.message()
    async def echo(message: Message) -> None:
        await message.answer("Unknown command. Try /help.")

    return router


def register_handlers(dp: Dispatcher, settings: Settings) -> None:
    dp.include_router(build_router(settings))
