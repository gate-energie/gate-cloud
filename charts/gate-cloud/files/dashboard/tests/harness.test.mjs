import { test } from "node:test";
import assert from "node:assert/strict";
import { renderCard } from "./harness.mjs";

const fixture = (name) => `../tests/fixtures/${name}`;

test("renderCard rejects a card emitting NaN", () => {
  assert.throws(() => renderCard(fixture("bad_nan.js"), []), /NaN/);
});

test("renderCard rejects an inline event handler inside a tag", () => {
  assert.throws(() => renderCard(fixture("bad_onclick.js"), []), /event handler/);
});

test("renderCard accepts 'onboarding =' in text outside tags", () => {
  assert.equal(renderCard(fixture("ok_text_on.js"), []), "<div>Data onboarding = done</div>");
});

test("renderCard rejects an event handler after a slash", () => {
  assert.throws(() => renderCard(fixture("bad_slash_onclick.js"), []), /event handler/);
});
