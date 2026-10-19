"""Adult tennis registration at Amy Yee Tennis Center and Tennis Center Sand Point.

Both are read from public pages rather than an API, so the markup may change
without notice; a page that loads without the expected schedule raises
UnrecognizedPageError. Seattle Parks lists Amy Yee's registration dates, and
ActiveNet has opened online registration at noon on each of them. Sand Point
posts one table per session with dates and times by membership type but no
year, so each date is placed in the year nearest to today.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup, Tag

from ._retry import retry_async

PACIFIC = ZoneInfo("America/Los_Angeles")

UNREACHABLE = "unreachable"
UNRECOGNIZED = "unrecognized"

logger = logging.getLogger(__name__)

_AMY_YEE_OPENS_AT = time(12, 0)
_SAND_POINT_MEMBERSHIP = "Standard Annual"
_SAND_POINT_PROGRAMS = {
    "Adult Workouts & Ball Machine Clinics": "workouts & ball machine",
    "Adult Tennis Clinics": "tennis clinics",
}
_NOT_SCHEDULED = frozenset({"", "n/a", "tbd"})

_AMY_YEE_DATE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*\((.+)\)")
_SAND_POINT_DATE = re.compile(
    r"([A-Z][a-z]+ \d{1,2})\s*\((\d{1,2}:\d{2}\s*[AP]M)\)", re.IGNORECASE
)

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    ),
}


class _TransientHTTPError(Exception):
    """Raised internally to trigger a retry on a transient HTTP status."""


class UnrecognizedPageError(Exception):
    """Raised when a page loads without the expected registration schedule."""


_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
    _TransientHTTPError,
)


@dataclass(slots=True, frozen=True)
class Opening:
    center: str
    term: str
    opens_at: datetime
    url: str


@dataclass(slots=True, frozen=True)
class Center:
    name: str
    page_url: str
    parse: Callable[[BeautifulSoup, date], list[tuple[str, datetime]]]


@dataclass(slots=True, frozen=True)
class CenterStatus:
    center: Center
    openings: tuple[Opening, ...] = ()
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


def _parse_datetime(text: str, layout: str) -> datetime:
    try:
        return datetime.strptime(text, layout)
    except ValueError as exc:
        raise UnrecognizedPageError(f"Unreadable date {text!r}") from exc


def _nearest_date(month_day: str, today: date) -> date:
    days = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            days.append(datetime.strptime(f"{month_day} {year}", "%B %d %Y").date())
        except ValueError:
            continue
    if not days:
        raise UnrecognizedPageError(f"Unreadable date {month_day!r}")
    return min(days, key=lambda day: abs(day - today))


def _schedule_heading(soup: BeautifulSoup, text: str) -> Tag:
    heading = soup.find(
        lambda tag: tag.name in ("h2", "h3", "h4") and text in tag.get_text()
    )
    if heading is None:
        raise UnrecognizedPageError(f"No {text!r} heading")
    return heading


def _parse_amy_yee(soup: BeautifulSoup, today: date) -> list[tuple[str, datetime]]:
    heading = _schedule_heading(soup, "Upcoming Registration Dates")
    dates = heading.find_next("ul")
    lines = (
        [item.get_text(" ", strip=True) for item in dates.find_all("li")]
        if dates
        else []
    )
    if not lines:
        raise UnrecognizedPageError("No dates under the registration heading")
    openings = []
    for line in lines:
        match = _AMY_YEE_DATE.fullmatch(line)
        if not match:
            raise UnrecognizedPageError(f"Unreadable registration date {line!r}")
        day = _parse_datetime(match.group(1), "%B %d, %Y").date()
        openings.append(
            (match.group(2).strip(), datetime.combine(day, _AMY_YEE_OPENS_AT, PACIFIC))
        )
    return openings


def _sand_point_opening(cell: str, today: date) -> datetime:
    match = _SAND_POINT_DATE.fullmatch(cell)
    if not match:
        raise UnrecognizedPageError(f"Unreadable registration time {cell!r}")
    month_day, clock = match.groups()
    opens = _parse_datetime(clock.upper().replace(" ", ""), "%I:%M%p").time()
    return datetime.combine(_nearest_date(month_day, today), opens, PACIFIC)


def _column(header: list[str], program: str, title: str) -> int:
    for index, cell in enumerate(header):
        if program.lower() in cell.lower():
            return index
    raise UnrecognizedPageError(f"No {program!r} column in {title!r}")


def _parse_sand_point(soup: BeautifulSoup, today: date) -> list[tuple[str, datetime]]:
    heading = _schedule_heading(soup, "Registration Schedule")
    title = heading.get_text(" ", strip=True)
    session = title.replace("Registration Schedule", "").strip()
    table = heading.find_next("table")
    rows = [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        for row in (table.find_all("tr") if table else [])
    ]
    if not rows:
        raise UnrecognizedPageError(f"No table under {title!r}")
    header, *body = rows
    membership = _SAND_POINT_MEMBERSHIP.lower()
    row = next(
        (cells for cells in body if cells and cells[0].lower() == membership), None
    )
    if row is None:
        raise UnrecognizedPageError(f"No {_SAND_POINT_MEMBERSHIP!r} row in {title!r}")
    programs: dict[datetime, list[str]] = {}
    for program, label in _SAND_POINT_PROGRAMS.items():
        column = _column(header, program, title)
        cell = row[column] if column < len(row) else ""
        if cell.lower() in _NOT_SCHEDULED:
            continue
        programs.setdefault(_sand_point_opening(cell, today), []).append(label)
    return [
        (" \u00b7 ".join(filter(None, [session, ", ".join(labels)])), opens_at)
        for opens_at, labels in programs.items()
    ]


AMY_YEE = Center(
    name="Amy Yee Tennis Center",
    page_url="https://www.seattle.gov/parks/sports/tennis#adult",
    parse=_parse_amy_yee,
)

SAND_POINT = Center(
    name="Tennis Center Sand Point",
    page_url="https://tenniscentersandpoint.com/classes-programs/adults/",
    parse=_parse_sand_point,
)

CENTERS = (AMY_YEE, SAND_POINT)


async def center_status(center: Center, today: date) -> CenterStatus:
    try:
        page = await _fetch_page(center.page_url, f"{center.name} page")
        found = center.parse(page, today)
    except UnrecognizedPageError:
        logger.exception("%s page not recognized", center.name)
        return CenterStatus(center, problem=UNRECOGNIZED)
    except Exception:
        logger.exception("%s check failed", center.name)
        return CenterStatus(center, problem=UNREACHABLE)
    openings = sorted(
        (
            Opening(center.name, term, opens_at, center.page_url)
            for term, opens_at in found
        ),
        key=lambda opening: opening.opens_at,
    )
    return CenterStatus(center, tuple(openings))


async def check_centers(today: date) -> list[CenterStatus]:
    return list(
        await asyncio.gather(*(center_status(center, today) for center in CENTERS))
    )
