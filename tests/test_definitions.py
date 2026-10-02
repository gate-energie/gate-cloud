"""The Dagster wiring: the asset materialises, the sensor fires only on change."""
import datetime as dt
import json
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


def test_site_requires_latitude_and_longitude():
    assert d.site(yaml.safe_load(MODEL_WITH_SITE)) == (46.35, -72.58)
    with pytest.raises(dg.Failure, match="needs latitude and longitude"):
        d.site(yaml.safe_load(MODEL_YAML))
