"""Runtime configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. See .env.example.")
    return value


def _parse_chat_ids(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def _parse_tickers(raw: str) -> list[str]:
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _parse_hhmm(raw: str) -> tuple[int, int]:
    hour, minute = raw.split(":")
    return int(hour), int(minute)


@dataclass(slots=True, frozen=True)
class Settings:
    bot_token: str
    chat_ids: list[int]
    latitude: float
    longitude: float
    timezone: str
    notify_hour: int
    notify_minute: int
    tickers: list[str] = field(default_factory=list)
    location_name: str = ""

    @property
    def notify_time(self) -> str:
        return f"{self.notify_hour:02d}:{self.notify_minute:02d}"


def load_settings() -> Settings:
    load_dotenv()
    hour, minute = _parse_hhmm(os.getenv("NOTIFY_TIME", "08:00"))
    return Settings(
        bot_token=_require("BOT_TOKEN"),
        chat_ids=_parse_chat_ids(_require("CHAT_IDS")),
        latitude=float(_require("LATITUDE")),
        longitude=float(_require("LONGITUDE")),
        timezone=os.getenv("TIMEZONE", "UTC"),
        notify_hour=hour,
        notify_minute=minute,
        tickers=_parse_tickers(os.getenv("TICKERS", "")),
        location_name=os.getenv("LOCATION_NAME", "").strip(),
    )
