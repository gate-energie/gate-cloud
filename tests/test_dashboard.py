"""Dashboard template rendering. Pure: dicts in, dicts out."""
import copy
import json
import re
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from gate_cloud.dashboard import (PALETTE, CircuitSeries, TemplateError, load_template, main, referenced_files, render,
                                  templatize, unsupported)

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


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "charts/gate-cloud/files/dashboard"
RAW = json.loads((TEMPLATE_DIR / "dashboard.json").read_text(encoding="utf-8"))
REAL_CIRCUITS = [CircuitSeries(k, k.replace("_", " ").title()) for k in REAL_KEYS]
STATES = {"overview": "Overview", "analytics": "Analytics", "assets": "Assets"}
CARDS = ["power_flow", "live_telemetry", "grid_phases", "today_energy", "daily_bars", "weather", "budget",
         "circuit_cost", "heatmap", "scatter", "breakdown", "asset_cards"]
# What each card reads, by alias: (key, type). Monitor keys are time series (singleEntity serves no
# attributes); building and circuit values are attributes; label/type are entity fields.
MONITOR, BUILDING, CIRC, WEATHER = "monitor", "building", "circuits", "weather"
CARD_KEYS = {
    "power_flow": {(MONITOR, "main_total_active_power", "timeseries"), (BUILDING, "today_energy_kwh", "attribute")},
    "live_telemetry": {(MONITOR, k, "timeseries") for k in ("main_total_active_power", "main_phase_1_voltage",
                                                            "main_phase_2_voltage", "main_total_current")}
    | {(BUILDING, k, "attribute") for k in ("today_peak_w", "today_energy_kwh")},
    "grid_phases": {(MONITOR, k, "timeseries") for k in ("main_phase_1_active_power", "main_phase_2_active_power",
                                                         "main_phase_1_voltage", "main_phase_2_voltage",
                                                         "main_total_current")}
    | {(BUILDING, k, "attribute") for k in ("today_peak_w", "yesterday_peak_w")},
    "today_energy": {(BUILDING, k, "attribute") for k in ("today_energy_kwh", "today_cost_cad", "yesterday_same_time_kwh")}
    | {(CIRC, "label", "entityField"), (CIRC, "today_energy_kwh", "attribute")},
    "daily_bars": {(BUILDING, "analytics_scatter", "attribute")},
    "weather": {(WEATHER, k, "timeseries") for k in ("temperature_c", "humidity_pct", "wind_speed_ms")}
    | {(WEATHER, "forecast", "attribute")},
    "budget": {(BUILDING, k, "attribute") for k in ("monthly_budget", "month_cost_cad", "month_projected_cost_cad",
                                                    "month_budget_used_pct", "month_avg_daily_cost_cad",
                                                    "month_days_left", "today_cost_cad", "yesterday_cost_cad")},
    "circuit_cost": {(CIRC, "label", "entityField")}
    | {(CIRC, k, "attribute") for k in ("today_energy_kwh", "today_cost_cad", "twin_7d_energy_kwh", "twin_7d_cost_cad",
                                        "twin_energy_kwh", "twin_cost_cad")},
    "heatmap": {(BUILDING, "analytics_heatmap", "attribute")},
    "scatter": {(BUILDING, "analytics_scatter", "attribute")},
    "breakdown": {(CIRC, "label", "entityField")}
    | {(CIRC, k, "attribute") for k in ("twin_energy_kwh", "twin_energy_fraction_pct", "twin_quality")},
    "asset_cards": {(CIRC, "label", "entityField"), (CIRC, "type", "entityField")}
    | {(CIRC, k, "attribute") for k in ("twin_energy_kwh", "twin_cost_cad", "twin_energy_fraction_pct",
                                        "twin_utilization_pct", "twin_corr_temperature", "twin_corr_humidity",
                                        "today_energy_kwh", "twin_quality", "twin_health_score", "twin_overload",
                                        "rated_power_w")},
}


def real_rendered():
    return render(load_template(TEMPLATE_DIR), "dev-1", "wx-1", REAL_CIRCUITS, ["HVAC", "Lighting"])


def _card_widgets(conf):
    """card name -> widget, for markdown cards whose text function is a widget file."""
    out = {}
    for widget in conf["widgets"].values():
        settings = widget["config"].get("settings", {})
        if widget["typeFullFqn"] == "system.cards.markdown_card" and settings.get("useMarkdownTextFunction"):
            ref = settings["markdownTextFunction"].split("\n")[-1]
            out[ref.removeprefix("${FILE:widgets/").removesuffix(".js}")] = widget
    return out


def _layout(conf, state):
    return conf["states"][state]["layouts"]["main"]


def test_real_template_renders_and_is_supported():
    out = real_rendered()
    assert out["title"] == "GATE — Operación"
    assert unsupported(out) == []
    states = out["configuration"]["states"]
    assert {k: v["name"] for k, v in states.items()} == STATES
    assert [k for k, v in states.items() if v["root"]] == ["overview"]
    assert out["configuration"]["entityAliases"]["circuits"]["filter"]["assetTypes"] == ["HVAC", "Lighting"]


def test_real_template_round_trips_through_templatize():
    files = referenced_files(TEMPLATE_DIR)
    assert "widgets/_lib.js" in files and "theme.css" in files and not any(f.startswith("tests/") for f in files)
    assert templatize(real_rendered(), "dev-1", "wx-1", files) == RAW


def test_every_file_reference_exists_and_custom_cards_start_with_the_library():
    text = json.dumps(RAW, ensure_ascii=False)
    refs = set(re.findall(r"\$\{FILE:([^}]*)\}", text))
    assert refs == {"theme.css", "widgets/_lib.js"} | {f"widgets/{c}.js" for c in CARDS}
    assert all((TEMPLATE_DIR / ref).is_file() for ref in refs)
    assert RAW["configuration"]["settings"]["dashboardCss"] == "${FILE:theme.css}"
    cards = _card_widgets(RAW["configuration"])
    assert sorted(cards) == sorted(CARDS)
    for name, widget in cards.items():
        settings = widget["config"]["settings"]
        assert settings["markdownTextFunction"] == "${FILE:widgets/_lib.js}\n${FILE:widgets/" + name + ".js}"
        assert settings["applyDefaultMarkdownStyle"] is False
        assert widget["config"]["showTitle"] is False


def test_settings_and_grid_follow_the_theme():
    conf = RAW["configuration"]
    settings = conf["settings"]
    assert (settings["stateControllerId"], settings["showTitle"], settings["showDashboardTimewindow"],
            settings["toolbarAlwaysOpen"]) == ("entity", False, True, True)
    for state in STATES:
        grid = _layout(conf, state)["gridSettings"]
        assert (grid["columns"], grid["rowHeight"], grid["margin"], grid["backgroundColor"]) == (24, 50, 10, "#0b1120")
    for widget in conf["widgets"].values():
        cfg = widget["config"]
        assert (cfg["backgroundColor"], cfg["color"], cfg["padding"], cfg["enableFullscreen"]) == \
            ("#111827", "#e5e7eb", "8px", False)


def test_widgets_of_a_state_fit_do_not_overlap_and_each_widget_is_placed_once():
    conf = RAW["configuration"]
    placed = []
    for name in STATES:
        cells = {}
        for wid, pos in _layout(conf, name)["widgets"].items():
            assert wid in conf["widgets"]
            assert pos["col"] + pos["sizeX"] <= 24
            placed.append(wid)
            for r in range(pos["row"], pos["row"] + pos["sizeY"]):
                for c in range(pos["col"], pos["col"] + pos["sizeX"]):
                    assert (r, c) not in cells, f"{name}: {wid} overlaps {cells[(r, c)]}"
                    cells[(r, c)] = wid
    assert sorted(placed) == sorted(conf["widgets"])


def test_layout_places_the_cards_as_designed():
    conf = RAW["configuration"]
    cards = {id(w): name for name, w in _card_widgets(conf).items()}
    expected = {
        "overview": {"nav": (24, 2, 0, 0), "power_flow": (10, 6, 0, 2), "live_telemetry": (7, 6, 10, 2),
                     "grid_phases": (7, 6, 17, 2), "today_energy": (7, 6, 0, 8), "chart": (10, 3, 7, 8),
                     "daily_bars": (10, 3, 7, 11), "weather": (7, 6, 17, 8), "budget": (8, 7, 0, 14),
                     "circuit_cost": (16, 7, 8, 14)},
        "analytics": {"nav": (24, 2, 0, 0), "chart": (24, 7, 0, 2), "heatmap": (12, 7, 0, 9),
                      "scatter": (12, 7, 12, 9), "breakdown": (24, 6, 0, 16)},
        "assets": {"nav": (24, 2, 0, 0), "asset_cards": (24, 12, 0, 2), "chart": (24, 8, 0, 14)},
    }
    for state, want in expected.items():
        got = {}
        for wid, pos in _layout(conf, state)["widgets"].items():
            widget = conf["widgets"][wid]
            name = cards.get(id(widget)) or ("chart" if widget["typeFullFqn"] == "system.time_series_chart" else "nav")
            assert name not in got
            got[name] = (pos["sizeX"], pos["sizeY"], pos["col"], pos["row"])
        assert got == want, state


def test_nav_cards_link_to_the_other_two_states():
    conf = RAW["configuration"]
    for state, title in STATES.items():
        navs = [conf["widgets"][wid] for wid in _layout(conf, state)["widgets"]
                if conf["widgets"][wid]["typeFullFqn"] == "system.cards.markdown_card"
                and not conf["widgets"][wid]["config"]["settings"].get("useMarkdownTextFunction")]
        assert len(navs) == 1
        cfg = navs[0]["config"]
        assert cfg["showTitle"] is True and cfg["title"] == f"GATE · {title}"
        buttons = cfg["actions"]["headerButton"]
        assert [(b["type"], b["targetDashboardStateId"]) for b in buttons] == \
            [("openDashboardState", s) for s in STATES if s != state]


def test_card_and_type_alias_keys_have_label_equal_to_name():
    """Cards read rows by label, and ThingsFlow type aliases need label == name; charts may name series."""
    conf = RAW["configuration"]
    aliases = {k: a["filter"]["type"] for k, a in conf["entityAliases"].items()}
    for widget in conf["widgets"].values():
        card = widget["typeFullFqn"] == "system.cards.markdown_card"
        for ds in widget["config"].get("datasources", []):
            keys = ds["dataKeys"] if isinstance(ds["dataKeys"], list) else []
            for key in keys:
                if key != "${CIRCUIT_POWER_KEYS}" and (card or aliases[ds["entityAliasId"]] in ("assetType", "deviceType")):
                    assert key["label"] == key["name"], key


def test_single_entity_chart_keys_have_human_labels():
    conf = RAW["configuration"]
    aliases = {k: a["filter"]["type"] for k, a in conf["entityAliases"].items()}
    labels = {}
    for widget in conf["widgets"].values():
        if widget["typeFullFqn"] != "system.time_series_chart":
            continue
        for ds in widget["config"]["datasources"]:
            assert aliases[ds["entityAliasId"]] == "singleEntity"
            for key in ds["dataKeys"] if isinstance(ds["dataKeys"], list) else []:
                assert key["label"] != key["name"], key
                labels.setdefault(widget["config"]["title"], []).append(key["label"])
    assert labels == {"CONSUMPTION — 24 HOURS (HOURLY kW)": ["Consumption"],
                      "POWER TIMELINE — 7 DAYS": ["Total", "Phase 1", "Phase 2"]}


def test_every_custom_card_also_carries_the_theme():
    for widget in _card_widgets(RAW["configuration"]).values():
        assert widget["config"]["settings"]["markdownCss"] == "${FILE:theme.css}"
    assert RAW["configuration"]["settings"]["dashboardCss"] == "${FILE:theme.css}"


def test_circuit_keys_must_not_look_like_main_aggregates():
    with pytest.raises(TemplateError, match="main_"):
        render(TEMPLATE, "dev-1", "wx-1", [CircuitSeries("main_total", "Main")], ["HVAC"])


def test_each_card_has_the_keys_it_reads():
    conf = RAW["configuration"]
    for name, widget in _card_widgets(conf).items():
        have = {(ds["entityAliasId"], k["name"], k["type"]) for ds in widget["config"]["datasources"]
                for k in (ds["dataKeys"] if isinstance(ds["dataKeys"], list) else []) if isinstance(k, dict)}
        assert CARD_KEYS[name] <= have, (name, CARD_KEYS[name] - have)
        assert {a for a, *_ in have} == {a for a, *_ in CARD_KEYS[name]}, name


def test_power_flow_reads_total_and_circuits_in_one_monitor_row():
    flow = real_rendered()["configuration"]["widgets"][_card_widgets(RAW["configuration"])["power_flow"]["id"]]
    [circuits] = [ds for ds in flow["config"]["datasources"] if ds.get("name") == "gate:circuits"]
    assert circuits["entityAliasId"] == "monitor"
    names = [k["name"] for k in circuits["dataKeys"]]
    assert names[0] == "main_total_active_power"
    assert names[1:] == sorted(f"{k}_active_power" for k in REAL_KEYS)


def test_time_series_charts():
    conf = RAW["configuration"]
    charts = {}
    for state in STATES:
        for wid in _layout(conf, state)["widgets"]:
            if conf["widgets"][wid]["typeFullFqn"] == "system.time_series_chart":
                charts[state] = conf["widgets"][wid]["config"]
    hourly, timeline, history = charts["overview"], charts["analytics"], charts["assets"]
    [key] = hourly["datasources"][0]["dataKeys"]
    assert key["name"] == "main_total_active_power" and key["settings"]["type"] == "bar"
    assert key["usePostProcessing"] is True and key["postFuncBody"] == "return value / 1000;"
    assert hourly["timewindow"]["realtime"]["timewindowMs"] == 24 * 3_600_000
    assert hourly["timewindow"]["realtime"]["interval"] == 3_600_000
    assert [k["name"] for k in timeline["datasources"][0]["dataKeys"]] == \
        ["main_total_active_power", "main_phase_1_active_power", "main_phase_2_active_power"]
    assert timeline["timewindow"]["history"] == {"historyType": 0, "timewindowMs": 7 * 86_400_000, "interval": 900_000}
    assert history["datasources"][0]["name"] == "gate:circuits"
    assert history["datasources"][0]["dataKeys"] == "${CIRCUIT_POWER_KEYS}"
    assert history["timewindow"]["history"] == {"historyType": 0, "timewindowMs": 7 * 86_400_000, "interval": 3_600_000}
    for chart in charts.values():
        assert chart["useDashboardTimewindow"] is False
        assert all(ds["entityAliasId"] == "monitor" for ds in chart["datasources"])


def test_theme_styles_every_class_the_cards_use():
    css = (TEMPLATE_DIR / "theme.css").read_text(encoding="utf-8")
    used = set()
    for path in (TEMPLATE_DIR / "widgets").glob("*.js"):
        used |= set(re.findall(r"gate-[a-z0-9-]+", path.read_text(encoding="utf-8")))
    missing = sorted(c for c in used if not re.search(r"\." + re.escape(c) + r"(?![a-z0-9-])", css))
    assert missing == []
    for colour in ("#0b1120", "#111827", "#1f2937", "#e5e7eb", "#9ca3af", "#22d3ee", "#3b82f6", "#f59e0b",
                   "#ef4444", "#22c55e", "#a855f7", "#4b5563", "#94a3b8"):
        assert colour in css.lower()
    assert ".unknown" in css


def test_templatize_requires_the_circuits_datasource():
    export = rendered()
    export["configuration"]["widgets"]["w1"]["config"]["datasources"][0]["name"] = "other"
    with pytest.raises(TemplateError, match="gate:circuits"):
        templatize(export, "dev-1", "wx-1")


def test_templatize_requires_the_circuit_asset_types_alias():
    export = rendered()
    export["configuration"]["entityAliases"]["circuits"]["filter"]["assetTypes"] = ["Building"]
    with pytest.raises(TemplateError, match="assetTypes"):
        templatize(export, "dev-1", "wx-1")


def test_templatize_keeps_only_title_and_configuration():
    export = {**rendered(), "id": {"id": "d1"}, "tenantId": {"id": "t"}, "createdTime": 1,
              "assignedCustomers": [{"id": "c"}], "image": "data:...", "mobileHide": True}
    assert set(templatize(export, "dev-1", "wx-1")) == {"title", "configuration"}


def test_templatize_cli_prints_the_template(tmp_path, capsys):
    export = tmp_path / "export.json"
    export.write_text(json.dumps({**rendered(), "id": {"id": "d1"}}), encoding="utf-8")
    main(["templatize", str(export), "dev-1", "wx-1"])
    assert json.loads(capsys.readouterr().out) == TEMPLATE
    done = subprocess.run([sys.executable, "-m", "gate_cloud.dashboard", "templatize", str(export), "dev-1", "wx-1"],
                          capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == TEMPLATE


def test_widget_with_timeseries_key_type_needs_a_single_entity_alias():
    bad = copy.deepcopy(rendered())
    bad["configuration"]["widgets"]["w6"] = {"typeFullFqn": "custom.chart", "type": "timeseries", "config": {"datasources": [
        {"type": "entity", "entityAliasId": "circuits", "dataKeys": []}]}}
    assert any("w6" in p for p in unsupported(bad))


def _dir(tmp_path, dashboard, files):
    (tmp_path / "dashboard.json").write_text(json.dumps(dashboard), encoding="utf-8")
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


FILES = {"widgets/_lib.js": "const lib = 1;", "widgets/a.js": "return lib + 'á';"}
REFS = "${FILE:widgets/_lib.js}\n${FILE:widgets/a.js}"


def _with_card(text):
    out = copy.deepcopy(TEMPLATE)
    out["configuration"]["widgets"]["w2"]["config"]["settings"] = {"markdownTextFunction": text}
    return out


def _rendered_card(text):
    out = rendered()
    out["configuration"]["widgets"]["w2"]["config"]["settings"] = {"markdownTextFunction": text}
    return out


def test_load_template_inlines_every_file_reference(tmp_path):
    loaded = load_template(_dir(tmp_path, _with_card(REFS), FILES))
    assert loaded["configuration"]["widgets"]["w2"]["config"]["settings"]["markdownTextFunction"] == \
        "const lib = 1;\nreturn lib + 'á';"
    assert "${FILE:" not in json.dumps(loaded)


def test_load_template_names_a_missing_file(tmp_path):
    with pytest.raises(TemplateError, match="widgets/nope.js"):
        load_template(_dir(tmp_path, _with_card("${FILE:widgets/nope.js}"), {}))


@pytest.mark.parametrize("rel", ["../x", "widgets/../../x", "/etc/passwd"])
def test_load_template_refuses_paths_outside_the_directory(tmp_path, rel):
    (tmp_path.parent / "x").write_text("secret", encoding="utf-8")
    with pytest.raises(TemplateError, match="x|passwd"):
        load_template(_dir(tmp_path, _with_card("${FILE:" + rel + "}"), {}))


def test_templatize_restores_file_references_longest_first(tmp_path):
    files = {"widgets/_lib.js": "const lib = 1;", "widgets/a.js": "const lib = 1;\nreturn lib;"}
    body = "const lib = 1;\nreturn lib;"
    out = templatize(_rendered_card(body), "dev-1", "wx-1", files)
    assert out["configuration"]["widgets"]["w2"]["config"]["settings"]["markdownTextFunction"] == "${FILE:widgets/a.js}"
    both = "const lib = 1;\n" + "return lib + 'á';"
    out = templatize(_rendered_card(both), "dev-1", "wx-1", FILES)
    assert out["configuration"]["widgets"]["w2"]["config"]["settings"]["markdownTextFunction"] == REFS


def _with_key(alias, key):
    bad = copy.deepcopy(rendered())
    bad["configuration"]["widgets"]["w7"] = {"typeFullFqn": "system.cards.entities_table", "config": {"datasources": [
        {"type": "entity", "entityAliasId": alias, "dataKeys": [key]}]}}
    return unsupported(bad)


def test_attribute_keys_on_type_aliases_must_be_type_attribute():
    for kind in ("SERVER_SCOPE", "SERVER_ATTRIBUTE", "function"):
        assert any("w7" in p for p in _with_key("circuits", {"name": "twin_cost_cad", "label": "twin_cost_cad", "type": kind}))
    for kind in ("attribute", "timeseries", "entityField"):
        assert _with_key("circuits", {"name": "twin_cost_cad", "label": "twin_cost_cad", "type": kind}) == []


def test_data_key_label_must_equal_name_on_type_aliases():
    problems = _with_key("building", {"name": "month_cost_cad", "label": "Costo", "type": "attribute"})
    assert any("w7" in p and "label" in p for p in problems)


def _flow_template():
    out = copy.deepcopy(TEMPLATE)
    out["configuration"]["widgets"]["w8"] = {"typeFullFqn": "system.cards.markdown_card", "config": {"datasources": [
        {"type": "entity", "name": "gate:circuits", "entityAliasId": "monitor", "dataKeys": [
            {"name": "main_total_active_power", "type": "timeseries", "label": "main_total_active_power"},
            "${CIRCUIT_POWER_KEYS}"]}]}}
    return out


def test_power_keys_placeholder_as_a_list_element_expands_in_place():
    out = render(_flow_template(), "dev-1", "wx-1", CIRCUITS, ["HVAC"])
    keys = out["configuration"]["widgets"]["w8"]["config"]["datasources"][0]["dataKeys"]
    assert [(k["name"], k["label"]) for k in keys] == [
        ("main_total_active_power", "main_total_active_power"),
        ("heating_active_power", "Heating"), ("lights_active_power", "Lights")]
    whole = out["configuration"]["widgets"]["w1"]["config"]["datasources"][0]["dataKeys"]
    assert [k["name"] for k in whole] == ["heating_active_power", "lights_active_power"]


def test_templatize_restores_the_power_keys_element_in_place():
    out = render(_flow_template(), "dev-1", "wx-1", CIRCUITS, ["HVAC"])
    assert templatize(out, "dev-1", "wx-1") == _flow_template()
    none = render(_flow_template(), "dev-1", "wx-1", [], ["HVAC"])
    assert templatize(none, "dev-1", "wx-1") == _flow_template()


def test_templatize_cli_reads_only_files_the_template_references(tmp_path, capsys):
    (tmp_path / "t").mkdir()
    tdir = _dir(tmp_path / "t", _with_card(REFS), {**FILES, "tests/x.js": "lib", "widgets/unused.js": "1"})
    export = tmp_path / "export.json"
    export.write_text(json.dumps(_rendered_card("const lib = 1;\nreturn lib + 'á';")), encoding="utf-8")
    main(["templatize", str(export), "dev-1", "wx-1", "--files", str(tdir)])
    got = json.loads(capsys.readouterr().out)
    assert got["configuration"]["widgets"]["w2"]["config"]["settings"]["markdownTextFunction"] == REFS


def test_templatize_cli_fails_on_a_missing_directory(tmp_path):
    export = tmp_path / "export.json"
    export.write_text(json.dumps(rendered()), encoding="utf-8")
    with pytest.raises((TemplateError, SystemExit)):
        main(["templatize", str(export), "dev-1", "wx-1", "--files", str(tmp_path / "nope")])
