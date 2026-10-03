var gate = gate || {};
gate.json = function (raw) {
  if (raw === null || raw === undefined || raw === "") return null;
  if (typeof raw === "string") {
    try { raw = JSON.parse(raw); } catch (e) { return null; }
  }
  return raw !== null && typeof raw === "object" ? raw : null;
};
gate.num = function (raw) {
  if (raw === null || raw === undefined || raw === "" || typeof raw === "boolean") return null;
  var n = Number(raw);
  return isFinite(n) ? n : null;
};
gate.fmt = function (value, decimals, unit) {
  var n = gate.num(value);
  if (n === null) return "—";
  return n.toFixed(decimals || 0) + (unit ? " " + unit : "");
};
gate.mean = function (values) {
  if (!Array.isArray(values) || !values.length) return null;
  var nums = values.map(gate.num);
  if (nums.some(function (n) { return n === null; })) return null;
  return nums.reduce(function (s, n) { return s + n; }, 0) / nums.length;
};
gate.rows = function (data, key) {
  if (!Array.isArray(data)) return [];
  return data.filter(function (r) {
    return r && r[key] !== undefined && r[key] !== null && r[key] !== "";
  });
};
gate.row = function (data, key) {
  return gate.rows(data, key)[0] || null;
};
gate.withKey = function (data, key) {
  if (!Array.isArray(data)) return [];
  return data.filter(function (r) {
    return r !== null && typeof r === "object" && Object.prototype.hasOwnProperty.call(r, key);
  });
};
gate.firstWithKey = function (data, key) {
  return gate.withKey(data, key)[0] || null;
};
gate.money = function (value, decimals) {
  var n = gate.num(value);
  if (n === null) return "—";
  var d = decimals === undefined ? 2 : decimals;
  return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(d);
};
gate.esc = function (text) {
  return String(text === null || text === undefined ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;")
    // ThingsBoard compiles markdown HTML as an Angular template: no {{ }} or @-blocks from data.
    .replace(/\{/g, "&#123;").replace(/\}/g, "&#125;").replace(/@/g, "&#64;");
};
// A value is unknown when its group's `*_unknown` attribute lists its key (comma-separated):
// unknown values are never written (no null), so an old value stays on the entity after it stops being current.
gate.known = function (row, key, unknownKey) {
  if (row === null || typeof row !== "object") return null;
  var listed = String(row[unknownKey] || "").split(",");
  return listed.indexOf(key) >= 0 ? null : row[key];
};
// family (optional): "energy", "cost" or "weather" sets the card's accent colour.
gate.card = function (title, bodyHtml, family) {
  var cls = family === "energy" || family === "cost" || family === "weather" ? "gate-card gate-" + family : "gate-card";
  return '<div class="' + cls + '"><div class="gate-title">' + gate.esc(title) + "</div>" + bodyHtml + "</div>";
};
// Today so far against yesterday up to the same local time, in percent; a change that rounds
// to 0.0 % is neutral (muted, no arrow). Returns the gate-delta span.
gate.deltaPct = function (today, before, suffix) {
  var t = gate.num(today), b = gate.num(before);
  var shown = t !== null && b ? Math.round((t - b) / b * 1000) / 10 : null;
  var tail = suffix ? " " + suffix : "";
  if (shown === null) return '<span class="gate-delta">—</span>';
  if (shown === 0) return '<span class="gate-delta gate-muted">' + gate.fmt(0, 1, "%") + tail + "</span>";
  return '<span class="gate-delta ' + (shown > 0 ? "gate-amber" : "gate-ok") + '">' + (shown > 0 ? "▲ " : "▼ ") +
    gate.fmt(Math.abs(shown), 1, "%") + tail + "</span>";
};
// Budget status colour for a used percentage: green below 80 %, amber below 100 %, red beyond.
gate.budgetColor = function (used) {
  var u = gate.num(used);
  return u === null ? "#1f2937" : u < 80 ? "#22c55e" : u < 100 ? "#f59e0b" : "#ef4444";
};
gate.bar = function (fraction, color) {
  var f = gate.num(fraction);
  var cls = f === null ? "gate-bar unknown" : "gate-bar";
  var pct = f === null ? 0 : Math.round(Math.min(1, Math.max(0, f)) * 100);
  return '<div class="' + cls + '"><span style="width:' + pct + "%;background:" + gate.esc(color) + '"></span></div>';
};
// Correlation strength of a coefficient r: |r| > 0.7 High, > 0.4 Moderate, else Low.
gate.strength = function (r) {
  var n = gate.num(r);
  if (n === null) return null;
  var a = Math.abs(n);
  return a > 0.7 ? "High" : a > 0.4 ? "Moderate" : "Low";
};
// Data age. ctx.now (ms) fixes the clock in tests; otherwise the browser's.
gate.now = function (ctx) {
  var n = gate.num(ctx && ctx.now);
  return n === null ? Date.now() : n;
};
// Stale past twice the cadence: today_snapshot every 15 min, the forecast hourly, the summary nightly.
gate.AGE = { today: 30 * 60000, forecast: 2 * 3600000, nightly: 26 * 3600000 };
gate.clock = function (ms) {
  var parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/Toronto", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(ms));
  var get = function (type) { return parts.filter(function (p) { return p.type === type; })[0].value; };
  return get("hour") + ":" + get("minute");
};
// A muted "updated HH:MM" line (America/Toronto); gate-stale when older than maxAgeMs or unknown.
gate.updated = function (ts, maxAgeMs, ctx) {
  var t = gate.num(ts);
  var stale = t === null || gate.now(ctx) - t > maxAgeMs;
  return '<div class="gate-updated gate-muted' + (stale ? " gate-stale" : "") + '">updated ' +
    (t === null ? "—" : gate.clock(t)) + "</div>";
};
