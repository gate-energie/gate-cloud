"""Open-Meteo: parsing and the forecast/archive split. No network."""
import datetime as dt
from zoneinfo import ZoneInfo

from gate_cloud.weather import OpenMeteo, parse_forecast, parse_hourly, plan_requests

UTC = dt.timezone.utc
PAYLOAD = {
    "hourly": {
        "time": [1790553600, 1790557200, 1790560800],
        "temperature_2m": [16.2, 15.1, None],
        "relative_humidity_2m": [60, 62, None],
        "cloud_cover": [100, 98, None],
        "wind_speed_10m": [5.88, 4.67, None],
        "shortwave_radiation": [0.0, 0.0, None],
    }
}


def test_parse_hourly_maps_keys_and_drops_empty_hours():
    points = parse_hourly(PAYLOAD)
    assert len(points) == 2
    assert points[0] == {"ts": 1790553600000, "values": {
        "temperature_c": 16.2, "humidity_pct": 60, "cloud_cover_pct": 100,
        "wind_speed_ms": 5.88, "shortwave_radiation_wm2": 0.0}}


def test_recent_range_uses_forecast_only():
    start = dt.datetime(2026, 9, 30, 10, tzinfo=UTC)
    assert plan_requests(start, start + dt.timedelta(hours=1), dt.date(2026, 10, 1)) == [
        ("forecast", dt.date(2026, 9, 30), dt.date(2026, 9, 30))]


def test_range_straddling_the_boundary_splits_without_overlap():
    start = dt.datetime(2026, 9, 1, tzinfo=UTC)
    end = dt.datetime(2026, 10, 1, tzinfo=UTC)
    today = dt.date(2026, 10, 1)  # forecast covers today - 7 days onwards
    assert plan_requests(start, end, today) == [
        ("archive", dt.date(2026, 9, 1), dt.date(2026, 9, 23)),
        ("forecast", dt.date(2026, 9, 24), dt.date(2026, 9, 30)),
    ]


def test_hourly_filters_to_the_requested_hours():
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params["start_date"], params["end_date"]))
        return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: PAYLOAD})()

    client = OpenMeteo("https://f", "https://a", get=fake_get)
    start = dt.datetime.fromtimestamp(1790557200, UTC)
    points = client.hourly(46.35, -72.58, start, start + dt.timedelta(hours=1), dt.date(2026, 10, 1))
    assert [p["ts"] for p in points] == [1790557200000]
    assert calls == [("https://f", "2026-09-28", "2026-09-28")]


FORECAST = {
    "daily": {"time": [1790985600, 1791072000, 1791158400, 1791244800],
              "temperature_2m_max": [15.1, 12.0, 9.5, 8.0], "temperature_2m_min": [5.0, 3.2, 1.1, 0.5],
              "weather_code": [3, 61, 0, 2]},
    "hourly": {"time": [1791000000 + 3600 * i for i in range(30)],
               "temperature_2m": [10.0 + i * 0.1 for i in range(30)], "weather_code": [3] * 30},
}


def test_parse_forecast_takes_three_days_and_next_twelve_hours():
    f = parse_forecast(FORECAST, now_ms=1791000000 * 1000 + 1)
    assert [d["code"] for d in f["days"]] == [3, 61, 0] and f["days"][0]["tmax"] == 15.1
    assert f["days"][0]["date"] == dt.datetime.fromtimestamp(1790985600, ZoneInfo("America/Toronto")).date().isoformat()
    assert len(f["hours"]) == 12 and f["hours"][0]["ts"] == (1791000000 + 3600) * 1000


def test_forecast_requests_four_days_in_toronto_time():
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return FORECAST

    def get(url, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    now = dt.datetime.fromtimestamp(1791000000, dt.timezone.utc)
    f = OpenMeteo(get=get).forecast(45.5, -73.5, now)
    (url, params, _), = calls
    assert url.endswith("/v1/forecast") and params["forecast_days"] == 4
    assert params["timezone"] == "America/Toronto" and params["timeformat"] == "unixtime"
    assert params["daily"] == "temperature_2m_max,temperature_2m_min,weather_code"
    assert params["hourly"] == "temperature_2m,weather_code"
    assert len(f["days"]) == 3
