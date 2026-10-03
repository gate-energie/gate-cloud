"""Daily metrics and the 30-day summary, end to end against the fake."""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import dagster as dg

from gate_cloud import definitions as d
from gate_cloud.analytics import MINUTE_MS, QUARTER_MS
from gate_cloud.tariff import RateD
from gate_cloud.twin import local_day
from tests.fakes import FakeThingsFlow, circuit_map
from tests.test_definitions import SHARED, FakeThingsFlowResource, weather_resources

MIN = 60_000
DAY = local_day(dt.date(2026, 9, 30))


def seed(watts_heating=1000.0, with_main=True, day=DAY):
    dev = "dev-1"
    SHARED.series[(dev, "heating_active_power")] = [(t, watts_heating) for t in range(day.start_ms, day.end_ms, MIN)]
    SHARED.series[(dev, "heating_energy_in_kwh")] = [(day.start_ms - MIN, 100.0), (day.end_ms - MIN, 124.0)]
    if with_main:
        SHARED.series[(dev, "main_total_active_power")] = [(t, 2000.0) for t in range(day.start_ms, day.end_ms, MIN)]
        SHARED.series[(dev, "main_total_energy_in_kwh")] = [(day.start_ms - MIN, 1000.0), (day.end_ms - MIN, 1048.0)]


def history_resources(tmp_path):
    res = weather_resources(tmp_path)
    res["thingsflow"] = FakeThingsFlowResource(
        url="x", username="u", password="p", monitor_device_id="dev-1", ingest_url="http://ingest", asset_history=True
    )
    return res


def written(entity):
    return {e: point for e, point in SHARED.asset_points}[entity]["values"]


def test_daily_partition_reports_metrics_and_writes_nothing_without_history(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()
    result = dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res)
    assert result.success
    meta = result.asset_materializations_for_node("circuit_daily_metrics")[0].metadata
    assert meta["building_energy_kwh"].value == 48.0
    assert meta["no_data"].value == []
    assert SHARED.asset_points == []  # asset_history is off


def test_daily_partition_writes_assets_when_history_is_on(tmp_path):
    res = weather_resources(tmp_path)
    res["thingsflow"] = FakeThingsFlowResource(
        url="x", username="u", password="p", monitor_device_id="dev-1", ingest_url="http://ingest", asset_history=True
    )
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()
    dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res)
    written = {entity: point for entity, point in SHARED.asset_points}
    assert written["asset-heating"]["ts"] == DAY.start_ms
    assert written["asset-heating"]["values"]["energy_kwh"] == 24.0
    assert written["asset-heating"]["values"]["energy_fraction_pct"] == 50.0
    assert written["asset-B"]["values"]["energy_kwh"] == 48.0


def test_circuit_without_data_is_listed_and_the_run_succeeds(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    SHARED.series.clear()
    result = dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res)
    assert result.success
    meta = result.asset_materializations_for_node("circuit_daily_metrics")[0].metadata
    assert meta["no_data"].value == ["heating"]


def test_summary_writes_twin_attributes(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()
    result = dg.materialize(
        [d.asset_twin_summary],
        resources=res,
        run_config={"ops": {"asset_twin_summary": {"config": {"end_date": "2026-10-01"}}}},
    )
    assert result.success
    attrs = SHARED.attrs["asset-heating"]
    assert attrs["twin_energy_kwh"] == 24.0  # one day of 1000 W in the 30-day window
    assert attrs["twin_max_power_w"] == 1000.0
    assert "twin_updated_at" in attrs
    # Unknowns are not stored (ThingsFlow would keep the string "null"); they are listed instead.
    assert None not in attrs.values()
    assert "twin_health_score" not in attrs and "twin_overload" not in attrs
    assert {"twin_health_score", "twin_overload", "twin_quality"} <= set(attrs["twin_unknown"].split(","))


def test_daily_peak_late_in_the_local_day_is_kept(tmp_path):
    # Toronto 22:00 is 02:00 UTC the next day: past the epoch-aligned day bucket
    # boundary, so a window-long MAX read returns two buckets.
    res = history_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()
    peak_ts = int(dt.datetime(2026, 9, 30, 22, tzinfo=ZoneInfo("America/Toronto")).timestamp() * 1000)
    SHARED.series[("dev-1", "heating_active_power")] = [
        (t, 3500.0 if t == peak_ts else 1000.0) for t in range(DAY.start_ms, DAY.end_ms, MIN)
    ]
    assert dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res).success
    assert written("asset-heating")["max_power_w"] == 3500.0


SUMMARY_CONFIG = {"ops": {"asset_twin_summary": {"config": {"end_date": "2026-10-01"}}}}


def recorded_reads():
    calls = []
    original = SHARED.timeseries

    def timeseries(entity, keys, start_ms, end_ms, interval_ms=0, agg="NONE"):
        calls.append((tuple(keys), interval_ms, agg))
        return original(entity, keys, start_ms, end_ms, interval_ms, agg)

    SHARED.timeseries = timeseries
    return calls


def test_summary_utilization_counts_minutes_and_correlates_on_quarter_hours(tmp_path):
    # On 5 minutes of every 15: the quarter-hour average is 333 W (> 10 W), but
    # the circuit is on a third of the minutes, as in the daily metric.
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    SHARED.series[("dev-1", "heating_active_power")] = [
        (t, 1000.0 if (t - DAY.start_ms) // MIN % 15 < 5 else 0.0) for t in range(DAY.start_ms, DAY.end_ms, MIN)
    ]
    calls = recorded_reads()
    assert dg.materialize([d.asset_twin_summary], resources=res, run_config=SUMMARY_CONFIG).success
    attrs = SHARED.attrs["asset-heating"]
    assert attrs["twin_utilization_pct"] == 1.1  # 480 of 43 200 minutes
    assert attrs["twin_coverage_pct"] == 3.3  # 1 day of 30
    assert attrs["twin_energy_kwh"] == 8.0 and attrs["twin_energy_source"] == "integrated"
    assert (("heating_active_power",), MINUTE_MS, "AVG") in calls
    assert (("heating_active_power",), QUARTER_MS, "AVG") in calls


def test_weather_is_read_as_hourly_averages(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()
    weather = SHARED.ensure_device(d.WEATHER_DEVICE, "weather", "Weather")
    SHARED.series[(weather, "temperature_c")] = [(t, 4.0) for t in range(DAY.start_ms, DAY.end_ms, 3_600_000)]
    calls = recorded_reads()
    result = dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res)
    meta = result.asset_materializations_for_node("circuit_daily_metrics")[0].metadata
    assert meta["temp_mean_c"].value == 4.0
    weather_reads = [c for c in calls if "temperature_c" in c[0]]
    assert weather_reads == [(("temperature_c", "humidity_pct"), 3_600_000, "AVG")]


def test_daily_dst_partition_counts_25_hours(tmp_path):
    res = history_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    fall_back = local_day(dt.date(2026, 11, 1))
    assert fall_back.minutes == 25 * 60
    seed(day=fall_back)
    # Invoked directly: the partition is still in the future for dg.materialize.
    with dg.build_asset_context(partition_key="2026-11-01") as context:
        d.circuit_daily_metrics(context, res["thingsflow"], res["asset_model_file"])
    values = written("asset-heating")
    assert values["coverage_pct"] == 100.0
    assert values["utilization_pct"] == 100.0 and values["on_hours"] == 25.0
    assert values["energy_kwh"] == 24.0 and values["avg_power_w"] == 1000.0


def test_daily_without_main_total_leaves_fraction_and_cost_unknown(tmp_path):
    res = history_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed(with_main=False)
    result = dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res)
    assert result.success
    meta = result.asset_materializations_for_node("circuit_daily_metrics")[0].metadata
    assert meta["building_energy_kwh"].value is None and meta["building_cost_cad"].value is None
    assert "| heating | 24.0 | counter | 1000.0 | 1000.0 | 100.0 | 100.0 | None | None |" in meta["circuits"].value
    values = written("asset-heating")
    assert values["energy_kwh"] == 24.0
    assert "energy_fraction_pct" not in values and "cost_cad" not in values  # None is not written


def test_circuit_without_an_asset_is_skipped_and_reported(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()
    del SHARED.entities["heating"]  # in the plan, not (yet) in ThingsFlow
    daily = dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res)
    assert daily.success
    meta = daily.asset_materializations_for_node("circuit_daily_metrics")[0].metadata
    assert meta["missing_assets"].value == ["heating"]
    summary = dg.materialize([d.asset_twin_summary], resources=res, run_config=SUMMARY_CONFIG)
    assert summary.success
    meta = summary.asset_materializations_for_node("asset_twin_summary")[0].metadata
    assert meta["missing_assets"].value == ["heating"]
    assert "twin_energy_kwh" not in SHARED.attrs["asset-heating"]


def test_building_aggregate_key_comes_from_the_circuit_map(tmp_path):
    res = weather_resources(tmp_path)
    SHARED.device_attributes["circuit_map"] = circuit_map("heating", aggregate="main_sum")
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed(with_main=False)
    SHARED.series[("dev-1", "main_sum_energy_in_kwh")] = [(DAY.start_ms - MIN, 1000.0), (DAY.end_ms - MIN, 1048.0)]
    result = dg.materialize([d.circuit_daily_metrics], partition_key="2026-09-30", resources=res)
    meta = result.asset_materializations_for_node("circuit_daily_metrics")[0].metadata
    assert meta["building_energy_kwh"].value == 48.0


def test_summary_writes_month_budget_on_the_building(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()  # main_total counter 1000 -> 1048 on 2026-09-30
    result = dg.materialize([d.asset_twin_summary], resources=res,
                            run_config={"ops": {"asset_twin_summary": {"config": {"end_date": "2026-10-01"}}}})
    assert result.success
    b = SHARED.attrs["asset-B"]
    assert None not in b.values()
    # end_date 2026-10-01 is the 1st: the month window is empty
    assert set(b["month_unknown"].split(",")) == {"month_energy_kwh", "month_cost_cad", "month_budget_used_pct",
                                                  "month_projected_cost_cad", "month_avg_daily_cost_cad"}


def test_summary_month_budget_mid_month(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    day = local_day(dt.date(2026, 10, 1))
    SHARED.series[("dev-1", "main_total_energy_in_kwh")] = [(day.start_ms - MIN, 1048.0), (day.end_ms - MIN, 1060.0)]
    dg.materialize([d.asset_twin_summary], resources=res,
                   run_config={"ops": {"asset_twin_summary": {"config": {"end_date": "2026-10-02"}}}})
    b = SHARED.attrs["asset-B"]
    cost = RateD().cost(12.0, days=1, apply_fixed_charge=True)["total"]
    assert b["month_energy_kwh"] == 12.0 and b["month_cost_cad"] == cost
    assert b["month_projected_cost_cad"] == round(cost * 31, 2)
    # the test model has no monthly_budget, so the percentage is unknown
    assert "month_budget_used_pct" in b["month_unknown"].split(",")


def _month_series(power_w=None, counter=(1048.0, 1060.0)):
    day = local_day(dt.date(2026, 10, 1))
    SHARED.series[("dev-1", "main_total_energy_in_kwh")] = [(day.start_ms - MIN, counter[0]), (day.end_ms - MIN, counter[1])]
    if power_w is not None:
        SHARED.series[("dev-1", "main_total_active_power")] = [(t, power_w) for t in range(day.start_ms, day.end_ms, MIN)]


MONTH_CONFIG = {"ops": {"asset_twin_summary": {"config": {"end_date": "2026-10-02"}}}}


def test_flagged_negative_main_aggregate_leaves_the_month_unknown(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    _month_series(power_w=-500.0, counter=(1060.0, 1048.0))  # counter went backwards, power is negative
    assert dg.materialize([d.asset_twin_summary], resources=res, run_config=MONTH_CONFIG).success
    b = SHARED.attrs["asset-B"]
    assert not {"month_energy_kwh", "month_cost_cad", "month_projected_cost_cad", "month_energy_source"} & set(b)
    assert set(b["month_unknown"].split(",")) == {"month_energy_kwh", "month_cost_cad", "month_budget_used_pct",
                                                  "month_projected_cost_cad", "month_avg_daily_cost_cad"}


def test_month_budget_percentage_end_to_end(tmp_path):
    res = weather_resources(tmp_path)
    model = Path(res["asset_model_file"].path)
    model.write_text(model.read_text().replace("longitude: -72.58", 'longitude: -72.58, monthly_budget: "150"'))
    dg.materialize([d.thingsflow_asset_model], resources=res)
    _month_series()
    assert dg.materialize([d.asset_twin_summary], resources=res, run_config=MONTH_CONFIG).success
    b = SHARED.attrs["asset-B"]
    cost = RateD().cost(12.0, days=1, apply_fixed_charge=True)["total"]
    assert b["month_cost_cad"] == cost
    assert b["month_budget_used_pct"] == round(100 * cost / 150, 1)
    assert b["month_energy_source"] == "counter"


TORONTO = ZoneInfo("America/Toronto")
TODAY = local_day(dt.date(2026, 10, 1))
NOON = dt.datetime(2026, 10, 1, 12, tzinfo=TORONTO)
NOON_MS = int(NOON.timestamp() * 1000)


def snapshot_config(now: str):
    return {"ops": {"today_snapshot": {"config": {"now": now}}}}


def seed_today():
    """Yesterday (2026-09-30) as in seed(); today 2 kW until noon with a 5 kW minute at 10:00."""
    seed()
    dev = "dev-1"
    peak_ts = int(dt.datetime(2026, 10, 1, 10, tzinfo=TORONTO).timestamp() * 1000)
    SHARED.series[(dev, "main_total_active_power")] += [
        (t, 5000.0 if t == peak_ts else 2000.0) for t in range(TODAY.start_ms, NOON_MS, MIN)
    ]
    SHARED.series[(dev, "main_total_energy_in_kwh")].append((NOON_MS - MIN, 1054.0))
    SHARED.series[(dev, "heating_active_power")] += [(t, 1000.0) for t in range(TODAY.start_ms, NOON_MS, MIN)]
    SHARED.series[(dev, "heating_energy_in_kwh")].append((NOON_MS - MIN, 127.0))
    return peak_ts


def test_today_snapshot_writes_today_and_yesterday_on_the_building(tmp_path):
    from gate_cloud.twin import day_cost

    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    peak_ts = seed_today()
    result = dg.materialize([d.today_snapshot], resources=res, run_config=snapshot_config(NOON.isoformat()))
    assert result.success
    b = SHARED.attrs["asset-B"]
    assert None not in b.values()
    assert b["today_energy_kwh"] == 6.0  # counter 1048 at midnight -> 1054 at noon
    assert b["today_cost_cad"] == day_cost(6.0, RateD())
    assert b["today_peak_w"] == 5000.0 and b["today_peak_at"] == peak_ts
    assert b["yesterday_energy_kwh"] == 48.0
    assert b["yesterday_cost_cad"] == day_cost(48.0, RateD())
    assert b["yesterday_peak_w"] == 2000.0
    assert b["yesterday_same_time_kwh"] == 24.0  # no counter near yesterday noon: 2 kW x 12 h integrated
    assert b["today_updated_at"] == NOON_MS
    assert b["today_unknown"] == ""
    heating = SHARED.attrs["asset-heating"]
    assert None not in heating.values()
    assert heating["today_energy_kwh"] == 3.0 and heating["today_cost_cad"] > 0
    assert heating["today_unknown"] == ""


def test_today_snapshot_just_after_midnight_lists_unknowns(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    SHARED.series.clear()
    result = dg.materialize([d.today_snapshot], resources=res,
                            run_config=snapshot_config("2026-10-01T00:05:00-04:00"))
    assert result.success
    b = SHARED.attrs["asset-B"]
    assert None not in b.values()
    assert {"today_energy_kwh", "today_cost_cad", "today_peak_w", "today_peak_at",
            "yesterday_energy_kwh"} <= set(b["today_unknown"].split(","))
    assert "today_energy_kwh" not in b
    assert set(SHARED.attrs["asset-heating"]["today_unknown"].split(",")) == {"today_energy_kwh", "today_cost_cad"}


def test_today_snapshot_leaves_a_flagged_aggregate_unknown(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    SHARED.series.clear()
    SHARED.series[("dev-1", "main_total_active_power")] = [(t, -500.0) for t in range(TODAY.start_ms, NOON_MS, MIN)]
    SHARED.series[("dev-1", "heating_active_power")] = [(t, 1000.0) for t in range(TODAY.start_ms, NOON_MS, MIN)]
    assert dg.materialize([d.today_snapshot], resources=res, run_config=snapshot_config(NOON.isoformat())).success
    b = SHARED.attrs["asset-B"]
    assert {"today_energy_kwh", "today_cost_cad"} <= set(b["today_unknown"].split(","))
    heating = SHARED.attrs["asset-heating"]
    assert heating["today_energy_kwh"] == 12.0
    assert heating["today_unknown"] == "today_cost_cad"


def test_summary_writes_nightly_analytics(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    seed()  # 2026-09-30: main_total 2 kW, counter 1000 -> 1048
    weather = SHARED.ensure_device(d.WEATHER_DEVICE, "weather", "Weather")
    SHARED.series[(weather, "temperature_c")] = [(t, 4.0) for t in range(DAY.start_ms, DAY.end_ms, 3_600_000)]
    calls = recorded_reads()
    assert dg.materialize([d.asset_twin_summary], resources=res, run_config=MONTH_CONFIG).success
    b = SHARED.attrs["asset-B"]
    assert None not in b.values()
    heat = b["analytics_heatmap"]
    assert len(heat["values"]) == 7 and all(len(row) == 24 for row in heat["values"])
    assert heat["values"][2][12] == 2.0  # 2026-09-30 is a Wednesday, 2 kW
    assert b["analytics_scatter"] == [{"date": "2026-09-30", "kwh": 48.0, "temp_mean_c": 4.0}]
    assert "analytics_updated_at" in b and b["analytics_unknown"] == ""
    assert b["month_days_left"] == 30  # end_date 2026-10-02: one day elapsed of 31
    assert "month_avg_daily_cost_cad" in b["month_unknown"].split(",")  # no October counter seeded
    assert (("main_total_active_power",), 3_600_000, "AVG") in calls
    heating = SHARED.attrs["asset-heating"]
    assert heating["twin_7d_energy_kwh"] == 24.0 and heating["twin_7d_cost_cad"] > 0
    assert None not in heating.values()


def test_summary_month_average_daily_cost(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    _month_series()
    assert dg.materialize([d.asset_twin_summary], resources=res, run_config=MONTH_CONFIG).success
    b = SHARED.attrs["asset-B"]
    assert b["month_avg_daily_cost_cad"] == b["month_cost_cad"]  # one elapsed day
    assert b["month_days_left"] == 30
