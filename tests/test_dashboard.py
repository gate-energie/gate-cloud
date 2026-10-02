"""Dashboard template rendering. Pure: dicts in, dicts out."""
import copy

import pytest

from gate_cloud.dashboard import CircuitSeries, TemplateError, render, templatize, unsupported

TEMPLATE = {
    "title": "GATE — Operación",
    "configuration": {
        "entityAliases": {
            "monitor": {"id": "monitor", "alias": "Monitor", "filter": {"type": "singleEntity", "singleEntity": {"entityType": "DEVICE", "id": "${MONITOR_DEVICE_ID}"}}},
            "weather": {"id": "weather", "alias": "Weather", "filter": {"type": "singleEntity", "singleEntity": {"entityType": "DEVICE", "id": "${WEATHER_DEVICE_ID}"}}},
            "circuits": {"id": "circuits", "alias": "Circuits", "filter": {"type": "assetType", "resolveMultiple": True, "assetTypes": "${CIRCUIT_ASSET_TYPES}"}},
            "building": {"id": "building", "alias": "Building", "filter": {"type": "assetType", "resolveMultiple": True, "assetTypes": ["Building"]}},
        },
        "widgets": {
            "w1": {"typeFullFqn": "system.time_series_chart", "config": {"title": "Potencia por circuito", "datasources": [
                {"type": "entity", "name": "gate:circuits", "entityAliasId": "monitor", "dataKeys": "${CIRCUIT_POWER_KEYS}"}]}},
            "w2": {"typeFullFqn": "system.cards.entities_table", "config": {"title": "Circuitos", "datasources": [
                {"type": "entity", "entityAliasId": "circuits", "dataKeys": []}]}},
        },
    },
}
CIRCUITS = [CircuitSeries("lights", "Lights"), CircuitSeries("heating", "Heating")]


def rendered():
    return render(TEMPLATE, "dev-1", "wx-1", CIRCUITS, ["Lighting", "HVAC"])


def test_render_fills_ids_types_and_one_series_per_circuit():
    out = rendered()
    aliases = out["configuration"]["entityAliases"]
    assert aliases["monitor"]["filter"]["singleEntity"]["id"] == "dev-1"
    assert aliases["weather"]["filter"]["singleEntity"]["id"] == "wx-1"
    assert aliases["circuits"]["filter"]["assetTypes"] == ["HVAC", "Lighting"]
    keys = out["configuration"]["widgets"]["w1"]["config"]["datasources"][0]["dataKeys"]
    assert [(k["name"], k["label"]) for k in keys] == [("heating_active_power", "Heating"), ("lights_active_power", "Lights")]
    assert TEMPLATE["configuration"]["entityAliases"]["monitor"]["filter"]["singleEntity"]["id"] == "${MONITOR_DEVICE_ID}"  # not mutated


def test_colours_are_stable_when_a_circuit_is_added():
    before = {k["name"]: k["color"] for k in rendered()["configuration"]["widgets"]["w1"]["config"]["datasources"][0]["dataKeys"]}
    more = render(TEMPLATE, "dev-1", "wx-1", CIRCUITS + [CircuitSeries("stove", "Stove")], ["HVAC"])
    after = {k["name"]: k["color"] for k in more["configuration"]["widgets"]["w1"]["config"]["datasources"][0]["dataKeys"]}
    assert after["heating_active_power"] == before["heating_active_power"]
    assert "stove_active_power" in after


def test_leftover_placeholder_fails_before_writing():
    bad = copy.deepcopy(TEMPLATE)
    bad["configuration"]["widgets"]["w2"]["config"]["title"] = "${SOMETHING}"
    with pytest.raises(TemplateError, match="SOMETHING"):
        render(bad, "dev-1", "wx-1", CIRCUITS, ["HVAC"])


def test_templatize_round_trips():
    assert templatize(rendered(), "dev-1", "wx-1") == TEMPLATE
    assert render(templatize(rendered(), "dev-1", "wx-1"), "dev-1", "wx-1", CIRCUITS, ["Lighting", "HVAC"]) == rendered()


def test_supported_template_has_no_problems():
    assert unsupported(rendered()) == []


def test_unsupported_aliases_and_bindings_are_reported():
    bad = copy.deepcopy(rendered())
    bad["configuration"]["entityAliases"]["x"] = {"id": "x", "alias": "X", "filter": {"type": "entityList"}}
    bad["configuration"]["widgets"]["w3"] = {"typeFullFqn": "system.time_series_chart", "config": {"title": "t", "datasources": [
        {"type": "entity", "entityAliasId": "circuits", "dataKeys": []}]}}
    bad["configuration"]["widgets"]["w4"] = {"typeFullFqn": "system.cards.value_card", "config": {"title": "${entityName}", "datasources": [
        {"type": "entity", "entityAliasId": "monitor", "dataKeys": []}]}}
    problems = "\n".join(unsupported(bad))
    assert "entityList" in problems and "w3" in problems and "w4" in problems
