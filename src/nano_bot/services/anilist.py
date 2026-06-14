"""AniList GraphQL client. No API key required."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp

from ._retry import retry_async

API_URL = "https://graphql.anilist.co"

logger = logging.getLogger(__name__)

# Only list anime whose final weighted rating is above this value (0-100).
# This also implicitly gates raw quality: the weighted rating never exceeds the
# raw score (it only blends downward toward the prior), so passing this bar
# requires a raw score above it too.
MIN_WEIGHTED_SCORE = 70

# Popularity-weighted rating parameters (Bayesian / IMDB-style):
#   rating = (v / (v + m)) * R + (m / (v + m)) * C
# where R is the raw score, m is the audience at which a title's own score
# carries 50% weight, and C is the prior mean pulled toward for low-audience
# titles. The effective audience v combines popularity (passive list-adds)
# with favourites (a stronger "passion" signal), the latter scaled up by
# _FAVOURITES_WEIGHT.
_RATING_PRIOR = 55.0
_RATING_POPULARITY_WEIGHT = 100_000
_FAVOURITES_WEIGHT = 80

# Media formats worth listing; AniList airing schedules also include ONAs,
# music videos and shorts we don't care about here.
_ALLOWED_FORMATS = frozenset({"TV", "TV_SHORT"})

# AniList caps page size at 50; cap total pages to bound a single command.
_PER_PAGE = 50
_MAX_PAGES = 5
# A week spans many more airing entries than a day; allow more pages so later
# days aren't truncated (results are TIME-sorted, so a low cap drops them).
_MAX_WEEK_PAGES = 20

# Status codes worth retrying (transient upstream / rate-limit issues).
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_HEADERS = {
    "User-Agent": "nano-bot (+https://github.com/anomalyco/opencode)",
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

_AIRING_QUERY = """
query ($start: Int, $end: Int, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    airingSchedules(
      airingAt_greater: $start
      airingAt_lesser: $end
      sort: TIME
    ) {
      episode
      airingAt
      media {
        siteUrl
        averageScore
        meanScore
        popularity
        favourites
        isAdult
        format
        title { romaji english }
      }
    }
  }
}
"""


async def _post_graphql(
    query: str, variables: dict, description: str, session: aiohttp.ClientSession
) -> dict:
    """POST a GraphQL query and return parsed JSON, retrying transient errors."""

    async def _request() -> dict:
        async with session.post(
            API_URL, json={"query": query, "variables": variables}
        ) as resp:
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


@dataclass(slots=True, frozen=True)
class AiringEpisode:
    title: str
    episode: int
    site_url: str
    score: int
    airing_at: datetime

    def format(self) -> str:
        return (
            f'<a href="{self.site_url}">{self.title}</a> '
            f"\u00b7 Ep {self.episode} \u00b7 \u2b50 {self.score}"
        )


def _day_bounds(timezone: str) -> tuple[int, int]:
    """Return (start, end) Unix timestamps for 'today' in the given IANA tz."""
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def _week_bounds(timezone: str) -> tuple[int, int]:
    """Return (start, end) Unix timestamps for the next 7 days in the given tz."""
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return int(start.timestamp()), int(end.timestamp())


def _weighted_rating(score: float, popularity: int, favourites: int) -> int:
    """Blend raw score with audience (popularity + favourites) into a 0-100 rating."""
    v = max(popularity, 0) + _FAVOURITES_WEIGHT * max(favourites, 0)
    m = _RATING_POPULARITY_WEIGHT
    rating = (v / (v + m)) * score + (m / (v + m)) * _RATING_PRIOR
    return round(rating)


def _to_episode(entry: dict, tz: ZoneInfo) -> AiringEpisode | None:
    """Map a raw airingSchedules entry to an AiringEpisode, or None if filtered."""
    media = entry.get("media") or {}
    if media.get("isAdult"):
        return None
    if media.get("format") not in _ALLOWED_FORMATS:
        return None

    score = media.get("averageScore")
    if score is None:
        score = media.get("meanScore")
    if score is None:
        # No rating yet (common for brand-new titles); can't score it.
        return None

    title = media.get("title") or {}
    name = title.get("english") or title.get("romaji")
    site_url = media.get("siteUrl")
    airing_at = entry.get("airingAt")
    if not name or not site_url or airing_at is None:
        return None

    weighted = _weighted_rating(
        score,
        int(media.get("popularity") or 0),
        int(media.get("favourites") or 0),
    )
    if weighted <= MIN_WEIGHTED_SCORE:
        return None

    return AiringEpisode(
        title=name,
        episode=int(entry.get("episode") or 0),
        site_url=site_url,
        score=weighted,
        airing_at=datetime.fromtimestamp(int(airing_at), tz=tz),
    )


async def _fetch_airing(
    timezone: str, start: int, end: int, max_pages: int
) -> list[AiringEpisode]:
    """Fetch airing episodes in [start, end), paginating up to max_pages."""
    tz = ZoneInfo(timezone)
    episodes: list[AiringEpisode] = []

    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        for page in range(1, max_pages + 1):
            data = await _post_graphql(
                _AIRING_QUERY,
                {"start": start, "end": end, "page": page, "perPage": _PER_PAGE},
                description="AniList airing schedule",
                session=session,
            )
            page_data = (data.get("data") or {}).get("Page") or {}
            for entry in page_data.get("airingSchedules") or []:
                episode = _to_episode(entry, tz)
                if episode is not None:
                    episodes.append(episode)
            if not (page_data.get("pageInfo") or {}).get("hasNextPage"):
                break

    return episodes


async def fetch_airing_today(timezone: str) -> list[AiringEpisode]:
    """Fetch today's highly-rated airing anime, sorted by score descending."""
    start, end = _day_bounds(timezone)
    episodes = await _fetch_airing(timezone, start, end, _MAX_PAGES)
    episodes.sort(key=lambda e: (-e.score, e.title.lower()))
    return episodes


async def fetch_airing_week(timezone: str) -> list[AiringEpisode]:
    """Fetch the next 7 days of highly-rated airing anime, time-ordered."""
    start, end = _week_bounds(timezone)
    episodes = await _fetch_airing(timezone, start, end, _MAX_WEEK_PAGES)
    # Time-ordered so the caller can group by day; score sorting is per-day.
    episodes.sort(key=lambda e: e.airing_at)
    return episodes
