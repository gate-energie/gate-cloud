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
  assert.ok(!/<[^>]*[\s/]on[a-z]+\s*=/i.test(out), "output contains an event handler");
  return out;
}

// Fields ThingsBoard adds to every markdown row besides the data keys.
export const ENTITY = /^(\$datasource|entity[A-Z]\w*|deviceName|deviceType|aliasName|dsIndex|dsName)$|\|ts$/;
export const TS = 1759500000000;

// A row the way ThingsBoard builds it: entity fields, datasource info and,
// for every key, obj[label] = value and obj[label + "|ts"] = timestamp.
export const real = (dsName, row) => {
  const out = {
    $datasource: { name: dsName, entityName: row.entityName },
    entityName: row.entityName, deviceName: row.entityName, entityId: "id-" + row.entityName,
    entityType: row.entityType, entityLabel: "", entityDescription: "", aliasName: dsName,
    dsIndex: 0, dsName, deviceType: null,
  };
  for (const [k, v] of Object.entries(row)) {
    if (k === "entityName" || k === "entityType") continue;
    out[k] = v;
    out[k + "|ts"] = TS;
  }
  return out;
};

export const blank = (rows) =>
  rows.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, ENTITY.test(k) ? v : ""])));
// Same data with JSON attributes as objects, numbers as numbers and booleans as booleans.
export const typed = (rows) =>
  rows.map((r) =>
    Object.fromEntries(
      Object.entries(r).map(([k, v]) => {
        if (ENTITY.test(k) || k === "label") return [k, v];
        if (typeof v === "string" && /^[[{]/.test(v)) return [k, JSON.parse(v)];
        if (v === "true" || v === "false") return [k, v === "true"];
        return [k, v !== "" && !isNaN(Number(v)) ? Number(v) : v];
      })
    )
  );

// Attribute groups by key prefix and the attribute listing each group's unknown keys.
const GROUPS = [["today_", "today_unknown"], ["yesterday_", "today_unknown"], ["month_", "month_unknown"],
  ["twin_", "twin_unknown"], ["analytics_", "analytics_unknown"], ["forecast", "forecast_unknown"]];
export const groupOf = (key) => {
  if (ENTITY.test(key) || /_(unknown|updated_at)$/.test(key)) return null;
  const hit = GROUPS.find(([prefix]) => key.startsWith(prefix));
  return hit ? hit[1] : null;
};

// Every grouped value listed in its group's unknown list: `listed` keeps the old values
// (stale on the entity), `blanked` empties them. A card honouring the lists renders both alike.
export const staleAndBlank = (rows) => {
  const listed = rows.map((r) => {
    const out = { ...r };
    const lists = {};
    for (const k of Object.keys(r)) {
      const g = groupOf(k);
      if (g) (lists[g] = lists[g] || []).push(k);
    }
    for (const [g, keys] of Object.entries(lists)) out[g] = keys.join(",");
    return out;
  });
  const blanked = listed.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, groupOf(k) ? "" : v])));
  return { listed, blanked };
};
