import { WEB } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { rateText } from "../../web/fmt.js";
import { workersWithUsage } from "../../web/views/project.js";
import { projectView } from "../../web/views/project.js";
import { fixture, s } from "./helpers.mjs";

// B1's frozen contract (deck/views_types.py). Found in this repo after the merge, or via DECK_B1=<path to agent-monitor-b1>.
const ROOT = path.resolve(WEB, "..");
const B1 = [ROOT, process.env.DECK_B1, path.resolve(ROOT, "../agent-monitor-b1")].find((p) => p && fs.existsSync(path.join(p, "deck/views_types.py")));

test("fixtures pass B1 views_types.check_vm()", { skip: !B1 && "deck/views_types.py not found" }, () => {
  const py = `
import json, sys
from deck.views_types import check_vm
d = sys.argv[1]
m = {"overview.json": "OverviewVM", "project.json": "ProjectVM", "project.client-portal.json": "ProjectVM", "review.json": "ReviewVM", "task.json": "TaskDetailVM"}
errs = []
for f, n in m.items(): errs += [f + ": " + e for e in check_vm(n, json.load(open(d + "/" + f)))]
for i, e in enumerate(json.load(open(d + "/eligible.json"))): errs += ["eligible[%d]: %s" % (i, x) for x in check_vm("EligibleVM", e)]
for i, t in enumerate(json.load(open(d + "/task.json"))["view"] and [json.load(open(d + "/task.json"))["view"]]): errs += check_vm("TaskVM", t)
print(json.dumps(errs))`;
  const r = spawnSync("python3", ["-c", py, path.join(WEB, "fixtures")], { cwd: B1, encoding: "utf8", env: { ...process.env, PYTHONPATH: B1 } });
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(JSON.parse(r.stdout), []);
});

test("UI action list matches B1 ACTIONS", { skip: !B1 && "deck/views_types.py not found" }, async () => {
  const { ACTIONS } = await import("../../web/api.js");
  const src = fs.readFileSync(path.join(B1, "deck/views_types.py"), "utf8");
  const b1 = src.match(/ACTIONS = \(([^)]*)\)/)[1].match(/"(\w+)"/g).map((x) => x.replace(/"/g, ""));
  assert.deepEqual([...ACTIONS].sort(), [...b1].sort());
});

test("metric rate (per minute) and left (minutes) are numbers and render as text", () => {
  assert.equal(rateText({ rate: 18, left: 35 }), " · ≈18/min · ~35m left");
  assert.equal(rateText({ rate: 0.25, left: 150 }), " · ≈0.3/min · ~2h30 left");
  assert.equal(rateText({ rate: null, left: null }), "");
});

test("B1 WorkerVM (no usage fields) is joined with the Overview account for quota bars", () => {
  const o = fixture("overview.json"), p = fixture("project.json");
  const ws = workersWithUsage(p, o);
  const w1 = ws.find((w) => w.worker === "W1");
  assert.equal(w1.pct5, 52);
  assert.equal(w1.capacity.lanesMax, 3);
  assert.equal(w1.adapter, "codex-exec");
  assert.match(s(projectView(p, o, "workers", o.time, "live")), /5h 52%/);
});

test("health entries with ok:null (B1 makes no network calls) render as not checked", () => {
  const o = fixture("overview.json"), p = fixture("project.json");
  p.project.health = [{ label: "App", ok: null }, { label: "API", ok: true }, { label: "Demo", ok: false }];
  const h = s(projectView(p, o, "repo", o.time, "live"));
  assert.match(h, /\? App \(not checked\)/);
  assert.match(h, /● API/);
  assert.match(h, /✗ Demo/);
});
