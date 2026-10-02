"""Read ThingsFlow history into twin inputs, and twin outputs back to assets.

The only module that knows which keys hold what. twin.py stays pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gate_cloud.asset_model import Plan
from gate_cloud.tariff import RateD
from gate_cloud.twin import CircuitInput, CircuitMetrics, Series, Window, allocate, circuit_metrics

MAIN = "main_total"
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
    main = circuit_metrics(window, MINUTE_MS, read_input(session, monitor, MAIN, window))
    result.building = allocate(result.circuits, main, rate, days) or {}
    return result
