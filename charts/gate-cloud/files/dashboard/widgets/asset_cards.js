// ASSETS. Classes: gate-assets, gate-asset, gate-asset-head, gate-asset-name, gate-asset-type,
// gate-asset-badges, gate-badge, gate-ok, gate-amber, gate-red, gate-muted, gate-list, gate-list-row,
// gate-label, gate-value, gate-subtitle, gate-bar (via gate.bar).
// Circuit rows: label, type, twin_energy_kwh, twin_cost_cad, twin_energy_fraction_pct (30 days),
// twin_utilization_pct, twin_corr_temperature, twin_corr_humidity, today_energy_kwh, twin_quality,
// twin_health_score, twin_overload, twin_unknown, today_unknown, rated_power_w. Health and overload exist only
// with a rated power. twin_quality is "" when not flagged.
var flag = function (v) { return v === true || v === "true" ? true : v === false || v === "false" ? false : null; };
var twin = function (r, key) { return gate.known(r, key, "twin_unknown"); };
var tiles = gate.withKey(data, "twin_energy_kwh").map(function (r) {
  var q = twin(r, "twin_quality");
  var quality = q === null || q === undefined ? "" : String(q);
  var kwh = quality ? null : gate.num(twin(r, "twin_energy_kwh"));
  return { r: r, quality: quality, kwh: kwh !== null && kwh < 0 ? null : kwh };
}).sort(function (a, b) {
  if (a.kwh === null || b.kwh === null) return a.kwh === null ? (b.kwh === null ? 0 : 1) : -1;
  return b.kwh - a.kwh;
});
if (!tiles.length) return gate.card("ASSETS", '<div class="gate-muted">—</div>', "energy");
var row = function (label, value) {
  return '<div class="gate-list-row"><span class="gate-label">' + label + '</span><span class="gate-value">' + value + "</span></div>";
};
var corr = function (value) {
  var s = gate.strength(value);
  return s ? gate.fmt(value, 2) + " · " + s : "—";
};
var badge = function (cls, text) { return '<span class="gate-badge ' + cls + '">' + text + "</span>"; };
var html = '<div class="gate-assets">' +
  tiles.map(function (t) {
    var r = t.r;
    var rated = gate.num(r.rated_power_w);
    var badges = "";
    if (t.quality) badges += badge("gate-red", gate.esc(t.quality.replace(/_/g, " ").toUpperCase()));
    if (rated === null) {
      badges += badge("gate-muted", "sin potencia nominal");
    } else {
      var health = gate.num(twin(r, "twin_health_score"));
      var cls = health === null ? "gate-muted" : health >= 90 ? "gate-ok" : health >= 70 ? "gate-amber" : "gate-red";
      badges += badge(cls, "Health " + gate.fmt(health, 0));
      if (flag(twin(r, "twin_overload")) === true) badges += badge("gate-red", "OVERLOAD");
    }
    var util = gate.num(twin(r, "twin_utilization_pct"));
    var type = r.type === null || r.type === undefined || r.type === "" ? "—" : gate.esc(r.type);
    return '<div class="gate-asset"><div class="gate-asset-head"><div class="gate-asset-name">' +
      gate.esc(r.label || r.entityLabel || r.entityName) + '</div><div class="gate-asset-type">' + type + "</div>" +
      '<div class="gate-asset-badges">' + badges + "</div></div>" +
      '<div class="gate-list">' +
      row("30 days", gate.fmt(t.kwh, 1, "kWh")) +
      row("Cost (30d)", gate.money(t.quality ? null : twin(r, "twin_cost_cad"))) +
      row("Share", gate.fmt(t.quality ? null : twin(r, "twin_energy_fraction_pct"), 1, "%")) +
      row("Today", gate.fmt(gate.known(r, "today_energy_kwh", "today_unknown"), 1, "kWh")) +
      (rated === null ? "" : row("Rated", gate.fmt(rated, 0, "W"))) +
      row("Utilization", gate.fmt(util, 1, "%")) +
      "</div>" + gate.bar(util === null ? null : util / 100, "#22d3ee") +
      '<div class="gate-subtitle">Weather correlation</div><div class="gate-list">' +
      row("Temperature", corr(twin(r, "twin_corr_temperature"))) +
      row("Humidity", corr(twin(r, "twin_corr_humidity"))) +
      "</div></div>";
  }).join("") + "</div>";
return gate.card("ASSETS", html, "energy");
