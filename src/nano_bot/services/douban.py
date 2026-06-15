"""Douban (豆瓣) trending lists via the mobile rexxar JSON API. No API key.

This uses the unofficial mobile endpoint (`m.douban.com/rexxar/...`), which
requires a `Referer: https://m.douban.com/` header. It is reverse-engineered and
may change without notice; content is zh-CN. Each command issues only a couple of
requests, so rate-limit exposure is low.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

from ._retry import retry_async

REXXAR_URL = "https://m.douban.com/rexxar/api/v2/subject_collection/{name}/items"

# Subject collections to surface.
MOVIE_COLLECTION = "movie_showing"  # 影院热映 (now showing)
BOOK_COLLECTION = "book_hot_monthly"  # 每月热门图书榜 (monthly hot books)

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


def _subtitle(card_subtitle: str, keep: int) -> str:
    """Keep the first `keep` slash-separated parts of a card subtitle.

    Movie subtitles read `year / country / genre / director / cast` and book
    subtitles read `author / year / publisher`; trimming drops the noisier tail.
    """
    parts = [p.strip() for p in (card_subtitle or "").split("/") if p.strip()]
    return " / ".join(parts[:keep])


async def _fetch_collection(
    session: aiohttp.ClientSession, name: str, count: int, subtitle_parts: int
) -> list[DoubanItem]:
    """Fetch a Douban subject collection and map it to DoubanItems."""

    async def _request() -> dict:
        url = REXXAR_URL.format(name=name)
        async with session.get(url, params={"count": count}) as resp:
            if resp.status in _RETRY_STATUSES:
                raise _TransientHTTPError(f"HTTP {resp.status} from Douban {name}")
            resp.raise_for_status()
            return await resp.json()

    data = await retry_async(
        _request,
        logger=logger,
        description=f"Douban {name}",
        retry_exceptions=_RETRY_EXCEPTIONS,
    )

    items: list[DoubanItem] = []
    for raw in data.get("subject_collection_items") or []:
        subject_id = raw.get("id")
        kind = raw.get("type")
        if not subject_id or kind not in ("movie", "book"):
            continue
        rating = (raw.get("rating") or {}).get("value")
        items.append(
            DoubanItem(
                title=raw.get("title") or "",
                rating=float(rating) if rating else None,
                rating_count=int((raw.get("rating") or {}).get("count") or 0),
                subject_url=f"https://{kind}.douban.com/subject/{subject_id}/",
                subtitle=_subtitle(raw.get("card_subtitle") or "", subtitle_parts),
            )
        )
    return items


def _top_by_rating(items: list[DoubanItem], count: int) -> list[DoubanItem]:
    """Return the highest-rated items, unrated ones last."""
    ranked = sorted(
        items, key=lambda i: i.rating if i.rating is not None else -1.0, reverse=True
    )
    return ranked[:count]


async def fetch_trending_movies(count: int = 10) -> list[DoubanItem]:
    """Fetch in-theaters movies, top `count` by rating (subtitle: year / country)."""
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        items = await _fetch_collection(session, MOVIE_COLLECTION, _POOL_SIZE, 2)
    return _top_by_rating(items, count)


async def fetch_trending_books(count: int = 15) -> list[DoubanItem]:
    """Fetch this month's hot books, top `count` by rating (subtitle: author / year)."""
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:
        items = await _fetch_collection(session, BOOK_COLLECTION, _POOL_SIZE, 2)
    return _top_by_rating(items, count)
