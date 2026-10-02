"""Dashboard template rendering. Pure: dicts in, dicts out."""
import copy
import json
import zlib
from pathlib import Path

import pytest

from gate_cloud.dashboard import PALETTE, CircuitSeries, TemplateError, render, templatize, unsupported

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


REAL_KEYS = ["heating", "heating_storage", "air_conditioner", "ventilation", "air_exchanger", "water_heater",
             "stove", "dishwasher", "range_hood", "refrigerator", "lights", "outlet", "plug_27", "plug_29",
             "plug_31_33", "plug_36_38"]


def _colours(keys):
    out = render(TEMPLATE, "dev-1", "wx-1", [CircuitSeries(k, k) for k in keys], ["HVAC"])
    return {k["label"]: k["color"] for k in out["configuration"]["widgets"]["w1"]["config"]["datasources"][0]["dataKeys"]}


def test_palette_has_no_repeats():
    assert len(set(PALETTE)) == len(PALETTE) == 20


def test_real_circuits_get_distinct_colours():
    assert len(set(_colours(REAL_KEYS).values())) == 16


def test_colours_of_16_real_circuits_survive_a_17th_sorting_first():
    before = _colours(REAL_KEYS)
    used = {PALETTE.index(c) for c in before.values()}
    new = next(k for k in (f"aaa_new{i}" for i in range(100))
               if zlib.crc32(k.encode()) % len(PALETTE) not in used)  # preferred slot is free
    after = _colours(REAL_KEYS + [new])
    assert new < min(REAL_KEYS)
    assert {k: after[k] for k in REAL_KEYS} == before
    assert after[new] not in before.values()


def test_duplicate_circuit_keys_and_empty_ids_are_rejected():
    with pytest.raises(TemplateError, match="duplicate"):
        render(TEMPLATE, "dev-1", "wx-1", [CircuitSeries("a", "A"), CircuitSeries("a", "B")], [])
    with pytest.raises(TemplateError):
        templatize(rendered(), "", "wx-1")


def test_entity_label_on_single_entity_alias_is_reported():
    bad = copy.deepcopy(rendered())
    bad["configuration"]["widgets"]["w5"] = {"typeFullFqn": "system.cards.value_card", "config": {"title": "t", "datasources": [
        {"type": "entity", "entityAliasId": "weather", "dataKeys": [{"label": "${entityLabel}"}]}]}}
    assert any("w5" in p for p in unsupported(bad))


REAL = json.loads((Path(__file__).resolve().parents[1] / "charts/gate-cloud/files/dashboard.json").read_text(encoding="utf-8"))


def test_real_template_renders_and_is_supported():
    out = render(REAL, "dev-1", "wx-1", CIRCUITS, ["HVAC", "Lighting"])
    assert out["title"] == "GATE — Operación"
    states = out["configuration"]["states"]
    assert set(states) == {"operation", "circuits", "weather"} and states["operation"]["root"] is True
    assert unsupported(out) == []
    assert templatize(out, "dev-1", "wx-1") == REAL


def test_every_layout_widget_exists_and_fits_the_grid():
    conf = REAL["configuration"]
    for state in conf["states"].values():
        for wid, pos in state["layouts"]["main"]["widgets"].items():
            assert wid in conf["widgets"]
            assert pos["col"] + pos["sizeX"] <= 24


def test_widgets_of_a_state_do_not_overlap_and_nav_headers_are_visible():
    conf = REAL["configuration"]
    for name, state in conf["states"].items():
        cells = {}
        for wid, pos in state["layouts"]["main"]["widgets"].items():
            for r in range(pos["row"], pos["row"] + pos["sizeY"]):
                for c in range(pos["col"], pos["col"] + pos["sizeX"]):
                    assert (r, c) not in cells, f"{name}: {wid} overlaps {cells[(r, c)]}"
                    cells[(r, c)] = wid
        navs = [conf["widgets"][wid] for wid in state["layouts"]["main"]["widgets"]
                if conf["widgets"][wid]["typeFullFqn"] == "system.cards.markdown_card"]
        assert len(navs) == 1
        assert navs[0]["config"]["showTitle"] is True
        assert len(navs[0]["config"]["actions"]["headerButton"]) == 2
    for widget in conf["widgets"].values():
        if widget["typeFullFqn"] == "system.cards.value_card":
            assert widget["config"]["settings"]["showLabel"] is False
