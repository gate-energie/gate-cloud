// POWER FLOW. Classes: gate-flow, gate-flow-source, gate-flow-node, gate-flow-grid,
// gate-flow-building, gate-flow-load, gate-flow-name, gate-flow-value, gate-flow-sub,
// gate-flow-link (CSS-border connector), gate-flow-bus, gate-flow-loads, gate-flow-branch.
// Circuit labels come from the "gate:circuits" datasource keys in ctx.datasources;
// the monitor row (first with main_total_active_power) holds their live W by label.
// Without ctx.datasources, every numeric data key of the monitor row is a circuit
// (ThingsBoard also adds entity fields, $datasource and "<label>|ts" timestamps).
// Building row: today_energy_kwh, today_unknown.
var monitor = gate.firstWithKey(data, "main_total_active_power") || {};
var building = gate.firstWithKey(data, "today_energy_kwh") || {};
var TOTAL = "main_total_active_power";
var entityField = /^(entity[A-Z][A-Za-z]*|deviceName|deviceType|aliasName|dsIndex|dsName)$/;
var sources = ctx && Array.isArray(ctx.datasources) ? ctx.datasources : [];
var circuitsDs = sources.filter(function (d) { return d && d.name === "gate:circuits"; })[0];
var labels = circuitsDs && Array.isArray(circuitsDs.dataKeys)
  ? circuitsDs.dataKeys.map(function (k) { return k && k.label; })
    .filter(function (l) { return typeof l === "string" && l !== TOTAL; })
  : Object.keys(monitor).filter(function (k) {
    return k !== TOTAL && k.indexOf("|") < 0 && k.charAt(0) !== "$" && !entityField.test(k) && gate.num(monitor[k]) !== null;
  });
var total = gate.num(monitor[TOTAL]);
var kw = function (w) { return w === null ? "—" : gate.fmt(w / 1000, 2, "kW"); };
var loads = labels
  .map(function (k) { return { name: k, w: gate.num(monitor[k]) }; })
  .sort(function (a, b) {
    if (a.w === null || b.w === null) return a.w === null ? (b.w === null ? 0 : 1) : -1;
    return b.w - a.w;
  })
  .slice(0, 4);
var shownKnown = loads.every(function (l) { return l.w !== null; });
var other = total === null || !shownKnown ? null
  : Math.max(0, total - loads.reduce(function (s, l) { return s + l.w; }, 0));
loads.push({ name: "Other loads", w: other });
var energy = gate.fmt(gate.known(building, "today_energy_kwh", "today_unknown"), 1, "kWh");
var node = function (cls, name, value, sub) {
  return '<div class="gate-flow-node ' + cls + '"><div class="gate-flow-name">' + gate.esc(name) +
    '</div><div class="gate-flow-value">' + value + "</div>" +
    (sub === undefined ? "" : '<div class="gate-flow-sub">' + sub + "</div>") + "</div>";
};
var html = '<div class="gate-flow"><div class="gate-flow-source">' +
  node("gate-flow-grid", "Grid", kw(total), energy) + '<div class="gate-flow-link"></div>' +
  node("gate-flow-building", "Building", kw(total), energy) +
  '</div><div class="gate-flow-bus"></div><div class="gate-flow-loads">' +
  loads.map(function (l) {
    return '<div class="gate-flow-branch"><div class="gate-flow-link"></div>' + node("gate-flow-load", l.name, kw(l.w)) + "</div>";
  }).join("") + "</div></div>";
return gate.card("POWER FLOW", html);
