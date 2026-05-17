"""Scheduled jobs (morning broadcast)."""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings
from .reports import build_morning_report

log = logging.getLogger(__name__)


async def broadcast_morning(bot: Bot, settings: Settings) -> None:
    text = await build_morning_report(settings)
    if not text:
        log.warning("Morning report empty, skipping broadcast")
        return
    for chat_id in settings.chat_ids:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            log.exception("Failed to send morning report to %s", chat_id)


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
    log.info("Morning report scheduled at %s %s", settings.notify_time, settings.timezone)
    return scheduler
