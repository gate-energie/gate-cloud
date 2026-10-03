// CONSUMPTION — 30 DAYS. Classes: gate-cols, gate-col, gate-col-empty, gate-col-fill,
// gate-cols-legend, gate-label, gate-value, gate-muted.
// Building row: analytics_daily (JSON list of {date: "YYYY-MM-DD", kwh}: counter energy of each complete
// local day, with or without weather), analytics_updated_at, analytics_unknown.
// Always 30 calendar days ending at the latest date; days without data are empty slots.
var building = gate.firstWithKey(data, "analytics_daily") || {};
var points = gate.json(gate.known(building, "analytics_daily", "analytics_unknown"));
var DAY = 86400000;
var parse = function (iso) {
  var m = typeof iso === "string" ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso) : null;
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
};
var byDay = {};
var last = null;
(Array.isArray(points) ? points : []).forEach(function (p) {
  var t = p ? parse(p.date) : null;
  var kwh = p ? gate.num(p.kwh) : null;
  if (t === null || kwh === null) return;
  byDay[t] = kwh;
  if (last === null || t > last) last = t;
});
var age = gate.updated(building.analytics_updated_at, gate.AGE.nightly, ctx);
if (last === null) return gate.card("CONSUMPTION — 30 DAYS", '<div class="gate-muted">No data yet</div>' + age);
var days = [];
for (var i = 29; i >= 0; i--) {
  var t = last - i * DAY;
  days.push({ date: new Date(t).toISOString().slice(0, 10), kwh: byDay.hasOwnProperty(t) ? byDay[t] : null });
}
var values = days.filter(function (d) { return d.kwh !== null; }).map(function (d) { return d.kwh; });
var max = Math.max.apply(null, values);
var avg = values.reduce(function (s, v) { return s + v; }, 0) / values.length;
var cols = days.map(function (d) {
  if (d.kwh === null) return '<div class="gate-col gate-col-empty" title="' + d.date + ': no data"></div>';
  var pct = max > 0 ? Math.round(d.kwh / max * 100) : 0;
  return '<div class="gate-col" title="' + d.date + ": " + gate.fmt(d.kwh, 1, "kWh") +
    '"><span class="gate-col-fill" style="height:' + pct + '%"></span></div>';
}).join("");
var html = '<div class="gate-cols">' + cols + '</div><div class="gate-cols-legend">' +
  '<span class="gate-label">Max</span> <span class="gate-value">' + gate.fmt(max, 1, "kWh") + "</span> " +
  '<span class="gate-label">Average</span> <span class="gate-value">' + gate.fmt(avg, 1, "kWh") + "</span></div>" + age;
return gate.card("CONSUMPTION — 30 DAYS", html);
