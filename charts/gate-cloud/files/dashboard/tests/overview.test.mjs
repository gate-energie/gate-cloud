import { test } from "node:test";
import assert from "node:assert/strict";
import { renderCard as renderRaw } from "./harness.mjs";

// gate.fmt puts a thin space (U+2009) before units; assertions use a plain space.
const renderCard = (file, data) => renderRaw(file, data).replace(/\u2009/g, " ");

// ThingsBoard hands every value to the markdown function as a string.
const ENTITY = /^(entity|deviceName|aliasName|ds)/;

const scatter = Array.from({ length: 35 }, (_, i) => ({
  date: `2026-09-${String((i % 30) + 1).padStart(2, "0")}`,
  kwh: i === 34 ? 20 : 10,
  temp_mean_c: 5,
}));
const forecast = {
  days: [
    { date: "2026-10-04", tmin: -1.2, tmax: 5.4, code: 61 },
    { date: "2026-10-05", tmin: 0, tmax: 8, code: 3 },
    { date: "2026-10-06", tmin: 2, tmax: 9, code: 95 },
  ],
  hours: [{ ts: 1, temp: 3, code: 45 }],
};

const monitorCircuits = {
  entityName: "GATE Monitor", entityType: "DEVICE",
  main_total_active_power: "2500",
  Heating: "1200", Lights: "80", "Water Heater": "600", Ventilation: "300", Refrigerator: "150",
};
const monitor = {
  entityName: "GATE Monitor", entityType: "DEVICE",
  main_total_active_power: "2500", main_phase_1_voltage: "121.5", main_phase_2_voltage: "122.5",
  main_total_current: "20.5", main_phase_1_active_power: "1500", main_phase_2_active_power: "1000",
};
const building = {
  entityName: "GATE", entityType: "ASSET",
  today_energy_kwh: "12.4", today_peak_w: "3100", yesterday_peak_w: "2900", today_cost_cad: "1.85",
  yesterday_same_time_kwh: "10", monthly_budget: "150", month_cost_cad: "60", month_projected_cost_cad: "140",
  month_budget_used_pct: "40", month_avg_daily_cost_cad: "2", month_days_left: "27", yesterday_cost_cad: "2.10",
  analytics_scatter: JSON.stringify(scatter),
};
const circuit = (label, today, cost, d7, c7, d30, c30) => ({
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
const weather = {
  entityName: "GATE Weather", entityType: "DEVICE",
  temperature_c: "-1.1", humidity_pct: "55", wind_speed_ms: "2.7", forecast: JSON.stringify(forecast),
};

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
  "power_flow.js": [monitorCircuits, { entityName: "GATE", entityType: "ASSET", today_energy_kwh: "12.4" }],
  "live_telemetry.js": [monitor, building],
  "grid_phases.js": [monitor, building],
  "today_energy.js": [building, ...circuits],
  "daily_bars.js": [building],
  "weather.js": [weather],
  "budget.js": [building],
  "circuit_cost.js": circuits,
};

for (const [file, data] of Object.entries(cards)) {
  test(`${file}: blank values render without exceptions`, () => {
    const out = renderCard(file, blank(data));
    if (file === "daily_bars.js") assert.match(out, /No data yet/);
    else assert.match(out, /—/);
  });
  test(`${file}: JSON and numbers as strings or values give identical HTML`, () => {
    assert.equal(renderCard(file, typed(data)), renderCard(file, data));
  });
  test(`${file}: missing data renders`, () => {
    renderCard(file, []);
    renderCard(file, undefined);
  });
}

const count = (out, re) => (out.match(re) || []).length;

test("power_flow: grid, building, top 4 loads and other loads", () => {
  const out = renderCard("power_flow.js", cards["power_flow.js"]);
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
});

test("power_flow: other loads never negative, unknown without total", () => {
  const low = renderCard("power_flow.js", [{ ...monitorCircuits, main_total_active_power: "1000" }]);
  assert.match(low, /Other loads<\/div><div class="gate-flow-value">0\.00 kW/);
  const none = renderCard("power_flow.js", [{ ...monitorCircuits, main_total_active_power: "" }]);
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
});

test("daily_bars: last 30 days with max and average", () => {
  const out = renderCard("daily_bars.js", cards["daily_bars.js"]);
  assert.match(out, /gate-title">CONSUMPTION — 30 DAYS</);
  assert.equal(count(out, /class="gate-col"/g), 30);
  assert.match(out, /title="2026-09-05: 20\.0 kWh"/);
  assert.match(out, /height:100%/);
  assert.match(out, /height:50%/);
  assert.match(out, /Max<\/span> <span class="gate-value">20\.0 kWh/);
  assert.match(out, /Average<\/span> <span class="gate-value">10\.3 kWh/);
  assert.match(renderCard("daily_bars.js", [{ ...building, analytics_scatter: "[]" }]), /No data yet/);
});

test("weather: current conditions and three forecast tiles", () => {
  const out = renderCard("weather.js", cards["weather.js"]);
  assert.match(out, /gate-title">WEATHER OUTLOOK</);
  assert.match(out, /-1\.1 °C/);
  assert.match(out, /gate-condition">Fog</);
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
    const f = JSON.stringify({ days: [], hours: [{ ts: 1, temp: 0, code: Number(code) }] });
    assert.match(renderCard("weather.js", [{ ...weather, forecast: f }]), new RegExp(`gate-condition">${text}<`));
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
    /gate-delta gate-ok">▼ \$0\.25 vs yesterday/]) assert.match(out, re);
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
