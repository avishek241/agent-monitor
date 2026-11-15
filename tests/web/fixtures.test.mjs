import { WEB, fixture } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const o = fixture("overview.json");
const p = fixture("project.json");

test("OverviewVM fixture has every §12 key", () => {
  for (const k of ["time", "inbox", "projects", "accounts", "machine", "backlog", "alerts"]) assert.ok(k in o, k);
  for (const pr of o.projects) for (const k of ["id", "title", "scope", "color", "icon", "enabled", "counts", "headline", "alerts"]) assert.ok(k in pr, `${pr.id}.${k}`);
});

test("accounts: every account has scope, usage, capacity and lanes; the office account is present with real usage", () => {
  for (const a of o.accounts) {
    for (const k of ["id", "label", "tool", "scope", "pct5", "state", "capacity", "lanes"]) assert.ok(k in a, `${a.id}.${k}`);
    assert.ok(["office", "personal"].includes(a.scope));
    for (const l of a.lanes) { assert.ok(l.worker && Number.isInteger(l.lane)); assert.ok(["free", "running", "cooling"].includes(l.state)); }
  }
  const office = o.accounts.filter((a) => a.scope === "office");
  assert.ok(office.length >= 1);
  assert.equal(typeof office[0].pct5, "number");
});

test("at least one account runs several lanes in parallel", () => {
  assert.ok(o.accounts.some((a) => a.lanes.filter((l) => l.state === "running").length >= 2));
});

test("ProjectVM fixture has all board columns, inbox, alerts, timeline, domains, merge", () => {
  for (const k of ["backlog", "upNext", "running", "review", "needsYou", "doneToday"]) assert.ok(Array.isArray(p.board[k]), k);
  for (const k of ["project", "workers", "inbox", "alerts", "timeline", "domains", "merge", "lanes"]) assert.ok(k in p, k);
  for (const col of Object.values(p.board)) for (const t of col) {
    for (const k of ["id", "num", "title", "state", "priority", "size", "rev"]) assert.ok(k in t, `${t.id}.${k}`);
    assert.ok(Array.isArray(t.metrics) && Array.isArray(t.deps));
  }
});

test("ReviewVM and handoff-derived fields", () => {
  const r = fixture("review.json");
  for (const k of ["task", "attempt", "summary", "outcome", "doneWhen", "tests", "risks", "decisionsNeeded", "git", "waitingSec", "pilot", "unlocks"]) assert.ok(k in r, k);
  for (const d of r.doneWhen) assert.deepEqual(Object.keys(d).sort(), ["evidence", "item", "met"]);
});

test("eligible-workers: disallowed rows carry a reason; the office account is refused for a personal project", () => {
  const e = fixture("eligible.json");
  for (const w of e) for (const k of ["worker", "account", "label", "scope", "allowed", "reason"]) assert.ok(k in w, `${w.worker}.${k}`);
  for (const w of e.filter((x) => !x.allowed)) assert.ok(w.reason && w.reason.length > 5);
  assert.ok(e.some((w) => w.scope === "office" && !w.allowed && /office/i.test(w.reason)));
});

test("task detail card follows card protocol v2 (steps need id/title/status/etaMin; metrics are a list)", () => {
  const t = fixture("task.json");
  for (const st of t.card.steps) {
    assert.match(st.id, /^[a-z0-9-]{1,32}$/);
    assert.ok(st.title.length >= 2 && st.title.length <= 70);
    assert.ok(["pending", "active", "done", "skipped", "failed"].includes(st.status));
    assert.ok(st.etaMin > 0);
  }
  assert.ok(Array.isArray(t.card.metrics));
});

test("fixtures contain neutral names only (no private paths or tokens)", () => {
  for (const f of fs.readdirSync(`${WEB}/fixtures`)) {
    const txt = fs.readFileSync(`${WEB}/fixtures/${f}`, "utf8");
    assert.doesNotMatch(txt, /\/Users\/|bhumi|Bearer |sk-[A-Za-z0-9]/i, f);
  }
});
