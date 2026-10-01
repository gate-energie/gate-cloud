"""Twin metrics are pure: synthetic series in, numbers out."""
import datetime as dt

import pytest

from gate_cloud.twin import CircuitInput, Window, circuit_metrics, local_day

MIN = 60_000


def flat(window, watts, bucket=MIN):
    return [(t, watts) for t in range(window.start_ms, window.end_ms, bucket)]


def test_local_day_has_23_and_25_hours_on_dst_changes():
    assert local_day(dt.date(2026, 3, 8)).minutes == 23 * 60
    assert local_day(dt.date(2026, 11, 1)).minutes == 25 * 60
    assert local_day(dt.date(2026, 10, 1)).minutes == 24 * 60


def test_energy_comes_from_the_annual_counter():
    day = local_day(dt.date(2026, 10, 1))
    m = circuit_metrics(day, MIN, CircuitInput(flat(day, 500.0), 900.0, 1200.0, 1212.5))
    assert (m.energy_kwh, m.energy_source) == (12.5, "counter")
    assert m.avg_power_w == 500.0 and m.max_power_w == 900.0


def test_counter_reset_falls_back_to_integration():
    day = local_day(dt.date(2026, 10, 1))
    m = circuit_metrics(day, MIN, CircuitInput(flat(day, 500.0), 500.0, 5000.0, 3.0))
    assert m.energy_source == "integrated"
    assert m.energy_kwh == pytest.approx(12.0)  # 500 W x 24 h


def test_missing_counter_falls_back_to_integration():
    day = local_day(dt.date(2026, 10, 1))
    m = circuit_metrics(day, MIN, CircuitInput(flat(day, 1000.0), 1000.0, None, 7.0))
    assert m.energy_source == "integrated" and m.energy_kwh == pytest.approx(24.0)


def test_utilization_and_coverage_use_the_real_day_length():
    day = local_day(dt.date(2026, 11, 1))  # 25 h
    half = [(t, 100.0 if i < 750 else 0.0) for i, (t, _) in enumerate(flat(day, 0))]  # 750 of 1500 min on
    m = circuit_metrics(day, MIN, CircuitInput(half, 100.0, None, None))
    assert m.on_hours == 12.5 and m.utilization_pct == 50.0 and m.coverage_pct == 100.0


def test_gaps_lower_coverage_not_power():
    day = local_day(dt.date(2026, 10, 1))
    m = circuit_metrics(day, MIN, CircuitInput(flat(day, 200.0)[:720], 200.0, 10.0, 12.4))
    assert m.coverage_pct == 50.0 and m.avg_power_w == 200.0 and m.energy_kwh == 2.4


def test_no_data_at_all_is_none():
    day = local_day(dt.date(2026, 10, 1))
    assert circuit_metrics(day, MIN, CircuitInput([], None, None, None)) is None
