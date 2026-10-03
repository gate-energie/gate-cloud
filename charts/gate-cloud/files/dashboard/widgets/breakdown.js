// APPLIANCE BREAKDOWN. Classes: gate-breakdown, gate-breakdown-bars, gate-breakdown-row,
// gate-breakdown-head, gate-bar (via gate.bar), gate-donut, unknown, gate-donut-inner, gate-donut-value,
// gate-legend, gate-legend-item, gate-swatch, gate-label, gate-value, gate-muted.
// Circuit rows: label, twin_energy_kwh (30 days), twin_energy_fraction_pct (share of the building),
// twin_quality ("" when not flagged), twin_unknown. A circuit flagged by quality, or with negative energy, is unknown.
// Donut: the top 6 circuits by kWh with a known share, then "Other + unmonitored" = the rest of the building.
// Red stays reserved for alarms; the fourth slot is a neutral slate.
var COLORS = ["#22d3ee", "#3b82f6", "#f59e0b", "#94a3b8", "#a855f7", "#22c55e"];
var OTHER = "#4b5563";
var items = gate.withKey(data, "twin_energy_kwh").map(function (r) {
  var twin = function (key) { return gate.known(r, key, "twin_unknown"); };
  var quality = twin("twin_quality");
  var flagged = quality !== null && quality !== undefined && quality !== "";
  var kwh = flagged ? null : gate.num(twin("twin_energy_kwh"));
  if (kwh !== null && kwh < 0) kwh = null;
  return {
    name: r.label || r.entityLabel || r.entityName,
    kwh: kwh,
    pct: kwh === null ? null : gate.num(twin("twin_energy_fraction_pct")),
  };
}).sort(function (a, b) {
  if (a.kwh === null || b.kwh === null) return a.kwh === null ? (b.kwh === null ? 0 : 1) : -1;
  return b.kwh - a.kwh;
});
if (!items.length) return gate.card("APPLIANCE BREAKDOWN", '<div class="gate-muted">—</div>');
var max = Math.max.apply(null, items.map(function (i) { return i.kwh || 0; }));
var bars = items.map(function (i) {
  return '<div class="gate-breakdown-row"><div class="gate-breakdown-head"><span class="gate-label">' +
    gate.esc(i.name) + '</span><span class="gate-value">' + gate.fmt(i.kwh, 1, "kWh") + " · " +
    gate.fmt(i.pct, 1, "%") + "</span></div>" +
    gate.bar(i.kwh === null ? null : max > 0 ? i.kwh / max : 0, "#3b82f6") + "</div>";
}).join("");
var top = items.filter(function (i) { return i.pct !== null; }).slice(0, 6);
var num = function (n) { return String(Number(n.toFixed(2))); };
var at = 0;
var stops = [];
var legend = top.map(function (i, k) {
  var from = at;
  at = Math.min(100, at + Math.max(0, i.pct));
  stops.push(COLORS[k] + " " + num(from) + "% " + num(at) + "%");
  return { name: i.name, color: COLORS[k], pct: i.pct };
});
var donut;
if (top.length) {
  stops.push(OTHER + " " + num(at) + "% 100%");
  legend.push({ name: "Other + unmonitored", color: OTHER, pct: 100 - at });
  donut = '<div class="gate-donut" style="background:conic-gradient(' + stops.join(", ") + ')">';
} else {
  donut = '<div class="gate-donut unknown" style="background:#1f2937">';
}
donut += '<div class="gate-donut-inner"><span class="gate-donut-value">' + gate.fmt(top.length ? at : null, 1, "%") +
  "</span>" + (top.length ? '<span class="gate-muted">top ' + top.length + "</span>" : "") + "</div></div>";
var html = '<div class="gate-breakdown"><div class="gate-breakdown-bars">' + bars + "</div>" + donut +
  '<div class="gate-legend">' + legend.map(function (l) {
    return '<div class="gate-legend-item"><span class="gate-swatch" style="background:' + l.color + '"></span>' +
      '<span class="gate-label">' + gate.esc(l.name) + '</span><span class="gate-value">' + gate.fmt(l.pct, 1, "%") +
      "</span></div>";
  }).join("") + "</div></div>";
return gate.card("APPLIANCE BREAKDOWN", html);
