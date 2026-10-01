"""GATE twin analytics: per-circuit metrics over a time window. Pure.

Inputs are series already read from ThingsFlow; nothing here does I/O or reads
the clock, so every number is reproducible from its inputs.

Energy comes from the Refoss annual counter (`<circuit>_energy_in_kwh`, kept
monotonic by the edge across device restarts): end minus start is exact and
survives gaps in the power series. When an end is missing or the difference is
negative (the counter restarts each 1 January) energy is integrated from the
power buckets instead, and `energy_source` says which one was used.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from zoneinfo import ZoneInfo

Series = list[tuple[int, float]]

ON_THRESHOLD_W = 10.0  # the legacy backend's "on" threshold
TIMEZONE = "America/Toronto"


@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int

    @property
    def minutes(self) -> int:
        return (self.end_ms - self.start_ms) // 60_000


def local_day(day: dt.date, tz: str = TIMEZONE) -> Window:
    """The calendar day in `tz`: 23 or 25 hours on DST changes."""
    zone = ZoneInfo(tz)
    start = dt.datetime.combine(day, dt.time(), zone)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), zone)
    return Window(int(start.timestamp() * 1000), int(end.timestamp() * 1000))


@dataclass(frozen=True)
class CircuitInput:
    power: Series  # bucket averages of <circuit>_active_power, W
    max_power_w: float | None  # agg=MAX over the window
    counter_start: float | None  # last <circuit>_energy_in_kwh at/before start
    counter_end: float | None  # last <circuit>_energy_in_kwh at/before end


@dataclass
class CircuitMetrics:
    energy_kwh: float
    energy_source: str
    avg_power_w: float | None
    max_power_w: float | None
    on_hours: float
    utilization_pct: float
    coverage_pct: float
    energy_fraction_pct: float | None = None
    cost_cad: float | None = None

    def as_values(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def circuit_metrics(window: Window, bucket_ms: int, data: CircuitInput) -> CircuitMetrics | None:
    buckets = [w for ts, w in data.power if window.start_ms <= ts < window.end_ms]
    counted = (
        data.counter_start is not None
        and data.counter_end is not None
        and data.counter_end >= data.counter_start
    )
    if not buckets and not counted:
        return None
    bucket_hours = bucket_ms / 3_600_000
    window_buckets = (window.end_ms - window.start_ms) // bucket_ms
    if counted:
        energy, source = data.counter_end - data.counter_start, "counter"
    else:
        energy, source = sum(buckets) * bucket_hours / 1000, "integrated"
    on = sum(1 for w in buckets if w > ON_THRESHOLD_W)
    return CircuitMetrics(
        energy_kwh=round(energy, 3),
        energy_source=source,
        avg_power_w=round(sum(buckets) / len(buckets), 1) if buckets else None,
        max_power_w=data.max_power_w,
        on_hours=round(on * bucket_hours, 2),
        utilization_pct=round(100 * on / window_buckets, 1),
        coverage_pct=round(100 * len(buckets) / window_buckets, 1),
    )
