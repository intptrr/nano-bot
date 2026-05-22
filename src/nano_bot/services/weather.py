"""Open-Meteo weather client. No API key required."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

import aiohttp

from ._retry import retry_async

API_URL = "https://api.open-meteo.com/v1/forecast"

logger = logging.getLogger(__name__)

# Status codes worth retrying (transient upstream / rate-limit issues).
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class _TransientHTTPError(Exception):
    """Raised internally to trigger a retry on a transient HTTP status."""


_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
    _TransientHTTPError,
)

# https://open-meteo.com/en/docs#weathervariables
WMO_CODE: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Heavy rain showers",
    82: "Violent rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}

WMO_EMOJI: dict[int, str] = {
    0: "\u2600\ufe0f",        # ☀️
    1: "\U0001f324\ufe0f",    # 🌤
    2: "\u26c5",              # ⛅
    3: "\u2601\ufe0f",        # ☁️
    45: "\U0001f32b\ufe0f",   # 🌫
    48: "\U0001f32b\ufe0f",
    51: "\U0001f327\ufe0f",   # 🌧
    53: "\U0001f327\ufe0f",
    55: "\U0001f327\ufe0f",
    61: "\U0001f327\ufe0f",
    63: "\U0001f327\ufe0f",
    65: "\u26c8\ufe0f",       # ⛈
    71: "\U0001f328\ufe0f",   # 🌨
    73: "\U0001f328\ufe0f",
    75: "\U0001f328\ufe0f",
    77: "\U0001f328\ufe0f",
    80: "\U0001f326\ufe0f",   # 🌦
    81: "\U0001f327\ufe0f",
    82: "\u26c8\ufe0f",
    85: "\U0001f328\ufe0f",
    86: "\U0001f328\ufe0f",
    95: "\u26c8\ufe0f",
    96: "\u26c8\ufe0f",
    99: "\u26c8\ufe0f",
}


@dataclass(slots=True)
class DailyForecast:
    forecast_date: date
    description: str
    weather_code: int
    temp_min: float
    temp_max: float
    precipitation_mm: float
    precipitation_probability: int
    wind_speed_max: float
    location_name: str = ""

    def format(self) -> str:
        emoji = WMO_EMOJI.get(self.weather_code, "\U0001f324\ufe0f")
        header = f"{emoji} <b>Weather</b>"
        if self.location_name:
            header += f"\n{self.location_name}"
        return (
            f"{header}\n"
            f"\n"
            f"{self.description}\n"
            f"Temp: {self.temp_min:.0f}\u2013{self.temp_max:.0f} \u00b0C\n"
            f"Precip: {self.precipitation_mm:.1f} mm "
            f"({self.precipitation_probability}% chance)\n"
            f"Wind: up to {self.wind_speed_max:.0f} km/h"
        )


async def fetch_daily_forecast(
    latitude: float,
    longitude: float,
    timezone: str,
    location_name: str = "",
) -> DailyForecast:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "forecast_days": 1,
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_min",
                "temperature_2m_max",
                "precipitation_sum",
                "precipitation_probability_max",
                "wind_speed_10m_max",
            ]
        ),
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        async def _request() -> dict:
            async with session.get(API_URL, params=params) as resp:
                if resp.status in _RETRY_STATUSES:
                    raise _TransientHTTPError(f"HTTP {resp.status} from Open-Meteo")
                resp.raise_for_status()
                return await resp.json()

        data = await retry_async(
            _request,
            logger=logger,
            description="Open-Meteo forecast",
            retry_exceptions=_RETRY_EXCEPTIONS,
        )

    daily = data["daily"]
    code = int(daily["weather_code"][0])
    return DailyForecast(
        forecast_date=date.fromisoformat(daily["time"][0]),
        description=WMO_CODE.get(code, f"Weather code {code}"),
        weather_code=code,
        temp_min=float(daily["temperature_2m_min"][0]),
        temp_max=float(daily["temperature_2m_max"][0]),
        precipitation_mm=float(daily["precipitation_sum"][0]),
        precipitation_probability=int(daily["precipitation_probability_max"][0] or 0),
        wind_speed_max=float(daily["wind_speed_10m_max"][0]),
        location_name=location_name,
    )
