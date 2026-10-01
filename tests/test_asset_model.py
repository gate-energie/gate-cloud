"""The asset sync must be safe to repeat and loud when its inputs disagree.

It runs on a schedule and whenever its inputs change, so a second run has to be
a no-op, and a mismatch between the monitor's circuit map and the asset model
has to stop it before it writes anything.
"""
import copy
import pathlib

import pytest
import yaml

from gate_cloud.asset_model import PlanError, apply_plan, build_plan
from tests.fakes import FakeThingsFlow, circuit_map, ref

ROOT = pathlib.Path(__file__).resolve().parents[1]

MODEL = {
    "building": {"name": "B", "attributes": {"monthly_budget": 150.0}},
    "panel": {"name": "P"},
    "asset_types": {"Building": "", "Electrical Panel": "", "HVAC": "", "Lighting": ""},
    "circuits": {"heating": {"type": "HVAC", "label": "Heating"}, "lights": {"type": "Lighting"}},
}
CIRCUITS = circuit_map("heating", "lights")


def apply(plan, api):
    return apply_plan(plan, api, "device-1", ref)


def test_hierarchy_and_circuit_keys():
    plan = build_plan(CIRCUITS, MODEL)
    by_name = {spec.name: spec for spec in plan.assets}
    assert set(by_name) == {"B", "P", "heating", "lights"}
    assert by_name["B"].parent is None
    assert by_name["P"].parent == "B"
    assert by_name["heating"].parent == "P"
    assert by_name["P"].attributes["circuit_key"] == "main_total"
    assert by_name["P"].attributes["main_circuit_keys"] == ["main_phase_1"]
    assert by_name["heating"].attributes["circuit_key"] == "heating"
    assert by_name["lights"].attributes["breaker"] == "3"
    assert plan.device_parent == "P"


def test_every_mismatch_is_reported_at_once():
    model = copy.deepcopy(MODEL)
    del model["circuits"]["lights"]
    model["circuits"]["sauna"] = {"type": "HVAC"}
    model["circuits"]["heating"]["type"] = "Boiler"
    with pytest.raises(PlanError) as err:
        build_plan(CIRCUITS, model)
    message = str(err.value)
    assert "'lights'" in message and "'sauna'" in message and "'Boiler'" in message


def test_missing_circuit_map_says_where_it_comes_from():
    with pytest.raises(PlanError, match="sync_attributes"):
        build_plan([], MODEL)


def test_unknown_ratings_are_not_invented():
    heating = next(s for s in build_plan(CIRCUITS, MODEL).assets if s.name == "heating")
    assert "breaker_amps" not in heating.attributes
    assert "rated_power_w" not in heating.attributes


def test_second_run_writes_no_entities():
    api, plan = FakeThingsFlow(), build_plan(CIRCUITS, MODEL)
    first = apply(plan, api)
    writes, relations = api.entity_writes, set(api.relations)
    second = apply(plan, api)
    assert second.asset_ids == first.asset_ids
    assert (second.created_assets, second.updated_assets, second.created_profiles) == ([], [], [])
    assert api.entity_writes == writes
    assert api.relations == relations


def test_relations_and_monitor_reference():
    api = FakeThingsFlow()
    ids = apply(build_plan(CIRCUITS, MODEL), api).asset_ids
    assert (ids["B"], ids["P"], "Contains") in api.relations
    assert (ids["P"], ids["heating"], "Contains") in api.relations
    assert (ids["P"], "device-1", "Contains") in api.relations
    assert api.attrs[ids["heating"]]["monitor_device_id"] == "device-1"
    assert "monitor_device_id" not in api.attrs[ids["B"]]


def test_label_change_updates_in_place():
    api, plan = FakeThingsFlow(), build_plan(CIRCUITS, MODEL)
    apply(plan, api)
    plan.assets[-1].label = "Ceiling lights"
    report = apply(plan, api)
    assert report.updated_assets == ["lights"]
    assert api.entities["lights"].label == "Ceiling lights"
    assert len(api.entities) == 4


def test_removed_circuit_is_reported_not_deleted():
    api, plan = FakeThingsFlow(), build_plan(CIRCUITS, MODEL)
    apply(plan, api)
    plan.assets = [s for s in plan.assets if s.name != "lights"]
    report = apply(plan, api)
    assert "lights" in api.entities
    assert report.orphans == ["lights"]


def test_null_values_are_not_sent():
    api, plan = FakeThingsFlow(), build_plan(CIRCUITS, MODEL)
    plan.assets[-1].attributes["phase"] = None
    ids = apply(plan, api).asset_ids
    assert "phase" not in api.attrs[ids["lights"]]


def test_shipped_model_is_well_formed():
    """Every circuit type in the shipped model is a declared asset type."""
    model = yaml.safe_load((ROOT / "charts/gate-cloud/files/asset_model.yaml").read_text())
    build_plan(circuit_map(*model["circuits"]), model)
