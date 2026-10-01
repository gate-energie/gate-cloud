"""The GATE asset model: what ThingsFlow should contain, and how to get there.

Two inputs, owned by two different places:

  - the circuit map, published by the edge as the `circuit_map` SERVER_SCOPE
    attribute of the monitor device (gate-energie/edge, sync_attributes.py).
    It says what each channel MEASURES. GATE cloud never reads the edge repo:
    the device attribute is the contract between the two projects;
  - the asset model (charts/gate-cloud/files/asset_model.yaml, shipped as a ConfigMap). It says
    what each circuit IS for the people operating the building.

`build_plan` joins them and is pure. `apply_plan` makes ThingsFlow match the
plan: it looks up before it writes, so a second run is a no-op, and it never
deletes -- a circuit that left the map is reported for a person to retire.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

RELATION = "Contains"
MANAGED_BY = "gate-cloud"
CORE_PROFILES = ("Building", "Electrical Panel")


class PlanError(ValueError):
    """The circuit map and the asset model disagree; nothing was written."""


@dataclass
class AssetSpec:
    name: str
    profile: str
    label: str
    attributes: dict[str, Any]
    parent: str | None = None


@dataclass
class Plan:
    profiles: dict[str, str]
    assets: list[AssetSpec]
    device_parent: str


@dataclass
class SyncReport:
    created_profiles: list[str] = field(default_factory=list)
    created_assets: list[str] = field(default_factory=list)
    updated_assets: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    asset_ids: dict[str, str] = field(default_factory=dict)


def build_plan(circuit_map: list[dict[str, Any]], asset_model: dict[str, Any]) -> Plan:
    """Derive the desired ThingsFlow state. No I/O, no clock.

    Collects every disagreement before failing, so one run lists them all.
    """
    errors: list[str] = []
    profiles: dict[str, str] = dict(asset_model.get("asset_types") or {})
    for required in CORE_PROFILES:
        if required not in profiles:
            errors.append(f"asset_types must declare {required!r}")

    building = asset_model.get("building") or {}
    panel = asset_model.get("panel") or {}
    if not building.get("name"):
        errors.append("building.name is required")
    if not panel.get("name"):
        errors.append("panel.name is required")

    branches = {row["name"]: row for row in circuit_map if row.get("kind") == "branch"}
    mains = [row["name"] for row in circuit_map if row.get("kind") == "main_phase"]
    aggregates = [row for row in circuit_map if row.get("kind") == "main_aggregate"]
    if not circuit_map:
        errors.append("the monitor device has no circuit_map attribute; run the edge sync_attributes first")

    modelled: dict[str, dict[str, Any]] = asset_model.get("circuits") or {}
    for name in sorted(set(branches) - set(modelled)):
        errors.append(f"circuit {name!r} is on the monitor but has no entry in the asset model")
    for name in sorted(set(modelled) - set(branches)):
        errors.append(f"the asset model lists {name!r}, which the monitor does not measure")
    for name, entry in sorted(modelled.items()):
        if (entry or {}).get("type") not in profiles:
            errors.append(f"circuit {name!r} has type {(entry or {}).get('type')!r}, not one of asset_types")

    if errors:
        raise PlanError("\n".join(errors))

    building_name, panel_name = building["name"], panel["name"]
    assets = [
        AssetSpec(
            name=building_name,
            profile="Building",
            label=building.get("label", ""),
            attributes={**(building.get("attributes") or {}), "managed_by": MANAGED_BY},
        ),
        AssetSpec(
            name=panel_name,
            profile="Electrical Panel",
            label=panel.get("label", ""),
            parent=building_name,
            attributes={
                "circuit_key": aggregates[0]["name"] if aggregates else "main_total",
                "main_circuit_keys": mains,
                "managed_by": MANAGED_BY,
            },
        ),
    ]
    for name, row in branches.items():
        entry = modelled[name] or {}
        attributes: dict[str, Any] = {
            # Prefix of this circuit's keys on the monitor device
            # (`heating` -> `heating_active_power`); telemetry is not copied.
            "circuit_key": name,
            "refoss_channel": row.get("channel"),
            "clamp": row.get("clamp"),
            "breaker": None if row.get("breaker") is None else str(row["breaker"]),
            "phase": row.get("phase"),
            "location": row.get("location"),
            "power_multiplier": row.get("power_multiplier", 1.0),
            "managed_by": MANAGED_BY,
        }
        # Ratings are optional and never guessed: an invented rating produces
        # believable load percentages that are wrong.
        for optional in ("rated_power_w", "breaker_amps"):
            if entry.get(optional) is not None:
                attributes[optional] = entry[optional]
        assets.append(
            AssetSpec(name=name, profile=entry["type"], label=entry.get("label", ""), parent=panel_name, attributes=attributes)
        )
    return Plan(profiles=profiles, assets=assets, device_parent=panel_name)


class Target(Protocol):
    """What apply_plan needs from ThingsFlow; gate_cloud.thingsflow implements it."""

    def asset_profiles(self) -> dict[str, str]: ...
    def assets(self) -> dict[str, Any]: ...
    def create_asset_profile(self, name: str, description: str) -> str: ...
    def create_asset(self, name: str, profile: str, label: str, profile_id: str) -> str: ...
    def update_asset(self, asset: Any) -> None: ...
    def save_attributes(self, entity: Any, scope: str, attributes: dict[str, Any]) -> None: ...
    def save_relation(self, from_entity: Any, to_entity: Any, relation_type: str) -> None: ...


def apply_plan(plan: Plan, target: Target, monitor_device_id: str, ref) -> SyncReport:
    """Make ThingsFlow match the plan. `ref(entity_type, id)` builds an entity id."""
    report = SyncReport()
    profile_ids = target.asset_profiles()
    for name, description in plan.profiles.items():
        if name not in profile_ids:
            profile_ids[name] = target.create_asset_profile(name, description)
            report.created_profiles.append(name)

    existing = target.assets()
    for spec in plan.assets:
        current = existing.get(spec.name)
        if current is None:
            report.asset_ids[spec.name] = target.create_asset(spec.name, spec.profile, spec.label, profile_ids[spec.profile])
            report.created_assets.append(spec.name)
            continue
        report.asset_ids[spec.name] = current.id.id
        if (current.type, current.label or "") != (spec.profile, spec.label):
            current.type, current.label = spec.profile, spec.label
            current.asset_profile_id = ref("ASSET_PROFILE", profile_ids[spec.profile])
            target.update_asset(current)
            report.updated_assets.append(spec.name)

    synced_at = int(time.time() * 1000)
    for spec in plan.assets:
        # A null would overwrite a value set in the UI with nothing.
        attributes = {k: v for k, v in spec.attributes.items() if v is not None}
        attributes["synced_at"] = synced_at
        if spec.parent is not None:
            attributes["monitor_device_id"] = monitor_device_id
        asset = ref("ASSET", report.asset_ids[spec.name])
        target.save_attributes(asset, "SERVER_SCOPE", attributes)
        if spec.parent is not None:
            target.save_relation(ref("ASSET", report.asset_ids[spec.parent]), asset, RELATION)
    target.save_relation(ref("ASSET", report.asset_ids[plan.device_parent]), ref("DEVICE", monitor_device_id), RELATION)

    managed_types = set(plan.profiles) - set(CORE_PROFILES)
    planned = {spec.name for spec in plan.assets}
    report.orphans = sorted(
        name for name, asset in existing.items() if asset.type in managed_types and name not in planned
    )
    return report
