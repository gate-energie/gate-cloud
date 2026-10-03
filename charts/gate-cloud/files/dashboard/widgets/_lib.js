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
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
};
gate.card = function (title, bodyHtml) {
  return '<div class="gate-card"><div class="gate-title">' + gate.esc(title) + "</div>" + bodyHtml + "</div>";
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
