import { fixture, s, count } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { WEB } from "./helpers.mjs";
import { workersSection, setWorkerColors, wc, usageGraph, dotMatrix } from "../../web/workers.js";
import { overviewView } from "../../web/views/overview.js";
import { render as mission } from "../../web/layouts/mission.js";
import { render as paper } from "../../web/layouts/paper.js";

const o = fixture("overview.json");
const now = o.time;

test("one colour per account in Okabe–Ito order; config colour overrides; workers inherit their account's colour", () => {
  setWorkerColors(o);
  assert.equal(wc("codex-a"), "var(--w0)");
  assert.equal(wc("codex-b"), "var(--w1)");
  assert.equal(wc("claude-office"), "#cc79a7", "AccountVM.color overrides the palette");
  assert.equal(wc("W1"), "var(--w0)");
  assert.equal(wc("W3"), "var(--w3)");
  assert.equal(wc("nobody"), null);
});

test("worker colours appear on task cards, lanes, account rows and the legend", () => {
  const h = s(overviewView(o, now, "live", new Set()));
  assert.match(h, /class="acct" href="#\/accounts" style="--wc:var\(--w0\)"/);
  assert.equal(count(h, 'class="wdot"') >= o.accounts.length, true, "legend dot per worker");
});

test("Workers section: collapsed shows the summary; expanding one worker shows graph, burn, time to limit, lanes, done today, spare hint, queue", () => {
  const closed = s(workersSection(o, now, new Set(), "paper"));
  assert.equal(count(closed, 'data-wexpand="'), o.accounts.length + 1);
  assert.doesNotMatch(closed, /class="wdetail"/);
  assert.match(closed, /aria-expanded="false"/);
  const open = s(workersSection(o, now, new Set(["codex-a"]), "paper"));
  assert.equal(count(open, 'class="wdetail"'), 1);
  assert.match(open, /<svg class="ugraph"/);
  assert.match(open, /class="p5"/); assert.match(open, /class="p7"/); assert.match(open, /class="reset"/);
  assert.match(open, /<dt>Burn<\/dt><dd>14%\/h<\/dd>/);
  assert.match(open, /<dt>To limit<\/dt><dd>~3h26/);
  assert.match(open, /W1·1 ▶ 106 Match receipts to bills/);
  assert.match(open, /<dt>Done today<\/dt><dd>3<\/dd>/);
  assert.match(open, /36% to spend before 22:42 · M fits/);
  assert.match(open, /⏳ 110 Attendance history/);
  const all = s(workersSection(o, now, new Set(["*"]), "mission"));
  assert.equal(count(all, 'class="wdetail"'), o.accounts.length);
});

test("Studio draws the usage graph as an LCD dot matrix", () => {
  const h = s(workersSection(o, now, new Set(["codex-b"]), "studio"));
  assert.match(h, /class="ugraph dm"/);
  assert.ok(count(s(dotMatrix(o.accounts[1], now)), "<circle") >= 100);
});

test("missing usage data hides the graph and facts instead of inventing them", () => {
  const bare = { ...o.accounts[0], usage: undefined, doneToday: undefined, queued: undefined };
  assert.equal(s(usageGraph(bare, now)), "");
  const h = s(workersSection({ accounts: [bare] }, now, new Set(["codex-a"]), "paper"));
  assert.doesNotMatch(h, /Burn|Done today|Queued/);
});

test("every layout shows the Workers section on its overview", () => {
  const p = { "shop-ops": fixture("project.json"), "client-portal": fixture("project.client-portal.json") };
  const ctx = { o, projects: p, details: {}, eligible: {}, now, conn: "live", openWorkers: new Set() };
  assert.match(s(overviewView(o, now, "live")), /class="wsec"/);
  assert.match(s(mission({ view: "overview" }, ctx)), /class="wsec"/);
  assert.match(s(paper({ view: "overview" }, ctx)), /class="wsec"/);
});

test("worker colour tokens --w0..--w6 keep ≥ 3:1 against the card in all six token sets", () => {
  const css = fs.readFileSync(path.join(WEB, "themes.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const sets = {};
  for (const m of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const l = m[1].match(/data-layout="(\w+)"/)?.[1] || "studio", md = m[1].match(/data-mode="(\w+)"/)?.[1] || "base";
    sets[`${l}-${md}`] = Object.fromEntries([...m[2].matchAll(/--([\w-]+):\s*([^;]+);/g)].map((x) => [x[1], x[2].trim()]));
  }
  const base = sets["studio-base"], lay = (l) => ({ ...base, ...(sets[`${l}-base`] || {}) });
  const all = { "studio-light": lay("studio"), "studio-dark": { ...lay("studio"), ...sets["studio-dark"] }, "mission-dark": lay("mission"),
    "mission-light": { ...lay("mission"), ...sets["mission-light"] }, "paper-light": lay("paper"), "paper-dark": { ...lay("paper"), ...sets["paper-dark"] } };
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const con = (a, b) => { const [x, y] = [lum(rgb(a)), lum(rgb(b))].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const bad = [];
  for (const [id, t] of Object.entries(all)) for (let i = 0; i < 7; i++) for (const bg of ["card", "bg"]) {
    const r = con(t[`w${i}`], t[bg]); if (r < 3) bad.push(`${id} --w${i} on --${bg} = ${r.toFixed(2)}`);
  }
  assert.deepEqual(bad, []);
});
