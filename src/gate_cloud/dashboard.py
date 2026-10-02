"""GATE dashboard: render the ThingsBoard template into the dashboard
ThingsFlow stores, and turn an exported dashboard back into a template. Pure.

Why a template: the dashboard is designed in ThingsBoard and exported as JSON.
That JSON carries ids and the circuit list of one installation, so it is kept
as a template with gate placeholders (`${MONITOR_DEVICE_ID}`,
`${WEATHER_DEVICE_ID}`, `${CIRCUIT_ASSET_TYPES}`, `${CIRCUIT_POWER_KEYS}`)
that `render` fills from the twin. `templatize` is the inverse. ThingsBoard's
own variables (`${entityName}`) are camelCase and are never touched.

Why these alias rules (ThingsFlow epic thingsflow-xse, issues 6mv, 8fo, l9m):
its websocket serves time-series history only for singleEntity aliases, does
not handle entityList, entityName or *SearchQuery aliases, and returns the
entity id for `${entityName}` on a singleEntity alias. `unsupported` reports
what would silently render empty or show ids, so it fails before publishing.

Colours: a circuit prefers the palette slot given by a stable hash (crc32) of
its key, not its position in the sorted list. Keys are processed in sorted
order and a taken slot is skipped by linear probing to the next free one, so up
to len(PALETTE) circuits get distinct colours. Adding a circuit leaves the
others unchanged unless it takes a slot an existing circuit had reached by
probing. Beyond len(PALETTE) circuits the palette wraps and colours repeat.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import zlib
from dataclasses import dataclass
from typing import Any

PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#ff8fab", "#469990",
    "#9a6324", "#800000", "#808000", "#000075", "#a9a9a9",
    "#ffe119", "#00bfa5", "#d2b48c", "#2f4f4f", "#7c4dff",
]
TIMESERIES_FQNS = {"system.time_series_chart"}
FORBIDDEN = {"entityList", "entityName", "relationsQuery", "assetSearchQuery",
             "deviceSearchQuery", "entityViewSearchQuery"}
CIRCUITS_SOURCE = "gate:circuits"
ASSET_TYPES_PLACEHOLDER = "${CIRCUIT_ASSET_TYPES}"
POWER_KEYS_PLACEHOLDER = "${CIRCUIT_POWER_KEYS}"
BUILDING_TYPES = ["Building"]
_GATE_PLACEHOLDER = re.compile(r"\$\{[A-Z][A-Z0-9_]*\}")


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class CircuitSeries:
    key: str
    label: str


def colours(keys: list[str]) -> dict[str, str]:
    taken: set[int] = set()
    out = {}
    for key in sorted(keys):
        slot = zlib.crc32(key.encode()) % len(PALETTE)
        if len(taken) >= len(PALETTE):
            taken.clear()  # palette exhausted: wrap around and reuse
        while slot in taken:
            slot = (slot + 1) % len(PALETTE)
        taken.add(slot)
        out[key] = PALETTE[slot]
    return out


def _data_key(series: CircuitSeries, color: str) -> dict:
    return {"name": f"{series.key}_active_power", "type": "timeseries", "label": series.label,
            "color": color, "units": "W", "decimals": 0, "settings": {}}


def _map(node: Any, fn) -> Any:
    """Rebuild `node` bottom-up, letting `fn` replace any dict, list or string."""
    if isinstance(node, dict):
        node = {k: _map(v, fn) for k, v in node.items()}
    elif isinstance(node, list):
        node = [_map(v, fn) for v in node]
    return fn(node)


def render(template: dict, monitor_id: str, weather_id: str,
           circuits: list[CircuitSeries], asset_types: list[str]) -> dict:
    names = [c.key for c in circuits]
    if len(set(names)) != len(names):
        raise TemplateError("duplicate circuit keys")
    palette = colours(names)
    keys = [_data_key(c, palette[c.key]) for c in sorted(circuits, key=lambda c: c.key)]

    def fill(node: Any) -> Any:
        if node == ASSET_TYPES_PLACEHOLDER:
            return sorted(asset_types)
        if isinstance(node, dict) and node.get("name") == CIRCUITS_SOURCE \
                and node.get("dataKeys") == POWER_KEYS_PLACEHOLDER:
            return {**node, "dataKeys": copy.deepcopy(keys)}
        if isinstance(node, str):
            return node.replace("${MONITOR_DEVICE_ID}", monitor_id).replace("${WEATHER_DEVICE_ID}", weather_id)
        return node

    out = _map(copy.deepcopy(template), fill)
    left = sorted(set(_GATE_PLACEHOLDER.findall(json.dumps(out, ensure_ascii=False))))
    if left:
        raise TemplateError(f"unfilled placeholders: {', '.join(left)}")
    return out


def templatize(dashboard: dict, monitor_id: str, weather_id: str) -> dict:
    if not monitor_id or not weather_id:
        raise TemplateError("monitor and weather ids must not be empty")
    def back(node: Any) -> Any:
        if isinstance(node, str):
            return node.replace(monitor_id, "${MONITOR_DEVICE_ID}").replace(weather_id, "${WEATHER_DEVICE_ID}")
        if isinstance(node, dict):
            if node.get("name") == CIRCUITS_SOURCE and "dataKeys" in node:
                node = {**node, "dataKeys": POWER_KEYS_PLACEHOLDER}
            if isinstance(node.get("assetTypes"), list) and node["assetTypes"] != BUILDING_TYPES:
                node = {**node, "assetTypes": ASSET_TYPES_PLACEHOLDER}
        return node

    return _map(copy.deepcopy(dashboard), back)


def unsupported(dashboard: dict) -> list[str]:
    config = dashboard.get("configuration", {})
    aliases = config.get("entityAliases", {})
    problems = []
    alias_type = {}
    for alias_id, alias in aliases.items():
        kind = alias.get("filter", {}).get("type")
        alias_type[alias_id] = kind
        if kind in FORBIDDEN:
            problems.append(f"alias {alias_id!r}: type {kind} is not served by ThingsFlow")
    for widget_id, widget in config.get("widgets", {}).items():
        kinds = {alias_type.get(ds.get("entityAliasId"))
                 for ds in widget.get("config", {}).get("datasources", [])}
        if widget.get("typeFullFqn") in TIMESERIES_FQNS and kinds - {"singleEntity"}:
            problems.append(f"widget {widget_id!r}: time-series widget needs a singleEntity alias")
        text = json.dumps(widget.get("config", {}), ensure_ascii=False)
        if "singleEntity" in kinds and ("${entityName}" in text or "${entityLabel}" in text):
            problems.append(f"widget {widget_id!r}: ${{entityName}}/${{entityLabel}} on a singleEntity alias shows the id")
    return problems


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m gate_cloud.dashboard")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("templatize", help="turn a dashboard export into a template on stdout")
    cmd.add_argument("export")
    cmd.add_argument("monitor_id")
    cmd.add_argument("weather_id")
    args = parser.parse_args(argv)
    with open(args.export, encoding="utf-8") as f:
        template = templatize(json.load(f), args.monitor_id, args.weather_id)
    json.dump(template, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
