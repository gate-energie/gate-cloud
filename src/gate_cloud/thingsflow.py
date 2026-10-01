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

from tb_rest_client.models.models_ce import (
    Asset,
    AssetId,
    AssetProfile,
    AssetProfileId,
    EntityId,
    EntityRelation,
)
from tb_rest_client.rest_client_ce import RestClientCE

PAGE_SIZE = 1000


class ThingsFlowError(RuntimeError):
    """A ThingsFlow call failed, including failures the client only returned."""


def _checked(result: Any) -> Any:
    if isinstance(result, BaseException):
        raise ThingsFlowError(str(result)) from result
    return result


def entity_ref(entity_type: str, entity_id: str) -> EntityId:
    return EntityId(id=entity_id, entity_type=entity_type)


class ThingsFlow:
    """A logged-in tenant session. Use as a context manager to log out."""

    def __init__(self, url: str, username: str, password: str) -> None:
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

