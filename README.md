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
| asset `thingsflow_dashboard` | Renders the dashboard template with the circuits of the asset model and the weather device, and creates or updates `GATE — Operación` in ThingsFlow (no write when unchanged). Needs `weather_observations` to have run once | with the sync (on change, and daily) |
| asset `weather_observations` | Hourly Open-Meteo weather at the building, ingested into the `GATE Weather` device | hourly, :10 |
| asset `circuit_daily_metrics` | Per-circuit and building metrics for one local day (energy from the annual counter, cost at the building's Rate D, utilisation, coverage, degree days) | daily 01:30, backfillable |
| asset `asset_twin_summary` | 30-day summary per circuit as `twin_*` attributes (energy, cost, fraction, correlation with temperature and humidity, overload against `rated_power_w`). Unknown values are not written; `twin_unknown` lists them, so a value left from an earlier window is not mistaken for a current one. Also `twin_7d_energy_kwh` and `twin_7d_cost_cad` per circuit, and on the Building the month (`month_*`) and the analytics (`analytics_*`) below | nightly 01:45 |
| asset `today_snapshot` | Today so far against yesterday, on the Building and each circuit, as `today_*` / `yesterday_*` attributes | every 15 min |
| asset `weather_forecast` | Open-Meteo outlook (next 3 days, next 12 hours) as the `forecast` attribute of `GATE Weather` | hourly, :05 |

### Attributes the dashboard reads

All are SERVER_SCOPE. Day and hour boundaries are America/Toronto. A value that
cannot be known is not written (never null); each group lists the keys it left
out in its `*_unknown` attribute (comma-separated, empty when none), so a stale
value from an earlier run is recognisable.

| Entity | Attributes | Written by |
|---|---|---|
| Building | `today_energy_kwh`, `today_cost_cad`, `today_peak_w`, `today_peak_at`, `yesterday_energy_kwh`, `yesterday_cost_cad`, `yesterday_peak_w`, `yesterday_same_time_kwh`, `today_updated_at`, `today_unknown` | `today_snapshot` |
| circuit | `today_energy_kwh`, `today_cost_cad`, `today_unknown` | `today_snapshot` |
| Building | `month_energy_kwh`, `month_energy_source`, `month_cost_cad`, `month_budget_used_pct`, `month_projected_cost_cad`, `month_avg_daily_cost_cad`, `month_days_left`, `month_updated_at`, `month_unknown` | `asset_twin_summary` |
| Building | `analytics_heatmap` (weekday x hour mean power, last 30 days), `analytics_scatter` (daily energy against mean temperature since `HISTORY_START`), `analytics_updated_at`, `analytics_unknown` | `asset_twin_summary` |
| circuit | `twin_*` (30 days), `twin_7d_energy_kwh`, `twin_7d_cost_cad`, `twin_updated_at`, `twin_unknown` | `asset_twin_summary` |
| `GATE Weather` | `forecast` (JSON: `days`, `hours`), `forecast_updated_at`, `forecast_unknown` | `weather_forecast` |

A main aggregate or circuit flagged `negative_power`, or with negative energy,
leaves its costs and totals unknown.

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
| `GATE_DASHBOARD_PATH` | directory of the mounted dashboard template (`/etc/gate-cloud/dashboard`); required by `thingsflow_dashboard` only |
| `GATE_ASSET_HISTORY` | `true` writes `circuit_daily_metrics` to assets as telemetry; default `false` |

## Dashboard

`thingsflow_dashboard` publishes `GATE — Operación`, dark-themed, with three
pages linked from a header on each:

- **Overview** (the root): power flow from the grid to each circuit, live
  telemetry, grid phases, today's energy and cost against yesterday, 24-hour
  and 30-day consumption, the weather forecast, the month's budget, and cost
  per circuit.
- **Analytics**: a 7-day power timeline, the weekday x hour heatmap, daily
  energy against temperature, and the 30-day share of each circuit.
- **Assets**: one card per circuit (30-day energy, cost and share,
  utilisation, correlation with weather, health and overload against the
  rated power, today's energy) and 7-day power per circuit.

The template is a directory, `charts/gate-cloud/files/dashboard/`:
`dashboard.json` (the ThingsFlow export with placeholders), `theme.css`, and
one script per custom card in `widgets/` (`_lib.js` is shared). Strings in
`dashboard.json` pull files in with `${FILE:widgets/<card>.js}`; the sync
inlines them, fills in the device ids and the circuits, and checks the result
against what ThingsFlow can serve. The card scripts output HTML and CSS only;
their tests run with
`node --test "charts/gate-cloud/files/dashboard/tests/*.test.mjs"`.

The chart ships the directory as the ConfigMap `gate-cloud-dashboard`
(`dashboard.json`, `theme.css` and `widgets/*.js`; `tests/` stays out) and
mounts it at `/etc/gate-cloud/dashboard`. A new widget file also needs its
item in the `volumes` of `values.yaml` (`tests/test_chart.py` checks the list).
The template ships with the chart: there is no values override for it.

### Changing the dashboard

Edit the dashboard in the ThingsFlow UI, then export it as JSON. Turn the
export back into the template with

```bash
uv run python -m gate_cloud.dashboard templatize export.json <monitor-id> <weather-id> \
  --files charts/gate-cloud/files/dashboard \
  --output charts/gate-cloud/files/dashboard/dashboard.json
```

Use `--output`, not a shell redirect: `--files` reads `dashboard.json` to find
the card files, and `> dashboard.json` would empty it before the command runs.
`--output` writes a temporary file next to the target and renames it over the
target once the template is complete, so a failed run leaves it unchanged.
Without `--output` the template goes to stdout.

`--files` puts each card script back as its `${FILE:...}` reference, so
changes to a card's code belong in `widgets/`, not in the UI. Commit, then
`helm upgrade`; the sensor sees the new template and re-publishes it. UI
edits are otherwise overwritten by the next sync.

ThingsFlow does not sort entity tables yet, so tables list rows in creation order.

## Credentials

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
GATE_ASSET_MODEL_PATH=charts/gate-cloud/files/asset_model.yaml \
GATE_DASHBOARD_PATH=charts/gate-cloud/files/dashboard uv run dagster dev
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
history, K8sRunLauncher) and adds the asset model and dashboard ConfigMaps and a NetworkPolicy
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
