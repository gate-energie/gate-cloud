// KPI STRIP. Classes: gate-kpis, gate-kpi, gate-kpi-label, gate-kpi-value, gate-kpi-unit, gate-kpi-second,
// gate-kpi-sub, gate-energy, gate-cost, gate-weather, gate-bar (via gate.bar), gate-delta, gate-amber, gate-ok, gate-muted.
// Four headline tiles with no card title: power now, today (kWh and $), the month against the budget,
// and the outside temperature. Monitor row: main_total_active_power. Building row: today_energy_kwh,
// today_cost_cad, today_peak_w, yesterday_same_time_kwh, today_unknown, monthly_budget, month_cost_cad,
// month_budget_used_pct, month_projected_cost_cad, month_unknown. Weather row: temperature_c, humidity_pct,
// wind_speed_ms (time series).
var monitor = gate.firstWithKey(data, "main_total_active_power") || {};
var b = gate.firstWithKey(data, "monthly_budget") || {};
var wx = gate.firstWithKey(data, "temperature_c") || {};
var T = "today_unknown", M = "month_unknown";
var today = function (key) { return gate.known(b, key, T); };
var month = function (key) { return gate.known(b, key, M); };
var kw = function (w) { var n = gate.num(w); return n === null ? null : n / 1000; };
var value = function (text, unit) {
  return text === "—" ? "—" : text + (unit ? ' <span class="gate-kpi-unit">' + unit + "</span>" : "");
};
var tile = function (family, label, valueHtml, subHtml, extra) {
  return '<div class="gate-kpi gate-' + family + '"><div class="gate-kpi-label">' + label + "</div>" +
    '<div class="gate-kpi-value">' + valueHtml + "</div>" + (extra || "") +
    '<div class="gate-kpi-sub">' + subHtml + "</div></div>";
};
var used = gate.num(month("month_budget_used_pct"));
var budget = gate.num(b.monthly_budget);
var html = '<div class="gate-card gate-kpis">' +
  tile("energy", "Power now", value(gate.fmt(kw(monitor.main_total_active_power), 2), "kW"),
    "Peak today " + gate.fmt(kw(today("today_peak_w")), 2, "kW")) +
  tile("energy", "Today", value(gate.fmt(today("today_energy_kwh"), 1), "kWh") +
    '<span class="gate-kpi-second">' + gate.money(today("today_cost_cad")) + "</span>",
    gate.deltaPct(today("today_energy_kwh"), today("yesterday_same_time_kwh"), "vs yesterday")) +
  tile("cost", "This month", value(gate.money(month("month_cost_cad")), budget === null ? "" : "of " + gate.money(budget, 0)),
    "Projected " + gate.money(month("month_projected_cost_cad")) + " · " + gate.fmt(used, 0, "%") + " used",
    gate.bar(used === null ? null : used / 100, gate.budgetColor(used))) +
  tile("weather", "Outside", value(gate.fmt(wx.temperature_c, 1), "°C"),
    gate.fmt(wx.humidity_pct, 0, "%") + " RH · " + gate.fmt(wx.wind_speed_ms, 1, "m/s")) +
  "</div>";
return html;
