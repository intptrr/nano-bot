"""Bangumi (bgm.tv) anime: the weekly airing calendar and seasonal lineups.

Public API with no key; Bangumi asks clients to send a descriptive User-Agent.
Names are Chinese where Bangumi has one, otherwise the original title. The
calendar's weekdays follow Japan's broadcast day.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import aiohttp

from ._retry import retry_async

CALENDAR_URL = "https://api.bgm.tv/calendar"
SUBJECTS_URL = "https://api.bgm.tv/v0/subjects"
SUBJECT_PAGE_URL = "https://bgm.tv/subject/{id}"

MIN_SCORE = 6.5
MIN_VOTES = 50

logger = logging.getLogger(__name__)

_ANIME_TYPE = 2
_PAGE_SIZE = 50
_MAX_SEASON_SUBJECTS = 500

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_HEADERS = {
    "User-Agent": "intptrr/nano-bot (https://github.com/intptrr/nano-bot)",
    "Accept": "application/json",
}


class _TransientHTTPError(Exception):
    """Raised internally to trigger a retry on a transient HTTP status."""


_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
    _TransientHTTPError,
)


@dataclass(slots=True, frozen=True)
class Anime:
    subject_id: int
    name: str
    score: float
    votes: int

    def format(self) -> str:
        url = SUBJECT_PAGE_URL.format(id=self.subject_id)
        return (
            f'<a href="{url}">{html.escape(self.name)}</a> '
            f"\u00b7 \u2b50 {self.score:.1f} ({self.votes:,})"
        )


@dataclass(slots=True, frozen=True)
class Season:
    year: int
    month: int

    @classmethod
    def containing(cls, day: date) -> Season:
        return cls(day.year, (day.month - 1) // 3 * 3 + 1)

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")


async def _get_json(url: str, description: str, params: dict | None = None):
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:

        async def _request():
            async with session.get(url, params=params) as resp:
                if resp.status in _RETRY_STATUSES:
                    raise _TransientHTTPError(f"HTTP {resp.status} from {description}")
                resp.raise_for_status()
                return await resp.json()

        return await retry_async(
            _request,
            logger=logger,
            description=description,
            retry_exceptions=_RETRY_EXCEPTIONS,
        )


def _to_anime(subject: dict) -> Anime:
    rating = subject.get("rating") or {}
    return Anime(
        subject_id=int(subject["id"]),
        name=subject.get("name_cn") or subject.get("name") or "",
        score=float(rating.get("score") or 0),
        votes=int(rating.get("total") or 0),
    )


def _highly_rated(anime: Iterable[Anime]) -> list[Anime]:
    return sorted(
        (show for show in anime if show.score >= MIN_SCORE and show.votes >= MIN_VOTES),
        key=lambda show: (show.score, show.votes),
        reverse=True,
    )


async def fetch_calendar() -> dict[int, list[Anime]]:
    days = await _get_json(CALENDAR_URL, "Bangumi calendar")
    return {
        int(day["weekday"]["id"]): _highly_rated(
            _to_anime(item) for item in day.get("items") or []
        )
        for day in days
    }


async def fetch_season(season: Season) -> list[Anime]:
    subjects: list[dict] = []
    for offset in range(0, _MAX_SEASON_SUBJECTS, _PAGE_SIZE):
        page = await _get_json(
            SUBJECTS_URL,
            f"Bangumi {season.label} anime",
            params={
                "type": _ANIME_TYPE,
                "year": season.year,
                "month": season.month,
                "sort": "rank",
                "limit": _PAGE_SIZE,
                "offset": offset,
            },
        )
        subjects.extend(page.get("data") or [])
        if offset + _PAGE_SIZE >= int(page.get("total") or 0):
            break
    return _highly_rated(
        _to_anime(subject)
        for subject in subjects
        if subject.get("platform") == "TV" and not subject.get("nsfw")
    )
