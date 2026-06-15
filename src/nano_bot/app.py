"""Application entry point."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import Settings, load_settings
from .handlers import register_handlers
from .scheduler import build_scheduler

log = logging.getLogger(__name__)

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Greet and show chat id"),
    BotCommand(command="weather", description="Forecast (optional: city)"),
    BotCommand(command="stock", description="Stock quotes (optional: TICKER ...)"),
    BotCommand(command="anime", description="Anime airing today (or: week)"),
    BotCommand(command="douban", description="Trending Douban movies & books"),
    BotCommand(command="help", description="List commands"),
]


def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run(settings: Settings) -> None:
    bot = build_bot(settings)
    dp = Dispatcher()
    register_handlers(dp, settings)

    scheduler = build_scheduler(bot, settings)
    scheduler.start()
    try:
        await bot.set_my_commands(BOT_COMMANDS)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run(load_settings()))


if __name__ == "__main__":
    main()
