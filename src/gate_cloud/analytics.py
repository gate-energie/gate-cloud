"""Read ThingsFlow history into twin inputs, and twin outputs back to assets.

The only module that knows which keys hold what. twin.py stays pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gate_cloud.asset_model import Plan
from gate_cloud.tariff import RateD
from gate_cloud.thingsflow import entity_ref
from gate_cloud.twin import CircuitInput, CircuitMetrics, Series, Window, allocate, circuit_metrics, summary_attributes, weather_day

MAIN = "main_total"


@dataclass(frozen=True)
class Circuit:
    name: str
    key: str
    asset_id: str
    rated_power_w: float | None


@dataclass
class DailyResult:
    circuits: dict[str, CircuitMetrics] = field(default_factory=dict)
    building: dict[str, Any] = field(default_factory=dict)
    no_data: list[str] = field(default_factory=list)


def circuits(session, plan: Plan) -> list[Circuit]:
    ids = {name: asset.id.id for name, asset in session.assets().items()}
    return [
        Circuit(spec.name, spec.attributes["circuit_key"], ids[spec.name], spec.attributes.get("rated_power_w"))
        for spec in plan.assets
        if spec.parent is not None and spec.profile not in ("Building", "Electrical Panel")
    ]


def read_input(session, device, key: str, window: Window, bucket_ms: int) -> CircuitInput:
    power = session.timeseries(device, [f"{key}_active_power"], window.start_ms, window.end_ms, bucket_ms, "AVG")
    peak = session.timeseries(device, [f"{key}_active_power"], window.start_ms, window.end_ms,
                              window.end_ms - window.start_ms, "MAX")
    peaks = peak.get(f"{key}_active_power") or []
    counter = f"{key}_energy_in_kwh"
    return CircuitInput(
        power=power.get(f"{key}_active_power") or [],
        max_power_w=peaks[0][1] if peaks else None,
        counter_start=session.last_value(device, counter, window.start_ms),
        counter_end=session.last_value(device, counter, window.end_ms),
    )


def read_weather(session, weather_device, window: Window) -> tuple[Series, Series]:
    if weather_device is None:
        return [], []
    raw = session.timeseries(weather_device, ["temperature_c", "humidity_pct"], window.start_ms, window.end_ms)
    return raw.get("temperature_c") or [], raw.get("humidity_pct") or []


def measure(session, monitor, plan: Plan, rate: RateD, window: Window, bucket_ms: int, days: int):
    found = circuits(session, plan)
    result = DailyResult()
    inputs: dict[str, CircuitInput] = {}
    for c in found:
        inputs[c.name] = read_input(session, monitor, c.key, window, bucket_ms)
        m = circuit_metrics(window, bucket_ms, inputs[c.name])
        if m is None:
            result.no_data.append(c.name)
        else:
            result.circuits[c.name] = m
    main = circuit_metrics(window, bucket_ms, read_input(session, monitor, MAIN, window, bucket_ms))
    result.building = allocate(result.circuits, main, rate, days) or {}
    return found, inputs, result
