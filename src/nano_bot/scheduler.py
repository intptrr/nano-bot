"""Scheduled jobs (morning broadcast, quarterly cashback heads-up)."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings
from .reports import build_cashback_report, build_morning_report
from .services.cashback import Quarter

log = logging.getLogger(__name__)


async def _send_to_chats(
    bot: Bot, settings: Settings, text: str, what: str, **kwargs
) -> None:
    for chat_id in settings.chat_ids:
        try:
            await bot.send_message(chat_id, text, **kwargs)
        except Exception:
            log.exception("Failed to send %s to %s", what, chat_id)


async def broadcast_morning(bot: Bot, settings: Settings) -> None:
    text = await build_morning_report(settings)
    if not text:
        log.warning("Morning report empty, skipping broadcast")
        return
    await _send_to_chats(bot, settings, text, "morning report")


async def broadcast_cashback(bot: Bot, settings: Settings) -> None:
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    text = await build_cashback_report([Quarter.containing(today).next()])
    await _send_to_chats(
        bot,
        settings,
        text,
        "cashback heads-up",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def build_scheduler(bot: Bot, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    trigger = CronTrigger(
        hour=settings.notify_hour,
        minute=settings.notify_minute,
        timezone=ZoneInfo(settings.timezone),
    )
    scheduler.add_job(
        broadcast_morning,
        trigger=trigger,
        args=[bot, settings],
        id="morning_report",
        replace_existing=True,
        misfire_grace_time=600,
    )
    log.info(
        "Morning report scheduled at %s %s", settings.notify_time, settings.timezone
    )
    scheduler.add_job(
        broadcast_cashback,
        trigger=CronTrigger(
            month="3,6,9,12",
            day=15,
            hour=settings.notify_hour,
            minute=settings.notify_minute,
            timezone=ZoneInfo(settings.timezone),
        ),
        args=[bot, settings],
        id="cashback_heads_up",
        replace_existing=True,
        misfire_grace_time=600,
    )
    log.info(
        "Cashback heads-up scheduled on Mar/Jun/Sep/Dec 15 at %s %s",
        settings.notify_time,
        settings.timezone,
    )
    return scheduler
