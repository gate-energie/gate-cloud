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
        self.series: dict[tuple[str, str], list[tuple[int, float]]] = {}
        self.devices_by_name: dict[str, SimpleNamespace] = {}
        self.ingested: list[tuple[str, str, list]] = []
        self.asset_points: list[tuple[str, dict]] = []

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

    def timeseries(self, entity, keys, start_ms, end_ms, interval_ms=0, agg="NONE"):
        """Like flow-core: agg applies only with an interval; buckets are
        date_bin with no origin, so aligned to the Unix epoch, not to start_ms."""
        out = {}
        for key in keys:
            points = [(t, v) for t, v in self.series.get((entity.id, key), []) if start_ms <= t < end_ms]
            if agg in ("AVG", "MAX") and points:
                if interval_ms > 0:
                    buckets: dict[int, list[float]] = {}
                    for t, v in points:
                        buckets.setdefault(t // interval_ms * interval_ms, []).append(v)
                    reduce = (lambda vs: sum(vs) / len(vs)) if agg == "AVG" else max
                    points = [(b, reduce(vs)) for b, vs in sorted(buckets.items())]
                elif agg == "MAX":
                    points = [(points[0][0], max(v for _, v in points))]
            out[key] = points
        return out

    def last_value(self, entity, key, at_ms, lookback_ms=6 * 3_600_000):
        points = [v for t, v in self.series.get((entity.id, key), []) if at_ms - lookback_ms <= t <= at_ms]
        return points[-1] if points else None

    def devices(self):
        return dict(self.devices_by_name)

    def ensure_device(self, name, device_type, label):
        if name not in self.devices_by_name:
            self.devices_by_name[name] = SimpleNamespace(id=ref("DEVICE", f"device-{name}"), name=name, type=device_type)
        return self.devices_by_name[name].id.id

    def device_jwt(self, device_id):
        return f"jwt-{device_id}"

    def ingest(self, ingest_url, jwt, points):
        self.ingested.append((ingest_url, jwt, list(points)))

    def save_timeseries(self, entity, point):
        self.asset_points.append((entity.id, point))
