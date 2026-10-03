// MONTHLY BUDGET. Classes: gate-budget, gate-ring, gate-ring-inner, gate-ring-value, unknown,
// gate-list, gate-list-row, gate-label, gate-value, gate-delta, gate-amber, gate-ok.
// Building row: monthly_budget, month_cost_cad, month_projected_cost_cad, month_budget_used_pct,
// month_avg_daily_cost_cad, month_days_left, today_cost_cad, yesterday_cost_cad.
var b = gate.firstWithKey(data, "month_budget_used_pct") || {};
var used = gate.num(b.month_budget_used_pct);
var color = used === null ? "#1f2937" : used < 80 ? "#22c55e" : used < 100 ? "#f59e0b" : "#ef4444";
var fill = used === null ? 0 : Math.min(100, Math.max(0, used));
var today = gate.num(b.today_cost_cad);
var yesterday = gate.num(b.yesterday_cost_cad);
var diff = today !== null && yesterday !== null ? today - yesterday : null;
var diffHtml = diff === null ? '<span class="gate-delta">—</span>'
  : '<span class="gate-delta ' + (diff > 0 ? "gate-amber" : "gate-ok") + '">' + (diff > 0 ? "▲ " : "▼ ") +
    gate.money(Math.abs(diff)) + " vs yesterday</span>";
var row = function (label, value) {
  return '<div class="gate-list-row"><span class="gate-label">' + label + '</span><span class="gate-value">' + value + "</span></div>";
};
var html = '<div class="gate-budget"><div class="gate-ring' + (used === null ? " unknown" : "") +
  '" style="background:conic-gradient(' + color + " 0% " + fill + "%, #1f2937 " + fill + '% 100%)">' +
  '<div class="gate-ring-inner"><span class="gate-ring-value">' + gate.fmt(used, 0, "%") + "</span></div></div>" +
  '<div class="gate-list">' +
  row("Budget", gate.money(b.monthly_budget)) +
  row("Projected", gate.money(b.month_projected_cost_cad)) +
  row("Month total", gate.money(b.month_cost_cad)) +
  row("Avg daily", gate.money(b.month_avg_daily_cost_cad)) +
  row("Days left", gate.fmt(b.month_days_left, 0, "days")) +
  row("Today's cost", gate.money(today)) +
  "</div>" + diffHtml + "</div>";
return gate.card("MONTHLY BUDGET", html);
