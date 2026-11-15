import "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { esc, html, raw, hhmm, dur, until, bumpPriority, tnum, stateIcon } from "../../web/fmt.js";
import { parseRoute, routeHash, neighbourProject, findTask, serverNow } from "../../web/store.js";
import { fixture } from "./helpers.mjs";

test("esc and html escape interpolations but keep raw()", () => {
  assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
  assert.equal(String(html`<b>${"<i>"}</b>${raw("<i>")}`), "<b>&lt;i&gt;</b><i>");
  assert.equal(String(html`${["a", "<"]}${null}${false}${0}`), "a&lt;0");
});

test("time formatting", () => {
  assert.equal(hhmm(1794759120), "21:42");
  assert.equal(dur(45), "45s");
  assert.equal(dur(540), "9m");
  assert.equal(dur(12000), "3h20");
  assert.equal(dur(3600), "1h");
  assert.equal(dur(3 * 86400 + 7200), "3d2h");
  assert.equal(until(1000 + 540, 1000), "in 9m");
  assert.equal(until(1000, 1000 + 120), "2m ago");
});

test("priority bump clamps at P0 and P3", () => {
  assert.equal(bumpPriority("P2", +1), "P1");
  assert.equal(bumpPriority("P0", +1), "P0");
  assert.equal(bumpPriority("P3", -1), "P3");
  assert.equal(bumpPriority("P1", -1), "P2");
});

test("task number chip and state icons", () => {
  assert.equal(tnum({ id: "107-supplier-import" }), "107");
  assert.equal(tnum({ num: 12, id: "x" }), "12");
  assert.equal(stateIcon("limit_paused"), "⏸");
});

test("routes round-trip", () => {
  const cases = [
    { view: "overview" }, { view: "accounts" }, { view: "pair" },
    { view: "project", project: "shop-ops", tab: "workers" },
    { view: "task", project: "shop-ops", task: "107-supplier-import", sheet: null },
    { view: "task", project: "shop-ops", task: "110-attendance-history", sheet: "reassign" },
  ];
  for (const r of cases) assert.deepEqual(parseRoute(routeHash(r)), r);
  assert.deepEqual(parseRoute("#/p/x/bogus"), { view: "project", project: "x", tab: "board" });
  assert.deepEqual(parseRoute("#/nonsense"), { view: "overview" });
  assert.deepEqual(parseRoute(""), { view: "overview" });
});

test("swipe neighbours skip disabled projects and wrap", () => {
  const ps = fixture("overview.json").projects;
  assert.equal(neighbourProject(ps, "shop-ops", +1), "client-portal");
  assert.equal(neighbourProject(ps, "client-portal", +1), "shop-ops");
  assert.equal(neighbourProject(ps, "shop-ops", -1), "client-portal");
  assert.equal(neighbourProject([], "x", 1), null);
});

test("findTask searches every column; serverNow advances with local time", () => {
  const p = fixture("project.json");
  assert.equal(findTask(p, "113-brief-draft").state, "draft");
  assert.equal(findTask(p, "nope"), null);
  assert.equal(serverNow(1000, 50, 80), 1030);
});
