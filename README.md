# GATE cloud

Energy asset operations for the GATE building, running **on top of**
[ThingsFlow](https://github.com/lirei-uqtr/thingsflow) — not inside it.

ThingsFlow is the IoT platform: device ingest, telemetry history, alarms,
dashboards, the ThingsBoard-compatible API. GATE cloud is a solution deployed
next to it in its own namespace. It owns what is specific to this building:
which circuit is which piece of equipment, the Hydro-Québec tariff, the budget,
and the analytics built on them.

```
gate-energie/edge          ThingsFlow (ns thingsflow)          gate-cloud (ns gate)
  Refoss EM16P   ──HTTP──►  device telemetry      ◄──REST──  Dagster
  sync_attributes ───────►  device circuit_map    ◄──REST──    thingsflow_asset_model
                            Building/Panel/circuit assets ◄──    (+ analytics, weather)
```

The only contract between the projects is the ThingsFlow REST API, used through
the official ThingsBoard Python client (`tb-rest-client`). GATE cloud never
reads ThingsFlow's databases and never reads the edge repository: the edge
publishes the circuit map as a device attribute, and GATE cloud reads it from
there.

## What runs

| Dagster object | What it does | When |
|---|---|---|
| asset `thingsflow_asset_model` | Creates/updates asset profiles, the Building, the Panel and one asset per circuit, with "Contains" relations down to the monitor device | on change, and daily |
| sensor `asset_model_inputs_changed` | Hashes the asset model file and the monitor's `circuit_map`; requests a run when either changes. On from deployment | every 2 min |
| schedule `daily_asset_model_sync` | Re-applies the model, restoring anything edited by hand | 06:00 America/Toronto |
| asset `weather_observations` | Hourly Open-Meteo weather at the building, ingested into the `GATE Weather` device | hourly, :10 |
| asset `circuit_daily_metrics` | Per-circuit and building metrics for one local day (energy from the annual counter, cost at the building's Rate D, utilisation, coverage, degree days) | daily 01:30, backfillable |
| asset `asset_twin_summary` | 30-day summary per circuit as `twin_*` attributes (energy, cost, fraction, correlation with temperature and humidity, overload against `rated_power_w`). Unknown values are not written; `twin_unknown` lists them, so a value left from an earlier window is not mistaken for a current one | daily 01:45 |

Metrics keep what the monitor measured. A circuit whose power is negative (a
clamp installed backwards) is flagged `quality: negative_power` and left out
of the building's fractions and costs rather than corrected.

The sync looks up before it writes (a second run writes nothing) and never
deletes: a circuit that disappears from the monitor is reported as an orphan
in the run metadata, for a person to retire.

## Configuration

`charts/gate-cloud/files/asset_model.yaml` — equipment type and label per circuit, plus the
tariff and budget on the Building. It ships in the chart as a ConfigMap; edit
it and `helm upgrade`, and the sensor picks up the change.

Environment, set by the chart:

| Variable | Meaning |
|---|---|
| `THINGSFLOW_URL` | flow-core base URL, in-cluster by default |
| `THINGSFLOW_USERNAME` / `THINGSFLOW_PASSWORD` | tenant-admin account dedicated to GATE (Secret) |
| `GATE_MONITOR_DEVICE_ID` | ThingsFlow id of the Refoss device |
| `GATE_ASSET_MODEL_PATH` | path of the mounted asset model |
| `THINGSFLOW_INGEST_URL` | ThingsFlow HTTP ingest gateway, in-cluster by default; required by `weather_observations` only |
| `GATE_ASSET_HISTORY` | `true` writes `circuit_daily_metrics` to assets as telemetry; default `false` |

ThingsFlow has no API keys, so GATE uses a dedicated tenant-admin user. Create
it in ThingsFlow and store its credentials in the Secret named by
`thingsflow.existingSecret`.

## Weather

`weather_observations` reads Open-Meteo, which needs no API key and is free for
non-commercial use (10 000 calls/day); GATE is UQTR research. The location is
the Building's `latitude` and `longitude` in the asset model.

## Develop

```bash
uv sync
uv run pytest
THINGSFLOW_URL=http://localhost:8082 THINGSFLOW_USERNAME=tenant@thingsboard.org \
THINGSFLOW_PASSWORD=tenant GATE_MONITOR_DEVICE_ID=<device-id> \
THINGSFLOW_INGEST_URL=http://localhost:8081 \
GATE_ASSET_MODEL_PATH=charts/gate-cloud/files/asset_model.yaml uv run dagster dev
```

## Deploy

```bash
helm repo add dagster https://dagster-io.github.io/helm
helm dependency build charts/gate-cloud
kubectl create namespace gate
kubectl -n gate create secret generic gate-cloud-thingsflow \
  --from-literal=THINGSFLOW_USERNAME=gate-ops@example.org \
  --from-literal=THINGSFLOW_PASSWORD='…'
helm upgrade --install gate-cloud charts/gate-cloud -n gate \
  --set monitorDeviceId=<device-id>
```

After the first install, backfill `weather_observations` from `HISTORY_START`
(`src/gate_cloud/definitions.py`) to now in the UI; its single-run backfill
policy fetches the whole range in one run. Then backfill
`circuit_daily_metrics` over the same days. `asset_twin_summary` needs no
backfill: it runs nightly by itself over the last 30 days.

The chart wraps the official Dagster chart (webserver, daemon, Postgres for run
history, K8sRunLauncher) and adds the asset model ConfigMap and a NetworkPolicy
that lets the user-code and run pods reach flow-core, the HTTP ingest gateway and
HTTPS to the internet (Open-Meteo) and nothing else outside the namespace.

## Known ThingsFlow gaps

`tb-rest-client` exposes a few places where ThingsFlow does not yet match the
ThingsBoard contract (asset lookup by name, the response of an asset update,
the v2 attributes path). `src/gate_cloud/thingsflow.py` works around each one
and names it; they are tracked in ThingsFlow as `thingsflow-nkn`.

## History

This repository replaces `gate-energie/gate` (FastAPI + Next.js on Cloud Run,
BigQuery, Firestore). See [docs/legacy.md](docs/legacy.md) for what was carried
over and what was deliberately left behind.
