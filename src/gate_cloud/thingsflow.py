"""ThingsFlow access through the official ThingsBoard Python client.

ThingsFlow implements the ThingsBoard REST contract, so GATE talks to it with
`tb-rest-client` rather than hand-written HTTP. This module only adds what the
client does not do for us:

  - it raises when the client *returns* an exception. On a response it cannot
    deserialise, tb-rest-client logs and returns the exception object instead
    of raising, which would let a failed write look like a successful one;
  - it pages through list endpoints and indexes the result by name;
  - it routes around the contract gaps ThingsFlow still has (tracked in the
    ThingsFlow repo as thingsflow-nkn). Each workaround names its gap, so it can
    be deleted when the gap closes.
"""
from __future__ import annotations

from typing import Any, Callable, Iterator

import requests
from tb_rest_client.models.models_ce import (
    Asset,
    AssetId,
    AssetProfile,
    AssetProfileId,
    Device,
    DeviceProfileId,
    EntityId,
    EntityRelation,
)
from tb_rest_client.rest_client_ce import RestClientCE

PAGE_SIZE = 1000
MAX_POINTS = 10_000  # flow-core caps `limit` at 10000


class ThingsFlowError(RuntimeError):
    """A ThingsFlow call failed, including failures the client only returned."""


def _checked(result: Any) -> Any:
    if isinstance(result, BaseException):
        raise ThingsFlowError(str(result)) from result
    return result


def entity_ref(entity_type: str, entity_id: str) -> EntityId:
    return EntityId(id=entity_id, entity_type=entity_type)


def chunks(start_ms: int, end_ms: int, interval_ms: int, max_points: int) -> list[tuple[int, int]]:
    """Split [start, end) so no request can exceed max_points per key.

    Raw reads (interval 0) are budgeted at one point per second.
    """
    step = (interval_ms or 1000) * max_points
    return [(s, min(s + step, end_ms)) for s in range(start_ms, end_ms, step)]


def parse_series(raw: dict[str, Any]) -> dict[str, list[tuple[int, float]]]:
    out: dict[str, list[tuple[int, float]]] = {}
    for key, rows in (raw or {}).items():
        points = []
        for row in rows or []:
            try:
                points.append((int(row["ts"]), float(row["value"])))
            except (TypeError, ValueError):
                continue
        out[key] = sorted(points)
    return out


class ThingsFlow:
    """A logged-in tenant session. Use as a context manager to log out."""

    def __init__(self, url: str, username: str, password: str) -> None:
        self._url = url.rstrip("/")
        self._client = RestClientCE(base_url=url.rstrip("/"))
        _checked(self._client.login(username=username, password=password))

    def __enter__(self) -> "ThingsFlow":
        return self

    def __exit__(self, *exc: object) -> None:
        # Best effort: the token expires on its own, and a failed logout must
        # not turn a completed sync into a failed run.
        try:
            self._client.logout()
        except Exception:  # noqa: BLE001
            pass

    def _pages(self, fetch: Callable[..., Any]) -> Iterator[Any]:
        page = 0
        while True:
            result = _checked(fetch(page_size=PAGE_SIZE, page=page))
            yield from result.data or []
            if not result.has_next:
                return
            page += 1

    # ── reads ────────────────────────────────────────────────────────────────

    def asset_profiles(self) -> dict[str, str]:
        """Profile name -> id."""
        return {p.name: p.id.id for p in self._pages(self._client.get_asset_profiles)}

    def assets(self) -> dict[str, Asset]:
        """Asset name -> asset, for the whole tenant.

        Gap thingsflow-nkn: GET /api/tenant/assets?assetName= ignores the name,
        so `get_tenant_asset` cannot be used for lookups; page and index instead.
        """
        return {a.name: a for a in self._pages(self._client.get_tenant_assets)}

    def attributes(self, entity: EntityId, scope: str, keys: list[str]) -> dict[str, Any]:
        rows = _checked(self._client.get_attributes_by_scope(entity, scope, keys=",".join(keys)))
        return {row["key"]: row["value"] for row in rows or []}

    def timeseries(self, entity: EntityId, keys: list[str], start_ms: int, end_ms: int,
                   interval_ms: int = 0, agg: str = "NONE") -> dict[str, list[tuple[int, float]]]:
        """History per key, chunked so no request exceeds MAX_POINTS per key.

        flow-core ignores intervalType/timeZone, and `agg` only applies when
        interval > 0.
        """
        merged: dict[str, list[tuple[int, float]]] = {key: [] for key in keys}
        for start, end in chunks(start_ms, end_ms, interval_ms, MAX_POINTS):
            raw = _checked(self._client.get_timeseries(
                entity, keys=",".join(keys), start_ts=start, end_ts=end - 1,
                interval=interval_ms or None, agg=agg, limit=MAX_POINTS, order_by="ASC"))
            for key, points in parse_series(raw).items():
                merged.setdefault(key, []).extend(points)
        return merged

    def last_value(self, entity: EntityId, key: str, at_ms: int, lookback_ms: int = 6 * 3_600_000) -> float | None:
        raw = _checked(self._client.get_timeseries(
            entity, keys=key, start_ts=at_ms - lookback_ms, end_ts=at_ms, limit=1, order_by="DESC"))
        points = parse_series(raw).get(key) or []
        return points[-1][1] if points else None

    def devices(self) -> dict[str, Device]:
        """Device name -> device. Paged and indexed, as for assets (thingsflow-nkn)."""
        return {d.name: d for d in self._pages(self._client.get_tenant_devices)}

    # ── writes ───────────────────────────────────────────────────────────────

    def create_asset_profile(self, name: str, description: str) -> str:
        profile = _checked(self._client.save_asset_profile(AssetProfile(name=name, description=description)))
        return profile.id.id

    def create_asset(self, name: str, profile: str, label: str, profile_id: str) -> str:
        asset = Asset(name=name, type=profile, label=label, asset_profile_id=AssetProfileId(profile_id, "ASSET_PROFILE"))
        return _checked(self._client.save_asset(asset)).id.id

    def update_asset(self, asset: Asset) -> None:
        """Gap thingsflow-nkn: an update answers 200 with no body, which the
        client fails to deserialise. The write itself succeeded, so read the
        asset back and compare instead of trusting the return value."""
        self._client.save_asset(asset)
        stored = _checked(self._client.get_asset_by_id(AssetId(asset.id.id, "ASSET")))
        if (stored.type, stored.label or "") != (asset.type, asset.label or ""):
            raise ThingsFlowError(f"asset {asset.name!r} did not take the update")

    def save_attributes(self, entity: EntityId, scope: str, attributes: dict[str, Any]) -> None:
        """Gap thingsflow-nkn: the v2 path (.../attributes/{scope}) is rejected
        as an unknown scope; the v1 path works for every entity type."""
        _checked(self._client.save_entity_attributes_v1(entity, scope, attributes))

    def save_relation(self, from_entity: EntityId, to_entity: EntityId, relation_type: str) -> None:
        # Upserted by ThingsFlow (topology_edge ON CONFLICT): repeats are free.
        relation = EntityRelation(_from=from_entity, to=to_entity, type=relation_type, type_group="COMMON")
        _checked(self._client.save_relation(relation))

    def ensure_device(self, name: str, device_type: str, label: str) -> str:
        existing = self.devices().get(name)
        if existing is not None:
            return existing.id.id
        # tb-rest-client's Device refuses a missing device_profile_id; the
        # tenant's default profile is enough for a device fed over HTTP ingest.
        profile = _checked(self._client.get_default_device_profile_info())
        device = Device(name=name, type=device_type, label=label,
                        device_profile_id=DeviceProfileId(profile.id.id, "DEVICE_PROFILE"))
        return _checked(self._client.save_device(device)).id.id

    def device_jwt(self, device_id: str) -> str:
        """A short-lived device JWT for HTTP ingest. Not in tb-rest-client."""
        token = self._client.configuration.api_key["X-Authorization"]
        response = requests.post(f"{self._url}/api/device/{device_id}/jwt",
                                 headers={"X-Authorization": f"Bearer {token}"}, timeout=30)
        if response.status_code != 200:
            raise ThingsFlowError(f"device JWT for {device_id}: {response.status_code} {response.text[:200]}")
        return response.json()["token"]

    def ingest(self, ingest_url: str, jwt: str, points: list[dict[str, Any]]) -> None:
        """Device telemetry through the HTTP ingest gateway (history, ts honoured)."""
        response = requests.post(f"{ingest_url.rstrip('/')}/api/v1/telemetry", json=points,
                                 headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
        if response.status_code >= 300:
            raise ThingsFlowError(f"ingest: {response.status_code} {response.text[:200]}")

    def save_timeseries(self, entity: EntityId, point: dict[str, Any]) -> None:
        """One timestamped sample on an entity. For assets this reaches history
        only once ThingsFlow ships asset-telemetry-history (thingsflow-hwz)."""
        _checked(self._client.save_entity_telemetry(entity, "ANY", point))
