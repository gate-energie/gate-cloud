# What came over from `gate-energie/gate`

`gate-energie/gate` (archived, last commit `273006e`, 2026-06-05) was a
FastAPI backend and a Next.js dashboard on Cloud Run, reading telemetry from
BigQuery and storing settings and twin results in Firestore. ThingsFlow now
holds the telemetry, the entities and the dashboards, so most of that code has
no job left. Only domain logic moves here, and only when something uses it.

| Legacy piece | Decision | Where it lives now |
|---|---|---|
| Hydro-Québec Rate D (`settings_service.calc_hydro_quebec_cost`) | **Carried over**, same arithmetic, tested against the legacy output | `src/gate_cloud/tariff.py` |
| Tariff and budget settings (Firestore `user_settings`) | **Replaced** by Building SERVER_SCOPE attributes | `charts/gate-cloud/files/asset_model.yaml` |
| Asset list (`circuit_config.yaml` read by `asset_service`) | **Replaced** by the monitor's `circuit_map` + the asset model | `src/gate_cloud/asset_model.py` |
| Digital twin metrics (`asset_service.update_asset_model`: utilisation, energy fraction, temperature/humidity correlation, health) | **To port in F3** as a partitioned Dagster asset reading ThingsFlow history; the BigQuery queries do not carry over | — |
| MCP server (`mcp_server.py`: phase imbalance, historical energy, circuit costs, twins, forecast) | **To port in F4**, pointed at the ThingsFlow API | — |
| Weather service (OpenWeatherMap → MQTT) | **To port in F3** as a ThingsFlow device fed over HTTP ingest. The legacy file has an API key hard-coded; it must be rotated, not copied | — |
| Energy endpoints (`energy_service.py`: current power, timeline, peaks, breakdown) | **Dropped**: ThingsFlow serves latest values and aggregated history through its telemetry API | — |
| Next.js dashboard | **Dropped for now**; ThingsBoard dashboards first. Components may be reused if a dedicated app (F5) is built | — |
| BigQuery / Firestore clients, Cloud Build, Cloud Run config | **Dropped** | — |
| `config/gate-481810-474f2278f355.json` | **Never copy.** A GCP service-account private key committed to the legacy repo; it must be revoked in IAM | — |
| Project plan, reports, presentations (`docs/`) | Stay in the archived repository, which remains readable | — |
