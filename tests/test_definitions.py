"""The Dagster wiring: the asset materialises, the sensor fires only on change."""
import json

import dagster as dg

from gate_cloud import definitions as d
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
        "thingsflow": FakeThingsFlowResource(url="x", username="u", password="p", monitor_device_id="dev-1"),
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
