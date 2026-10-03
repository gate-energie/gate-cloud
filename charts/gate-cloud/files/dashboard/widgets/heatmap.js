// CONSUMPTION HEATMAP. Classes: gate-heat, gate-heat-corner, gate-heat-hour, gate-heat-day,
// gate-heat-cell, gate-heat-empty, gate-heat-legend, gate-heat-swatch, gate-label, gate-value, gate-muted.
// Building row: analytics_heatmap (JSON {unit, days: [7 labels], values: [7][24] mean kW or null,
// samples: [7][24]}), local weekday x hour in America/Toronto. A null cell is unknown and stays empty.
var building = gate.firstWithKey(data, "analytics_heatmap") || {};
var heat = gate.json(building.analytics_heatmap);
if (!heat || !Array.isArray(heat.values)) return gate.card("CONSUMPTION HEATMAP", '<div class="gate-muted">—</div>');
var unit = typeof heat.unit === "string" && heat.unit ? heat.unit : "kW";
var days = Array.isArray(heat.days) ? heat.days : [];
// Five steps from the card background #111827 to the accent #22d3ee.
var SCALE = ["#111827", "#154759", "#1a768b", "#1ea4bc", "#22d3ee"];
var grid = [];
var max = null;
for (var d = 0; d < 7; d++) {
  var source = Array.isArray(heat.values[d]) ? heat.values[d] : [];
  var cells = [];
  for (var h = 0; h < 24; h++) {
    var v = gate.num(source[h]);
    cells.push(v);
    if (v !== null && (max === null || v > max)) max = v;
  }
  grid.push(cells);
}
var pad = function (n) { return (n < 10 ? "0" : "") + n + ":00"; };
var html = '<div class="gate-heat" style="display:grid;grid-template-columns:auto repeat(24,1fr)">' +
  '<div class="gate-heat-corner"></div>';
for (var hour = 0; hour < 24; hour++) {
  html += '<div class="gate-heat-hour">' + (hour % 6 === 0 ? hour : "") + "</div>";
}
grid.forEach(function (row, d) {
  var label = days[d] === null || days[d] === undefined || days[d] === "" ? "—" : gate.esc(days[d]);
  html += '<div class="gate-heat-day">' + label + "</div>";
  row.forEach(function (v, h) {
    var title = label + " " + pad(h) + " · ";
    if (v === null) {
      html += '<div class="gate-heat-cell gate-heat-empty" title="' + title + 'no data"></div>';
      return;
    }
    var step = max > 0 ? Math.min(4, Math.floor(Math.max(0, v) / max * 5)) : 0;
    html += '<div class="gate-heat-cell" style="background:' + SCALE[step] + '" title="' + title +
      gate.esc(gate.fmt(v, 2, unit)) + '"></div>';
  });
});
html += '</div><div class="gate-heat-legend"><span class="gate-label">Less</span> ' +
  SCALE.map(function (c) { return '<span class="gate-heat-swatch" style="background:' + c + '"></span>'; }).join("") +
  ' <span class="gate-label">More</span> <span class="gate-label">Max</span> <span class="gate-value">' +
  gate.esc(gate.fmt(max, 2, unit)) + "</span></div>";
return gate.card("CONSUMPTION HEATMAP", html);
