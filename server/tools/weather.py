"""Open-Meteo client — geocode a city + current weather, with a food "nudge".

Free, no API key. Used to gently steer the cuisine suggestion by the weather
(rainy/cold -> hearty comfort curry; hot -> light chaat/raita). Returns None on
any failure so the graph can skip the pairing cleanly.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("tools.weather")

_GEO = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_client: Optional[httpx.Client] = None

# Minimal WMO weather-code -> (label, is_wet) mapping (enough for a food nudge).
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle",
    61: "rain", 63: "rain", 65: "heavy rain", 71: "snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "rain showers",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}
_WET_CODES = {51, 53, 55, 61, 63, 65, 71, 73, 75, 80, 81, 82, 95, 96, 99}


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(6.0, connect=3.0))
    return _client


def _nudge(temp_c: float, code: int) -> tuple[str, Optional[str]]:
    """Return (human nudge, optional dish keyword override) for the weather."""
    wet = code in _WET_CODES
    if wet or temp_c <= 15:
        return ("cool and cozy weather — a warm, hearty curry hits the spot", "curry")
    if temp_c >= 30:
        return ("hot out — something light and refreshing suits better", "raita")
    return ("mild weather — anything goes", None)


def get_weather(city: str) -> Optional[dict]:
    """Geocode `city` then fetch current weather. Returns a dict or None.

    Shape: {location, temp_c, condition, nudge, keyword_override}.
    """
    try:
        geo = _http().get(_GEO, params={"name": city, "count": 1})
        geo.raise_for_status()
        results = geo.json().get("results") or []
        if not results:
            logger.info("get_weather: no geocode match for %r", city)
            return None
        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        label = ", ".join(
            p for p in (place.get("name"), place.get("country")) if p
        )

        fc = _http().get(
            _FORECAST,
            params={"latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code"},
        )
        fc.raise_for_status()
        current = fc.json().get("current") or {}
        temp_c = current.get("temperature_2m")
        code = int(current.get("weather_code", -1))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("get_weather(%r) failed: %s", city, exc)
        return None

    if temp_c is None:
        return None
    nudge, override = _nudge(float(temp_c), code)
    return {
        "location": label or city,
        "temp_c": temp_c,
        "condition": _WMO.get(code, "unknown"),
        "nudge": nudge,
        "keyword_override": override,
    }
