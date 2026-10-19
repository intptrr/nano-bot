"""Bangumi (bgm.tv) anime: the weekly airing calendar and seasonal lineups.

Public API with no key; Bangumi asks clients to send a descriptive User-Agent.
Only Japanese anime are listed (Bangumi's 日本 tag). Names are Chinese where
Bangumi has one, otherwise the original title. The calendar's weekdays follow
Japan's broadcast day. A season's lineup is every Japanese TV or web show that
started from three months before the season through its end and either
premiered in the season or has an episode airing after its first two weeks.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import aiohttp

from ._retry import retry_async

CALENDAR_URL = "https://api.bgm.tv/calendar"
SUBJECTS_URL = "https://api.bgm.tv/v0/subjects"
SUBJECT_URL = "https://api.bgm.tv/v0/subjects/{id}"
EPISODES_URL = "https://api.bgm.tv/v0/episodes"
SUBJECT_PAGE_URL = "https://bgm.tv/subject/{id}"

MIN_SCORE = 6.5
MIN_VOTES = 50

logger = logging.getLogger(__name__)

_ANIME_TYPE = 2
_MAIN_EPISODE_TYPE = 0
_JAPAN_TAG = "日本"
_SEASON_PLATFORMS = frozenset({"TV", "WEB"})
_LOOKBACK_MONTHS = 3
_SEASON_MONTHS = 3
_AIRING_GRACE = timedelta(days=14)
_MAX_WEEKS_PER_EPISODE = 2

_PAGE_SIZE = 50
_MAX_MONTH_SUBJECTS = 500
_EPISODE_PAGE_SIZE = 200
_MAX_EPISODES = 1000

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_HEADERS = {
    "User-Agent": "intptrr/nano-bot (https://github.com/intptrr/nano-bot)",
    "Accept": "application/json",
}

_REQUEST_SLOTS = asyncio.Semaphore(5)


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


def _month_start(year: int, month: int, offset: int) -> date:
    index = year * 12 + month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


@dataclass(slots=True, frozen=True)
class Season:
    year: int
    month: int

    @classmethod
    def containing(cls, day: date) -> Season:
        return cls(day.year, (day.month - 1) // 3 * 3 + 1)

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end(self) -> date:
        return _month_start(self.year, self.month, _SEASON_MONTHS) - timedelta(days=1)

    @property
    def airing_from(self) -> date:
        return self.start + _AIRING_GRACE

    @property
    def candidate_months(self) -> list[date]:
        return [
            _month_start(self.year, self.month, offset)
            for offset in range(-_LOOKBACK_MONTHS, _SEASON_MONTHS)
        ]

    @property
    def label(self) -> str:
        return self.start.strftime("%B %Y")


_season_cache: dict[Season, tuple[date, list[Anime]]] = {}
_japanese_cache: dict[int, bool] = {}


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    description: str,
    params: dict | None = None,
):
    async def _request():
        async with _REQUEST_SLOTS, session.get(url, params=params) as resp:
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


def _parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _to_anime(subject: dict) -> Anime:
    rating = subject.get("rating") or {}
    return Anime(
        subject_id=int(subject["id"]),
        name=subject.get("name_cn") or subject.get("name") or "",
        score=float(rating.get("score") or 0),
        votes=int(rating.get("total") or 0),
    )


def _is_japanese(subject: dict) -> bool:
    return _JAPAN_TAG in (subject.get("meta_tags") or [])


def _is_highly_rated(show: Anime) -> bool:
    return show.score >= MIN_SCORE and show.votes >= MIN_VOTES


def _highly_rated(anime: Iterable[Anime]) -> list[Anime]:
    return sorted(
        filter(_is_highly_rated, anime),
        key=lambda show: (show.score, show.votes),
        reverse=True,
    )


async def _remember_origins(
    session: aiohttp.ClientSession, subject_ids: Iterable[int]
) -> None:
    unknown = [
        subject_id for subject_id in subject_ids if subject_id not in _japanese_cache
    ]
    subjects = await asyncio.gather(
        *(
            _get_json(
                session,
                SUBJECT_URL.format(id=subject_id),
                f"Bangumi subject {subject_id}",
            )
            for subject_id in unknown
        )
    )
    for subject_id, subject in zip(unknown, subjects, strict=True):
        _japanese_cache[subject_id] = _is_japanese(subject)


async def fetch_calendar() -> dict[int, list[Anime]]:
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        days = await _get_json(session, CALENDAR_URL, "Bangumi calendar")
        calendar = {
            int(day["weekday"]["id"]): _highly_rated(
                _to_anime(item) for item in day.get("items") or []
            )
            for day in days
        }
        await _remember_origins(
            session,
            {show.subject_id for shows in calendar.values() for show in shows},
        )
    return {
        weekday: [show for show in shows if _japanese_cache[show.subject_id]]
        for weekday, shows in calendar.items()
    }


async def _subjects_started_in(
    session: aiohttp.ClientSession, month: date
) -> list[dict]:
    subjects: list[dict] = []
    for offset in range(0, _MAX_MONTH_SUBJECTS, _PAGE_SIZE):
        page = await _get_json(
            session,
            SUBJECTS_URL,
            f"Bangumi anime starting {month:%B %Y}",
            params={
                "type": _ANIME_TYPE,
                "year": month.year,
                "month": month.month,
                "sort": "date",
                "limit": _PAGE_SIZE,
                "offset": offset,
            },
        )
        subjects.extend(page.get("data") or [])
        if offset + _PAGE_SIZE >= int(page.get("total") or 0):
            break
    return subjects


def _could_air_during(subject: dict, season: Season) -> bool:
    started = _parse_date(subject.get("date"))
    if started is None or started > season.end:
        return False
    episodes = max(
        int(subject.get("eps") or 0), int(subject.get("total_episodes") or 0)
    )
    latest_end = started + timedelta(weeks=_MAX_WEEKS_PER_EPISODE * episodes)
    return not episodes or latest_end >= season.airing_from


def _is_season_candidate(subject: dict, season: Season) -> bool:
    return (
        subject.get("platform") in _SEASON_PLATFORMS
        and _is_japanese(subject)
        and not subject.get("nsfw")
        and _is_highly_rated(_to_anime(subject))
        and _could_air_during(subject, season)
    )


def _premiered_in(subject: dict, season: Season) -> bool:
    started = _parse_date(subject.get("date"))
    return started is not None and started >= season.start


async def _airs_during(
    session: aiohttp.ClientSession, subject_id: int, season: Season
) -> bool:
    for offset in range(0, _MAX_EPISODES, _EPISODE_PAGE_SIZE):
        page = await _get_json(
            session,
            EPISODES_URL,
            f"Bangumi episodes of {subject_id}",
            params={
                "subject_id": subject_id,
                "type": _MAIN_EPISODE_TYPE,
                "limit": _EPISODE_PAGE_SIZE,
                "offset": offset,
            },
        )
        for episode in page.get("data") or []:
            aired = _parse_date(episode.get("airdate"))
            if aired and season.airing_from <= aired <= season.end:
                return True
        if offset + _EPISODE_PAGE_SIZE >= int(page.get("total") or 0):
            break
    return False


async def _fetch_season(season: Season) -> list[Anime]:
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        months = await asyncio.gather(
            *(_subjects_started_in(session, month) for month in season.candidate_months)
        )
        candidates = {
            subject["id"]: subject
            for subjects in months
            for subject in subjects
            if _is_season_candidate(subject, season)
        }
        carried_over = [
            subject_id
            for subject_id, subject in candidates.items()
            if not _premiered_in(subject, season)
        ]
        airing = await asyncio.gather(
            *(_airs_during(session, subject_id, season) for subject_id in carried_over)
        )
    still_airing = {
        subject_id
        for subject_id, airs in zip(carried_over, airing, strict=True)
        if airs
    }
    return _highly_rated(
        _to_anime(subject)
        for subject_id, subject in candidates.items()
        if _premiered_in(subject, season) or subject_id in still_airing
    )


async def fetch_season(season: Season) -> list[Anime]:
    today = date.today()
    cached = _season_cache.get(season)
    if cached and cached[0] == today:
        return cached[1]
    anime = await _fetch_season(season)
    _season_cache[season] = (today, anime)
    return anime
