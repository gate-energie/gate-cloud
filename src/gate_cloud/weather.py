"""Hourly weather at the building, from Open-Meteo.

Open-Meteo needs no API key, so there is no secret to hold or rotate. Its
forecast endpoint serves roughly the last 92 days; anything older comes from the
archive endpoint, which lags a few days. Requests are split at
FORECAST_DAYS_BACK so a backfill never straddles the two.

Free tier: non-commercial use, 10 000 calls/day. GATE is UQTR research.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_DAYS_BACK = 60  # well inside the forecast endpoint's ~92-day window

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
