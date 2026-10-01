"""An in-memory ThingsFlow that behaves like flow-core for the calls GATE makes."""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any


def ref(entity_type: str, entity_id: str) -> SimpleNamespace:
    return SimpleNamespace(entity_type=entity_type, id=entity_id)


def circuit_map(*branches: str, mains: tuple[str, ...] = ("main_phase_1",)) -> list[dict[str, Any]]:
    """Rows shaped like the edge's sync_attributes.py circuit_map."""
    rows = [{"name": name, "kind": "main_phase", "location": "service"} for name in mains]
    rows += [
        {"name": name, "kind": "branch", "channel": f"em:{i}", "clamp": f"A{i}", "phase": "A", "breaker": i,
         "location": "general", "power_multiplier": 1.0}
        for i, name in enumerate(branches, start=2)
    ]
    rows.append({"name": "main_total", "kind": "main_aggregate", "location": "service"})
    return rows


class FakeThingsFlow:
    def __init__(self, device_attributes: dict[str, Any] | None = None) -> None:
        self.profiles: dict[str, str] = {}
        self.entities: dict[str, SimpleNamespace] = {}
        self.attrs: dict[str, dict[str, Any]] = {}
        self.relations: set[tuple[str, str, str]] = set()
        self.entity_writes = 0
        self.device_attributes = device_attributes or {}

    # context-manager surface of gate_cloud.thingsflow.ThingsFlow
    def __enter__(self) -> "FakeThingsFlow":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def asset_profiles(self) -> dict[str, str]:
        return dict(self.profiles)

    def assets(self) -> dict[str, SimpleNamespace]:
        return copy.deepcopy(self.entities)

    def attributes(self, entity, scope: str, keys: list[str]) -> dict[str, Any]:
        source = self.device_attributes if entity.entity_type == "DEVICE" else self.attrs.get(entity.id, {})
        return {key: source[key] for key in keys if key in source}

    def create_asset_profile(self, name: str, description: str) -> str:
        self.entity_writes += 1
        self.profiles[name] = f"profile-{name}"
        return self.profiles[name]

    def create_asset(self, name: str, profile: str, label: str, profile_id: str) -> str:
        self.entity_writes += 1
        asset_id = f"asset-{name}"
        self.entities[name] = SimpleNamespace(
            id=ref("ASSET", asset_id), name=name, type=profile, label=label, asset_profile_id=ref("ASSET_PROFILE", profile_id)
        )
        return asset_id

    def update_asset(self, asset) -> None:
        self.entity_writes += 1
        self.entities[asset.name] = copy.deepcopy(asset)

    def save_attributes(self, entity, scope: str, attributes: dict[str, Any]) -> None:
        self.attrs.setdefault(entity.id, {}).update(attributes)

    def save_relation(self, from_entity, to_entity, relation_type: str) -> None:
        self.relations.add((from_entity.id, to_entity.id, relation_type))
