"""Douban (豆瓣) trending lists via the mobile rexxar JSON API. No API key.

This uses the unofficial mobile endpoint (`m.douban.com/rexxar/...`), which
requires a `Referer: https://m.douban.com/` header. It is reverse-engineered and
may change without notice; content is zh-CN. Each command issues only a couple of
requests, so rate-limit exposure is low.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from dataclasses import dataclass
from datetime import date

import aiohttp

from ._retry import retry_async

REXXAR_URL = "https://m.douban.com/rexxar/api/v2/subject_collection/{name}/items"
COMING_SOON_URL = "https://m.douban.com/rexxar/api/v2/movie/coming_soon"

# Subject collections to surface.
MOVIE_COLLECTION = "movie_showing"  # 影院热映 (now showing)
BOOK_COLLECTION = "book_hot_monthly"  # 每月热门图书榜 (monthly hot books)

MIN_MOVIE_SCORE = 6.5
_NEW_MOVIE_MONTHS = 2

# Fetch a larger pool from each collection, then keep the top N by rating.
_POOL_SIZE = 50

logger = logging.getLogger(__name__)

# Status codes worth retrying (transient upstream / rate-limit issues).
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

# A mobile User-Agent plus the rexxar Referer are both required, or Douban 403s.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
    "Referer": "https://m.douban.com/",
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
class DoubanItem:
    title: str
    rating: float | None
    rating_count: int
    subject_url: str
    subtitle: str

    def format(self) -> str:
        if self.rating is not None:
            score = f"\u2b50 {self.rating} ({self.rating_count:,})"
        else:
            score = "\u6682\u65e0\u8bc4\u5206"  # 暂无评分 (no rating yet)
        line = f'<a href="{self.subject_url}">{self.title}</a> \u00b7 {score}'
        if self.subtitle:
            line += f" \u00b7 {self.subtitle}"
        return line


@dataclass(slots=True, frozen=True)
class UpcomingMovie:
    title: str
    release: str
    country: str
    wish_count: int
    subject_url: str

    def format(self) -> str:
        link = f'<a href="{self.subject_url}">{self.title}</a>'
        details = (self.release, self.country, f"{self.wish_count:,} 想看")
        return " \u00b7 ".join([link, *filter(None, details)])


def _subtitle_parts(card_subtitle: str) -> list[str]:
    return [p.strip() for p in (card_subtitle or "").split("/") if p.strip()]


def _subtitle(card_subtitle: str, keep: int) -> str:
    """Keep the first `keep` slash-separated parts of a card subtitle.

    Movie subtitles read `year / country / genre / director / cast` and book
    subtitles read `author / year / publisher`; trimming drops the noisier tail.
    """
    return " / ".join(_subtitle_parts(card_subtitle)[:keep])


def _country(card_subtitle: str) -> str:
    parts = _subtitle_parts(card_subtitle)
    return parts[1] if len(parts) > 1 else ""


def _is_rerelease(card_subtitle: str, today: date) -> bool:
    parts = _subtitle_parts(card_subtitle)
    made = int(parts[0]) if parts and parts[0].isdigit() else 0
    return made < today.year - 1


def _months_before(day: date, months: int) -> date:
    year, month_index = divmod(day.year * 12 + day.month - 1 - months, 12)
    last_day = calendar.monthrange(year, month_index + 1)[1]
    return date(year, month_index + 1, min(day.day, last_day))


def _opening_date(release_date: str | None, today: date) -> date | None:
    try:
        month, day = (int(part) for part in (release_date or "").split("."))
        this_year = date(today.year, month, day)
        return this_year if this_year <= today else date(today.year - 1, month, day)
    except ValueError:
        return None


async def _get_json(
    session: aiohttp.ClientSession, url: str, description: str, params: dict
) -> dict:
    async def _request() -> dict:
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


async def _fetch_collection(session: aiohttp.ClientSession, name: str) -> list[dict]:
    """Fetch the movie and book items of a Douban subject collection."""
    data = await _get_json(
        session, REXXAR_URL.format(name=name), f"Douban {name}", {"count": _POOL_SIZE}
    )
    return [
        raw
        for raw in data.get("subject_collection_items") or []
        if raw.get("id") and raw.get("type") in ("movie", "book")
    ]


def _to_item(raw: dict, subtitle: str) -> DoubanItem:
    rating = raw.get("rating") or {}
    return DoubanItem(
        title=raw.get("title") or "",
        rating=float(rating["value"]) if rating.get("value") else None,
        rating_count=int(rating.get("count") or 0),
        subject_url=f"https://{raw['type']}.douban.com/subject/{raw['id']}/",
        subtitle=subtitle,
    )


def _top_by_rating(items: list[DoubanItem], count: int) -> list[DoubanItem]:
    """Return the highest-rated items, unrated ones last."""
    ranked = sorted(
        items, key=lambda i: i.rating if i.rating is not None else -1.0, reverse=True
    )
    return ranked[:count]


def _new_movie(raw: dict, since: date, today: date) -> DoubanItem | None:
    card_subtitle = raw.get("card_subtitle") or ""
    opened = _opening_date(raw.get("release_date"), today)
    if opened is None or opened < since or _is_rerelease(card_subtitle, today):
        return None
    details = (f"{opened:%Y/%m/%d}", _country(card_subtitle))
    return _to_item(raw, " \u00b7 ".join(filter(None, details)))


async def fetch_trending_movies() -> list[DoubanItem]:
    """Fetch films that opened in Chinese cinemas in the last two months.

    Re-releases and films scoring under MIN_MOVIE_SCORE are dropped; best rated
    first (subtitle: opening date · country).
    """
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        items = await _fetch_collection(session, MOVIE_COLLECTION)
    today = date.today()
    since = _months_before(today, _NEW_MOVIE_MONTHS)
    movies = [movie for raw in items if (movie := _new_movie(raw, since, today))]
    rated = [movie for movie in movies if (movie.rating or 0) >= MIN_MOVIE_SCORE]
    return sorted(rated, key=lambda movie: movie.rating, reverse=True)


def _release_label(raw: dict) -> str:
    released = raw.get("release_date") or next(iter(raw.get("pubdate") or []), "")
    return released.split("(")[0].strip().replace("-", "/")


def _to_upcoming(raw: dict) -> UpcomingMovie:
    return UpcomingMovie(
        title=raw.get("title") or "",
        release=_release_label(raw),
        country=_country(raw.get("card_subtitle") or ""),
        wish_count=int(raw.get("wish_count") or 0),
        subject_url=f"https://movie.douban.com/subject/{raw['id']}/",
    )


async def fetch_anticipated_movies(count: int = 5) -> list[UpcomingMovie]:
    """Fetch films coming to Chinese cinemas, top `count` by 想看, no re-releases."""
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        data = await _get_json(
            session, COMING_SOON_URL, "Douban coming soon", {"count": _POOL_SIZE}
        )
    today = date.today()
    upcoming = [
        _to_upcoming(raw)
        for raw in data.get("subjects") or []
        if raw.get("id") and not _is_rerelease(raw.get("card_subtitle") or "", today)
    ]
    return sorted(upcoming, key=lambda movie: movie.wish_count, reverse=True)[:count]


async def fetch_trending_books(count: int = 15) -> list[DoubanItem]:
    """Fetch this month's hot books, top `count` by rating (subtitle: author / year)."""
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        items = await _fetch_collection(session, BOOK_COLLECTION)
    books = [
        _to_item(raw, _subtitle(raw.get("card_subtitle") or "", 2)) for raw in items
    ]
    return _top_by_rating(books, count)
