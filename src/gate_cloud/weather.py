"""Hourly weather at the building, from Open-Meteo.

Open-Meteo needs no API key, so there is no secret to hold or rotate. Its
forecast endpoint serves model data for recent days but returns null values for
dates older than about 2 months. The archive endpoint is complete up to 2 days
ago and is used for older dates. Requests are split at FORECAST_DAYS_BACK so
the forecast endpoint is used only for the last 7 days and the archive for
everything older.

Free tier: non-commercial use, 10 000 calls/day. GATE is UQTR research.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from gate_cloud.twin import TIMEZONE

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_DAYS_BACK = 7  # forecast endpoint's reliable coverage is the last 7 days

FORECAST_DAYS_SHOWN = 3
FORECAST_HOURS_SHOWN = 12

# Open-Meteo hourly variable -> telemetry key on the GATE Weather device.
VARIABLES = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "humidity_pct",
    "cloud_cover": "cloud_cover_pct",
    "wind_speed_10m": "wind_speed_ms",
    "shortwave_radiation": "shortwave_radiation_wm2",
}


def parse_hourly(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hourly = payload.get("hourly") or {}
    points = []
    for i, ts in enumerate(hourly.get("time", [])):
        values = {}
        for name, key in VARIABLES.items():
            column = hourly.get(name) or []
            if i < len(column) and column[i] is not None:
                values[key] = column[i]
        if values:
            points.append({"ts": int(ts) * 1000, "values": values})
    return points


def parse_forecast(payload: dict[str, Any], now_ms: int) -> dict[str, Any]:
    """Next three local days and next twelve hours from a forecast response.

    Daily `time` is local midnight (unixtime), so its date is taken in the
    building's timezone, not UTC. Missing columns yield None for that field.
    """
    zone = ZoneInfo(TIMEZONE)

    def column(block: dict, name: str, i: int):
        values = block.get(name) or []
        return values[i] if i < len(values) else None

    daily = payload.get("daily") or {}
    days = [
        {
            "date": dt.datetime.fromtimestamp(int(ts), zone).date().isoformat(),
            "tmin": column(daily, "temperature_2m_min", i),
            "tmax": column(daily, "temperature_2m_max", i),
            "code": column(daily, "weather_code", i),
        }
        for i, ts in enumerate(daily.get("time", [])[:FORECAST_DAYS_SHOWN])
    ]
    hourly = payload.get("hourly") or {}
    hours = [
        {"ts": int(ts) * 1000, "temp": column(hourly, "temperature_2m", i), "code": column(hourly, "weather_code", i)}
        for i, ts in enumerate(hourly.get("time", []))
        if int(ts) * 1000 > now_ms
    ][:FORECAST_HOURS_SHOWN]
    return {"days": days, "hours": hours}


def plan_requests(start: dt.datetime, end: dt.datetime, today: dt.date) -> list[tuple[str, dt.date, dt.date]]:
    """Endpoint and inclusive UTC day range for [start, end)."""
    first = start.astimezone(dt.timezone.utc).date()
    last = (end.astimezone(dt.timezone.utc) - dt.timedelta(microseconds=1)).date()
    boundary = today - dt.timedelta(days=FORECAST_DAYS_BACK)
    plan = []
    if first < boundary:
        plan.append(("archive", first, min(last, boundary - dt.timedelta(days=1))))
    if last >= boundary:
        plan.append(("forecast", max(first, boundary), last))
    return plan


class OpenMeteo:
    def __init__(self, forecast_url: str = FORECAST_URL, archive_url: str = ARCHIVE_URL, get: Callable = requests.get):
        self._urls = {"forecast": forecast_url, "archive": archive_url}
        self._get = get

    def hourly(self, latitude: float, longitude: float, start: dt.datetime, end: dt.datetime, today: dt.date) -> list[dict]:
        start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        points: dict[int, dict] = {}
        for endpoint, first, last in plan_requests(start, end, today):
            response = self._get(
                self._urls[endpoint],
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "hourly": ",".join(VARIABLES),
                    "wind_speed_unit": "ms",
                    "timezone": "GMT",
                    "timeformat": "unixtime",
                    "start_date": first.isoformat(),
                    "end_date": last.isoformat(),
                },
                timeout=30,
            )
            response.raise_for_status()
            for point in parse_hourly(response.json()):
                if start_ms <= point["ts"] < end_ms:
                    points[point["ts"]] = point
        return [points[ts] for ts in sorted(points)]

    def forecast(self, latitude: float, longitude: float, now: dt.datetime) -> dict:
        """The short-range outlook shown on the dashboard, one request."""
        response = self._get(
            self._urls["forecast"],
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "hourly": "temperature_2m,weather_code",
                "forecast_days": 4,
                "timezone": TIMEZONE,
                "timeformat": "unixtime",
            },
            timeout=30,
        )
        response.raise_for_status()
        return parse_forecast(response.json(), int(now.timestamp() * 1000))
