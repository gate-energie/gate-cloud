"""Dagster definitions for GATE cloud.

The ThingsFlow asset model is one Dagster asset. It is materialised:

  - when its inputs change: a sensor hashes the asset model file (a ConfigMap,
    updated by `helm upgrade`) and the monitor's `circuit_map` (updated by the
    edge), and requests a run keyed by that hash;
  - once a day regardless, to restore anything edited by hand in the UI.

The sensor is on from deployment; nobody has to start it in the UI.

Runs are idempotent, so a duplicate trigger costs a few reads.
"""

import datetime as dt
import hashlib
import json
import os
from zoneinfo import ZoneInfo
from typing import Any

import dagster as dg
import yaml

from gate_cloud.asset_model import apply_plan, build_plan
from gate_cloud.thingsflow import ThingsFlow, entity_ref
from gate_cloud.weather import ARCHIVE_URL, FORECAST_URL, OpenMeteo

TIMEZONE = "America/Toronto"
HISTORY_START = "2026-09-01"  # first partition day; confirm with the user before release
WEATHER_DEVICE = "GATE Weather"

hourly = dg.HourlyPartitionsDefinition(start_date=f"{HISTORY_START}-00:00", timezone=TIMEZONE)


class ThingsFlowResource(dg.ConfigurableResource):
    """A tenant-admin session on ThingsFlow, and the monitor device to model."""

    url: str
    username: str
    password: str
    monitor_device_id: str
    ingest_url: str
    asset_history: bool = False

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


@dg.sensor(
    job=sync_asset_model_job, minimum_interval_seconds=120, default_status=dg.DefaultSensorStatus.RUNNING
)
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


class WeatherResource(dg.ConfigurableResource):
    """Open-Meteo endpoints; overridable for tests and mirrors."""

    forecast_url: str = FORECAST_URL
    archive_url: str = ARCHIVE_URL

    def client(self) -> OpenMeteo:
        return OpenMeteo(self.forecast_url, self.archive_url)


def site(asset_model: dict[str, Any]) -> tuple[float, float]:
    attributes = (asset_model.get("building") or {}).get("attributes") or {}
    if "latitude" not in attributes or "longitude" not in attributes:
        raise dg.Failure("asset model building.attributes needs latitude and longitude")
    return float(attributes["latitude"]), float(attributes["longitude"])


@dg.asset(
    group_name="weather",
    partitions_def=hourly,
    backfill_policy=dg.BackfillPolicy.single_run(),
    retry_policy=dg.RetryPolicy(max_retries=3, delay=60, backoff=dg.Backoff.EXPONENTIAL),
    description="Hourly Open-Meteo observations at the building, ingested into the GATE Weather device.",
)
def weather_observations(
    context: dg.AssetExecutionContext,
    thingsflow: ThingsFlowResource,
    asset_model_file: AssetModelFile,
    weather: WeatherResource,
) -> dg.MaterializeResult:
    model = asset_model_file.load()
    latitude, longitude = site(model)
    window = context.partition_time_window
    today = dt.datetime.now(ZoneInfo(TIMEZONE)).date()
    points = weather.client().hourly(latitude, longitude, window.start, window.end, today)
    with thingsflow.session() as session:
        device_id = session.ensure_device(WEATHER_DEVICE, "weather", "Weather")
        name = model["building"]["name"]
        building = session.assets().get(name)
        if building is None:
            raise dg.Failure(
                f"building {name!r} is not in ThingsFlow yet; materialise thingsflow_asset_model first"
            )
        session.save_relation(entity_ref("ASSET", building.id.id), entity_ref("DEVICE", device_id), "Contains")
        if points:
            session.ingest(thingsflow.ingest_url, session.device_jwt(device_id), points)
    expected = int((window.end - window.start).total_seconds() // 3600)
    if len(points) < expected:
        context.log.warning(f"Open-Meteo returned {len(points)} of {expected} hours")
    return dg.MaterializeResult(metadata={"hours": len(points), "expected_hours": expected})


weather_job = dg.define_asset_job("weather", selection=[weather_observations])
hourly_weather_schedule = dg.build_schedule_from_partitioned_job(
    weather_job, minute_of_hour=10, default_status=dg.DefaultScheduleStatus.RUNNING
)


defs = dg.Definitions(
    assets=[thingsflow_asset_model, weather_observations],
    jobs=[sync_asset_model_job, weather_job],
    schedules=[daily_asset_model_sync, hourly_weather_schedule],
    sensors=[asset_model_inputs_changed],
    resources={
        "thingsflow": ThingsFlowResource(
            url=dg.EnvVar("THINGSFLOW_URL"),
            username=dg.EnvVar("THINGSFLOW_USERNAME"),
            password=dg.EnvVar("THINGSFLOW_PASSWORD"),
            monitor_device_id=dg.EnvVar("GATE_MONITOR_DEVICE_ID"),
            ingest_url=dg.EnvVar("THINGSFLOW_INGEST_URL"),
            asset_history=os.environ.get("GATE_ASSET_HISTORY", "false").lower() == "true",
        ),
        "asset_model_file": AssetModelFile(path=dg.EnvVar("GATE_ASSET_MODEL_PATH")),
        "weather": WeatherResource(),
    },
)
