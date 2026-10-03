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

from gate_cloud.tariff import RateD

Series = list[tuple[int, float]]

ON_THRESHOLD_W = 10.0  # the legacy backend's "on" threshold
# A load cannot return energy: negative power means a clamp installed backwards
# (heating_storage until it was fixed at the edge). The measured value is kept
# and flagged, and stays out of the building's fractions and costs.
NEGATIVE_POWER = "negative_power"
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
    on_hours: float | None
    utilization_pct: float | None
    coverage_pct: float
    energy_fraction_pct: float | None = None
    cost_cad: float | None = None
    quality: str | None = None  # NEGATIVE_POWER when the series cannot be a load

    def as_values(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def circuit_metrics(window: Window, bucket_ms: int, data: CircuitInput) -> CircuitMetrics | None:
    window_buckets = (window.end_ms - window.start_ms) // bucket_ms
    if window_buckets <= 0:  # an empty window (today at 00:00) has nothing to measure
        return None
    buckets = [w for ts, w in data.power if window.start_ms <= ts < window.end_ms]
    counted = (
        data.counter_start is not None
        and data.counter_end is not None
        and data.counter_end >= data.counter_start
    )
    if not buckets and not counted:
        return None
    bucket_hours = bucket_ms / 3_600_000
    if counted:
        energy, source = data.counter_end - data.counter_start, "counter"
    else:
        energy, source = sum(buckets) * bucket_hours / 1000, "integrated"
    on = sum(1 for w in buckets if w > ON_THRESHOLD_W)
    avg = round(sum(buckets) / len(buckets), 1) if buckets else None
    return CircuitMetrics(
        energy_kwh=round(energy, 3),
        energy_source=source,
        avg_power_w=avg,
        max_power_w=data.max_power_w,
        on_hours=round(on * bucket_hours, 2) if buckets else None,
        utilization_pct=round(100 * on / window_buckets, 1) if buckets else None,
        coverage_pct=round(100 * len(buckets) / window_buckets, 1),
        quality=NEGATIVE_POWER if energy < 0 or (avg is not None and avg < 0) else None,
    )


DEGREE_DAY_BASE_C = 18.0
CORRELATION_MIN_DAYS = 2
OVERLOAD_FACTOR = 1.1


def allocate(circuits: dict[str, CircuitMetrics], main: CircuitMetrics | None, rate: RateD, days: int) -> dict | None:
    """Fraction and cost per circuit against the building aggregate.

    Rate D's tier-1 threshold belongs to the building, so the bill is computed
    on main_total and each circuit pays the building's effective energy rate
    (taxes included). The fixed charge stays on the building. A flagged
    circuit gets neither: its numbers are not a share of the building's. A
    flagged aggregate has no usable bill or rate, so nothing is allocated.
    """
    if main is None or main.quality is not None or main.energy_kwh <= 0:
        return None
    bill = rate.cost(main.energy_kwh, days=days, apply_fixed_charge=True)
    energy_rate = bill["energy_cost"] * (1 + rate.tax_rate) / main.energy_kwh
    for m in circuits.values():
        if m.quality is not None:
            continue
        m.energy_fraction_pct = round(100 * m.energy_kwh / main.energy_kwh, 2)
        m.cost_cad = round(m.energy_kwh * energy_rate, 2)
    return {"energy_kwh": main.energy_kwh, "cost_cad": bill["total"], "peak_power_w": main.max_power_w}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def weather_day(temperature: Series, humidity: Series, window: Window) -> dict:
    temps = [v for ts, v in temperature if window.start_ms <= ts < window.end_ms]
    hums = [v for ts, v in humidity if window.start_ms <= ts < window.end_ms]
    mean = _mean(temps)
    return {
        "temp_mean_c": mean,
        "temp_min_c": min(temps) if temps else None,
        "temp_max_c": max(temps) if temps else None,
        "humidity_mean_pct": _mean(hums),
        "hdd": None if mean is None else round(max(0.0, DEGREE_DAY_BASE_C - mean), 2),
        "cdd": None if mean is None else round(max(0.0, mean - DEGREE_DAY_BASE_C), 2),
    }


def pearson(pairs: list[tuple[float, float]], min_pairs: int) -> float | None:
    """Pearson r, or None when it cannot be known (too few pairs, a constant series)."""
    n = len(pairs)
    if n < min_pairs:
        return None
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    if min(xs) == max(xs) or min(ys) == max(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return round(sxy / (sxx * syy) ** 0.5, 2)


def align_hourly(power: Series, weather: Series) -> list[tuple[float, float]]:
    """Pair each power bucket with the weather observation of its hour."""
    by_hour = {ts // 3_600_000: v for ts, v in weather}
    return [(w, by_hour[ts // 3_600_000]) for ts, w in power if ts // 3_600_000 in by_hour]


def summary_attributes(
    window: Window,
    m: CircuitMetrics,
    power: Series,
    temperature: Series,
    humidity: Series,
    rated_power_w: float | None,
    bucket_ms: int,
) -> dict:
    """The twin_* SERVER_SCOPE attributes of one circuit for one window."""
    min_pairs = CORRELATION_MIN_DAYS * 86_400_000 // bucket_ms
    overload = None
    health = None
    if rated_power_w and m.max_power_w is not None:
        overload = m.max_power_w > rated_power_w * OVERLOAD_FACTOR
        health = 70.0 if overload else 100.0
    return {
        "twin_energy_kwh": m.energy_kwh,
        "twin_energy_source": m.energy_source,
        "twin_cost_cad": m.cost_cad,
        "twin_avg_power_w": m.avg_power_w,
        "twin_max_power_w": m.max_power_w,
        "twin_utilization_pct": m.utilization_pct,
        "twin_coverage_pct": m.coverage_pct,
        "twin_quality": m.quality or "",  # "" clears a flag left by an earlier window
        "twin_energy_fraction_pct": m.energy_fraction_pct,
        "twin_corr_temperature": pearson(align_hourly(power, temperature), min_pairs),
        "twin_corr_humidity": pearson(align_hourly(power, humidity), min_pairs),
        "twin_health_score": health,
        "twin_overload": overload,
        "twin_window_start": window.start_ms,
        "twin_window_end": window.end_ms,
    }


def month_window(today: dt.date, tz: str = TIMEZONE) -> Window | None:
    """The current month up to today 00:00 (yesterday inclusive); None on the 1st."""
    if today.day == 1:
        return None
    return Window(local_day(today.replace(day=1), tz).start_ms, local_day(today, tz).start_ms)


def month_budget(energy_kwh: float | None, elapsed_days: int, days_in_month: int, rate: RateD,
                 monthly_budget: float | None) -> dict:
    """Month-to-date cost against the budget, and the cost projected to month end."""
    if energy_kwh is None or elapsed_days <= 0:
        return dict.fromkeys(("month_energy_kwh", "month_cost_cad", "month_budget_used_pct", "month_projected_cost_cad"))
    cost = rate.cost(energy_kwh, days=elapsed_days, apply_fixed_charge=True)["total"]
    return {
        "month_energy_kwh": round(energy_kwh, 3),
        "month_cost_cad": cost,
        "month_budget_used_pct": round(100 * cost / monthly_budget, 1) if monthly_budget else None,
        "month_projected_cost_cad": round(cost * days_in_month / elapsed_days, 2),
    }


def today_window(now: dt.datetime, tz: str = TIMEZONE) -> Window:
    """Local midnight of `now`'s local date up to `now` floored to the minute.

    `now` is passed in (never read here) so the window is reproducible.
    """
    local = now.astimezone(ZoneInfo(tz))
    end = local.replace(second=0, microsecond=0)
    start = local_day(local.date(), tz).start_ms
    return Window(start, int(end.timestamp() * 1000))


def same_time_yesterday(window: Window, tz: str = TIMEZONE) -> Window:
    """The previous local day, from its midnight to the same local clock time.

    Built from wall-clock dates, not `-86_400_000`: on a DST change the
    previous day is 23 or 25 hours long and a fixed offset would drift.
    """
    zone = ZoneInfo(tz)
    end = dt.datetime.fromtimestamp(window.end_ms / 1000, zone)
    start = dt.datetime.fromtimestamp(window.start_ms / 1000, zone)
    day = start.date() - dt.timedelta(days=1)
    yesterday_end = dt.datetime.combine(day, end.time(), zone)
    return Window(local_day(day, tz).start_ms, int(yesterday_end.timestamp() * 1000))


def day_cost(energy_kwh: float | None, rate: RateD) -> float | None:
    """One day's bill (fixed charge included); None when the energy is unknown."""
    if energy_kwh is None:
        return None
    return rate.cost(energy_kwh, days=1, apply_fixed_charge=True)["total"]


WEEKDAYS_ES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def heatmap(hourly: Series, tz: str = TIMEZONE) -> dict:
    """Mean kW per local weekday and hour from hourly average-power points (W).

    Buckets use the local weekday and hour, so the pattern matches the
    building's schedule across DST. An empty cell is None (unknown), not 0.
    """
    zone = ZoneInfo(tz)
    cells: list[list[list[float]]] = [[[] for _ in range(24)] for _ in range(7)]
    for ts, watts in hourly:
        local = dt.datetime.fromtimestamp(ts / 1000, zone)
        cells[local.weekday()][local.hour].append(watts)
    return {
        "unit": "kW",
        "days": list(WEEKDAYS_ES),
        "values": [[round(sum(c) / len(c) / 1000, 3) if c else None for c in row] for row in cells],
        "samples": [[len(c) for c in row] for row in cells],
    }


def daily_scatter(days: list[tuple[dt.date, float | None, float | None]]) -> list[dict]:
    """Energy against mean temperature, only for days where both are known.

    A negative energy day comes from a counter reset or a backwards clamp and
    is not a measurement, so it stays out.
    """
    return [
        {"date": day.isoformat(), "kwh": kwh, "temp_mean_c": temp}
        for day, kwh, temp in sorted(days, key=lambda d: d[0])
        if kwh is not None and temp is not None and kwh >= 0
    ]


def month_extras(month_cost: float | None, elapsed_days: int, days_in_month: int) -> dict:
    """Average daily cost so far (None if unknown) and days left (always known)."""
    avg = round(month_cost / elapsed_days, 2) if month_cost is not None and elapsed_days > 0 else None
    return {"month_avg_daily_cost_cad": avg, "month_days_left": days_in_month - elapsed_days}
