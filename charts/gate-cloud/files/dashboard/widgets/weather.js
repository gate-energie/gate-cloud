// WEATHER OUTLOOK. Classes: gate-big, gate-condition, gate-condition-now, gate-list, gate-list-row, gate-label,
// gate-value, gate-subtitle, gate-tiles, gate-tile, gate-tile-date, gate-tile-range, gate-muted.
// Weather row: temperature_c, humidity_pct, wind_speed_ms (time series), forecast (JSON {days, hours}),
// forecast_updated_at, forecast_unknown.
var weather = gate.firstWithKey(data, "temperature_c") || {};
var forecast = gate.json(gate.known(weather, "forecast", "forecast_unknown")) || {};
var days = Array.isArray(forecast.days) ? forecast.days.slice(0, 3) : [];
var hours = Array.isArray(forecast.hours) ? forecast.hours : [];
var condition = function (raw) {
  var c = gate.num(raw);
  if (c === null) return "—";
  if (c === 0) return "Clear";
  if (c === 1 || c === 2) return "Partly cloudy";
  if (c === 3) return "Cloudy";
  if (c === 45 || c === 48) return "Fog";
  if (c >= 51 && c <= 57) return "Drizzle";
  if (c >= 61 && c <= 67) return "Rain";
  if (c >= 71 && c <= 77) return "Snow";
  if (c >= 80 && c <= 82) return "Showers";
  if (c === 85 || c === 86) return "Snow showers";
  if (c >= 95 && c <= 99) return "Thunderstorm";
  return "—";
};
// No live weather code is recorded: the forecast hour closest to now stands for "now"
// (ctx.now lets tests fix the clock), else today's daily code.
var now = gate.now(ctx);
var nearest = null;
hours.forEach(function (h) {
  var ts = h ? gate.num(h.ts) : null;
  if (ts !== null && (nearest === null || Math.abs(ts - now) < Math.abs(nearest.ts - now))) nearest = { ts: ts, code: h.code };
});
var nowCode = nearest ? nearest.code : days.length && days[0] ? days[0].code : null;
var row = function (label, value) {
  return '<div class="gate-list-row"><span class="gate-label">' + label + '</span><span class="gate-value">' + value + "</span></div>";
};
var deg = function (v) { var s = gate.fmt(v, 0); return s === "—" ? s : s + "°"; };
var tiles = days.map(function (d) {
  d = d || {};
  var date = typeof d.date === "string" ? d.date.slice(5).replace("-", "/") : "—";
  return '<div class="gate-tile"><div class="gate-tile-date">' + gate.esc(date) + '</div><div class="gate-condition">' +
    condition(d.code) + '</div><div class="gate-tile-range">' + deg(d.tmin) + " / " + deg(d.tmax) + "</div></div>";
}).join("");
var html = '<div class="gate-big">' + gate.fmt(weather.temperature_c, 1, "°C") + "</div>" +
  '<div class="gate-condition gate-condition-now">' + condition(nowCode) + "</div>" +
  '<div class="gate-list">' + row("Humidity", gate.fmt(weather.humidity_pct, 0, "%")) +
  row("Wind", gate.fmt(weather.wind_speed_ms, 1, "m/s")) + "</div>" +
  '<div class="gate-subtitle">Forecast (3 days)</div>' +
  (tiles ? '<div class="gate-tiles">' + tiles + "</div>" : '<div class="gate-muted">—</div>') +
  gate.updated(weather.forecast_updated_at, gate.AGE.forecast, ctx);
return gate.card("WEATHER OUTLOOK", html);
