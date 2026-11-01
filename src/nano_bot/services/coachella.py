"""Coachella festival pass sales on coachella.com/passes.

The passes page is hand-edited and its markup changes between sales, so it is
read by shape rather than exact classes: the buttons between the "Festival
Passes" h2 and the next h2, each under the latest h3/h4 pass name. Register,
Purchase and Buy buttons count as open; a page without that section raises
UnrecognizedPageError.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup, Tag

from ._retry import retry_async

PASSES_URL = "https://www.coachella.com/passes"

UNREACHABLE = "unreachable"
UNRECOGNIZED = "unrecognized"

logger = logging.getLogger(__name__)

_SECTION = "festival passes"
_OPEN = re.compile(r"(?:purchase|buy|register)\b", re.IGNORECASE)
_REGISTER = re.compile(r"register\b", re.IGNORECASE)
_WEEKEND = re.compile(r"\bW(?:eekend\s*)?([12])\b", re.IGNORECASE)
_PACIFIC_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s*[ap]m\s*P[DS]?T\b", re.IGNORECASE)
_MAX_NOTES = 3

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
    """Raised when the passes page loads without its festival pass section."""


_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
    _TransientHTTPError,
)


@dataclass(slots=True, frozen=True)
class PassButton:
    name: str
    label: str
    url: str

    @property
    def key(self) -> str:
        return f"{self.name} \u00b7 {self.label}"

    @property
    def open(self) -> bool:
        return bool(_OPEN.match(self.label))

    @property
    def registration(self) -> bool:
        return bool(_REGISTER.match(self.label))

    @property
    def weekend(self) -> str | None:
        match = _WEEKEND.search(self.label)
        return f"W{match.group(1)}" if match else None

    @property
    def action(self) -> str:
        return " ".join(_WEEKEND.sub("", self.label).split())


@dataclass(slots=True, frozen=True)
class PassesStatus:
    buttons: tuple[PassButton, ...] = ()
    notes: tuple[str, ...] = ()
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


def _text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def _section_heading(soup: BeautifulSoup) -> Tag:
    for heading in soup.find_all("h2"):
        if _text(heading).casefold() == _SECTION:
            return heading
    raise UnrecognizedPageError("No Festival Passes heading")


def _parse_buttons(soup: BeautifulSoup) -> list[PassButton]:
    name = None
    buttons = []
    for element in _section_heading(soup).find_all_next(["h2", "h3", "h4", "a"]):
        if element.name == "h2":
            break
        if element.name != "a":
            name = _text(element)
            continue
        label = _text(element)
        if name and len(label) > 1:
            url = urljoin(PASSES_URL, element.get("href", ""))
            buttons.append(PassButton(name=name, label=label, url=url))
    if name is None:
        raise UnrecognizedPageError("No passes under the Festival Passes heading")
    return buttons


def _sale_notes(soup: BeautifulSoup) -> tuple[str, ...]:
    for hidden in soup(["script", "style", "noscript", "template"]):
        hidden.decompose()
    lines = (" ".join(text.split()) for text in soup.stripped_strings)
    notes = dict.fromkeys(line for line in lines if _PACIFIC_TIME.search(line))
    return tuple(notes)[:_MAX_NOTES]


async def passes_status() -> PassesStatus:
    try:
        soup = await _fetch_page(PASSES_URL, "Coachella passes page")
        buttons = _parse_buttons(soup)
    except UnrecognizedPageError:
        logger.exception("Coachella passes page not recognized")
        return PassesStatus(problem=UNRECOGNIZED)
    except Exception:
        logger.exception("Coachella passes check failed")
        return PassesStatus(problem=UNREACHABLE)
    return PassesStatus(tuple(buttons), _sale_notes(soup))
