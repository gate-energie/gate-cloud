import { test } from "node:test";
import assert from "node:assert/strict";
import { loadLib } from "./harness.mjs";

const load = (extra = "") => new Function(loadLib() + extra + "\nreturn gate;")();
const gate = load();

test("json parses strings and passes objects through", () => {
  assert.deepEqual(gate.json('{"a":1}'), { a: 1 });
  assert.deepEqual(gate.json({ a: 1 }), gate.json('{"a":1}'));
  for (const bad of ["", null, undefined, "{nope", "  "]) assert.equal(gate.json(bad), null);
});

test("json accepts arrays as strings and as values", () => {
  assert.deepEqual(gate.json("[1,2]"), [1, 2]);
  assert.deepEqual(gate.json([1]), [1]);
});

test("num returns finite numbers or null", () => {
  assert.equal(gate.num("12.5"), 12.5);
  assert.equal(gate.num(3), 3);
  assert.equal(gate.num(0), 0);
  for (const bad of ["", "abc", null, undefined, NaN, Infinity]) assert.equal(gate.num(bad), null);
});

test("fmt", () => {
  assert.equal(gate.fmt(null), "—");
  assert.equal(gate.fmt(undefined, 1, "kW"), "—");
  assert.equal(gate.fmt(1.256, 1), "1.3");
  assert.equal(gate.fmt(0, 0), "0");
  assert.equal(gate.fmt(2.5, 1, "kW"), "2.5 kW");
  assert.equal(gate.fmt(7), "7");
});

test("row and rows pick rows with a non-empty value", () => {
  const data = [{ a: "" }, { a: "1", n: "x" }, { a: "2" }, { b: 1 }];
  assert.equal(gate.row(data, "a"), data[1]);
  assert.deepEqual(gate.rows(data, "a"), [data[1], data[2]]);
  assert.equal(gate.row(data, "zzz"), null);
  assert.deepEqual(gate.rows(data, "zzz"), []);
  assert.equal(gate.row(null, "a"), null);
  assert.deepEqual(gate.rows(undefined, "a"), []);
});

test("row and rows tolerate data that is not an array", () => {
  for (const bad of [{}, { a: 1 }, "abc", 5]) {
    assert.deepEqual(gate.rows(bad, "a"), []);
    assert.equal(gate.row(bad, "a"), null);
  }
});

test("esc escapes html", () => {
  assert.equal(gate.esc(`<a href="x">&'</a>`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;");
  assert.equal(gate.esc(null), "");
});

test("card wraps and escapes the title", () => {
  assert.equal(
    gate.card("A<b>", "<p>x</p>"),
    '<div class="gate-card"><div class="gate-title">A&lt;b&gt;</div><p>x</p></div>'
  );
});

test("bar clamps the fraction", () => {
  assert.equal(gate.bar(0.5, "#fff"), '<div class="gate-bar"><span style="width:50%;background:#fff"></span></div>');
  assert.match(gate.bar(1.5, "#fff"), /width:100%/);
  assert.match(gate.bar(-1, "#fff"), /width:0%/);
  assert.match(gate.bar(null, "#fff"), /^<div class="gate-bar unknown"><span style="width:0%/);
});

test("including the library twice is harmless", () => {
  const twice = new Function(loadLib() + "\n" + loadLib() + "\nreturn gate;")();
  assert.equal(twice.fmt(1, 0), "1");
});

test("withKey and firstWithKey keep rows that carry the key, even blank", () => {
  const data = [{ a: "" }, { b: "1" }, { a: "2" }];
  assert.deepEqual(gate.withKey(data, "a"), [data[0], data[2]]);
  assert.equal(gate.firstWithKey(data, "a"), data[0]);
  assert.equal(gate.firstWithKey(data, "z"), null);
  for (const bad of [null, undefined, {}, "x"]) {
    assert.deepEqual(gate.withKey(bad, "a"), []);
    assert.equal(gate.firstWithKey(bad, "a"), null);
  }
});

test("money formats dollars with a sign before the symbol", () => {
  assert.equal(gate.money("1.5"), "$1.50");
  assert.equal(gate.money(0), "$0.00");
  assert.equal(gate.money(-2), "-$2.00");
  assert.equal(gate.money(12.345, 0), "$12");
  assert.equal(gate.money(""), "—");
});

test("fmt puts a thin space (U+2009) before the unit", () => {
  assert.equal(gate.fmt(2.5, 1, "kW"), "2.5 kW");
  assert.equal(gate.fmt(3, 0, "%"), "3 %");
});

test("mean is null unless every value is known", () => {
  assert.equal(gate.mean(["121.5", 122.5]), 122);
  assert.equal(gate.mean([1]), 1);
  for (const bad of [["1", ""], [null, 2], [], null, undefined]) assert.equal(gate.mean(bad), null);
});

test("strength labels a correlation coefficient, null when unknown", () => {
  assert.equal(gate.strength("-0.82"), "High");
  assert.equal(gate.strength(0.45), "Moderate");
  assert.equal(gate.strength("0.4"), "Low");
  assert.equal(gate.strength(-0.1), "Low");
  for (const bad of ["", null, undefined, "x"]) assert.equal(gate.strength(bad), null);
});

test("known hides a value its group lists as unknown", () => {
  const row = { today_energy_kwh: "12.4", today_cost_cad: "1.85", today_unknown: "today_cost_cad,today_peak_w" };
  assert.equal(gate.known(row, "today_energy_kwh", "today_unknown"), "12.4");
  assert.equal(gate.known(row, "today_cost_cad", "today_unknown"), null);
  // Only whole names match; "" lists nothing.
  assert.equal(gate.known({ a_b: 1, u: "a" }, "a_b", "u"), 1);
  assert.equal(gate.known({ twin_quality: "", twin_unknown: "" }, "twin_quality", "twin_unknown"), "");
  assert.equal(gate.known({ x: 0 }, "x", "missing_unknown"), 0);
  assert.equal(gate.known({ x: "1", u: null }, "x", "u"), "1");
  for (const bad of [null, undefined, "row"]) assert.equal(gate.known(bad, "x", "u"), null);
  assert.equal(gate.known({}, "x", "u"), undefined);
});

test("esc also escapes Angular template braces and @", () => {
  // ThingsBoard compiles markdown HTML as an Angular template: {{ }} and @if would run.
  assert.equal(gate.esc("{{a}} @if"), "&#123;&#123;a&#125;&#125; &#64;if");
});
