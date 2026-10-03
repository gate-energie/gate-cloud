// TODAY'S ENERGY. Classes: gate-big, gate-unit, gate-stats, gate-stat, gate-stat-label,
// gate-stat-value, gate-delta, gate-amber, gate-ok, gate-subtitle, gate-list, gate-list-row,
// gate-label, gate-value, gate-muted.
// Building row: today_energy_kwh, today_cost_cad, yesterday_same_time_kwh, today_updated_at, today_unknown;
// circuit rows: label, today_energy_kwh, today_unknown. Today so far is compared with yesterday up to the
// same local time (yesterday_same_time_kwh), not with the whole of yesterday.
var building = gate.firstWithKey(data, "yesterday_same_time_kwh") || {};
var T = "today_unknown";
var today = gate.num(gate.known(building, "today_energy_kwh", T));
var before = gate.num(gate.known(building, "yesterday_same_time_kwh", T));
var delta = today !== null && before ? (today - before) / before * 100 : null;
// A change that rounds to 0.0 % is neutral: no arrow, muted.
var shown = delta === null ? null : Math.round(delta * 10) / 10;
var deltaHtml = shown === null ? '<span class="gate-delta">—</span>'
  : shown === 0 ? '<span class="gate-delta gate-muted">' + gate.fmt(0, 1, "%") + "</span>"
  : '<span class="gate-delta ' + (shown > 0 ? "gate-amber" : "gate-ok") + '">' + (shown > 0 ? "▲ " : "▼ ") +
    gate.fmt(Math.abs(shown), 1, "%") + "</span>";
var top = gate.withKey(data, "label")
  .map(function (r) { return { name: r.label || r.entityLabel || r.entityName, kwh: gate.num(gate.known(r, "today_energy_kwh", T)) }; })
  .filter(function (c) { return c.kwh !== null; })
  .sort(function (a, b) { return b.kwh - a.kwh; })
  .slice(0, 3);
var stat = function (label, value) {
  return '<div class="gate-stat"><div class="gate-stat-label">' + label + '</div><div class="gate-stat-value">' + value + "</div></div>";
};
var html = '<div class="gate-big">' + gate.fmt(today, 1) + ' <span class="gate-unit">kWh</span></div>' +
  '<div class="gate-stats">' + stat("Cost", gate.money(gate.known(building, "today_cost_cad", T))) +
  stat("vs same time yesterday", deltaHtml) + "</div>" +
  '<div class="gate-subtitle">Top consumers (today)</div><div class="gate-list">' +
  (top.length ? top.map(function (c) {
    return '<div class="gate-list-row"><span class="gate-label">' + gate.esc(c.name) +
      '</span><span class="gate-value">' + gate.fmt(c.kwh, 1, "kWh") + "</span></div>";
  }).join("") : '<div class="gate-muted">—</div>') + "</div>" +
  gate.updated(building.today_updated_at, gate.AGE.today, ctx);
return gate.card("TODAY'S ENERGY", html);
