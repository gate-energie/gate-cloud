"""The Dagster wiring: the asset materialises, the sensor fires only on change."""
import datetime as dt
import json
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import dagster as dg
import pytest
import yaml

from gate_cloud import definitions as d
from gate_cloud.weather import OpenMeteo
from tests.fakes import FakeThingsFlow, circuit_map

MODEL_YAML = """
building: {name: B}
panel: {name: P}
asset_types: {Building: "", Electrical Panel: "", HVAC: ""}
circuits: {heating: {type: HVAC}}
"""


class FakeThingsFlowResource(d.ThingsFlowResource):
    """Same resource, with the session swapped for the in-memory fake."""

    def session(self):
        return SHARED


SHARED = FakeThingsFlow()


def resources(tmp_path, circuits):
    model = tmp_path / "asset_model.yaml"
    model.write_text(MODEL_YAML)
    SHARED.__init__(device_attributes={"circuit_map": circuits})
    return {
        "thingsflow": FakeThingsFlowResource(
            url="x", username="u", password="p", monitor_device_id="dev-1", ingest_url="http://ingest"
        ),
        "asset_model_file": d.AssetModelFile(path=str(model)),
        "dashboard_file": d.DashboardFile(path=""),
    }


def test_definitions_load():
    d.defs.resolve_all_job_defs()


def test_asset_materialises_with_counts(tmp_path):
    result = dg.materialize([d.thingsflow_asset_model], resources=resources(tmp_path, circuit_map("heating")))
    assert result.success
    metadata = result.asset_materializations_for_node("thingsflow_asset_model")[0].metadata
    assert metadata["assets"].value == 3
    assert metadata["created_assets"].value == ["B", "P", "heating"]


def test_circuit_map_serialised_as_json_string_is_accepted(tmp_path):
    result = dg.materialize(
        [d.thingsflow_asset_model], resources=resources(tmp_path, json.dumps(circuit_map("heating")))
    )
    assert result.success


def test_sensor_requests_a_run_only_when_inputs_change(tmp_path):
    res = resources(tmp_path, circuit_map("heating"))
    first = d.asset_model_inputs_changed(dg.build_sensor_context(resources=res))
    assert isinstance(first, dg.RunRequest)

    unchanged = d.asset_model_inputs_changed(dg.build_sensor_context(cursor=first.run_key, resources=res))
    assert isinstance(unchanged, dg.SkipReason)

    SHARED.device_attributes["circuit_map"] = circuit_map("heating", "lights")
    changed = d.asset_model_inputs_changed(dg.build_sensor_context(cursor=first.run_key, resources=res))
    assert isinstance(changed, dg.RunRequest) and changed.run_key != first.run_key


MODEL_WITH_SITE = MODEL_YAML.replace(
    "building: {name: B}", "building: {name: B, attributes: {latitude: 46.35, longitude: -72.58}}"
)


class FakeWeather(d.WeatherResource):
    def client(self):
        class C(OpenMeteo):
            def hourly(self, latitude, longitude, start, end, today):
                hours = int((end - start).total_seconds() // 3600)
                return [
                    {"ts": int(start.timestamp() * 1000) + h * 3_600_000, "values": {"temperature_c": 1.0}}
                    for h in range(hours)
                ]

        return C("f", "a")


def weather_resources(tmp_path):
    res = resources(tmp_path, circuit_map("heating"))
    (tmp_path / "asset_model.yaml").write_text(MODEL_WITH_SITE)
    res["weather"] = FakeWeather()
    return res


def test_weather_partition_ingests_one_point_per_hour_to_the_weather_device(tmp_path):
    res = weather_resources(tmp_path)
    dg.materialize([d.thingsflow_asset_model], resources=res)
    result = dg.materialize([d.weather_observations], partition_key="2026-09-30-10:00", resources=res)
    assert result.success
    url, jwt, points = SHARED.ingested[-1]
    assert url == "http://ingest"
    assert jwt == "jwt-device-GATE Weather" and len(points) == 1
    start = dt.datetime(2026, 9, 30, 10, tzinfo=ZoneInfo("America/Toronto"))
    assert points[0]["ts"] == int(start.timestamp() * 1000)
    assert ("asset-B", "device-GATE Weather", "Contains") in SHARED.relations


def test_weather_fails_clearly_without_building_in_thingsflow(tmp_path):
    res = weather_resources(tmp_path)  # the asset-model sync has not run
    context = dg.build_asset_context(partition_key="2026-09-30-10:00")
    with pytest.raises(dg.Failure, match="materialise thingsflow_asset_model first"):
        d.weather_observations(context, res["thingsflow"], res["asset_model_file"], res["weather"])
    assert d.WEATHER_DEVICE not in SHARED.devices_by_name  # nothing created before the check


def test_asset_model_sync_does_not_need_the_ingest_url(tmp_path):
    res = resources(tmp_path, circuit_map("heating"))
    res["thingsflow"] = FakeThingsFlowResource(url="x", username="u", password="p", monitor_device_id="dev-1")
    assert dg.materialize([d.thingsflow_asset_model], resources=res).success


def test_weather_fails_clearly_without_ingest_url(tmp_path):
    res = weather_resources(tmp_path)
    res["thingsflow"] = FakeThingsFlowResource(url="x", username="u", password="p", monitor_device_id="dev-1")
    dg.materialize([d.thingsflow_asset_model], resources=res)
    context = dg.build_asset_context(partition_key="2026-09-30-10:00")
    with pytest.raises(dg.Failure, match="THINGSFLOW_INGEST_URL is not set"):
        d.weather_observations(context, res["thingsflow"], res["asset_model_file"], res["weather"])
    assert SHARED.ingested == []


def test_site_requires_latitude_and_longitude():
    assert d.site(yaml.safe_load(MODEL_WITH_SITE)) == (46.35, -72.58)
    with pytest.raises(dg.Failure, match="needs latitude and longitude"):
        d.site(yaml.safe_load(MODEL_YAML))


def test_every_schedule_starts_running():
    # Nobody turns schedules on by hand after a deploy (0.1.x shipped the
    # 06:00 sync STOPPED and it never ran in prod).
    for schedule in d.defs.schedules:
        assert schedule.default_status == dg.DefaultScheduleStatus.RUNNING, schedule.name


TEMPLATE_DIR = Path(__file__).parent.parent / "charts" / "gate-cloud" / "files" / "dashboard"


def dashboard_resources(tmp_path):
    res = weather_resources(tmp_path)
    template = tmp_path / "dashboard"
    shutil.copytree(TEMPLATE_DIR, template)
    res["dashboard_file"] = d.DashboardFile(path=str(template))
    dg.materialize([d.thingsflow_asset_model], resources=res)
    SHARED.ensure_device(d.WEATHER_DEVICE, "weather", "Weather")
    return res


def _action(result):
    return result.asset_materializations_for_node("thingsflow_dashboard")[0].metadata["action"].value


def _template_json(res):
    return Path(res["dashboard_file"].path) / "dashboard.json"


def test_dashboard_is_created_then_left_alone_then_updated(tmp_path):
    res = dashboard_resources(tmp_path)
    first = dg.materialize([d.thingsflow_dashboard], resources=res)
    assert first.success and _action(first) == "created"
    assert SHARED.dashboard_writes == 1
    dashboard_id, configuration = SHARED.dashboard("GATE \u2014 Operaci\u00f3n")
    assert "${FILE:" not in json.dumps(configuration)

    second = dg.materialize([d.thingsflow_dashboard], resources=res)
    assert _action(second) == "unchanged" and SHARED.dashboard_writes == 1

    widget = Path(res["dashboard_file"].path) / "widgets" / "power_flow.js"
    widget.write_text(widget.read_text() + "\n// edited\n")
    third = dg.materialize([d.thingsflow_dashboard], resources=res)
    assert _action(third) == "updated" and SHARED.dashboard_writes == 2
    assert SHARED.dashboard("GATE \u2014 Operaci\u00f3n")[0] == dashboard_id
    assert "// edited" in json.dumps(SHARED.dashboard("GATE \u2014 Operaci\u00f3n")[1])


def test_dashboard_needs_the_weather_device(tmp_path):
    res = dashboard_resources(tmp_path)
    SHARED.devices_by_name.pop(d.WEATHER_DEVICE)
    with pytest.raises(dg.Failure, match="weather_observations"):
        d.thingsflow_dashboard(res["thingsflow"], res["asset_model_file"], res["dashboard_file"])
    assert SHARED.dashboard_writes == 0


def test_dashboard_needs_its_path(tmp_path):
    res = dashboard_resources(tmp_path)
    res["dashboard_file"] = d.DashboardFile(path="")
    with pytest.raises(dg.Failure, match="GATE_DASHBOARD_PATH is not set"):
        d.thingsflow_dashboard(res["thingsflow"], res["asset_model_file"], res["dashboard_file"])


def test_dashboard_with_a_missing_directory_fails_cleanly(tmp_path):
    res = dashboard_resources(tmp_path)
    res["dashboard_file"] = d.DashboardFile(path=str(tmp_path / "missing"))
    with pytest.raises(dg.Failure, match="dashboard template"):
        d.thingsflow_dashboard(res["thingsflow"], res["asset_model_file"], res["dashboard_file"])
    assert SHARED.dashboard_writes == 0


def test_asset_model_sync_works_without_the_dashboard_path(tmp_path):
    res = resources(tmp_path, circuit_map("heating"))
    assert dg.materialize([d.thingsflow_asset_model], resources=res).success
    assert isinstance(d.asset_model_inputs_changed(dg.build_sensor_context(resources=res)), dg.RunRequest)


def test_dashboard_digest_covers_dashboard_json_and_referenced_files_only(tmp_path):
    shutil.copytree(TEMPLATE_DIR, tmp_path / "dashboard")
    file = d.DashboardFile(path=str(tmp_path / "dashboard"))
    first = file.digest()
    assert file.digest() == first
    (tmp_path / "dashboard" / "tests" / "lib.test.mjs").write_text("// not part of the template\n")
    (tmp_path / "dashboard" / "notes.txt").write_text("unreferenced\n")
    assert file.digest() == first
    theme = tmp_path / "dashboard" / "theme.css"
    theme.write_text(theme.read_text() + "\n")
    assert file.digest() != first


def test_sensor_run_key_follows_the_dashboard_template(tmp_path):
    res = dashboard_resources(tmp_path)
    first = d.asset_model_inputs_changed(dg.build_sensor_context(resources=res))
    same = d.asset_model_inputs_changed(dg.build_sensor_context(resources=res))
    assert first.run_key == same.run_key
    _template_json(res).write_text(_template_json(res).read_text() + "\n")
    changed = d.asset_model_inputs_changed(dg.build_sensor_context(cursor=first.run_key, resources=res))
    assert isinstance(changed, dg.RunRequest) and changed.run_key != first.run_key


def test_sensor_run_key_follows_a_widget_file(tmp_path):
    res = dashboard_resources(tmp_path)
    first = d.asset_model_inputs_changed(dg.build_sensor_context(resources=res))
    widget = Path(res["dashboard_file"].path) / "widgets" / "heatmap.js"
    widget.write_text(widget.read_text() + "\n")
    changed = d.asset_model_inputs_changed(dg.build_sensor_context(cursor=first.run_key, resources=res))
    assert isinstance(changed, dg.RunRequest) and changed.run_key != first.run_key


def test_sensor_skips_a_missing_dashboard_template_and_still_triggers(tmp_path):
    res = resources(tmp_path, circuit_map("heating"))
    res["dashboard_file"] = d.DashboardFile(path=str(tmp_path / "missing"))
    assert isinstance(d.asset_model_inputs_changed(dg.build_sensor_context(resources=res)), dg.RunRequest)


def test_sensor_skips_a_broken_file_reference_and_still_triggers(tmp_path):
    res = dashboard_resources(tmp_path)
    (Path(res["dashboard_file"].path) / "widgets" / "heatmap.js").unlink()
    assert isinstance(d.asset_model_inputs_changed(dg.build_sensor_context(resources=res)), dg.RunRequest)


def test_dashboard_template_errors_become_failures(tmp_path):
    res = dashboard_resources(tmp_path)
    template = json.loads(_template_json(res).read_text())
    template["configuration"]["entityAliases"]["monitor"]["filter"]["singleEntity"]["id"] = "${UNKNOWN_ID}"
    _template_json(res).write_text(json.dumps(template))
    with pytest.raises(dg.Failure, match="UNKNOWN_ID"):
        d.thingsflow_dashboard(res["thingsflow"], res["asset_model_file"], res["dashboard_file"])
    assert SHARED.dashboard_writes == 0


FORECAST = {"days": [{"date": f"2026-10-0{i}", "tmin": 1.0, "tmax": 9.0, "code": 3} for i in (1, 2, 3)],
            "hours": [{"ts": 1, "temp": 4.0, "code": 3}]}


class ForecastWeather(FakeWeather):
    def client(self):
        class C(OpenMeteo):
            def forecast(self, latitude, longitude, now):
                assert (latitude, longitude) == (46.35, -72.58)
                return FORECAST

        return C("f", "a")


def test_weather_forecast_is_saved_on_the_weather_device(tmp_path):
    res = weather_resources(tmp_path)
    res["weather"] = ForecastWeather()
    SHARED.ensure_device(d.WEATHER_DEVICE, "weather", "Weather")
    result = dg.materialize([d.weather_forecast], resources=res)
    assert result.success
    attrs = SHARED.attrs["device-GATE Weather"]
    assert len(attrs["forecast"]["days"]) == 3
    assert isinstance(attrs["forecast_updated_at"], int)
    assert attrs["forecast_unknown"] == ""


def test_weather_forecast_needs_the_weather_device(tmp_path):
    res = weather_resources(tmp_path)
    res["weather"] = ForecastWeather()
    with pytest.raises(dg.Failure, match="weather_observations"):
        d.weather_forecast(res["thingsflow"], res["asset_model_file"], res["weather"])
    assert SHARED.attrs == {}
