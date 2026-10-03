// POWER vs TEMPERATURE. Classes: gate-scatter, gate-scatter-plot, gate-dot, gate-trend, gate-axis,
// gate-axis-y-max, gate-axis-y-min, gate-axis-x, gate-axis-x-min, gate-axis-x-max, gate-scatter-r,
// gate-label, gate-value, gate-muted.
// .gate-dot and .gate-trend are absolutely positioned inside .gate-scatter-plot (a 5:2 box, wide enough for a short card):
// dot left = temperature, bottom = kWh; the trend is a thin div rotated about its left end.
// Building row: analytics_scatter (JSON list of {date, kwh, temp_mean_c}, one point per day), analytics_updated_at,
// analytics_unknown.
var building = gate.firstWithKey(data, "analytics_scatter") || {};
var raw = gate.json(gate.known(building, "analytics_scatter", "analytics_unknown"));
var points = (Array.isArray(raw) ? raw : []).map(function (p) {
  return p && typeof p === "object" ? { date: p.date, x: gate.num(p.temp_mean_c), y: gate.num(p.kwh) } : null;
}).filter(function (p) { return p && p.x !== null && p.y !== null; });
var age = gate.updated(building.analytics_updated_at, gate.AGE.nightly, ctx);
if (!points.length) return gate.card("POWER vs TEMPERATURE", '<div class="gate-muted">—</div>' + age, "energy");
var xs = points.map(function (p) { return p.x; });
var ys = points.map(function (p) { return p.y; });
var xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
var ymin = Math.min(0, Math.min.apply(null, ys)), ymax = Math.max.apply(null, ys);
// A flat axis still gets a range so every dot lands inside the box.
var xlo = xmin === xmax ? xmin - 1 : xmin, xhi = xmin === xmax ? xmax + 1 : xmax;
var yhi = ymax === ymin ? ymin + 1 : ymax;
var px = function (x) { return (x - xlo) / (xhi - xlo) * 100; };
var py = function (y) { return (y - ymin) / (yhi - ymin) * 100; };
var fix = function (n, d) { var s = n.toFixed(d); return /^-0\.?0*$/.test(s) ? s.slice(1) : s; };
var ASPECT = 2.5; // plot width / height
var n = points.length;
var mx = xs.reduce(function (s, v) { return s + v; }, 0) / n;
var my = ys.reduce(function (s, v) { return s + v; }, 0) / n;
var sxx = 0, syy = 0, sxy = 0;
points.forEach(function (p) {
  sxx += (p.x - mx) * (p.x - mx);
  syy += (p.y - my) * (p.y - my);
  sxy += (p.x - mx) * (p.y - my);
});
// Pearson r needs a week of days and spread on both axes.
var r = n >= 7 && sxx > 0 && syy > 0 ? sxy / Math.sqrt(sxx * syy) : null;
// Least-squares line across the temperature range, clipped to the box (percent coordinates);
// like r, only with a week of days.
var trend = "";
if (n >= 7 && sxx > 0) {
  var slope = sxy / sxx;
  var y1 = py(my + slope * (xmin - mx)), y2 = py(my + slope * (xmax - mx));
  var lo = 0, hi = 1;
  if (y2 !== y1) {
    var t0 = -y1 / (y2 - y1), t1 = (100 - y1) / (y2 - y1);
    lo = Math.max(0, Math.min(t0, t1));
    hi = Math.min(1, Math.max(t0, t1));
  } else if (y1 < 0 || y1 > 100) {
    hi = lo;
  }
  if (hi > lo) {
    var ya = y1 + (y2 - y1) * lo, yb = y1 + (y2 - y1) * hi;
    var dx = (hi - lo) * 100;          // % of the plot width
    var dy = (yb - ya) / ASPECT;       // % of the plot height, in plot-width units
    trend = '<div class="gate-trend" style="left:' + fix(lo * 100, 1) + "%;bottom:" + fix(ya, 1) + "%;width:" +
      fix(Math.sqrt(dx * dx + dy * dy), 1) + "%;transform:rotate(" + fix(Math.atan2(-dy, dx) * 180 / Math.PI, 1) +
      'deg);transform-origin:0 50%"></div>';
  }
}
var dots = points.map(function (p) {
  var date = p.date === null || p.date === undefined || p.date === "" ? "—" : gate.esc(p.date);
  return '<span class="gate-dot" style="left:' + fix(px(p.x), 1) + "%;bottom:" + fix(py(p.y), 1) + '%" title="' +
    date + ": " + gate.fmt(p.y, 1, "kWh") + " · " + gate.fmt(p.x, 1, "°C") + '"></span>';
}).join("");
var strength = gate.strength(r);
var html = '<div class="gate-scatter">' +
  '<div class="gate-axis gate-axis-y-max">' + gate.fmt(ymax, 1, "kWh") + "</div>" +
  '<div class="gate-scatter-plot" style="position:relative;aspect-ratio:' + ASPECT + '/1">' + dots + trend + "</div>" +
  '<div class="gate-axis gate-axis-y-min">' + gate.fmt(ymin, 1, "kWh") + "</div>" +
  '<div class="gate-axis-x"><span class="gate-axis gate-axis-x-min">' + gate.fmt(xmin, 1, "°C") +
  '</span><span class="gate-muted">Mean outdoor temperature · ' + n + (n === 1 ? " day" : " days") +
  '</span><span class="gate-axis gate-axis-x-max">' + gate.fmt(xmax, 1, "°C") + "</span></div>" +
  '<div class="gate-scatter-r"><span class="gate-label">Pearson</span> <span class="gate-value">r = ' +
  (r === null ? "—" : fix(r, 2)) + "</span>" + (strength ? ' <span class="gate-muted">' + strength + "</span>" : "") +
  "</div></div>" + age;
return gate.card("POWER vs TEMPERATURE", html, "energy");
