"""Dagster definitions for GATE cloud.

The ThingsFlow asset model is one Dagster asset. It is materialised:

  - when its inputs change: a sensor hashes the asset model file (a ConfigMap,
    updated by `helm upgrade`) and the monitor's `circuit_map` (updated by the
    edge), and requests a run keyed by that hash;
  - once a day regardless, to restore anything edited by hand in the UI.

Runs are idempotent, so a duplicate trigger costs a few reads.
"""

import hashlib
import json
from typing import Any

import dagster as dg
import yaml

from gate_cloud.asset_model import apply_plan, build_plan
from gate_cloud.thingsflow import ThingsFlow, entity_ref


class ThingsFlowResource(dg.ConfigurableResource):
    """A tenant-admin session on ThingsFlow, and the monitor device to model."""

    url: str
    username: str
    password: str
    monitor_device_id: str

    def session(self) -> ThingsFlow:
        return ThingsFlow(self.url, self.username, self.password)

    def circuit_map(self, session: ThingsFlow) -> list[dict[str, Any]]:
        value = session.attributes(entity_ref("DEVICE", self.monitor_device_id), "SERVER_SCOPE", ["circuit_map"]).get(
            "circuit_map", []
        )
        # JSON attributes may come back serialised, depending on the server.
        return json.loads(value) if isinstance(value, str) else list(value or [])


class AssetModelFile(dg.ConfigurableResource):
    """The site asset model (charts/gate-cloud/files/asset_model.yaml), mounted from a ConfigMap."""

    path: str

    def load(self) -> dict[str, Any]:
        with open(self.path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def digest(self) -> str:
        with open(self.path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()


@dg.asset(
    group_name="thingsflow",
    description="Building, panel and circuit assets in ThingsFlow, joined from the monitor's circuit_map "
    "and the asset model file.",
)
def thingsflow_asset_model(
    context: dg.AssetExecutionContext, thingsflow: ThingsFlowResource, asset_model_file: AssetModelFile
) -> dg.MaterializeResult:
    with thingsflow.session() as session:
        plan = build_plan(thingsflow.circuit_map(session), asset_model_file.load())
        report = apply_plan(plan, session, thingsflow.monitor_device_id, entity_ref)
    for name in report.orphans:
        context.log.warning(f"asset {name!r} is no longer in the model; retire it by hand if intended")
    return dg.MaterializeResult(
        metadata={
            "assets": len(report.asset_ids),
            "profiles": len(plan.profiles),
            "created_profiles": report.created_profiles,
            "created_assets": report.created_assets,
            "updated_assets": report.updated_assets,
            "orphans": report.orphans,
        }
    )


sync_asset_model_job = dg.define_asset_job("sync_asset_model", selection=[thingsflow_asset_model])

daily_asset_model_sync = dg.ScheduleDefinition(
    job=sync_asset_model_job,
    cron_schedule="0 6 * * *",
    execution_timezone="America/Toronto",
)


@dg.sensor(job=sync_asset_model_job, minimum_interval_seconds=120)
def asset_model_inputs_changed(
    context: dg.SensorEvaluationContext, thingsflow: ThingsFlowResource, asset_model_file: AssetModelFile
):
    with thingsflow.session() as session:
        circuit_map = thingsflow.circuit_map(session)
    digest = hashlib.sha256(
        (asset_model_file.digest() + json.dumps(circuit_map, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    if digest == context.cursor:
        return dg.SkipReason("asset model and circuit map unchanged")
    context.update_cursor(digest)
    return dg.RunRequest(run_key=digest)


defs = dg.Definitions(
    assets=[thingsflow_asset_model],
    jobs=[sync_asset_model_job],
    schedules=[daily_asset_model_sync],
    sensors=[asset_model_inputs_changed],
    resources={
        "thingsflow": ThingsFlowResource(
            url=dg.EnvVar("THINGSFLOW_URL"),
            username=dg.EnvVar("THINGSFLOW_USERNAME"),
            password=dg.EnvVar("THINGSFLOW_PASSWORD"),
            monitor_device_id=dg.EnvVar("GATE_MONITOR_DEVICE_ID"),
        ),
        "asset_model_file": AssetModelFile(path=dg.EnvVar("GATE_ASSET_MODEL_PATH")),
    },
)
