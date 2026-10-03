"""GATE dashboard: render the ThingsBoard template into the dashboard
ThingsFlow stores, and turn an exported dashboard back into a template. Pure.

Why a template: the dashboard is designed in ThingsBoard and exported as JSON.
That JSON carries ids and the circuit list of one installation, so it is kept
as a template with gate placeholders (`${MONITOR_DEVICE_ID}`,
`${WEATHER_DEVICE_ID}`, `${CIRCUIT_ASSET_TYPES}`, `${CIRCUIT_POWER_KEYS}`)
that `render` fills from the twin. `${CIRCUIT_POWER_KEYS}` is either the whole
dataKeys of a `gate:circuits` datasource or one element of that list (the other
keys stay). `templatize` is the inverse. ThingsBoard's
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
from pathlib import Path
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
_FILE_REF = re.compile(r"\$\{FILE:([^}]*)\}")
# Attribute-reading keys on assetType/deviceType aliases must be typed "attribute";
# ThingsFlow does not resolve other scopes there.
TYPED_ALIASES = {"assetType", "deviceType"}
KEY_TYPES = {"attribute", "timeseries", "entityField"}


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
    clash = sorted(n for n in names if n.startswith("main_"))
    if clash:
        # templatize tells circuit series from the main aggregate by this prefix
        raise TemplateError(f"circuit keys must not start with 'main_': {', '.join(clash)}")
    palette = colours(names)
    keys = [_data_key(c, palette[c.key]) for c in sorted(circuits, key=lambda c: c.key)]

    def fill(node: Any) -> Any:
        if node == ASSET_TYPES_PLACEHOLDER:
            return sorted(asset_types)
        if isinstance(node, dict) and node.get("name") == CIRCUITS_SOURCE:
            data_keys = node.get("dataKeys")
            if data_keys == POWER_KEYS_PLACEHOLDER:
                return {**node, "dataKeys": copy.deepcopy(keys)}
            if isinstance(data_keys, list) and POWER_KEYS_PLACEHOLDER in data_keys:
                spliced = []
                for key in data_keys:
                    spliced.extend(copy.deepcopy(keys) if key == POWER_KEYS_PLACEHOLDER else [key])
                return {**node, "dataKeys": spliced}
        if isinstance(node, str):
            return node.replace("${MONITOR_DEVICE_ID}", monitor_id).replace("${WEATHER_DEVICE_ID}", weather_id)
        return node

    out = _map(copy.deepcopy(template), fill)
    left = sorted(set(_GATE_PLACEHOLDER.findall(json.dumps(out, ensure_ascii=False))))
    if left:
        raise TemplateError(f"unfilled placeholders: {', '.join(left)}")
    return out


def load_template(directory: Path) -> dict:
    """Read `dashboard.json` from a template directory and inline every
    `${FILE:<relpath>}` inside its strings with that file's UTF-8 text."""
    root = Path(directory).resolve()
    try:
        template = json.loads((root / "dashboard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TemplateError(f"cannot read dashboard.json in {root}: {exc}") from exc

    def read(match: re.Match) -> str:
        rel = match.group(1)
        path = (root / rel).resolve()
        if Path(rel).is_absolute() or not path.is_relative_to(root):
            raise TemplateError(f"file reference {rel!r} escapes the template directory")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TemplateError(f"file reference {rel!r} cannot be read: {exc}") from exc

    out = _map(template, lambda node: _FILE_REF.sub(read, node) if isinstance(node, str) else node)
    if "${FILE:" in json.dumps(out, ensure_ascii=False):
        raise TemplateError("a ${FILE:...} reference survived loading (nested or malformed)")
    return out


def _is_circuit_series(key: Any) -> bool:
    """A rendered circuit series: `<circuit>_active_power` on the monitor, not a main aggregate."""
    name = key.get("name") if isinstance(key, dict) else None
    return isinstance(name, str) and name.endswith("_active_power") and not name.startswith("main_")


def _restore_power_keys(data_keys: Any) -> Any:
    """The whole list becomes the placeholder when it holds only circuit series; otherwise the
    circuit series collapse into one placeholder element where the first one stood (at the end
    when there were none, as with no circuits), keeping every other key in place."""
    if not isinstance(data_keys, list) or all(_is_circuit_series(k) for k in data_keys):
        return POWER_KEYS_PLACEHOLDER
    out, placed = [], False
    for key in data_keys:
        if key == POWER_KEYS_PLACEHOLDER or _is_circuit_series(key):
            if not placed:
                out.append(POWER_KEYS_PLACEHOLDER)
                placed = True
        else:
            out.append(key)
    return out if placed else out + [POWER_KEYS_PLACEHOLDER]


def referenced_files(directory: Path) -> dict[str, str]:
    """The files `dashboard.json` in a template directory references as `${FILE:<relpath>}`,
    by relative path, with their text. Other files in the directory (tests) are left out."""
    root = Path(directory)
    if not root.is_dir():
        raise TemplateError(f"template directory {root} does not exist")
    try:
        raw = (root / "dashboard.json").read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"cannot read dashboard.json in {root}: {exc}") from exc
    # Loading resolves and checks every reference (missing files, paths outside the directory).
    load_template(root)
    return {rel: (root / rel).read_text(encoding="utf-8") for rel in sorted(set(_FILE_REF.findall(raw)))}


def templatize(dashboard: dict, monitor_id: str, weather_id: str,
               files: dict[str, str] | None = None) -> dict:
    if not monitor_id or not weather_id:
        raise TemplateError("monitor and weather ids must not be empty")
    if not isinstance(dashboard.get("configuration"), dict):
        raise TemplateError("the export has no configuration")
    found = {"datasource": False, "asset_types": False}
    # longest first, so a file embedded in a longer body is not split by its shorter part
    by_size = sorted(((rel, text) for rel, text in (files or {}).items() if text),
                     key=lambda item: len(item[1]), reverse=True)

    def back(node: Any) -> Any:
        if isinstance(node, str):
            for rel, text in by_size:
                node = node.replace(text, "${FILE:" + rel + "}")
            return node.replace(monitor_id, "${MONITOR_DEVICE_ID}").replace(weather_id, "${WEATHER_DEVICE_ID}")
        if isinstance(node, dict):
            if node.get("name") == CIRCUITS_SOURCE and "dataKeys" in node:
                node = {**node, "dataKeys": _restore_power_keys(node["dataKeys"])}
                found["datasource"] = True
            if isinstance(node.get("assetTypes"), list) and node["assetTypes"] != BUILDING_TYPES:
                node = {**node, "assetTypes": ASSET_TYPES_PLACEHOLDER}
                found["asset_types"] = True
        return node

    kept = {key: dashboard[key] for key in ("title", "configuration") if key in dashboard}
    out = _map(copy.deepcopy(kept), back)
    if not found["datasource"]:
        raise TemplateError(f"the export has no datasource named {CIRCUITS_SOURCE!r} with dataKeys")
    if not found["asset_types"]:
        raise TemplateError("the export has no circuit assetTypes alias list (other than ['Building'])")
    return out


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
        timeseries = widget.get("typeFullFqn") in TIMESERIES_FQNS or widget.get("type") == "timeseries"
        if timeseries and kinds - {"singleEntity"}:
            problems.append(f"widget {widget_id!r}: time-series widget needs a singleEntity alias")
        for ds in widget.get("config", {}).get("datasources", []):
            if alias_type.get(ds.get("entityAliasId")) not in TYPED_ALIASES or not isinstance(ds.get("dataKeys"), list):
                continue
            for key in ds["dataKeys"]:
                name = key.get("name")
                if key.get("type") not in KEY_TYPES:
                    problems.append(f"widget {widget_id!r}: data key {name!r} has type {key.get('type')!r}, "
                                    "expected attribute, timeseries or entityField")
                if key.get("label") != name:
                    problems.append(f"widget {widget_id!r}: data key {name!r} has label {key.get('label')!r}; "
                                    "label must equal name")
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
    cmd.add_argument("--files", metavar="DIR", help="template directory whose files are restored as ${FILE:...} references")
    args = parser.parse_args(argv)
    files = None
    if args.files:
        try:
            files = referenced_files(Path(args.files))
        except TemplateError as exc:
            parser.error(str(exc))
    with open(args.export, encoding="utf-8") as f:
        template = templatize(json.load(f), args.monitor_id, args.weather_id, files)
    json.dump(template, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
