// CIRCUIT COST. Classes: gate-table, gate-table-head, gate-table-row, gate-table-total,
// gate-table-name, gate-table-label, gate-table-cell, gate-kwh, gate-money, gate-bar (via gate.bar), gate-muted.
// A table on wide cards; on a phone each circuit is a list item: name and 30-day bar on one line,
// the three periods (kWh over $) below it.
// Circuit rows: label, today_energy_kwh, today_cost_cad, twin_7d_energy_kwh, twin_7d_cost_cad,
// twin_energy_kwh (30 days), twin_cost_cad (30 days); today_* honour today_unknown, twin_* twin_unknown.
var periods = [
  ["Today", "today_energy_kwh", "today_cost_cad"],
  ["7 days", "twin_7d_energy_kwh", "twin_7d_cost_cad"],
  ["30 days", "twin_energy_kwh", "twin_cost_cad"],
];
var value = function (r, key) {
  return gate.known(r, key, key.indexOf("today_") === 0 ? "today_unknown" : "twin_unknown");
};
var rows = gate.withKey(data, "twin_energy_kwh").slice().sort(function (a, b) {
  var x = gate.num(value(a, "twin_energy_kwh")), y = gate.num(value(b, "twin_energy_kwh"));
  if (x === null || y === null) return x === null ? (y === null ? 0 : 1) : -1;
  return y - x;
});
if (!rows.length) return gate.card("CIRCUIT COST", '<div class="gate-muted">—</div>', "cost");
var max = Math.max.apply(null, rows.map(function (r) { return gate.num(value(r, "twin_energy_kwh")) || 0; }));
var cell = function (kwh, cost) {
  return '<div class="gate-table-cell"><span class="gate-kwh">' + gate.fmt(kwh, 1, "kWh") + '</span><span class="gate-money">' +
    gate.money(cost) + "</span></div>";
};
// A total is shown only when every circuit's value is known.
var total = function (key) {
  var values = rows.map(function (r) { return gate.num(value(r, key)); });
  return values.some(function (v) { return v === null; }) ? null
    : values.reduce(function (s, v) { return s + v; }, 0);
};
var name = function (text, bar) {
  return '<div class="gate-table-name"><span class="gate-table-label" title="' + text + '">' + text + "</span>" + bar + "</div>";
};
var html = '<div class="gate-table"><div class="gate-table-head">' + name("Circuit", "") +
  periods.map(function (p) { return '<div class="gate-table-cell">' + p[0] + "</div>"; }).join("") + "</div>" +
  rows.map(function (r) {
    var kwh = gate.num(value(r, "twin_energy_kwh"));
    return '<div class="gate-table-row">' + name(gate.esc(r.label || r.entityLabel || r.entityName),
      gate.bar(kwh === null ? null : max > 0 ? kwh / max : 0, "#22d3ee")) +
      periods.map(function (p) { return cell(value(r, p[1]), value(r, p[2])); }).join("") + "</div>";
  }).join("") +
  '<div class="gate-table-row gate-table-total">' + name("Monitored total", "") +
  periods.map(function (p) { return cell(total(p[1]), total(p[2])); }).join("") + "</div></div>";
return gate.card("CIRCUIT COST", html, "cost");
