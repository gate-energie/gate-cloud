"""Read ThingsFlow history into twin inputs, and twin outputs back to assets.

The only module that knows which keys hold what. twin.py stays pure.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from gate_cloud.asset_model import Plan
from gate_cloud.tariff import RateD
from gate_cloud.twin import CircuitInput, CircuitMetrics, Series, Window, allocate, circuit_metrics, local_day

MAIN = "main_total"  # used only when the plan's Panel has no circuit_key
MINUTE_MS = 60_000  # metric buckets: "on" means a minute averaging over 10 W
QUARTER_MS = 900_000  # correlation buckets, paired with hourly weather
HOUR_MS = 3_600_000


@dataclass(frozen=True)
class Circuit:
    name: str
    key: str
    asset_id: str
    rated_power_w: float | None


@dataclass
class DailyResult:
    found: list[Circuit] = field(default_factory=list)
    missing_assets: list[str] = field(default_factory=list)
    circuits: dict[str, CircuitMetrics] = field(default_factory=dict)
    building: dict[str, Any] = field(default_factory=dict)
    no_data: list[str] = field(default_factory=list)
    main: CircuitMetrics | None = None  # the building aggregate over the same window


def circuits(session, plan: Plan) -> tuple[list[Circuit], list[str]]:
    """The plan's circuits that have a ThingsFlow asset, and the names of those
    that do not yet (the asset-model sync has not run since they appeared)."""
    ids = {name: asset.id.id for name, asset in session.assets().items()}
    found: list[Circuit] = []
    missing: list[str] = []
    for spec in plan.assets:
        if spec.parent is None or spec.profile in ("Building", "Electrical Panel"):
            continue
        if spec.name not in ids:
            missing.append(spec.name)
            continue
        found.append(Circuit(spec.name, spec.attributes["circuit_key"], ids[spec.name],
                             spec.attributes.get("rated_power_w")))
    return found, missing


def main_key(plan: Plan) -> str:
    """The building aggregate's key prefix: the Panel's circuit_key, set by
    build_plan from the circuit_map main_aggregate row."""
    for spec in plan.assets:
        if spec.profile == "Electrical Panel" and spec.attributes.get("circuit_key"):
            return spec.attributes["circuit_key"]
    return MAIN


def read_power(session, device, key: str, window: Window, bucket_ms: int) -> Series:
    """Bucket averages of `<key>_active_power`, epoch-aligned."""
    power = session.timeseries(device, [f"{key}_active_power"], window.start_ms, window.end_ms, bucket_ms, "AVG")
    return power.get(f"{key}_active_power") or []


def read_input(session, device, key: str, window: Window) -> CircuitInput:
    # flow-core buckets on the epoch, not on start: a window-long MAX read
    # spans two buckets for a local day, so take the max of all of them.
    peak = session.timeseries(device, [f"{key}_active_power"], window.start_ms, window.end_ms,
                              window.end_ms - window.start_ms, "MAX")
    peaks = peak.get(f"{key}_active_power") or []
    counter = f"{key}_energy_in_kwh"
    return CircuitInput(
        power=read_power(session, device, key, window, MINUTE_MS),
        max_power_w=max((v for _, v in peaks), default=None),
        counter_start=session.last_value(device, counter, window.start_ms),
        counter_end=session.last_value(device, counter, window.end_ms),
    )


def read_weather(session, weather_device, window: Window) -> tuple[Series, Series]:
    if weather_device is None:
        return [], []
    # Hourly averages: the weather is hourly, and a raw read is budgeted at one
    # point per second (about 260 requests for 30 days).
    raw = session.timeseries(weather_device, ["temperature_c", "humidity_pct"], window.start_ms, window.end_ms,
                             HOUR_MS, "AVG")
    return raw.get("temperature_c") or [], raw.get("humidity_pct") or []


def measure(session, monitor, plan: Plan, rate: RateD, window: Window, days: int) -> DailyResult:
    """Metrics of every circuit and the building over `window`, on 1-minute buckets."""
    found, missing = circuits(session, plan)
    result = DailyResult(found=found, missing_assets=missing)
    for c in result.found:
        m = circuit_metrics(window, MINUTE_MS, read_input(session, monitor, c.key, window))
        if m is None:
            result.no_data.append(c.name)
        else:
            result.circuits[c.name] = m
    result.main = circuit_metrics(window, MINUTE_MS, read_input(session, monitor, main_key(plan), window))
    result.building = allocate(result.circuits, result.main, rate, days) or {}
    return result


def usable_energy(m: CircuitMetrics | None) -> float | None:
    """Energy fit for totals and costs: None when missing, flagged or negative."""
    if m is None or m.quality is not None or m.energy_kwh < 0:
        return None
    return m.energy_kwh


def read_metrics(session, device, key: str, window: Window) -> CircuitMetrics | None:
    """Metrics of `key` over `window` on 1-minute buckets (counter energy, integration fallback)."""
    return circuit_metrics(window, MINUTE_MS, read_input(session, device, key, window))


def read_energy(session, device, key: str, window: Window) -> float | None:
    """Usable energy of `key` over `window`."""
    return usable_energy(read_metrics(session, device, key, window))


def usable_peak(m: CircuitMetrics | None, peak: tuple[float | None, int | None]) -> tuple[float | None, int | None]:
    """A peak is unknown when its series is quality-flagged or the maximum is negative."""
    watts, _ = peak
    if watts is None or watts < 0 or (m is not None and m.quality is not None):
        return None, None
    return peak


def read_peak(session, device, key: str, window: Window) -> tuple[float | None, int | None]:
    """Highest 1-minute maximum of `<key>_active_power` and the start of its minute."""
    raw = session.timeseries(device, [f"{key}_active_power"], window.start_ms, window.end_ms, MINUTE_MS, "MAX")
    points = raw.get(f"{key}_active_power") or []
    if not points:
        return None, None
    ts, watts = max(points, key=lambda p: p[1])  # earliest minute on a tie
    return watts, ts


def read_hourly(session, device, key: str, window: Window) -> Series:
    """Hourly average power of `key` (W), for the weekday x hour heatmap."""
    return read_power(session, device, key, window, HOUR_MS)


def read_daily_energy(session, device, key: str, first: dt.date, end: dt.date) -> list[tuple[dt.date, float | None]]:
    """Counter energy of each local day in [first, end): one last_value per local
    midnight, no minute data. A day missing either counter is None; a negative
    difference (counter reset) is kept and left to the caller to discard."""
    counter = f"{key}_energy_in_kwh"
    days = [first + dt.timedelta(days=i) for i in range((end - first).days)]
    midnights = [local_day(day).start_ms for day in days] + ([local_day(end).start_ms] if days else [])
    values = [session.last_value(device, counter, ms) for ms in midnights]
    return [
        (day, round(values[i + 1] - values[i], 3) if values[i] is not None and values[i + 1] is not None else None)
        for i, day in enumerate(days)
    ]
