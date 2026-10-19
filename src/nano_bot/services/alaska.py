"""Alaska Airlines promotions: fare sales and Atmos Rewards Global Getaways.

Both are read from Alaska's public pages rather than a documented API, so the
markup may change without notice. A page that loads but matches neither the
live nor the idle layout raises UnrecognizedPageError.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

import aiohttp
from bs4 import BeautifulSoup

from ._retry import retry_async

FARE_SALE_URL = "https://www.alaskaair.com/content/deals/flights/sale"
GLOBAL_GETAWAYS_URL = "https://www.alaskaair.com/content/global-getaways"

IDLE = "idle"
UNRECOGNIZED = "unrecognized"

logger = logging.getLogger(__name__)

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    ),
}

_DATE = r"[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}"
_SALE_ENDS = re.compile(rf"Sale ends ({_DATE})")
_SALE_OVER = re.compile(r"sale you.re looking for has ended", re.IGNORECASE)
_BOOK_BY = re.compile(rf"Book by ({_DATE})")
_TRAVEL = re.compile(
    rf"travel between ({_DATE}),? (?:to|and|through|-|\u2013) ({_DATE})"
)
_POINTS = re.compile(r"(?:starting at |from )?([\d,]+) points", re.IGNORECASE)
_GETAWAYS_OVER = re.compile(
    r"More Global Getaways await|More deals are landing soon", re.IGNORECASE
)
_POINTS_LABELS = frozenset({"starting at", "from"})


class _TransientHTTPError(Exception):
    """Raised internally to trigger a retry on a transient HTTP status."""


class UnexpectedPageError(Exception):
    """Raised when a response is not the expected Alaska page."""


class UnrecognizedPageError(Exception):
    """Raised when a page loads but matches neither the live nor the idle layout."""


_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
    _TransientHTTPError,
)


def _short_date(day: date) -> str:
    return day.strftime("%b %-d")


def _parse_date(text: str) -> date:
    month, rest = text.replace(".", "").split(" ", 1)
    return datetime.strptime(f"{month[:3]} {rest}", "%b %d, %Y").date()


def _heading(emoji: str, title: str) -> str:
    return f"{emoji} <b>{title}</b>"


def _link(url: str) -> str:
    escaped = html.escape(url)
    return f'<a href="{escaped}">{escaped}</a>'


_FARE_SALE_HEADING = _heading("\u2708\ufe0f", "Alaska fare sale")
_GLOBAL_GETAWAYS_HEADING = _heading("\U0001f30f", "Global Getaways")


@dataclass(slots=True, frozen=True)
class FareSale:
    summary: str
    ends: date

    @property
    def key(self) -> str:
        return f"{self.ends.isoformat()} {self.summary}"

    def format(self) -> str:
        lines = [_FARE_SALE_HEADING]
        if self.summary:
            lines.append(html.escape(self.summary))
        lines.append(f"Ends {_short_date(self.ends)}")
        lines.append(_link(FARE_SALE_URL))
        return "\n".join(lines)


@dataclass(slots=True, frozen=True)
class Destination:
    name: str
    points: int


@dataclass(slots=True, frozen=True)
class GlobalGetaways:
    destinations: tuple[Destination, ...]
    book_by: date | None
    travel: tuple[date, date] | None

    @property
    def key(self) -> str:
        if self.book_by:
            return self.book_by.isoformat()
        return ",".join(destination.name for destination in self.destinations)

    def format(self) -> str:
        details = []
        if self.book_by:
            details.append(f"Book by {_short_date(self.book_by)}")
        if self.travel:
            start, end = self.travel
            details.append(f"travel {_short_date(start)} \u2013 {_short_date(end)}")
        lines = [_GLOBAL_GETAWAYS_HEADING]
        if details:
            summary = " \u00b7 ".join(details)
            lines.append(summary[:1].upper() + summary[1:])
        lines.extend(
            f"\u2022 {html.escape(destination.name)} \u2014 "
            f"from {destination.points:,} points"
            for destination in self.destinations
        )
        lines.append(_link(GLOBAL_GETAWAYS_URL))
        return "\n".join(lines)


async def _fetch_text(url: str, description: str) -> str:
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:

        async def _request() -> str:
            async with session.get(url) as resp:
                if resp.status in _RETRY_STATUSES:
                    raise _TransientHTTPError(f"HTTP {resp.status} from {description}")
                resp.raise_for_status()
                return await resp.text()

        return await retry_async(
            _request,
            logger=logger,
            description=description,
            retry_exceptions=_RETRY_EXCEPTIONS,
        )


def _visible_text(page: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(page, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for hidden in soup(["script", "style", "noscript", "template"]):
        hidden.decompose()
    return title, [" ".join(text.split()) for text in soup.stripped_strings]


def _parse_fare_sale(title: str, strings: list[str]) -> FareSale | None:
    if "Alaska" not in title:
        raise UnexpectedPageError(f"Unexpected fare sale page title {title!r}")
    for text in strings:
        ends = _SALE_ENDS.search(text)
        if ends:
            return FareSale(
                summary=text[: ends.start()].strip(),
                ends=_parse_date(ends.group(1)),
            )
    if any(_SALE_OVER.search(text) for text in strings):
        return None
    raise UnrecognizedPageError("Fare sale page shows neither a live nor an ended sale")


def _parse_destinations(strings: list[str]) -> tuple[Destination, ...]:
    destinations: dict[str, Destination] = {}
    for index, text in enumerate(strings):
        points = _POINTS.fullmatch(text)
        if not points or index == 0:
            continue
        name_index = index - 1
        if strings[name_index].lower() in _POINTS_LABELS and name_index > 0:
            name_index -= 1
        name = strings[name_index].split(",")[0].strip()
        destinations.setdefault(
            name, Destination(name=name, points=int(points.group(1).replace(",", "")))
        )
    return tuple(destinations.values())


def _parse_travel_window(text: str) -> tuple[date, date] | None:
    windows = {match.groups() for match in _TRAVEL.finditer(text)}
    if len(windows) != 1:
        return None
    start, end = windows.pop()
    return _parse_date(start), _parse_date(end)


def _parse_global_getaways(title: str, strings: list[str]) -> GlobalGetaways | None:
    if "Global Getaways" not in title:
        raise UnexpectedPageError(f"Unexpected Global Getaways page title {title!r}")
    text = " ".join(strings)
    destinations = _parse_destinations(strings)
    book_by = _BOOK_BY.search(text)
    if destinations or book_by:
        return GlobalGetaways(
            destinations=destinations,
            book_by=_parse_date(book_by.group(1)) if book_by else None,
            travel=_parse_travel_window(text),
        )
    if _GETAWAYS_OVER.search(text):
        return None
    raise UnrecognizedPageError(
        "Global Getaways page shows neither live deals nor the idle message"
    )


async def fetch_fare_sale() -> FareSale | None:
    page = await _fetch_text(FARE_SALE_URL, "Alaska fare sale page")
    return _parse_fare_sale(*_visible_text(page))


async def fetch_global_getaways() -> GlobalGetaways | None:
    page = await _fetch_text(GLOBAL_GETAWAYS_URL, "Alaska Global Getaways page")
    return _parse_global_getaways(*_visible_text(page))


@dataclass(slots=True, frozen=True)
class PromotionSource:
    name: str
    heading: str
    url: str
    fetch: Callable[[], Awaitable[FareSale | GlobalGetaways | None]]
    idle: str
    unrecognized: str

    def notice(self, text: str) -> str:
        return "\n".join([self.heading, text, _link(self.url)])


@dataclass(slots=True, frozen=True)
class PromotionStatus:
    state: str | None
    message: str


FARE_SALES = PromotionSource(
    name="Alaska fare sale",
    heading=_FARE_SALE_HEADING,
    url=FARE_SALE_URL,
    fetch=fetch_fare_sale,
    idle="No sale running right now.",
    unrecognized=(
        "The sale page changed and couldn't be read. Check it for new sales."
    ),
)

GLOBAL_GETAWAYS = PromotionSource(
    name="Global Getaways",
    heading=_GLOBAL_GETAWAYS_HEADING,
    url=GLOBAL_GETAWAYS_URL,
    fetch=fetch_global_getaways,
    idle=(
        "No deals right now. New ones usually drop on Atmos Members Day, "
        "the first Wednesday of each month."
    ),
    unrecognized="The page changed and couldn't be read. Check it for new deals.",
)

PROMOTION_SOURCES = (FARE_SALES, GLOBAL_GETAWAYS)


async def promotion_status(source: PromotionSource) -> PromotionStatus:
    try:
        promotion = await source.fetch()
    except UnrecognizedPageError:
        logger.exception("%s page not recognized", source.name)
        return PromotionStatus(UNRECOGNIZED, source.notice(source.unrecognized))
    except Exception:
        logger.exception("%s check failed", source.name)
        return PromotionStatus(None, source.notice("Couldn't check right now."))
    if promotion is None:
        return PromotionStatus(IDLE, source.notice(source.idle))
    return PromotionStatus(promotion.key, promotion.format())
