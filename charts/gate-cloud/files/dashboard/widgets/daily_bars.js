// CONSUMPTION — 30 DAYS. Classes: gate-cols, gate-col, gate-col-fill, gate-cols-legend,
// gate-label, gate-value, gate-muted.
// Building row: analytics_scatter (JSON list of {date, kwh, temp_mean_c}).
var building = gate.firstWithKey(data, "analytics_scatter") || {};
var points = gate.json(building.analytics_scatter);
var days = (Array.isArray(points) ? points : [])
  .filter(function (p) { return p && gate.num(p.kwh) !== null; })
  .slice(-30);
if (!days.length) return gate.card("CONSUMPTION — 30 DAYS", '<div class="gate-muted">No data yet</div>');
var values = days.map(function (p) { return gate.num(p.kwh); });
var max = Math.max.apply(null, values);
var avg = values.reduce(function (s, v) { return s + v; }, 0) / values.length;
var cols = days.map(function (p, i) {
  var pct = max > 0 ? Math.round(values[i] / max * 100) : 0;
  return '<div class="gate-col" title="' + gate.esc(p.date) + ": " + gate.fmt(values[i], 1, "kWh") +
    '"><span class="gate-col-fill" style="height:' + pct + '%"></span></div>';
}).join("");
var html = '<div class="gate-cols">' + cols + '</div><div class="gate-cols-legend">' +
  '<span class="gate-label">Max</span> <span class="gate-value">' + gate.fmt(max, 1, "kWh") + "</span> " +
  '<span class="gate-label">Average</span> <span class="gate-value">' + gate.fmt(avg, 1, "kWh") + "</span></div>";
return gate.card("CONSUMPTION — 30 DAYS", html);
