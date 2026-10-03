import { test } from "node:test";
import assert from "node:assert/strict";
import { renderCard as renderRaw, real, blank, typed } from "./harness.mjs";

// gate.fmt puts a thin space (U+2009) before units; assertions use a plain space.
const renderCard = (file, data, ctx) => renderRaw(file, data, ctx).replace(/\u2009/g, " ");
const count = (out, re) => (out.match(re) || []).length;

// Heatmap: 0.2 kW at night, 1.0 kW by day, a 2.5 kW peak on Wednesday 18:00, Sunday 03:00 unknown.
const DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];
const heatValues = DAYS.map((_, d) => Array.from({ length: 24 }, (_, h) =>
  d === 6 && h === 3 ? null : d === 2 && h === 18 ? 2.5 : h < 6 ? 0.2 : 1.0));
const heatmap = {
  unit: "kW", days: DAYS, values: heatValues,
  samples: heatValues.map((row) => row.map((v) => (v === null ? 0 : 4))),
};

// Scatter: ten days on a perfect line kWh = 30 - temperature, temperatures -5 .. 13 °C.
const scatter = Array.from({ length: 10 }, (_, i) => ({
  date: `2026-09-${String(20 + i).padStart(2, "0")}`, kwh: 35 - 2 * i, temp_mean_c: -5 + 2 * i,
}));

const building = real("building", {
  entityName: "GATE", entityType: "ASSET",
  analytics_heatmap: JSON.stringify(heatmap), analytics_scatter: JSON.stringify(scatter),
});

const circuit = (label, type, values) => real("circuits", {
  entityName: label, entityType: "ASSET", label, type,
  twin_energy_kwh: "", twin_cost_cad: "", twin_energy_fraction_pct: "", twin_utilization_pct: "",
  twin_corr_temperature: "", twin_corr_humidity: "", today_energy_kwh: "", twin_quality: "",
  twin_health_score: "", twin_overload: "", rated_power_w: "", ...values,
});
const breakdownRows = [
  circuit("Lights", "Lighting", { twin_energy_kwh: "12", twin_energy_fraction_pct: "2.4" }),
  circuit("Heating", "Heating", { twin_energy_kwh: "180", twin_energy_fraction_pct: "36" }),
  circuit("Ceiling Fan", "Fan", {}),
  circuit("Water Heater", "Water heater", { twin_energy_kwh: "60", twin_energy_fraction_pct: "12" }),
  circuit("Dryer", "Appliance", { twin_energy_kwh: "5", twin_energy_fraction_pct: "1" }),
  circuit("Ventilation", "HVAC", { twin_energy_kwh: "30", twin_energy_fraction_pct: "6" }),
  circuit("Solar", "Generation", { twin_energy_kwh: "-3", twin_quality: "negative_power" }),
  circuit("Refrigerator", "Appliance", { twin_energy_kwh: "20", twin_energy_fraction_pct: "4" }),
  circuit("Range Hood", "Appliance", { twin_energy_kwh: "8", twin_energy_fraction_pct: "1.6" }),
];
const assetRows = [
  circuit("Lights", "Lighting", {
    twin_energy_kwh: "12", twin_cost_cad: "1.2", twin_energy_fraction_pct: "2.4", twin_utilization_pct: "30",
    today_energy_kwh: "0.5",
  }),
  circuit("Heating", "Heating", {
    twin_energy_kwh: "180", twin_cost_cad: "18", twin_energy_fraction_pct: "36", twin_utilization_pct: "42.5",
    twin_corr_temperature: "-0.82", twin_corr_humidity: "0.1", today_energy_kwh: "6",
    twin_health_score: "70", twin_overload: "true", rated_power_w: "3000",
  }),
  circuit("Solar", "Generation", { twin_energy_kwh: "-3", twin_utilization_pct: "10", today_energy_kwh: "0", twin_quality: "negative_power" }),
  circuit("Water Heater", "Water heater", {
    twin_energy_kwh: "60", twin_cost_cad: "6", twin_energy_fraction_pct: "12", twin_utilization_pct: "20",
    twin_corr_temperature: "0.05", twin_corr_humidity: "-0.45", today_energy_kwh: "2",
    twin_health_score: "100", twin_overload: "false", rated_power_w: "4500",
  }),
];

const cards = {
  "heatmap.js": [building],
  "scatter.js": [building],
  "breakdown.js": breakdownRows,
  "asset_cards.js": assetRows,
};

for (const [file, data] of Object.entries(cards)) {
  test(`${file}: blank values render without exceptions`, () => {
    assert.match(renderCard(file, blank(data)), /—/);
  });
  test(`${file}: JSON and numbers as strings or values give identical HTML`, () => {
    assert.equal(renderCard(file, typed(data)), renderCard(file, data));
  });
  test(`${file}: missing data renders`, () => {
    assert.match(renderCard(file, []), /—/);
    renderCard(file, undefined);
    renderCard(file, [], undefined);
  });
}

test("heatmap: 7 day rows by 24 hours with a five-step scale", () => {
  const out = renderCard("heatmap.js", [building]);
  assert.match(out, /gate-title">CONSUMPTION HEATMAP</);
  assert.match(out, /grid-template-columns:auto repeat\(24, ?1fr\)/);
  assert.equal(count(out, /class="gate-heat-day"/g), 7);
  for (const day of DAYS) assert.match(out, new RegExp(`class="gate-heat-day">${day}<`));
  assert.equal(count(out, /class="gate-heat-cell[" ]/g), 168);
  assert.equal(count(out, /class="gate-heat-hour"/g), 24);
  assert.deepEqual([...out.matchAll(/class="gate-heat-hour">(\d+)</g)].map((m) => m[1]), ["0", "6", "12", "18"]);
  // 2.5 / 2.5 → top step, 1.0 / 2.5 → middle step, 0.2 / 2.5 → bottom step.
  assert.equal(count(out, /class="gate-heat-cell" style="background:#22d3ee"/g), 1);
  assert.match(out, /class="gate-heat-cell" style="background:#22d3ee" title="mié 18:00 · 2\.50 kW"/);
  assert.equal(count(out, /class="gate-heat-cell" style="background:#1a768b"/g), 7 * 18 - 1);
  assert.equal(count(out, /class="gate-heat-cell" style="background:#111827"/g), 7 * 6 - 1);
  assert.equal(count(out, /gate-heat-empty/g), 1);
  assert.match(out, /class="gate-heat-cell gate-heat-empty" title="dom 03:00 · no data"/);
  assert.equal(count(out, /class="gate-heat-swatch"/g), 5);
  assert.match(out, /Max<\/span> <span class="gate-value">2\.50 kW/);
});

test("heatmap: labels from data are escaped, all-unknown values show no scale", () => {
  const odd = { ...heatmap, days: ["<b>x</b>", ...DAYS.slice(1)], values: heatmap.values.map((r) => r.map(() => null)) };
  const out = renderCard("heatmap.js", [{ ...building, analytics_heatmap: JSON.stringify(odd) }]);
  assert.match(out, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.doesNotMatch(out, /<b>/);
  assert.equal(count(out, /gate-heat-empty/g), 168);
  assert.match(out, /Max<\/span> <span class="gate-value">—/);
});

test("scatter: positioned dots, axis range, trend line and Pearson r", () => {
  const out = renderCard("scatter.js", [building]);
  assert.match(out, /gate-title">POWER vs TEMPERATURE</);
  assert.equal(count(out, /class="gate-dot"/g), 10);
  assert.match(out, /class="gate-dot" style="left:0\.0%;bottom:100\.0%" title="2026-09-20: 35\.0 kWh · -5\.0 °C"/);
  assert.match(out, /class="gate-dot" style="left:100\.0%;bottom:48\.6%" title="2026-09-29: 17\.0 kWh · 13\.0 °C"/);
  assert.match(out, /gate-axis-x-min">-5\.0 °C/);
  assert.match(out, /gate-axis-x-max">13\.0 °C/);
  assert.match(out, /gate-axis-y-min">0\.0 kWh/);
  assert.match(out, /gate-axis-y-max">35\.0 kWh/);
  // From (0 %, 100 %) to (100 %, 48.6 %) in a 2:1 box: 14.4° down, 103.3 % of the width long.
  assert.match(out, /class="gate-trend" style="left:0\.0%;bottom:100\.0%;width:103\.3%;transform:rotate\(14\.4deg\);transform-origin:0 50%"/);
  assert.match(out, /r = -1\.00/);
  assert.match(out, /High/);
});

test("scatter: fewer than 7 days gives no r; points without temperature are skipped", () => {
  const six = renderCard("scatter.js", [{ ...building, analytics_scatter: JSON.stringify(scatter.slice(0, 6)) }]);
  assert.equal(count(six, /class="gate-dot"/g), 6);
  assert.match(six, /r = —/);
  // Like r, the trend needs a week of days.
  assert.doesNotMatch(six, /gate-trend/);
  const seven = renderCard("scatter.js", [{ ...building, analytics_scatter: JSON.stringify(scatter.slice(0, 7)) }]);
  assert.match(seven, /gate-trend/);
  const gaps = [...scatter, { date: "2026-09-30", kwh: 50, temp_mean_c: null }, { date: "<i>", kwh: "", temp_mean_c: 1 }];
  const out = renderCard("scatter.js", [{ ...building, analytics_scatter: JSON.stringify(gaps) }]);
  assert.equal(count(out, /class="gate-dot"/g), 10);
  assert.doesNotMatch(out, /<i>/);
  const flat = renderCard("scatter.js", [{ ...building, analytics_scatter: JSON.stringify(scatter.map((p) => ({ ...p, temp_mean_c: 5 }))) }]);
  assert.match(flat, /r = —/);
  assert.doesNotMatch(flat, /gate-trend/);
});

test("breakdown: bars sorted by kWh and a donut of the top 6 plus other", () => {
  const out = renderCard("breakdown.js", breakdownRows);
  assert.match(out, /gate-title">APPLIANCE BREAKDOWN</);
  assert.equal(count(out, /class="gate-breakdown-row"/g), 9);
  assert.match(out, /Heating[\s\S]*Water Heater[\s\S]*Ventilation[\s\S]*Refrigerator[\s\S]*Lights[\s\S]*Range Hood[\s\S]*Dryer[\s\S]*Ceiling Fan[\s\S]*Solar/);
  assert.match(out, /Heating<\/span><span class="gate-value">180\.0 kWh · 36\.0 %/);
  assert.match(out, /Ceiling Fan<\/span><span class="gate-value">— · —/);
  // A circuit flagged by quality is unknown, not a negative share.
  assert.match(out, /Solar<\/span><span class="gate-value">— · —/);
  assert.match(out, /width:100%/);
  assert.match(out, /width:33%/);
  assert.match(out, /conic-gradient\(#22d3ee 0% 36%, #3b82f6 36% 48%, #f59e0b 48% 54%, #94a3b8 54% 58%, #a855f7 58% 60\.4%, #22c55e 60\.4% 62%, #4b5563 62% 100%\)/);
  assert.equal(count(out, /class="gate-legend-item"/g), 7);
  assert.match(out, /Other \+ unmonitored<\/span><span class="gate-value">38\.0 %/);
  assert.doesNotMatch(out, /#ef4444/);
  assert.match(out, /gate-donut-value">62\.0 %/);
});

test("breakdown: without known shares the donut is empty", () => {
  const out = renderCard("breakdown.js", breakdownRows.map((r) => ({ ...r, twin_energy_fraction_pct: "" })));
  assert.match(out, /gate-donut unknown/);
  assert.equal(count(out, /class="gate-legend-item"/g), 0);
  assert.match(out, /gate-donut-value">—/);
});

test("asset_cards: one tile per circuit sorted by 30-day kWh with health and quality badges", () => {
  const out = renderCard("asset_cards.js", assetRows);
  assert.match(out, /gate-title">ASSETS</);
  assert.match(out, /grid-template-columns:repeat\(auto-fill, ?minmax\(/);
  assert.equal(count(out, /class="gate-asset"/g), 4);
  const tiles = out.split('class="gate-asset"').slice(1);
  assert.deepEqual(tiles.map((t) => /gate-asset-name">([^<]*)</.exec(t)[1]), ["Heating", "Water Heater", "Lights", "Solar"]);
  const [heating, water, lights, solar] = tiles;
  assert.match(heating, /gate-asset-type">Heating</);
  assert.match(heating, /gate-badge gate-amber">Health 70</);
  assert.match(heating, /gate-badge gate-red">OVERLOAD</);
  assert.match(heating, /30 days<\/span><span class="gate-value">180\.0 kWh/);
  assert.match(heating, /Cost \(30d\)<\/span><span class="gate-value">\$18\.00/);
  assert.match(heating, /Share<\/span><span class="gate-value">36\.0 %/);
  assert.match(heating, /Today<\/span><span class="gate-value">6\.0 kWh/);
  assert.match(heating, /Rated<\/span><span class="gate-value">3000 W/);
  assert.match(heating, /Utilization<\/span><span class="gate-value">42\.5 %/);
  assert.match(heating, /width:43%/);
  assert.match(heating, /Temperature<\/span><span class="gate-value">-0\.82 · High/);
  assert.match(heating, /Humidity<\/span><span class="gate-value">0\.10 · Low/);
  assert.match(water, /gate-badge gate-ok">Health 100</);
  assert.doesNotMatch(water, /OVERLOAD/);
  assert.match(water, /Humidity<\/span><span class="gate-value">-0\.45 · Moderate/);
  assert.match(lights, /gate-badge gate-muted">sin potencia nominal</);
  assert.doesNotMatch(lights, /Health|OVERLOAD|Rated/);
  assert.match(lights, /Temperature<\/span><span class="gate-value">—</);
  assert.match(solar, /gate-badge gate-red">NEGATIVE POWER</);
  assert.match(solar, /30 days<\/span><span class="gate-value">—/);
  assert.match(solar, /sin potencia nominal/);
  assert.doesNotMatch(lights + water + heating, /NEGATIVE/);
});

test("asset_cards: rated power without a health score shows an unknown health", () => {
  const out = renderCard("asset_cards.js", [{ ...assetRows[1], twin_health_score: "", twin_overload: "" }]);
  assert.match(out, /gate-badge gate-muted">Health —</);
  assert.doesNotMatch(out, /OVERLOAD|sin potencia nominal/);
});

test("asset_cards: names and types from data are escaped", () => {
  const out = renderCard("asset_cards.js", [{ ...assetRows[0], label: "<b>x</b>", type: "<i>t</i>", twin_quality: "<s>" }]);
  assert.doesNotMatch(out, /<b>|<i>|<s>/);
  assert.match(out, /&lt;b&gt;x&lt;\/b&gt;/);
});
