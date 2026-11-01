"""Beyond Wonderland at The Gorge passes on Insomniac's Front Gate storefront.

The storefront is read as a public page rather than an API, so the markup may
change without notice. Each listed pass carries its on-sale date and time in
Pacific time as data attributes; a page with neither passes nor the "no items
on sale" notice raises UnrecognizedPageError. A pass's own page lists its price
tiers, fees included.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup, Tag

from ._retry import retry_async

STORE_URL = "https://beyondpnw.frontgatetickets.com/"
SITE_URL = "https://pnw.beyondwonderland.com/"

PACIFIC = ZoneInfo("America/Los_Angeles")

UNREACHABLE = "unreachable"
UNRECOGNIZED = "unrecognized"

logger = logging.getLogger(__name__)

_FESTIVAL_IN_NAME = " Beyond Wonderland at the Gorge - "

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    ),
}


class _TransientHTTPError(Exception):
    """Raised internally to trigger a retry on a transient HTTP status."""


class UnrecognizedPageError(Exception):
    """Raised when the storefront loads without passes or the no-sale notice."""


_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
    _TransientHTTPError,
)


@dataclass(slots=True, frozen=True)
class Pass:
    name: str
    url: str
    on_sale: datetime

    @property
    def key(self) -> str:
        return f"{self.name} {self.on_sale.isoformat()}"

    @property
    def label(self) -> str:
        return self.name.replace(_FESTIVAL_IN_NAME, " \u00b7 ", 1)


@dataclass(slots=True, frozen=True)
class StoreStatus:
    passes: tuple[Pass, ...] = ()
    problem: str | None = None


async def _fetch_page(url: str, description: str) -> BeautifulSoup:
    async with aiohttp.ClientSession(
        timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as session:

        async def _request() -> str:
            async with session.get(url) as resp:
                if resp.status in _RETRY_STATUSES:
                    raise _TransientHTTPError(f"HTTP {resp.status} from {description}")
                resp.raise_for_status()
                return await resp.text()

        page = await retry_async(
            _request,
            logger=logger,
            description=description,
            retry_exceptions=_RETRY_EXCEPTIONS,
        )
    return BeautifulSoup(page, "html.parser")


def _parse_pass(card: Tag) -> Pass:
    link = card.select_one("h2 a[href]")
    name = " ".join(link.get_text(" ", strip=True).split()) if link else ""
    if not name:
        raise UnrecognizedPageError("Listed pass without a name")
    try:
        on_sale = datetime.combine(
            date.fromisoformat(card.get("data-eventonsalestartdate", "")),
            time.fromisoformat(card.get("data-eventonsalestarttime", "")),
            PACIFIC,
        )
    except ValueError as exc:
        raise UnrecognizedPageError(f"Unreadable on-sale time for {name!r}") from exc
    return Pass(name=name, url=link["href"], on_sale=on_sale)


def _parse_store(soup: BeautifulSoup) -> list[Pass]:
    cards = soup.select("div.label-event")
    if cards:
        return [_parse_pass(card) for card in cards]
    if soup.select_one("div.noSale"):
        return []
    raise UnrecognizedPageError("Store shows neither passes nor the no-sale notice")


async def fetch_passes() -> list[Pass]:
    return _parse_store(await _fetch_page(STORE_URL, "Beyond Wonderland store"))


async def lowest_price(item: Pass) -> float | None:
    try:
        page = await _fetch_page(item.url, f"{item.label} page")
        prices = [
            float(section["data-price"])
            for section in page.select("div.ticket-price-section[data-price]")
        ]
    except Exception:
        logger.exception("%s price check failed", item.label)
        return None
    return min(prices, default=None)


async def store_status() -> StoreStatus:
    try:
        passes = await fetch_passes()
    except UnrecognizedPageError:
        logger.exception("Beyond Wonderland store not recognized")
        return StoreStatus(problem=UNRECOGNIZED)
    except Exception:
        logger.exception("Beyond Wonderland store check failed")
        return StoreStatus(problem=UNREACHABLE)
    return StoreStatus(tuple(passes))
