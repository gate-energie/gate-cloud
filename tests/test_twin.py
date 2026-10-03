"""Twin metrics are pure: synthetic series in, numbers out."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from gate_cloud.tariff import RateD
from gate_cloud.twin import (
    CircuitInput,
    CircuitMetrics,
    Window,
    align_hourly,
    allocate,
    circuit_metrics,
    local_day,
    month_budget,
    month_window,
    pearson,
    summary_attributes,
    weather_day,
)
from gate_cloud.twin import daily_scatter, day_cost, heatmap, month_extras, same_time_yesterday, today_window

TZ = ZoneInfo("America/Toronto")

MIN = 60_000
HOUR = 3_600_000


def metrics(kwh, max_w=1000.0):
    return CircuitMetrics(kwh, "counter", 100.0, max_w, 1.0, 10.0, 100.0)


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


def test_counter_only_day_has_unknown_utilization():
    day = local_day(dt.date(2026, 10, 1))
    m = circuit_metrics(day, MIN, CircuitInput([], None, 10.0, 12.0))
    assert m.energy_kwh == 2.0 and m.energy_source == "counter"
    assert m.on_hours is None and m.utilization_pct is None and m.coverage_pct == 0.0


def test_cost_is_allocated_at_the_building_effective_energy_rate():
    circuits = {"heating": metrics(30.0), "lights": metrics(10.0)}
    building = allocate(circuits, metrics(50.0, max_w=8000.0), RateD(), days=1)
    day = RateD().cost(50.0, days=1, apply_fixed_charge=True)
    assert building == {"energy_kwh": 50.0, "cost_cad": day["total"], "peak_power_w": 8000.0}
    energy_with_tax = day["energy_cost"] * (1 + RateD().tax_rate)
    assert circuits["heating"].cost_cad == round(30.0 * energy_with_tax / 50.0, 2)
    assert circuits["heating"].energy_fraction_pct == 60.0


def test_without_main_total_fraction_and_cost_are_unknown():
    circuits = {"heating": metrics(30.0)}
    assert allocate(circuits, None, RateD(), days=1) is None
    assert circuits["heating"].cost_cad is None and circuits["heating"].energy_fraction_pct is None


def test_weather_day_degree_days():
    day = local_day(dt.date(2026, 1, 15))
    temps = [(day.start_ms + h * HOUR, -10.0 if h < 12 else 0.0) for h in range(24)]
    hums = [(day.start_ms + h * HOUR, 80.0) for h in range(24)]
    w = weather_day(temps, hums, day)
    assert w == {"temp_mean_c": -5.0, "temp_min_c": -10.0, "temp_max_c": 0.0,
                 "humidity_mean_pct": 80.0, "hdd": 23.0, "cdd": 0.0}


def test_weather_day_without_points_is_all_none():
    assert set(weather_day([], [], local_day(dt.date(2026, 1, 15))).values()) == {None}


def test_pearson_is_none_when_unknowable():
    assert pearson([(1.0, 2.0)] * 500, min_pairs=192) is None  # zero variance
    assert pearson([(1.0, 1.0), (2.0, 2.0)], min_pairs=192) is None  # too few
    pairs = [(float(i), 2.0 * i + 1) for i in range(200)]
    assert pearson(pairs, min_pairs=192) == 1.0


def test_pearson_constant_non_representable_series_is_none():
    assert pearson([(21.3, float(i)) for i in range(193)], min_pairs=192) is None
    assert pearson([(float(i), 21.3) for i in range(193)], min_pairs=192) is None


def test_align_hourly_maps_buckets_to_their_hour():
    power = [(0, 1.0), (15 * MIN, 2.0), (HOUR, 3.0), (2 * HOUR, 4.0)]
    weather = [(0, 10.0), (HOUR, 20.0)]
    assert align_hourly(power, weather) == [(1.0, 10.0), (2.0, 10.0), (3.0, 20.0)]


def test_summary_without_rating_has_no_health():
    window = Window(0, 30 * 24 * HOUR)
    attrs = summary_attributes(window, metrics(300.0), [], [], [], None, 15 * MIN)
    assert attrs["twin_health_score"] is None and attrs["twin_overload"] is None
    assert attrs["twin_corr_temperature"] is None
    assert attrs["twin_energy_kwh"] == 300.0
    assert attrs["twin_window_start"] == 0 and attrs["twin_window_end"] == window.end_ms


def test_summary_overload_against_rating():
    window = Window(0, 30 * 24 * HOUR)
    attrs = summary_attributes(window, metrics(300.0, max_w=2300.0), [], [], [], 2000.0, 15 * MIN)
    assert attrs["twin_overload"] is True and attrs["twin_health_score"] == 70.0
    ok = summary_attributes(window, metrics(300.0, max_w=2100.0), [], [], [], 2000.0, 15 * MIN)
    assert ok["twin_overload"] is False and ok["twin_health_score"] == 100.0


def test_negative_integrated_energy_is_flagged_not_rewritten():
    # A clamp installed backwards reads negative power (heating_storage, 2026-08-10).
    day = local_day(dt.date(2026, 8, 10))
    m = circuit_metrics(day, MIN, CircuitInput(flat(day, -372.0), -100.0, None, None))
    assert m.energy_kwh < 0 and m.avg_power_w == -372.0
    assert m.quality == "negative_power"


def test_clean_day_has_no_quality_flag():
    day = local_day(dt.date(2026, 10, 1))
    assert circuit_metrics(day, MIN, CircuitInput(flat(day, 500.0), 500.0, 1.0, 13.0)).quality is None


def test_flagged_circuit_gets_no_fraction_or_cost():
    flagged = metrics(-3.0)
    flagged.quality = "negative_power"
    circuits = {"heating_storage": flagged, "lights": metrics(10.0)}
    allocate(circuits, metrics(50.0), RateD(), days=1)
    assert flagged.energy_fraction_pct is None and flagged.cost_cad is None
    assert circuits["lights"].energy_fraction_pct == 20.0


def test_summary_carries_the_quality_flag():
    flagged = metrics(-3.0)
    flagged.quality = "negative_power"
    attrs = summary_attributes(Window(0, 30 * 24 * HOUR), flagged, [], [], [], None, 15 * MIN)
    assert attrs["twin_quality"] == "negative_power"


def test_month_window_runs_from_the_first_to_today():
    w = month_window(dt.date(2026, 10, 2))
    assert w == Window(local_day(dt.date(2026, 10, 1)).start_ms, local_day(dt.date(2026, 10, 2)).start_ms)


def test_month_window_is_none_on_the_first():
    assert month_window(dt.date(2026, 11, 1)) is None


def test_month_window_spans_dst_change():
    w = month_window(dt.date(2026, 11, 3))  # Nov 1 is 25 h
    assert w.minutes == (25 + 24) * 60


def test_month_budget_projects_to_the_full_month():
    b = month_budget(300.0, elapsed_days=10, days_in_month=31, rate=RateD(), monthly_budget=150.0)
    cost = RateD().cost(300.0, days=10, apply_fixed_charge=True)["total"]
    assert b["month_energy_kwh"] == 300.0 and b["month_cost_cad"] == cost
    assert b["month_projected_cost_cad"] == round(cost * 31 / 10, 2)
    assert b["month_budget_used_pct"] == round(100 * cost / 150.0, 1)


def test_month_budget_without_energy_or_budget_is_unknown():
    assert set(month_budget(None, 10, 31, RateD(), 150.0).values()) == {None}
    b = month_budget(300.0, 10, 31, RateD(), None)
    assert b["month_budget_used_pct"] is None and b["month_cost_cad"] is not None


def test_today_window_runs_from_local_midnight_to_now():
    now = dt.datetime(2026, 10, 3, 9, 37, 20, tzinfo=TZ)
    w = today_window(now)
    assert w.start_ms == local_day(dt.date(2026, 10, 3)).start_ms
    assert w.end_ms == int(dt.datetime(2026, 10, 3, 9, 37, tzinfo=TZ).timestamp() * 1000)


def test_same_time_yesterday_respects_dst():
    now = dt.datetime(2026, 11, 2, 10, 0, tzinfo=TZ)  # Nov 1 had 25 h
    y = same_time_yesterday(today_window(now))
    assert y.start_ms == local_day(dt.date(2026, 11, 1)).start_ms
    assert y.end_ms == int(dt.datetime(2026, 11, 1, 10, 0, tzinfo=TZ).timestamp() * 1000)


def test_day_cost():
    assert day_cost(None, RateD()) is None
    assert day_cost(20.0, RateD()) == RateD().cost(20.0, days=1, apply_fixed_charge=True)["total"]


def test_heatmap_buckets_by_local_weekday_and_hour():
    monday_8 = int(dt.datetime(2026, 9, 28, 8, 0, tzinfo=TZ).timestamp() * 1000)
    week = 7 * 86_400_000
    h = heatmap([(monday_8, 1000.0), (monday_8 + week, 3000.0)])
    assert h["unit"] == "kW" and len(h["values"]) == 7 and len(h["values"][0]) == 24
    assert h["values"][0][8] == 2.0 and h["samples"][0][8] == 2
    assert h["values"][0][9] is None and h["values"][1][8] is None


def test_daily_scatter_keeps_complete_non_negative_days():
    rows = [(dt.date(2026, 8, 11), -3.1, 20.0), (dt.date(2026, 8, 10), 5.0, None), (dt.date(2026, 9, 1), 18.2, 14.5)]
    assert daily_scatter(rows) == [{"date": "2026-09-01", "kwh": 18.2, "temp_mean_c": 14.5}]


def test_month_extras():
    assert month_extras(18.0, 2, 31) == {"month_avg_daily_cost_cad": 9.0, "month_days_left": 29}
    assert month_extras(None, 0, 31) == {"month_avg_daily_cost_cad": None, "month_days_left": 31}


def test_empty_window_has_no_metrics():
    # At 00:00 the today window is zero-length: no buckets to divide by.
    empty = Window(1_000_000, 1_000_000)
    assert circuit_metrics(empty, 60_000, CircuitInput([], None, 5.0, 5.0)) is None
    assert circuit_metrics(Window(2_000_000, 1_000_000), 60_000, CircuitInput([], None, 5.0, 6.0)) is None


def test_a_flagged_main_aggregate_allocates_nothing():
    main = metrics(50.0)
    main.quality = "negative_power"
    circuits = {"heating": metrics(30.0)}
    assert allocate(circuits, main, RateD(), days=1) is None
    assert circuits["heating"].cost_cad is None and circuits["heating"].energy_fraction_pct is None
