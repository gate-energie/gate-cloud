// GRID & PHASES. Classes: gate-stats, gate-stat, gate-stat-label, gate-stat-value,
// gate-phases, gate-phase, gate-phase-head, gate-label, gate-value, gate-bar (via gate.bar),
// gate-list-row, gate-badge, gate-ok, gate-amber.
// Monitor row: main_phase_{1,2}_active_power, main_phase_{1,2}_voltage, main_total_current;
// building row: today_peak_w, yesterday_peak_w, today_unknown.
var monitor = gate.firstWithKey(data, "main_phase_1_active_power") || {};
var building = gate.firstWithKey(data, "yesterday_peak_w") || {};
var p1 = gate.num(monitor.main_phase_1_active_power);
var p2 = gate.num(monitor.main_phase_2_active_power);
var voltage = gate.mean([monitor.main_phase_1_voltage, monitor.main_phase_2_voltage]);
var sum = p1 !== null && p2 !== null ? p1 + p2 : null;
var spread = sum === null ? null : Math.abs(p1 - p2);
var imbalance = sum ? spread / sum * 100 : null;
var stat = function (label, value) {
  return '<div class="gate-stat"><div class="gate-stat-label">' + label + '</div><div class="gate-stat-value">' + value + "</div></div>";
};
var phase = function (name, w, v) {
  return '<div class="gate-phase"><div class="gate-phase-head"><span class="gate-label">' + name +
    '</span><span class="gate-value">' + gate.fmt(w, 0, "W") + " · " + gate.fmt(v, 1, "V") + "</span></div>" +
    gate.bar(sum ? w / sum : null, "#22d3ee") + "</div>";
};
var badge = imbalance === null ? ""
  : imbalance < 20 ? '<span class="gate-badge gate-ok">BALANCED</span>'
  : '<span class="gate-badge gate-amber">IMBALANCED</span>';
var html = '<div class="gate-stats">' +
  stat("Total voltage", gate.fmt(voltage, 1, "V")) +
  stat("Total current", gate.fmt(monitor.main_total_current, 1, "A")) +
  stat("Today's peak", gate.fmt(gate.known(building, "today_peak_w", "today_unknown"), 0, "W")) +
  stat("Yesterday's peak", gate.fmt(gate.known(building, "yesterday_peak_w", "today_unknown"), 0, "W")) +
  '</div><div class="gate-phases">' +
  phase("Phase 1", p1, monitor.main_phase_1_voltage) + phase("Phase 2", p2, monitor.main_phase_2_voltage) +
  '</div><div class="gate-list-row"><span class="gate-label">Imbalance</span><span class="gate-value">' +
  gate.fmt(imbalance, 1, "%") + '</span><span class="gate-label">Spread</span><span class="gate-value">' +
  gate.fmt(spread, 0, "W") + "</span>" + badge + "</div>";
return gate.card("GRID & PHASES", html);
