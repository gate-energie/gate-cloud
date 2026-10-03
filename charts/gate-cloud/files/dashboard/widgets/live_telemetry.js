// LIVE TELEMETRY. Classes: gate-list, gate-list-row, gate-label, gate-value.
// Monitor row: main_total_active_power, main_phase_{1,2}_voltage, main_total_current;
// building row: today_peak_w, today_energy_kwh, today_unknown.
var monitor = gate.firstWithKey(data, "main_total_active_power") || {};
var building = gate.firstWithKey(data, "today_peak_w") || {};
var voltage = gate.mean([monitor.main_phase_1_voltage, monitor.main_phase_2_voltage]);
var rows = [
  ["Real-time load", gate.fmt(monitor.main_total_active_power, 0, "W")],
  ["Avg phase voltage", gate.fmt(voltage, 1, "V")],
  ["Current", gate.fmt(monitor.main_total_current, 1, "A")],
  ["Daily peak", gate.fmt(gate.known(building, "today_peak_w", "today_unknown"), 0, "W")],
  ["Energy used", gate.fmt(gate.known(building, "today_energy_kwh", "today_unknown"), 1, "kWh")],
];
return gate.card("LIVE TELEMETRY", '<div class="gate-list">' + rows.map(function (r) {
  return '<div class="gate-list-row"><span class="gate-label">' + r[0] + '</span><span class="gate-value">' + r[1] + "</span></div>";
}).join("") + "</div>");
