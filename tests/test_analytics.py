"""Daily metrics and the 30-day summary, end to end against the fake."""
import datetime as dt
from zoneinfo import ZoneInfo

import dagster as dg

from gate_cloud import definitions as d
from gate_cloud.analytics import MINUTE_MS, QUARTER_MS
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
    assert attrs["twin_health_score"] is None
    assert "twin_updated_at" in attrs


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
