"""Rotating 5% cash back categories for Chase Freedom Flex and Discover it.

Both sources are the issuers' own public calendars rather than documented APIs,
so either may change without notice: Chase's is the calendar section of the
Freedom Flex product page, Discover's is the JSON feed behind its cashback
calendar page.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup, Tag

from ._retry import retry_async

CHASE_CALENDAR_URL = "https://creditcards.chase.com/cash-back-credit-cards/freedom/flex"
DISCOVER_CALENDAR_URL = (
    "https://card.discover.com/cardissuer/public/rewards/offer/v1/offer-categories"
)

logger = logging.getLogger(__name__)

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    ),
}

_CHASE_QUARTERS = {"JAN-MAR": 1, "APR-JUN": 2, "JUL-SEP": 3, "OCT-DEC": 4}

_TAG = re.compile(r"<[^>]+>")


class _TransientHTTPError(Exception):
    """Raised internally to trigger a retry on a transient HTTP status."""


class CalendarFormatError(Exception):
    """Raised when a calendar source has no recognizable quarters."""


_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
    _TransientHTTPError,
)


def _short_date(day: date) -> str:
    return day.strftime("%b %-d")


@dataclass(slots=True, frozen=True)
class Quarter:
    year: int
    number: int

    @classmethod
    def containing(cls, day: date) -> Quarter:
        return cls(day.year, (day.month - 1) // 3 + 1)

    @property
    def start(self) -> date:
        return date(self.year, 3 * self.number - 2, 1)

    @property
    def end(self) -> date:
        return self.next().start - timedelta(days=1)

    @property
    def label(self) -> str:
        return f"Q{self.number} {self.year}"

    @property
    def span(self) -> str:
        return f"{_short_date(self.start)} \u2013 {_short_date(self.end)}"

    def next(self) -> Quarter:
        if self.number == 4:
            return Quarter(self.year + 1, 1)
        return Quarter(self.year, self.number + 1)


@dataclass(slots=True, frozen=True)
class Card:
    name: str
    activation_url: str
    retroactive: bool

    def link(self, text: str) -> str:
        return f'<a href="{html.escape(self.activation_url)}">{text}</a>'


CHASE = Card(
    name="Chase Freedom Flex",
    activation_url="https://www.chasebonus.com/",
    retroactive=True,
)

DISCOVER = Card(
    name="Discover it",
    activation_url=(
        "https://www.discover.com/credit-cards/cash-back/cashback-calendar.html"
        "?link=%2Fweb%2Frewards%2F5percent%2F"
    ),
    retroactive=False,
)


@dataclass(slots=True, frozen=True)
class CardBonus:
    card: Card
    categories: tuple[str, ...]
    activation_opens: date
    activation_closes: date

    def format(self) -> str:
        window = (
            f"{_short_date(self.activation_opens)} \u2013 "
            f"{_short_date(self.activation_closes)}"
        )
        if not self.card.retroactive:
            window += " \u00b7 not retroactive"
        return "\n".join(
            [
                f"<b>{self.card.name}</b>",
                *(f"\u2022 {html.escape(category)}" for category in self.categories),
                f"{self.card.link('Activate')} {window}",
            ]
        )


type Calendar = dict[Quarter, CardBonus]


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


def _require_quarters(calendar: Calendar, description: str) -> Calendar:
    if not calendar:
        raise CalendarFormatError(f"No quarters found in {description}")
    return calendar


def _chase_quarter(heading: Tag) -> Quarter | None:
    number = _CHASE_QUARTERS.get(heading.get_text(strip=True).upper())
    year = heading.find_next_sibling("p")
    if number is None or year is None or not year.get_text(strip=True).isdigit():
        return None
    return Quarter(int(year.get_text(strip=True)), number)


def _chase_category(item: Tag) -> str | None:
    if item.find(class_="activate-state"):
        return None
    for noise in item.select("sup, .od-links--no-color, .visually-hidden"):
        noise.decompose()
    for line_break in item.find_all("br"):
        line_break.replace_with(" ")
    text = " ".join(item.get_text().split())
    if not text or text.lower() == "coming soon":
        return None
    return text


def _parse_chase_calendar(page: str) -> Calendar:
    calendar: Calendar = {}
    soup = BeautifulSoup(page, "html.parser")
    for heading in soup.select(".cmp-calendar-wrapper h2"):
        quarter = _chase_quarter(heading)
        items = heading.find_next_sibling("ul")
        if quarter is None or items is None:
            continue
        categories = tuple(filter(None, map(_chase_category, items.find_all("li"))))
        if categories:
            calendar[quarter] = CardBonus(
                card=CHASE,
                categories=categories,
                activation_opens=(quarter.start - timedelta(days=1)).replace(day=15),
                activation_closes=quarter.end.replace(day=14),
            )
    return calendar


def _parse_long_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%B %d, %Y").date()


def _parse_discover_calendar(data: dict) -> Calendar:
    calendar: Calendar = {}
    for offer in data.get("quarters") or []:
        title = " ".join(html.unescape(_TAG.sub("", offer.get("title") or "")).split())
        starts = offer.get("quarterLabelStartDate")
        opens = offer.get("preEnrollmentStartDate")
        if not title or not starts or not opens:
            continue
        quarter = Quarter.containing(_parse_long_date(starts))
        calendar[quarter] = CardBonus(
            card=DISCOVER,
            categories=(title,),
            activation_opens=_parse_long_date(opens),
            activation_closes=quarter.end,
        )
    return calendar


async def fetch_chase_calendar() -> Calendar:
    description = "Chase Freedom Flex calendar"
    page = await _fetch_text(CHASE_CALENDAR_URL, description)
    return _require_quarters(_parse_chase_calendar(page), description)


async def fetch_discover_calendar() -> Calendar:
    description = "Discover cashback calendar"
    body = await _fetch_text(DISCOVER_CALENDAR_URL, description)
    return _require_quarters(_parse_discover_calendar(json.loads(body)), description)
