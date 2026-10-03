"""Dagster definitions for GATE cloud.

The ThingsFlow asset model is one Dagster asset. It is materialised:

  - when its inputs change: a sensor hashes the asset model file (a ConfigMap,
    updated by `helm upgrade`) and the monitor's `circuit_map` (updated by the
    edge), and requests a run keyed by that hash;
  - once a day regardless, to restore anything edited by hand in the UI.

The sensor is on from deployment; nobody has to start it in the UI.

Runs are idempotent, so a duplicate trigger costs a few reads.
"""

import calendar
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

import dagster as dg
import yaml

from gate_cloud.analytics import (
    MINUTE_MS, QUARTER_MS, DailyResult, circuits, main_key, measure, read_daily_energy, read_energy, read_hourly,
    read_input, read_metrics, read_peak, read_power, read_weather, usable_energy, usable_peak,
)
from gate_cloud.asset_model import CORE_PROFILES, apply_plan, build_plan
from gate_cloud.dashboard import (CircuitSeries, TemplateError, load_template, referenced_files, render,
                                  unsupported)
from gate_cloud.tariff import RateD
from gate_cloud.thingsflow import ThingsFlow, entity_ref
from gate_cloud.twin import (
    Window, circuit_metrics, daily_energy, daily_scatter, day_cost, heatmap, local_day, month_budget, month_extras, month_window,
    same_time_yesterday, summary_attributes, today_window, weather_day,
)
from gate_cloud.weather import ARCHIVE_URL, FORECAST_URL, OpenMeteo

TIMEZONE = "America/Toronto"
HISTORY_START = "2026-08-10"  # first Refoss telemetry in ThingsFlow: 2026-08-10 15:29 local (partial day, see coverage_pct)
WEATHER_DEVICE = "GATE Weather"

hourly = dg.HourlyPartitionsDefinition(start_date=f"{HISTORY_START}-00:00", timezone=TIMEZONE)


class ThingsFlowResource(dg.ConfigurableResource):
    """A tenant-admin session on ThingsFlow, and the monitor device to model."""

    url: str
    username: str
    password: str
    monitor_device_id: str
    ingest_url: str = ""  # only weather_observations needs it
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


class DashboardFile(dg.ConfigurableResource):
    """The dashboard template directory (charts/gate-cloud/files/dashboard): dashboard.json and
    the files it references as ${FILE:...}. Empty path: not configured."""

    path: str = ""

    def load(self) -> dict[str, Any]:
        if not self.path:
            raise dg.Failure("GATE_DASHBOARD_PATH is not set")
        try:
            return load_template(Path(self.path))
        except TemplateError as error:
            raise dg.Failure(f"dashboard template: {error}") from error

    def digest(self) -> str:
        """Hash of dashboard.json and the files it references; tests and other files are left out."""
        root = Path(self.path)
        files = {"dashboard.json": (root / "dashboard.json").read_text(encoding="utf-8"),
                 **referenced_files(root)}
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()


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
    if not thingsflow.ingest_url:
        raise dg.Failure("THINGSFLOW_INGEST_URL is not set")
    model = asset_model_file.load()
    latitude, longitude = site(model)
    window = context.partition_time_window
    today = dt.datetime.now(ZoneInfo(TIMEZONE)).date()
    points = weather.client().hourly(latitude, longitude, window.start, window.end, today)
    with thingsflow.session() as session:
        name = model["building"]["name"]
        building = session.assets().get(name)
        if building is None:
            raise dg.Failure(
                f"building {name!r} is not in ThingsFlow yet; materialise thingsflow_asset_model first"
            )
        device_id = session.ensure_device(WEATHER_DEVICE, "weather", "Weather")
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


@dg.asset(
    group_name="weather",
    deps=[weather_observations],
    retry_policy=dg.RetryPolicy(max_retries=3, delay=60, backoff=dg.Backoff.EXPONENTIAL),
    description="Open-Meteo outlook (next 3 days, next 12 hours) as the forecast attribute of the GATE Weather device.",
)
def weather_forecast(
    thingsflow: ThingsFlowResource, asset_model_file: AssetModelFile, weather: WeatherResource
) -> dg.MaterializeResult:
    latitude, longitude = site(asset_model_file.load())
    with thingsflow.session() as session:
        device = session.devices().get(WEATHER_DEVICE)
        if device is None:
            raise dg.Failure("GATE Weather device not found; materialise weather_observations first")
        now = dt.datetime.now(dt.timezone.utc)
        forecast = weather.client().forecast(latitude, longitude, now)
        attrs = {"forecast": forecast, "forecast_updated_at": int(now.timestamp() * 1000)}
        session.save_attributes(entity_ref("DEVICE", device.id.id), "SERVER_SCOPE", _known(attrs, "forecast_unknown"))
    return dg.MaterializeResult(metadata={"days": len(forecast.get("days") or []), "hours": len(forecast.get("hours") or [])})


weather_forecast_job = dg.define_asset_job("forecast", selection=[weather_forecast])
weather_forecast_schedule = dg.ScheduleDefinition(
    job=weather_forecast_job, cron_schedule="5 * * * *", execution_timezone=TIMEZONE,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)


@dg.asset(
    group_name="thingsflow",
    deps=[thingsflow_asset_model, weather_observations],
    description="The GATE dashboard in ThingsFlow, rendered from the template and the circuits in the asset model.",
)
def thingsflow_dashboard(
    thingsflow: ThingsFlowResource, asset_model_file: AssetModelFile, dashboard_file: DashboardFile
) -> dg.MaterializeResult:
    template = dashboard_file.load()
    with thingsflow.session() as session:
        plan = build_plan(thingsflow.circuit_map(session), asset_model_file.load())
        weather = session.devices().get(WEATHER_DEVICE)
        if weather is None:
            raise dg.Failure("GATE Weather device not found; materialise weather_observations first")
        branches = [s for s in plan.assets if s.parent is not None and s.profile not in CORE_PROFILES]
        circuits = [CircuitSeries(s.attributes["circuit_key"], s.label or s.name) for s in branches]
        try:
            out = render(template, thingsflow.monitor_device_id, weather.id.id, circuits,
                         sorted({s.profile for s in branches}))
        except TemplateError as error:
            raise dg.Failure(f"dashboard template: {error}") from error
        problems = unsupported(out)
        if problems:
            raise dg.Failure("dashboard uses what ThingsFlow cannot serve: " + "; ".join(problems))
        existing = session.dashboard(out["title"])
        if existing and existing[1] == out["configuration"]:
            action, dashboard_id = "unchanged", existing[0]
        elif existing:
            action, dashboard_id = "updated", session.save_dashboard(out["title"], out["configuration"], existing[0])
        else:
            action, dashboard_id = "created", session.save_dashboard(out["title"], out["configuration"])
    return dg.MaterializeResult(
        metadata={"action": action, "dashboard_id": dashboard_id, "circuits": len(circuits)}
    )


sync_asset_model_job = dg.define_asset_job(
    "sync_asset_model", selection=[thingsflow_asset_model, thingsflow_dashboard]
)

daily_asset_model_sync = dg.ScheduleDefinition(
    job=sync_asset_model_job,
    cron_schedule="0 6 * * *",
    execution_timezone="America/Toronto",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)


@dg.sensor(
    job=sync_asset_model_job, minimum_interval_seconds=120, default_status=dg.DefaultSensorStatus.RUNNING
)
def asset_model_inputs_changed(
    context: dg.SensorEvaluationContext,
    thingsflow: ThingsFlowResource,
    asset_model_file: AssetModelFile,
    dashboard_file: DashboardFile,
):
    with thingsflow.session() as session:
        circuit_map = thingsflow.circuit_map(session)
    template = ""
    if dashboard_file.path:
        try:
            template = dashboard_file.digest()
        except (OSError, TemplateError) as error:
            # Keep syncing the asset model; thingsflow_dashboard reports the broken template itself.
            context.log.warning(f"dashboard template {dashboard_file.path!r} is unreadable ({error}); left out of the digest")
    digest = hashlib.sha256(
        (asset_model_file.digest() + template + json.dumps(circuit_map, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    if digest == context.cursor:
        return dg.SkipReason("asset model, dashboard template and circuit map unchanged")
    context.update_cursor(digest)
    return dg.RunRequest(run_key=digest)


daily = dg.DailyPartitionsDefinition(start_date=HISTORY_START, timezone=TIMEZONE)
SUMMARY_DAYS = 30
WEEK_DAYS = 7


def _context(session, thingsflow: ThingsFlowResource, model: dict[str, Any]):
    plan = build_plan(thingsflow.circuit_map(session), model)
    rate = RateD.from_attributes((model.get("building") or {}).get("attributes") or {})
    weather = session.devices().get(WEATHER_DEVICE)
    weather_ref = entity_ref("DEVICE", weather.id.id) if weather else None
    return plan, rate, entity_ref("DEVICE", thingsflow.monitor_device_id), weather_ref


@dg.asset(
    group_name="twin",
    partitions_def=daily,
    deps=[weather_observations, thingsflow_asset_model],
    description="Per-circuit and building metrics for one local day; written to assets when asset_history is on.",
)
def circuit_daily_metrics(
    context: dg.AssetExecutionContext, thingsflow: ThingsFlowResource, asset_model_file: AssetModelFile
) -> dg.MaterializeResult:
    model = asset_model_file.load()
    window = local_day(dt.date.fromisoformat(context.partition_key))
    with thingsflow.session() as session:
        plan, rate, monitor, weather_device = _context(session, thingsflow, model)
        result = measure(session, monitor, plan, rate, window, days=1)
        temperature, humidity = read_weather(session, weather_device, window)
        building = {**result.building, **weather_day(temperature, humidity, window)}
        if thingsflow.asset_history:
            ids = {c.name: c.asset_id for c in result.found}
            for name, m in result.circuits.items():
                values = {k: v for k, v in m.as_values().items() if v is not None}
                session.save_timeseries(entity_ref("ASSET", ids[name]), {"ts": window.start_ms, "values": values})
            building_name = model["building"]["name"]
            building_asset = session.assets().get(building_name)
            if building_asset is None:
                raise dg.Failure(
                    f"building {building_name!r} is not in ThingsFlow yet; materialise thingsflow_asset_model first"
                )
            building_id = building_asset.id.id
            values = {k: v for k, v in building.items() if v is not None}
            session.save_timeseries(entity_ref("ASSET", building_id), {"ts": window.start_ms, "values": values})
    for name in result.no_data:
        context.log.warning(f"circuit {name!r} has no data on {context.partition_key}")
    _warn_missing(context, result.missing_assets)
    return dg.MaterializeResult(metadata={
        "building_energy_kwh": building.get("energy_kwh"),
        "building_cost_cad": building.get("cost_cad"),
        "temp_mean_c": building.get("temp_mean_c"),
        "no_data": result.no_data,
        "missing_assets": result.missing_assets,
        "written_to_assets": thingsflow.asset_history,
        "circuits": dg.MetadataValue.md(_table(result.circuits)),
    })


def _warn_missing(context: dg.AssetExecutionContext, names: list[str]) -> None:
    for name in names:
        context.log.warning(f"circuit {name!r} has no ThingsFlow asset yet; skipped until thingsflow_asset_model runs")


def _known(attrs: dict[str, Any], unknown_key: str = "twin_unknown") -> dict[str, Any]:
    """Attributes to store: the known values, plus `unknown_key` naming the rest.

    ThingsFlow stores a JSON null as the string "null", and it cannot delete
    attributes (DELETE .../attributes returns 404), so an unknown value is not
    written. A value left from an earlier window may then remain on the asset;
    `unknown_key` says it does not describe the current one.
    """
    known = {k: v for k, v in attrs.items() if v is not None}
    known[unknown_key] = ",".join(sorted(k for k, v in attrs.items() if v is None))
    return known


def _table(metrics) -> str:
    rows = ["| circuit | kWh | source | avg W | max W | util % | cover % | frac % | CAD | quality |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in sorted(metrics.items()):
        rows.append(f"| {name} | {m.energy_kwh} | {m.energy_source} | {m.avg_power_w} | {m.max_power_w} | "
                    f"{m.utilization_pct} | {m.coverage_pct} | {m.energy_fraction_pct} | {m.cost_cad} | "
                    f"{m.quality or ''} |")
    return "\n".join(rows)


class SummaryConfig(dg.Config):
    end_date: str | None = None  # local date the window ends on (exclusive); default today in TIMEZONE


@dg.asset(
    group_name="twin",
    deps=[weather_observations, thingsflow_asset_model],
    description="30-day twin summary per circuit, written as twin_* SERVER_SCOPE attributes.",
)
def asset_twin_summary(
    context: dg.AssetExecutionContext, config: SummaryConfig, thingsflow: ThingsFlowResource,
    asset_model_file: AssetModelFile,
) -> dg.MaterializeResult:
    model = asset_model_file.load()
    end_day = dt.date.fromisoformat(config.end_date) if config.end_date else dt.datetime.now(ZoneInfo(TIMEZONE)).date()
    window = Window(local_day(end_day - dt.timedelta(days=SUMMARY_DAYS)).start_ms, local_day(end_day).start_ms)
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    with thingsflow.session() as session:
        plan, rate, monitor, weather_device = _context(session, thingsflow, model)
        result = measure(session, monitor, plan, rate, window, days=SUMMARY_DAYS)
        week = Window(local_day(end_day - dt.timedelta(days=WEEK_DAYS)).start_ms, window.end_ms)
        last_week = measure(session, monitor, plan, rate, week, days=WEEK_DAYS)
        week_main_known = usable_energy(last_week.main) is not None
        temperature, humidity = read_weather(session, weather_device, window)
        for c in result.found:
            m = result.circuits.get(c.name)
            if m is None:
                continue
            # Metrics come from 1-minute buckets; only the weather pairing uses quarter hours.
            power = read_power(session, monitor, c.key, window, QUARTER_MS)
            attrs = summary_attributes(window, m, power, temperature, humidity, c.rated_power_w, QUARTER_MS)
            w = last_week.circuits.get(c.name)
            attrs["twin_7d_energy_kwh"] = usable_energy(w)
            attrs["twin_7d_cost_cad"] = w.cost_cad if attrs["twin_7d_energy_kwh"] is not None and week_main_known else None
            attrs["twin_updated_at"] = now_ms
            session.save_attributes(entity_ref("ASSET", c.asset_id), "SERVER_SCOPE", _known(attrs))
        month = month_window(end_day)
        energy, energy_source = None, None
        if month is not None:
            m = circuit_metrics(month, MINUTE_MS, read_input(session, monitor, main_key(plan), month))
            # A flagged or negative aggregate is not a usable month: leave it unknown.
            if m is not None and m.quality is None and m.energy_kwh >= 0:
                energy, energy_source = m.energy_kwh, m.energy_source
        elapsed = 0 if month is None else (end_day - end_day.replace(day=1)).days
        days_in_month = calendar.monthrange(end_day.year, end_day.month)[1]
        building_attrs = (model.get("building") or {}).get("attributes") or {}
        monthly_budget = building_attrs.get("monthly_budget")
        monthly_budget = float(monthly_budget) if monthly_budget is not None else None
        budget = month_budget(energy, elapsed, days_in_month, rate, monthly_budget)
        budget.update(month_extras(budget["month_cost_cad"], elapsed, days_in_month))
        if energy_source is not None and budget["month_energy_kwh"] is not None:
            budget["month_energy_source"] = energy_source
        budget["month_updated_at"] = now_ms
        analytics = {}
        building = session.assets().get(model["building"]["name"])
        if building is not None:
            ref = entity_ref("ASSET", building.id.id)
            session.save_attributes(ref, "SERVER_SCOPE", _known(budget, "month_unknown"))
            analytics = _analytics(session, monitor, main_key(plan), weather_device, window, end_day)
            analytics["analytics_updated_at"] = now_ms
            session.save_attributes(ref, "SERVER_SCOPE", _known(analytics, "analytics_unknown"))
    _warn_missing(context, result.missing_assets)
    return dg.MaterializeResult(metadata={
        "window_start": window.start_ms, "window_end": window.end_ms,
        "circuits": len(result.circuits), "no_data": result.no_data,
        "missing_assets": result.missing_assets,
        "month_cost_cad": budget["month_cost_cad"], "month_budget_used_pct": budget["month_budget_used_pct"],
        "scatter_days": len(analytics.get("analytics_scatter") or []),
        "daily_days": len(analytics.get("analytics_daily") or []),
    })


def _analytics(session, monitor, key: str, weather_device, window: Window, end_day: dt.date) -> dict[str, Any]:
    """Building analytics: weekday x hour heatmap over `window`, energy of every
    complete local day since HISTORY_START (no weather needed), and that energy
    against mean temperature for the days that have both."""
    hourly_power = read_hourly(session, monitor, key, window)
    first = dt.date.fromisoformat(HISTORY_START)
    history = Window(local_day(first).start_ms, local_day(end_day).start_ms)
    temperature, humidity = read_weather(session, weather_device, history)
    energy = read_daily_energy(session, monitor, key, first, end_day)
    days = [(day, kwh, weather_day(temperature, humidity, local_day(day))["temp_mean_c"]) for day, kwh in energy]
    return {
        "analytics_heatmap": heatmap(hourly_power) if hourly_power else None,
        "analytics_daily": daily_energy(energy),
        "analytics_scatter": daily_scatter(days),
    }


class SnapshotConfig(dg.Config):
    now: str | None = None  # ISO datetime the snapshot is taken at; default the current time


@dg.asset(
    group_name="twin",
    deps=[thingsflow_asset_model],
    description="Today so far against yesterday, on the building and each circuit, as today_* SERVER_SCOPE attributes.",
)
def today_snapshot(
    context: dg.AssetExecutionContext, config: SnapshotConfig, thingsflow: ThingsFlowResource,
    asset_model_file: AssetModelFile,
) -> dg.MaterializeResult:
    model = asset_model_file.load()
    now = dt.datetime.fromisoformat(config.now) if config.now else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:  # a naive time is the building's wall clock, not the server's
        now = now.replace(tzinfo=ZoneInfo(TIMEZONE))
    today = today_window(now)
    # At 00:00 today's window is empty: nothing is known about today yet, and
    # nothing is read for it (nor for the equally empty same time yesterday).
    started = today.end_ms > today.start_ms
    yesterday = local_day(dt.datetime.fromtimestamp(today.start_ms / 1000, ZoneInfo(TIMEZONE)).date() - dt.timedelta(days=1))
    with thingsflow.session() as session:
        plan, rate, monitor, _ = _context(session, thingsflow, model)
        name = model["building"]["name"]
        building = session.assets().get(name)
        if building is None:
            raise dg.Failure(f"building {name!r} is not in ThingsFlow yet; materialise thingsflow_asset_model first")
        key = main_key(plan)
        if started:
            result = measure(session, monitor, plan, rate, today, days=1)
            peak_w, peak_at = usable_peak(result.main, read_peak(session, monitor, key, today))
            same_time = read_energy(session, monitor, key, same_time_yesterday(today))
        else:
            found, missing = circuits(session, plan)
            result = DailyResult(found=found, missing_assets=missing)
            peak_w, peak_at, same_time = None, None, None
        energy = usable_energy(result.main)
        before = read_metrics(session, monitor, key, yesterday)
        yesterday_energy = usable_energy(before)
        attrs = {
            "today_energy_kwh": energy,
            "today_cost_cad": day_cost(energy, rate),
            "today_peak_w": peak_w,
            "today_peak_at": peak_at,
            "yesterday_energy_kwh": yesterday_energy,
            "yesterday_cost_cad": day_cost(yesterday_energy, rate),
            "yesterday_peak_w": usable_peak(before, read_peak(session, monitor, key, yesterday))[0],
            "yesterday_same_time_kwh": same_time,
            # Priced like today_cost_cad (one day's bill), so the dashboard compares like with like.
            "yesterday_same_time_cost_cad": day_cost(same_time, rate),
            "today_updated_at": int(now.timestamp() * 1000),
        }
        session.save_attributes(entity_ref("ASSET", building.id.id), "SERVER_SCOPE", _known(attrs, "today_unknown"))
        for c in result.found:
            circuit_energy = usable_energy(result.circuits.get(c.name))
            # allocate prices circuits at the building's effective rate; a flagged aggregate has no usable rate.
            cost = result.circuits[c.name].cost_cad if circuit_energy is not None and energy is not None else None
            values = {"today_energy_kwh": circuit_energy, "today_cost_cad": cost}
            session.save_attributes(entity_ref("ASSET", c.asset_id), "SERVER_SCOPE", _known(values, "today_unknown"))
    _warn_missing(context, result.missing_assets)
    return dg.MaterializeResult(metadata={
        "window_start": today.start_ms, "window_end": today.end_ms,
        "today_energy_kwh": energy, "unknown": _known(attrs, "today_unknown")["today_unknown"],
    })


today_snapshot_job = dg.define_asset_job("today", selection=[today_snapshot])
today_snapshot_schedule = dg.ScheduleDefinition(
    job=today_snapshot_job, cron_schedule="*/15 * * * *", execution_timezone=TIMEZONE,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)

daily_metrics_job = dg.define_asset_job("daily_metrics", selection=[circuit_daily_metrics])
daily_metrics_schedule = dg.build_schedule_from_partitioned_job(
    daily_metrics_job, hour_of_day=1, minute_of_hour=30, default_status=dg.DefaultScheduleStatus.RUNNING
)
twin_summary_job = dg.define_asset_job("twin_summary", selection=[asset_twin_summary])
twin_summary_schedule = dg.ScheduleDefinition(
    job=twin_summary_job, cron_schedule="45 1 * * *", execution_timezone=TIMEZONE,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)


defs = dg.Definitions(
    assets=[thingsflow_asset_model, weather_observations, weather_forecast, thingsflow_dashboard,
            circuit_daily_metrics, asset_twin_summary, today_snapshot],
    jobs=[sync_asset_model_job, weather_job, weather_forecast_job, daily_metrics_job, twin_summary_job,
          today_snapshot_job],
    schedules=[daily_asset_model_sync, hourly_weather_schedule, weather_forecast_schedule, daily_metrics_schedule,
               twin_summary_schedule, today_snapshot_schedule],
    sensors=[asset_model_inputs_changed],
    resources={
        "thingsflow": ThingsFlowResource(
            url=dg.EnvVar("THINGSFLOW_URL"),
            username=dg.EnvVar("THINGSFLOW_USERNAME"),
            password=dg.EnvVar("THINGSFLOW_PASSWORD"),
            monitor_device_id=dg.EnvVar("GATE_MONITOR_DEVICE_ID"),
            # Read like asset_history: a Dagster EnvVar cannot have a default, and
            # the asset-model sync and sensor run without the ingest gateway.
            ingest_url=os.environ.get("THINGSFLOW_INGEST_URL", ""),
            asset_history=os.environ.get("GATE_ASSET_HISTORY", "false").lower() == "true",
        ),
        "asset_model_file": AssetModelFile(path=dg.EnvVar("GATE_ASSET_MODEL_PATH")),
        # Read like THINGSFLOW_INGEST_URL: only thingsflow_dashboard needs it.
        "dashboard_file": DashboardFile(path=os.environ.get("GATE_DASHBOARD_PATH", "")),
        "weather": WeatherResource(),
    },
)
