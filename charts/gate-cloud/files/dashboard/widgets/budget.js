// MONTHLY BUDGET. Classes: gate-budget, gate-ring, gate-ring-inner, gate-ring-value, gate-ring-sub, unknown,
// gate-list, gate-list-row, gate-label, gate-value, gate-delta, gate-amber, gate-ok, gate-muted.
// Building row: monthly_budget, month_cost_cad, month_projected_cost_cad, month_budget_used_pct,
// month_avg_daily_cost_cad, month_days_left, month_updated_at, month_unknown, today_cost_cad, yesterday_same_time_cost_cad, today_unknown.
// Today so far is compared with yesterday up to the same local time, both priced as one day's bill.
var b = gate.firstWithKey(data, "month_budget_used_pct") || {};
var M = "month_unknown", T = "today_unknown";
var month = function (key) { return gate.known(b, key, M); };
var used = gate.num(month("month_budget_used_pct"));
var color = gate.budgetColor(used);
var fill = used === null ? 0 : Math.min(100, Math.max(0, used));
var today = gate.num(gate.known(b, "today_cost_cad", T));
var yesterday = gate.num(gate.known(b, "yesterday_same_time_cost_cad", T));
// Cents: a difference that rounds to $0.00 is neutral (no arrow, muted).
var diff = today !== null && yesterday !== null ? Math.round((today - yesterday) * 100) / 100 : null;
var diffHtml = diff === null ? '<span class="gate-delta">—</span>'
  : diff === 0 ? '<span class="gate-delta gate-muted">' + gate.money(0) + " vs same time yesterday</span>"
  : '<span class="gate-delta ' + (diff > 0 ? "gate-amber" : "gate-ok") + '">' + (diff > 0 ? "▲ " : "▼ ") +
    gate.money(Math.abs(diff)) + " vs same time yesterday</span>";
var budget = gate.num(b.monthly_budget);
var spent = gate.num(month("month_cost_cad"));
var remaining = budget !== null && spent !== null ? budget - spent : null;
var row = function (label, value) {
  return '<div class="gate-list-row"><span class="gate-label">' + label + '</span><span class="gate-value">' + value + "</span></div>";
};
var html = '<div class="gate-budget"><div class="gate-ring' + (used === null ? " unknown" : "") +
  '" style="background:conic-gradient(' + color + " 0% " + fill + "%, #1f2937 " + fill + '% 100%)">' +
  '<div class="gate-ring-inner"><span class="gate-ring-value">' + gate.fmt(used, 0, "%") + '</span><span class="gate-ring-sub">used</span></div></div>' +
  '<div class="gate-list">' +
  row("Budget", gate.money(b.monthly_budget)) +
  row("Remaining", gate.money(remaining)) +
  row("Projected", gate.money(month("month_projected_cost_cad"))) +
  row("Month total", gate.money(spent)) +
  row("Avg daily", gate.money(month("month_avg_daily_cost_cad"))) +
  row("Days left", gate.fmt(month("month_days_left"), 0, "days")) +
  row("Today's cost", gate.money(today)) +
  "</div>" + diffHtml + "</div>" + gate.updated(b.month_updated_at, gate.AGE.nightly, ctx);
return gate.card("MONTHLY BUDGET", html, "cost");
