import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const widgets = join(dirname(fileURLToPath(import.meta.url)), "..", "widgets");

export function loadLib() {
  return readFileSync(join(widgets, "_lib.js"), "utf8");
}

// Evaluates _lib.js + a card body the way ThingsBoard does and checks the HTML.
export function renderCard(file, data, ctx = {}) {
  const body = readFileSync(join(widgets, file), "utf8");
  const out = new Function("data", "ctx", loadLib() + "\n" + body)(data, ctx);
  assert.equal(typeof out, "string", `${file} must return a string`);
  assert.ok(!out.includes("NaN"), "output contains NaN");
  assert.ok(!out.includes("undefined"), "output contains undefined");
  assert.ok(!/<svg/i.test(out), "output contains <svg");
  assert.ok(!/<script/i.test(out), "output contains <script");
  assert.ok(!/<[^>]*\son[a-z]+\s*=/i.test(out), "output contains an event handler");
  return out;
}
