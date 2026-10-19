"""Scheduled jobs (morning broadcast, cashback heads-up, Alaska alerts, anime season,
tennis registration)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .config import Settings
from .reports import (
    build_anime_season_report,
    build_cashback_report,
    build_morning_report,
    build_tennis_alert,
    build_tennis_heads_up,
    build_tennis_warning,
)
from .services.alaska import (
    IDLE,
    PROMOTION_SOURCES,
    PromotionSource,
    promotion_status,
)
from .services.bangumi import Season
from .services.cashback import Quarter
from .services.tennis import PACIFIC, CenterStatus, Opening, check_centers

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


async def broadcast_anime_season(bot: Bot, settings: Settings) -> None:
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    text = await build_anime_season_report(Season.containing(today))
    await _send_to_chats(
        bot,
        settings,
        text,
        "anime season notification",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@dataclass(slots=True)
class PromotionWatch:
    source: PromotionSource
    last_state: str | None = None

    async def poll(self) -> str | None:
        status = await promotion_status(self.source)
        if status.state is None or status.state == self.last_state:
            return None
        self.last_state = status.state
        return None if status.state == IDLE else status.message


async def check_alaska_promotions(
    bot: Bot, settings: Settings, watches: list[PromotionWatch]
) -> None:
    for watch in watches:
        text = await watch.poll()
        if text:
            await _send_to_chats(
                bot,
                settings,
                text,
                "Alaska promotion alert",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )


@dataclass(slots=True)
class TennisWatch:
    warned: dict[str, str] = field(default_factory=dict)

    def warning(self, status: CenterStatus) -> str | None:
        name = status.center.name
        if status.problem is None:
            self.warned.pop(name, None)
            return None
        if self.warned.get(name) == status.problem:
            return None
        self.warned[name] = status.problem
        return build_tennis_warning(status)


def _openings_on(statuses: list[CenterStatus], day: date) -> list[Opening]:
    return [
        opening
        for status in statuses
        for opening in status.openings
        if opening.opens_at.date() == day
    ]


async def send_tennis_alert(bot: Bot, settings: Settings, opening: Opening) -> None:
    await _send_to_chats(
        bot,
        settings,
        build_tennis_alert(opening),
        "tennis registration alert",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def _schedule_tennis_alerts(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    settings: Settings,
    openings: list[Opening],
) -> None:
    for opening in openings:
        scheduler.add_job(
            send_tennis_alert,
            trigger=DateTrigger(run_date=opening.opens_at),
            args=[bot, settings, opening],
            id=f"tennis_alert {opening.center} {opening.opens_at.isoformat()}",
            replace_existing=True,
            misfire_grace_time=600,
        )
        log.info(
            "Tennis alert for %s scheduled at %s", opening.center, opening.opens_at
        )


async def plan_tennis_alerts(
    bot: Bot, settings: Settings, scheduler: AsyncIOScheduler
) -> None:
    now = datetime.now(PACIFIC)
    statuses = await check_centers(now.date())
    openings = [
        opening
        for opening in _openings_on(statuses, now.date())
        if opening.opens_at > now
    ]
    _schedule_tennis_alerts(scheduler, bot, settings, openings)


async def tennis_heads_up(
    bot: Bot, settings: Settings, scheduler: AsyncIOScheduler, watch: TennisWatch
) -> None:
    today = datetime.now(PACIFIC).date()
    statuses = await check_centers(today)
    for status in statuses:
        warning = watch.warning(status)
        if warning:
            await _send_to_chats(
                bot,
                settings,
                warning,
                "tennis warning",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
    openings = _openings_on(statuses, today + timedelta(days=1))
    if not openings:
        return
    _schedule_tennis_alerts(scheduler, bot, settings, openings)
    await _send_to_chats(
        bot,
        settings,
        build_tennis_heads_up(openings),
        "tennis heads-up",
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
    scheduler.add_job(
        check_alaska_promotions,
        trigger=CronTrigger(
            hour="7-21/2", minute=0, timezone=ZoneInfo(settings.timezone)
        ),
        args=[bot, settings, [PromotionWatch(source) for source in PROMOTION_SOURCES]],
        id="alaska_promotions",
        replace_existing=True,
        misfire_grace_time=600,
    )
    log.info(
        "Alaska promotion checks scheduled every 2 hours, 07:00-21:00 %s",
        settings.timezone,
    )
    scheduler.add_job(
        broadcast_anime_season,
        trigger=CronTrigger(
            month="1,4,7,10",
            day=21,
            hour=settings.notify_hour,
            minute=settings.notify_minute,
            timezone=ZoneInfo(settings.timezone),
        ),
        args=[bot, settings],
        id="anime_season",
        replace_existing=True,
        misfire_grace_time=600,
    )
    log.info(
        "Anime season notification scheduled on Jan/Apr/Jul/Oct 21 at %s %s",
        settings.notify_time,
        settings.timezone,
    )
    scheduler.add_job(
        tennis_heads_up,
        trigger=CronTrigger(
            hour=settings.notify_hour,
            minute=settings.notify_minute,
            timezone=ZoneInfo(settings.timezone),
        ),
        args=[bot, settings, scheduler, TennisWatch()],
        id="tennis_heads_up",
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        plan_tennis_alerts,
        trigger=CronTrigger(hour=0, minute=5, timezone=PACIFIC),
        args=[bot, settings, scheduler],
        id="tennis_alert_planner",
        replace_existing=True,
        misfire_grace_time=600,
        next_run_time=datetime.now(PACIFIC),
    )
    log.info(
        "Tennis registration heads-up scheduled daily at %s %s; alerts planned "
        "at 00:05 Pacific and on startup",
        settings.notify_time,
        settings.timezone,
    )
    return scheduler
