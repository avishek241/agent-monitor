import { fixture, s, count } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { overviewView, accountsView, nextReset } from "../../web/views/overview.js";
import { projectView } from "../../web/views/project.js";
import { taskView, reviewSheet, reassignSheet, answerSheet, actionsFor } from "../../web/views/task.js";
import { freeLane, quotaText } from "../../web/views/common.js";
import { findTask, TABS } from "../../web/store.js";

const o = fixture("overview.json");
const p = fixture("project.json");
const now = o.time;
const acct = (id) => o.accounts.find((a) => a.id === id);

test("overview: project tiles link to project pages; disabled project is not a link", () => {
  const h = s(overviewView(o, now, "live"));
  assert.match(h, /href="#\/p\/shop-ops\/board"/);
  assert.match(h, /href="#\/p\/client-portal\/board"/);
  assert.doesNotMatch(h, /href="#\/p\/home-lab/);
  assert.match(h, /tile disabled/);
});

test("overview: accounts strip shows the office account with usage and scope badges", () => {
  const h = s(overviewView(o, now, "live"));
  assert.match(h, /Claude · office<span class="sc o">OFFICE<\/span>/);
  assert.match(h, /5h 18%/);
  assert.ok(count(h, 'class="sc p"') >= 4);
  assert.match(h, /5\/8 JOBS/);
});

test("overview: needs-you list links reviews to the review sheet and questions to the answer sheet", () => {
  const h = s(overviewView(o, now, "live"));
  assert.match(h, /#\/p\/shop-ops\/t\/107-supplier-import\/review/);
  assert.match(h, /#\/p\/shop-ops\/t\/111-tax-number-rows\/answer/);
  assert.match(h, /NEXT RESET · CLAUDE · WORKER 21:51 → resumes 104/);
  assert.equal(nextReset({ accounts: [] }, now), null);
});

test("overview: OFFLINE badge replaces the LED when the stream drops", () => {
  assert.match(s(overviewView(o, now, "offline")), /OFFLINE · last update 21:42/);
  assert.doesNotMatch(s(overviewView(o, now, "offline")), /class="led"/);
  assert.match(s(overviewView(o, now, "live")), /class="led"/);
});

test("free lanes explain themselves with the planner's words only", () => {
  const a = freeLane(acct("codex-a"), now);
  assert.equal(a.cls, "free");
  assert.match(a.text, /36% to spend before 22:42 · M fits · ⚠ spare 5m/);
  const office = freeLane(acct("claude-office"), now);
  assert.equal(office.cls, "pol");
  assert.match(office.text, /3 queued tasks are personal projects \(account policy\)/);
  assert.equal(freeLane(acct("codex-b"), now).cls, "idle");
  assert.equal(freeLane(acct("claude-worker"), now).cls, "cool");
  // without whyKind, a policy reason is still recognised
  assert.equal(freeLane({ capacity: { why: "only office projects (policy)" } }, now).cls, "pol");
});

test("a cooling account shows a LIMIT countdown, never '100%'", () => {
  const q = quotaText(acct("claude-worker"), now);
  assert.equal(q.text, "LIMIT → 21:51 (in 9m)");
  assert.ok(q.hot);
  assert.doesNotMatch(q.text, /100%/);
});

test("accounts screen: every lane W·n is listed; policy-idle lane is grey not orange; spare card for codex-a", () => {
  const h = s(accountsView(o, now, "live"));
  for (const id of ["W1·1", "W1·2", "W1·3", "W2·1", "W2·2", "W2·3", "W4·1", "W4·2", "W3·1"]) assert.ok(h.includes(`>${id}<`), id);
  assert.match(h, /lane pol"><span class="id">W4·2/);
  assert.match(h, /lane free"><span class="id">W1·3/);
  assert.match(h, /resumes 21:52 \(same session\)/);
  assert.match(h, /Spare: Codex · a · M fits/);
  assert.match(h, /MACHINE CAP 6/);
  assert.match(h, /usage only/); // the orchestrator account is shown, not dispatched
});

test("project page renders all six tabs with the active one marked", () => {
  for (const tab of TABS) {
    const h = s(projectView(p, o, tab, now, "live"));
    assert.equal(count(h, 'role="tab"'), 6);
    assert.match(h, new RegExp(`aria-selected="true" class="on" href="#/p/shop-ops/${tab}"`));
  }
});

test("board: columns in order with task keys; limit-paused task shows its resume time", () => {
  const h = s(projectView(p, o, "board", now, "live"));
  const order = ["RUNNING · 3", "REVIEW · 2", "NEEDS YOU · 1", "UP NEXT · 3", "BACKLOG · 1", "DONE TODAY · 4"].map((c) => h.indexOf(c));
  assert.ok(order.every((i, k) => i >= 0 && (k === 0 || i > order[k - 1])), order.join(","));
  assert.match(h, /resumes 21:52 \(in 11m\)/);
});

test("workers tab: eligible accounts with lanes, office account greyed with the policy reason", () => {
  const h = s(projectView(p, o, "workers", now, "live"));
  assert.match(h, /NOT ELIGIBLE/);
  assert.match(h, /acctcard off" aria-disabled="true"/);
  assert.match(h, /Office account: never runs personal projects \(account policy\)/);
});

test("review tab, alerts, timeline, repo", () => {
  assert.match(s(projectView(p, o, "review", now, "live")), /t\/107-supplier-import\/review/);
  assert.match(s(projectView(p, o, "review", now, "live")), /⚠ 1 uncommitted/);
  assert.match(s(projectView(p, o, "alerts", now, "live")), /policy\.denied/);
  const tl = s(projectView(p, o, "timeline", now, "live"));
  assert.ok(tl.indexOf("Map columns") < tl.indexOf("103 merged"), "newest first");
  const repo = s(projectView(p, o, "repo", now, "live"));
  assert.match(repo, /ext:pc-ssh/);
  assert.match(repo, /110 waiting/);
  assert.match(repo, /✗ Demo :3700/);
});

test("task detail: steps with ETA, metrics, attempts, and actions for a running task", () => {
  const h = s(taskView(fixture("task.json"), findTask(p, "106-match-receipts"), p, now, "live"));
  assert.match(h, /data-p="shop-ops" data-t="106-match-receipts" data-rev="14"/);
  assert.match(h, /◉ Map columns · ~22m left/);
  assert.match(h, /412 \/ 1,050 · ≈18\/min/);
  assert.match(h, /3 steps added by attempt 2 \(resumed after limit\)/);
  assert.match(h, /data-act="pause"/);
  assert.match(h, /data-act="priority" data-priority="P0"/);
  assert.match(h, /data-hold="cancel"/);
  assert.match(h, /t\/106-match-receipts\/reassign/);
});

test("actionsFor maps states to sensible keys", () => {
  assert.deepEqual(actionsFor("limit_paused"), ["resume", "reassign", "prio-up", "prio-down", "cancel"]);
  assert.deepEqual(actionsFor("review"), ["review"]);
  assert.ok(actionsFor("blocked", { question: true }).includes("answer"));
  assert.ok(!actionsFor("blocked", { question: true }).includes("resume"));
  assert.ok(actionsFor("hung").includes("retry"));
  assert.deepEqual(actionsFor("done"), []);
});

test("task detail falls back to the TaskVM when the detail endpoint has nothing", () => {
  const h = s(taskView({ task: null, card: null }, findTask(p, "104-vendor-model"), p, now, "live"));
  assert.match(h, /VENDOR MODEL/);
  assert.match(h, /data-act="resume"/);
  assert.match(s(taskView(null, null, p, now, "live")), /NOT FOUND/);
});

test("review sheet: done-when evidence, decision chips, approve/changes, reject on hold", () => {
  const h = s(reviewSheet(p.inbox[0], p, now, "live"));
  assert.match(h, /data-rev="21"/);
  assert.equal(count(h, 'data-act="answer" data-decision="0"'), 3);
  assert.match(h, /data-act="approve"/);
  assert.match(h, /data-act="changes"/);
  assert.match(h, /data-hold="reject"/);
  assert.match(h, /pilot\/suppliers-20\.json · 20 rows/);
  assert.match(h, /unlocks 108/);
  assert.match(s(reviewSheet(p.inbox[1], p, now, "live")), /1 uncommitted change will not be merged/);
  assert.match(s(reviewSheet(undefined, p, now, "live")), /NOTHING TO REVIEW/);
});

test("reassign sheet: disallowed workers are shown greyed with the server reason and cannot be tapped", () => {
  const e = fixture("eligible.json");
  const h = s(reassignSheet(findTask(p, "110-attendance-history"), e, p, now, "live"));
  assert.equal(count(h, 'data-act="reassign"'), 2);
  assert.match(h, /data-worker="W1"/);
  assert.match(h, /data-worker="W2"/);
  assert.equal(count(h, 'class="wk off" aria-disabled="true"'), 3);
  for (const w of e.filter((x) => !x.allowed)) assert.ok(h.includes(w.reason), w.reason);
  assert.doesNotMatch(h, /data-worker="W4"/);
  assert.match(h, /🔒/);
});

test("answer sheet offers one-tap options plus free text", () => {
  const h = s(answerSheet(findTask(p, "111-tax-number-rows"), p, now, "live"));
  assert.match(h, /data-act="answer" data-note="inactive"/);
  assert.match(h, /inactive \(default\)/);
  assert.match(h, /id="note"/);
});

test("server text is escaped everywhere (no HTML injection from task titles or reasons)", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const t = { ...findTask(p, "106-match-receipts"), title: evil, summary: evil };
  const h = s(taskView(null, t, p, now, "live"));
  assert.doesNotMatch(h, /<img/);
  const r = s(reassignSheet(t, [{ worker: "W9", label: evil, scope: "office", allowed: false, reason: evil }], p, now, "live"));
  assert.doesNotMatch(r, /<img/);
  const ov = s(overviewView({ ...o, projects: [{ ...o.projects[0], headline: evil }] }, now, "live"));
  assert.doesNotMatch(ov, /<img/);
});
