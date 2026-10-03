import { test } from "node:test";
import assert from "node:assert/strict";
import { renderCard as renderRaw } from "./harness.mjs";

// gate.fmt puts a thin space (U+2009) before units; assertions use a plain space.
const renderCard = (file, data, ctx) => renderRaw(file, data, ctx).replace(/ /g, " ");

// Fields ThingsBoard adds to every markdown row besides the data keys.
const ENTITY = /^(\$datasource|entity[A-Z]\w*|deviceName|deviceType|aliasName|dsIndex|dsName)$|\|ts$/;
const TS = 1759500000000;

// A row the way ThingsBoard builds it: entity fields, datasource info and,
// for every key, obj[label] = value and obj[label + "|ts"] = timestamp.
const real = (dsName, row) => {
  const out = {
    $datasource: { name: dsName, entityName: row.entityName },
    entityName: row.entityName, deviceName: row.entityName, entityId: "id-" + row.entityName,
    entityType: row.entityType, entityLabel: "", entityDescription: "", aliasName: dsName,
    dsIndex: 0, dsName, deviceType: null,
  };
  for (const [k, v] of Object.entries(row)) {
    if (k === "entityName" || k === "entityType") continue;
    out[k] = v;
    out[k + "|ts"] = TS;
  }
  return out;
};

// 35 consecutive days ending 2026-10-02, with 2026-09-20 missing; the last day is 20 kWh.
const dayIso = (offset) => new Date(Date.UTC(2026, 9, 2) - offset * 86400000).toISOString().slice(0, 10);
const scatter = Array.from({ length: 35 }, (_, i) => 34 - i)
  .map((o) => ({ date: dayIso(o), kwh: o === 0 ? 20 : 10, temp_mean_c: 5 }))
  .filter((p) => p.date !== "2026-09-20");
const NOW = 1759510800000; // 2026-10-03T17:00:00Z
const forecast = {
  days: [
    { date: "2026-10-04", tmin: -1.2, tmax: 5.4, code: 61 },
    { date: "2026-10-05", tmin: 0, tmax: 8, code: 3 },
    { date: "2026-10-06", tmin: 2, tmax: 9, code: 95 },
  ],
  hours: [
    { ts: NOW - 3 * 3600000, temp: 3, code: 0 },
    { ts: NOW - 600000, temp: 3, code: 45 },
    { ts: NOW + 3600000, temp: 3, code: 61 },
  ],
};

const monitorCircuits = real("gate:circuits", {
  entityName: "GATE Monitor", entityType: "DEVICE",
  main_total_active_power: "2500",
  Heating: "1200", Lights: "80", "Water Heater": "600", Ventilation: "300", Refrigerator: "150",
});
const flowCtx = {
  datasources: [
    { name: "gate:circuits", dataKeys: ["main_total_active_power", "Heating", "Lights", "Water Heater", "Ventilation", "Refrigerator"]
      .map((label) => ({ name: label.toLowerCase().replace(/ /g, "_") + "_active_power", label })) },
    { name: "building", dataKeys: [{ name: "today_energy_kwh", label: "today_energy_kwh" }] },
  ],
};
const monitor = real("monitor", {
  entityName: "GATE Monitor", entityType: "DEVICE",
  main_total_active_power: "2500", main_phase_1_voltage: "121.5", main_phase_2_voltage: "122.5",
  main_total_current: "20.5", main_phase_1_active_power: "1500", main_phase_2_active_power: "1000",
});
const building = real("building", {
  entityName: "GATE", entityType: "ASSET",
  today_energy_kwh: "12.4", today_peak_w: "3100", yesterday_peak_w: "2900", today_cost_cad: "1.85",
  yesterday_same_time_kwh: "10", monthly_budget: "150", month_cost_cad: "60", month_projected_cost_cad: "140",
  month_budget_used_pct: "40", month_avg_daily_cost_cad: "2", month_days_left: "27", yesterday_cost_cad: "2.10",
  analytics_scatter: JSON.stringify(scatter),
});
const circuit = (label, today, cost, d7, c7, d30, c30) => real("circuits", {
  entityName: label, entityType: "ASSET", label,
  today_energy_kwh: today, today_cost_cad: cost,
  twin_7d_energy_kwh: d7, twin_7d_cost_cad: c7, twin_energy_kwh: d30, twin_cost_cad: c30,
});
const circuits = [
  circuit("Lights", "0.5", "0.05", "3", "0.3", "12", "1.2"),
  circuit("Heating", "6", "0.6", "40", "4", "180", "18"),
  circuit("Water Heater", "2", "0.2", "14", "1.4", "60", "6"),
  circuit("Ventilation", "1", "0.1", "7", "0.7", "30", "3"),
];
const weather = real("weather", {
  entityName: "GATE Weather", entityType: "DEVICE",
  temperature_c: "-1.1", humidity_pct: "55", wind_speed_ms: "2.7", forecast: JSON.stringify(forecast),
});

const blank = (rows) =>
  rows.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, ENTITY.test(k) ? v : ""])));
// Same data with JSON attributes as objects and numbers as numbers.
const typed = (rows) =>
  rows.map((r) =>
    Object.fromEntries(
      Object.entries(r).map(([k, v]) => {
        if (ENTITY.test(k) || k === "label") return [k, v];
        if (typeof v === "string" && /^[[{]/.test(v)) return [k, JSON.parse(v)];
        return [k, v !== "" && !isNaN(Number(v)) ? Number(v) : v];
      })
    )
  );

const cards = {
  "power_flow.js": [monitorCircuits, real("building", { entityName: "GATE", entityType: "ASSET", today_energy_kwh: "12.4" })],
  "live_telemetry.js": [monitor, building],
  "grid_phases.js": [monitor, building],
  "today_energy.js": [building, ...circuits],
  "daily_bars.js": [building],
  "weather.js": [weather],
  "budget.js": [building],
  "circuit_cost.js": circuits,
};
const ctxs = { "power_flow.js": flowCtx, "weather.js": { now: NOW } };

for (const [file, data] of Object.entries(cards)) {
  const ctx = ctxs[file] || {};
  test(`${file}: blank values render without exceptions`, () => {
    const out = renderCard(file, blank(data), ctx);
    if (file === "daily_bars.js") assert.match(out, /No data yet/);
    else assert.match(out, /—/);
  });
  test(`${file}: JSON and numbers as strings or values give identical HTML`, () => {
    assert.equal(renderCard(file, typed(data), ctx), renderCard(file, data, ctx));
  });
  test(`${file}: missing data renders`, () => {
    renderCard(file, [], ctx);
    renderCard(file, undefined, ctx);
    renderCard(file, [], undefined);
  });
}

const count = (out, re) => (out.match(re) || []).length;

test("power_flow: grid, building, top 4 loads and other loads", () => {
  const out = renderCard("power_flow.js", cards["power_flow.js"], flowCtx);
  assert.match(out, /gate-title">POWER FLOW</);
  assert.match(out, /gate-flow-grid/);
  assert.match(out, /gate-flow-building/);
  assert.equal(count(out, /2\.50 kW/g), 2);
  assert.match(out, /12\.4 kWh/);
  assert.equal(count(out, /gate-flow-load"/g), 5);
  for (const [name, kw] of [["Heating", "1.20"], ["Water Heater", "0.60"], ["Ventilation", "0.30"], ["Refrigerator", "0.15"]]) {
    assert.match(out, new RegExp(`${name}</div><div class="gate-flow-value">${kw} kW`));
  }
  assert.doesNotMatch(out, /Lights/);
  assert.doesNotMatch(out, /GATE Monitor|DEVICE/);
  assert.match(out, /Other loads<\/div><div class="gate-flow-value">0\.25 kW/);
  assert.match(out, /gate-flow-link/);
  assert.doesNotMatch(out, /\|ts|1759500000/);
});

test("power_flow: without ctx.datasources, only numeric data keys count as circuits", () => {
  const out = renderCard("power_flow.js", cards["power_flow.js"], {});
  assert.equal(count(out, /gate-flow-load"/g), 5);
  assert.match(out, /Heating<\/div><div class="gate-flow-value">1\.20 kW/);
  assert.match(out, /Other loads<\/div><div class="gate-flow-value">0\.25 kW/);
  assert.doesNotMatch(out, /\|ts|1759500000|datasource|GATE Monitor/);
});

test("power_flow: a circuit key missing from the row shows as unknown", () => {
  const row = { ...monitorCircuits };
  delete row.Heating;
  // Unknown loads rank after known ones; "Other loads" then covers the unknown one.
  const out = renderCard("power_flow.js", [row], flowCtx);
  assert.doesNotMatch(out, /Heating/);
  assert.match(out, /Lights<\/div><div class="gate-flow-value">0\.08 kW/);
  assert.match(out, /Other loads<\/div><div class="gate-flow-value">1\.37 kW/);
  // With fewer than four circuits an unknown one is shown, and other loads cannot be computed.
  const few = { datasources: [{ name: "gate:circuits", dataKeys: [{ label: "Heating" }, { label: "Lights" }] }] };
  const small = renderCard("power_flow.js", [row], few);
  assert.match(small, /Heating<\/div><div class="gate-flow-value">—/);
  assert.match(small, /Other loads<\/div><div class="gate-flow-value">—/);
});

test("power_flow: other loads never negative, unknown without total", () => {
  const low = renderCard("power_flow.js", [{ ...monitorCircuits, main_total_active_power: "1000" }], flowCtx);
  assert.match(low, /Other loads<\/div><div class="gate-flow-value">0\.00 kW/);
  const none = renderCard("power_flow.js", [{ ...monitorCircuits, main_total_active_power: "" }], flowCtx);
  assert.match(none, /Other loads<\/div><div class="gate-flow-value">—/);
});

test("power_flow: circuit labels are escaped", () => {
  const out = renderCard("power_flow.js", [{ main_total_active_power: "100", "<b>x</b>": "50" }]);
  assert.match(out, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.doesNotMatch(out, /<b>/);
});

test("live_telemetry: five labelled rows", () => {
  const out = renderCard("live_telemetry.js", cards["live_telemetry.js"]);
  assert.match(out, /gate-title">LIVE TELEMETRY</);
  assert.equal(count(out, /class="gate-list-row"/g), 5);
  assert.match(out, /Real-time load<\/span><span class="gate-value">2500 W/);
  assert.match(out, /Voltage<\/span><span class="gate-value">122\.0 V/);
  assert.match(out, /Current<\/span><span class="gate-value">20\.5 A/);
  assert.match(out, /Daily peak<\/span><span class="gate-value">3100 W/);
  assert.match(out, /Energy used<\/span><span class="gate-value">12\.4 kWh/);
  const one = renderCard("live_telemetry.js", [{ ...monitor, main_phase_2_voltage: "" }, building]);
  assert.match(one, /Voltage<\/span><span class="gate-value">—/);
});

test("grid_phases: totals, phases and balance", () => {
  const out = renderCard("grid_phases.js", cards["grid_phases.js"]);
  assert.match(out, /gate-title">GRID &amp; PHASES</);
  assert.match(out, /Total voltage<\/div><div class="gate-stat-value">122\.0 V/);
  assert.match(out, /Total current<\/div><div class="gate-stat-value">20\.5 A/);
  assert.match(out, /Today's peak<\/div><div class="gate-stat-value">3100 W/);
  assert.match(out, /Yesterday's peak<\/div><div class="gate-stat-value">2900 W/);
  assert.equal(count(out, /class="gate-bar"/g), 2);
  assert.match(out, /1500 W/);
  assert.match(out, /1000 W/);
  assert.match(out, /20\.0 %/);
  assert.match(out, /gate-badge gate-amber">IMBALANCED/);
  assert.match(out, /Spread<\/span><span class="gate-value">500 W/);
  const one = renderCard("grid_phases.js", [{ ...monitor, main_phase_1_voltage: "" }, building]);
  assert.match(one, /Total voltage<\/div><div class="gate-stat-value">—/);
  const lost = renderCard("grid_phases.js", [{ ...monitor, main_phase_2_active_power: "" }, building]);
  assert.match(lost, /Spread<\/span><span class="gate-value">—/);
  const even = renderCard("grid_phases.js", [{ ...monitor, main_phase_2_active_power: "1400" }]);
  assert.match(even, /3\.4 %/);
  assert.match(even, /gate-badge gate-ok">BALANCED/);
  const zero = renderCard("grid_phases.js", [{ ...monitor, main_phase_1_active_power: "0", main_phase_2_active_power: "0" }]);
  assert.match(zero, /Imbalance<\/span><span class="gate-value">—/);
  assert.doesNotMatch(zero, /BALANCED/);
});

test("today_energy: kWh, cost, delta and top 3 circuits", () => {
  const out = renderCard("today_energy.js", cards["today_energy.js"]);
  assert.match(out, /gate-title">TODAY&#39;S ENERGY</);
  assert.match(out, /gate-big">12\.4 <span class="gate-unit">kWh/);
  assert.match(out, /\$1\.85/);
  assert.match(out, /gate-delta gate-amber">▲ 24\.0 %/);
  assert.equal(count(out, /class="gate-list-row"/g), 3);
  assert.match(out, /Heating<\/span><span class="gate-value">6\.0 kWh[\s\S]*Water Heater[\s\S]*Ventilation/);
  assert.doesNotMatch(out, /Lights/);
  const down = renderCard("today_energy.js", [{ ...building, today_energy_kwh: "8" }]);
  assert.match(down, /gate-delta gate-ok">▼ 20\.0 %/);
  const same = renderCard("today_energy.js", [{ ...building, today_energy_kwh: "10" }]);
  assert.match(same, /gate-delta gate-muted">0\.0 %/);
  assert.doesNotMatch(same, /[▲▼]/);
});

test("daily_bars: last 30 days with max and average", () => {
  const out = renderCard("daily_bars.js", cards["daily_bars.js"]);
  assert.match(out, /gate-title">CONSUMPTION — 30 DAYS</);
  assert.equal(count(out, /class="gate-col( gate-col-empty)?"/g), 30);
  assert.equal(count(out, /gate-col-empty/g), 1);
  assert.match(out, /^[\s\S]*?class="gate-col" title="2026-09-03: 10\.0 kWh"/);
  assert.match(out, /class="gate-col gate-col-empty" title="2026-09-20: no data"/);
  assert.match(out, /title="2026-10-02: 20\.0 kWh"/);
  assert.doesNotMatch(out, /2026-09-02/);
  assert.match(out, /height:100%/);
  assert.match(out, /height:50%/);
  assert.match(out, /Max<\/span> <span class="gate-value">20\.0 kWh/);
  assert.match(out, /Average<\/span> <span class="gate-value">10\.3 kWh/);
  assert.match(renderCard("daily_bars.js", [{ ...building, analytics_scatter: "[]" }]), /No data yet/);
});

test("weather: current conditions and three forecast tiles", () => {
  const out = renderCard("weather.js", cards["weather.js"], { now: NOW });
  assert.match(out, /gate-title">WEATHER OUTLOOK</);
  assert.match(out, /-1\.1 °C/);
  assert.match(out, /gate-condition gate-condition-now">Fog</);
  assert.match(out, /Humidity<\/span><span class="gate-value">55 %/);
  assert.match(out, /Wind<\/span><span class="gate-value">2\.7 m\/s/);
  assert.equal(count(out, /class="gate-tile"/g), 3);
  assert.match(out, /10\/04[\s\S]*Rain[\s\S]*-1° \/ 5°/);
  assert.match(out, /Cloudy/);
  assert.match(out, /Thunderstorm/);
});

test("weather: WMO codes map to conditions", () => {
  const cases = { 0: "Clear", 1: "Partly cloudy", 2: "Partly cloudy", 3: "Cloudy", 48: "Fog", 53: "Drizzle",
    65: "Rain", 75: "Snow", 81: "Showers", 86: "Snow showers", 99: "Thunderstorm", 7: "—" };
  for (const [code, text] of Object.entries(cases)) {
    const f = JSON.stringify({ days: [], hours: [{ ts: NOW, temp: 0, code: Number(code) }] });
    assert.match(renderCard("weather.js", [{ ...weather, forecast: f }], { now: NOW }), new RegExp(`gate-condition gate-condition-now">${text}<`));
  }
});

test("budget: ring colour and figures", () => {
  const out = renderCard("budget.js", cards["budget.js"]);
  assert.match(out, /gate-title">MONTHLY BUDGET</);
  assert.match(out, /conic-gradient\(#22c55e 0% 40%/);
  assert.match(out, /40 %/);
  for (const re of [/Budget<\/span><span class="gate-value">\$150\.00/, /Projected<\/span><span class="gate-value">\$140\.00/,
    /Month total<\/span><span class="gate-value">\$60\.00/, /Avg daily<\/span><span class="gate-value">\$2\.00/,
    /Days left<\/span><span class="gate-value">27/, /Today's cost<\/span><span class="gate-value">\$1\.85/,
    /Remaining<\/span><span class="gate-value">\$90\.00/,
    /gate-delta gate-ok">▼ \$0\.25 vs yesterday/]) assert.match(out, re);
  const same = renderCard("budget.js", [{ ...building, yesterday_cost_cad: "1.85" }]);
  assert.match(same, /gate-delta gate-muted">\$0\.00 vs yesterday/);
  assert.doesNotMatch(same, /[▲▼]/);
  assert.match(renderCard("budget.js", [{ ...building, month_cost_cad: "" }]), /Remaining<\/span><span class="gate-value">—/);
  assert.match(renderCard("budget.js", [{ ...building, month_budget_used_pct: "85" }]), /conic-gradient\(#f59e0b 0% 85%/);
  assert.match(renderCard("budget.js", [{ ...building, month_budget_used_pct: "130" }]), /conic-gradient\(#ef4444 0% 100%/);
});

test("circuit_cost: sorted rows, three periods and totals", () => {
  const out = renderCard("circuit_cost.js", cards["circuit_cost.js"]);
  assert.match(out, /gate-title">CIRCUIT COST</);
  assert.match(out, /Heating[\s\S]*Water Heater[\s\S]*Ventilation[\s\S]*Lights/);
  assert.equal(count(out, /class="gate-table-row"/g), 4);
  assert.match(out, /Today[\s\S]*7 days[\s\S]*30 days/);
  assert.match(out, /180\.0 kWh · \$18\.00/);
  assert.match(out, /width:100%/);
  assert.match(out, /gate-table-total[\s\S]*9\.5 kWh · \$0\.95[\s\S]*64\.0 kWh · \$6\.40[\s\S]*282\.0 kWh · \$28\.20/);
  const partial = renderCard("circuit_cost.js", [...circuits.slice(1), { ...circuits[0], twin_energy_kwh: "" }]);
  assert.match(partial, /gate-table-total[\s\S]*64\.0 kWh · \$6\.40[\s\S]*— · \$28\.20/);
});

test("weather: the current condition comes from the forecast hour closest to now", () => {
  const later = renderCard("weather.js", cards["weather.js"], { now: NOW + 3000000 });
  assert.match(later, /gate-condition gate-condition-now">Rain</);
  const earlier = renderCard("weather.js", cards["weather.js"], { now: NOW - 3 * 3600000 });
  assert.match(earlier, /gate-condition gate-condition-now">Clear</);
});
