"""Alaska Airlines promotions: fare sales and Atmos Rewards Global Getaways.

Both are read from Alaska's public pages rather than a documented API, so the
markup may change without notice. The pages are Next.js apps that render most
of their content from the __NEXT_DATA__ JSON, so its strings are read along
with the visible HTML, whose fallback idle message can show while a promotion
is live. A page that mentions a promotion it can't parse, or matches neither
the live nor the idle layout, raises UnrecognizedPageError.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
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
_SALE_HINT = re.compile(r"Sale ends|Book by", re.IGNORECASE)
_BOOK_BY = re.compile(rf"Book by ({_DATE})")
_TRAVEL = re.compile(
    rf"travel (?:between )?({_DATE}),?\s*(?:to|and|through|-|\u2013)\s*({_DATE})"
)
_POINTS = re.compile(r"(?:starting at |from )?([\d,]+) points", re.IGNORECASE)
_CARD_POINTS = re.compile(r"\bfrom ([\d,]+) points", re.IGNORECASE)
_AIRPORT_CODE = re.compile(r"\s*\([A-Z]{3}\)\s*$")
_GETAWAYS_OVER = re.compile(
    r"More Global Getaways await|More deals are landing soon", re.IGNORECASE
)
_GETAWAYS_HINT = re.compile(r"Book by|from [\d,]+ points", re.IGNORECASE)
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


@dataclass(slots=True, frozen=True)
class _Page:
    title: str
    visible: list[str]
    data: object

    @property
    def strings(self) -> list[str]:
        return [*self.visible, *_json_strings(self.data)]


def _json_strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        text = BeautifulSoup(node, "html.parser").get_text(" ") if "<" in node else node
        text = " ".join(text.split())
        if text:
            yield text
    elif isinstance(node, dict):
        for value in node.values():
            yield from _json_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _json_strings(value)


def _next_data(soup: BeautifulSoup) -> object:
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None:
        return None
    try:
        return json.loads(script.string or "")
    except ValueError as exc:
        raise UnrecognizedPageError("Unreadable __NEXT_DATA__") from exc


def _read_page(page: str) -> _Page:
    soup = BeautifulSoup(page, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    data = _next_data(soup)
    for hidden in soup(["script", "style", "noscript", "template"]):
        hidden.decompose()
    return _Page(
        title, [" ".join(text.split()) for text in soup.stripped_strings], data
    )


def _parse_fare_sale(page: _Page) -> FareSale | None:
    if "Alaska" not in page.title:
        raise UnexpectedPageError(f"Unexpected fare sale page title {page.title!r}")
    strings = page.strings
    for text in strings:
        ends = _SALE_ENDS.search(text)
        if ends:
            return FareSale(
                summary=text[: ends.start()].strip(),
                ends=_parse_date(ends.group(1)),
            )
    text = " ".join(strings)
    if _SALE_HINT.search(text):
        raise UnrecognizedPageError("Fare sale page mentions a sale it can't read")
    if _SALE_OVER.search(text):
        return None
    raise UnrecognizedPageError("Fare sale page shows neither a live nor an ended sale")


def _listed_destinations(strings: list[str]) -> Iterator[Destination]:
    for index, text in enumerate(strings):
        points = _POINTS.fullmatch(text)
        if not points or index == 0:
            continue
        name_index = index - 1
        if strings[name_index].lower() in _POINTS_LABELS and name_index > 0:
            name_index -= 1
        name = strings[name_index].split(",")[0].strip()
        yield Destination(name=name, points=int(points.group(1).replace(",", "")))


def _headline(node: object) -> str:
    if isinstance(node, dict):
        node = node.get("large") or node.get("small")
    return node if isinstance(node, str) else ""


def _card_destinations(node: object) -> Iterator[Destination]:
    if isinstance(node, list):
        for value in node:
            yield from _card_destinations(value)
    elif isinstance(node, dict):
        points = _CARD_POINTS.search(" ".join(_json_strings(node.get("description"))))
        name = _AIRPORT_CODE.sub("", _headline(node.get("headline")))
        if points and name:
            yield Destination(
                name=name.split(",")[0].strip(),
                points=int(points.group(1).replace(",", "")),
            )
        for value in node.values():
            yield from _card_destinations(value)


def _unique(destinations: Iterable[Destination]) -> tuple[Destination, ...]:
    found: dict[str, Destination] = {}
    for destination in destinations:
        found.setdefault(destination.name, destination)
    return tuple(found.values())


def _parse_travel_window(text: str) -> tuple[date, date] | None:
    windows = {match.groups() for match in _TRAVEL.finditer(text)}
    if len(windows) != 1:
        return None
    start, end = windows.pop()
    return _parse_date(start), _parse_date(end)


def _parse_global_getaways(page: _Page) -> GlobalGetaways | None:
    if "Global Getaways" not in page.title:
        raise UnexpectedPageError(
            f"Unexpected Global Getaways page title {page.title!r}"
        )
    text = " ".join(page.strings)
    destinations = _unique(_card_destinations(page.data)) or _unique(
        _listed_destinations(page.visible)
    )
    book_by = _BOOK_BY.search(text)
    if destinations or book_by:
        return GlobalGetaways(
            destinations=destinations,
            book_by=_parse_date(book_by.group(1)) if book_by else None,
            travel=_parse_travel_window(text),
        )
    if _GETAWAYS_HINT.search(text):
        raise UnrecognizedPageError("Global Getaways page mentions deals it can't read")
    if _GETAWAYS_OVER.search(text):
        return None
    raise UnrecognizedPageError(
        "Global Getaways page shows neither live deals nor the idle message"
    )


async def fetch_fare_sale() -> FareSale | None:
    page = await _fetch_text(FARE_SALE_URL, "Alaska fare sale page")
    return _parse_fare_sale(_read_page(page))


async def fetch_global_getaways() -> GlobalGetaways | None:
    page = await _fetch_text(GLOBAL_GETAWAYS_URL, "Alaska Global Getaways page")
    return _parse_global_getaways(_read_page(page))


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
