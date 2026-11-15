import { fixture, s, count } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { render } from "../../web/layouts/mission.js";
import { RENDERERS } from "../../web/layouts/index.js";
import { parseRoute } from "../../web/store.js";

const o = fixture("overview.json");
const projects = { "shop-ops": fixture("project.json"), "client-portal": fixture("project.client-portal.json") };
const ctx = (over = {}) => ({ o, projects, details: {}, eligible: {}, now: o.time, conn: "live", pref: { layout: "mission", mode: "dark" }, token: "", fixtures: true, ...over });
const R = (hash, over) => { const out = render(parseRoute(hash), ctx(over)); return out == null ? null : s(out); };
const kpi = (h, label) => h.match(new RegExp(`<div class="v">([^<]*(?:<small>[^<]*</small>)?)</div><div class="l"><span class="led \\w+" aria-hidden="true"></span>${label}</div>`))?.[1];

test("registered as the mission renderer", () => assert.equal(RENDERERS.mission, render));

test("every Mission route renders the whole screen; task detail and other sheets fall back (null)", () => {
  for (const h of ["#/", "#/accounts", "#/go/projects", "#/go/workers", "#/go/workers/shop-ops", "#/go/board", "#/go/board/client-portal", "#/go/inbox", "#/go/inbox/shop-ops",
    "#/go/ops", "#/go/ops/shop-ops", "#/p/shop-ops/board", "#/p/shop-ops/workers", "#/p/shop-ops/review", "#/p/shop-ops/alerts", "#/p/shop-ops/timeline", "#/p/shop-ops/repo",
    "#/p/shop-ops/t/107-supplier-import/review"]) {
    const out = R(h);
    assert.ok(out, h);
    for (const part of ['class="mc"', 'class="top"', 'class="ticker"', 'class="scope"', 'class="tabs"', "data-layout-cycle", "data-mode-cycle", 'href="#/settings"']) assert.ok(out.includes(part), `${h} ${part}`);
  }
  for (const h of ["#/p/shop-ops/t/106-match-receipts", "#/p/shop-ops/t/111-tax-number-rows/answer", "#/p/shop-ops/t/106-match-receipts/reassign", "#/settings"]) assert.equal(R(h), null, h);
  assert.equal(R("#/p/home-lab/board"), null, "a project without data falls back to the shared view");
  assert.equal(render(parseRoute("#/"), { ...ctx(), o: null }), null);
});

test("overview: KPI grid sums project counts and shows machine lanes x/y", () => {
  const h = R("#/");
  assert.equal(kpi(h, "RUNNING"), "5"); // shop-ops 2 running + 1 limit_paused, client-portal 2
  assert.equal(kpi(h, "READY"), "3");
  assert.equal(kpi(h, "REVIEW"), "2");
  assert.equal(kpi(h, "BLOCKED"), "1");
  assert.equal(kpi(h, "ALERTS"), "2");
  assert.equal(kpi(h, "LANES"), "5<small>/8</small>");
  assert.equal(kpi(h, "DONE TODAY"), "5");
  assert.equal(kpi(h, "INBOX"), "2");
  assert.equal(count(h, 'class="kpi"'), 8);
});

test("scope strip: ALL plus one entry per project with run/alert counts; disabled project is not a link", () => {
  const h = R("#/go/board");
  assert.match(h, /<a class="on" href="#\/go\/board"><span class="nm">ALL<\/span><span class="sub">3 projects<\/span>/);
  assert.match(h, /href="#\/go\/board\/shop-ops" title="Shop operations"><span class="nm"><span class="led rd"[^>]*><\/span>Shop ops<\/span><span class="sub">3 run · 2 alerts<\/span>/);
  assert.match(h, /href="#\/go\/board\/client-portal"[^>]*>.*2 run · 0 alerts/);
  assert.match(h, /<span class="dis" title="Home lab is disabled">.*disabled<\/span>/);
  assert.doesNotMatch(h, /href="#\/go\/board\/home-lab"/);
  // a project scope keeps the current page and marks the project
  assert.match(R("#/go/workers/shop-ops"), /<a class="on" href="#\/go\/workers\/shop-ops"/);
  assert.match(R("#/"), /href="#\/p\/shop-ops\/board" title="Shop operations"/, "OVR scope → project page");
});

test("bottom tabs: OVR · WORKERS · BOARD · REVIEW · OPS with review and alert badges; project scope says PROJECT", () => {
  const h = R("#/");
  const tabs = h.slice(h.indexOf('<nav class="tabs"'));
  for (const t of ["OVR", "WORKERS", "BOARD", "REVIEW", "OPS"]) assert.ok(tabs.includes(t), t);
  assert.match(tabs, /<a class="on" href="#\/" aria-current="page">/);
  assert.match(tabs, /href="#\/go\/inbox">.*REVIEW<span class="bd mg" aria-label="2 waiting">2<\/span>/);
  assert.match(tabs, /href="#\/go\/ops">.*OPS<span class="bd " aria-label="2 waiting">2<\/span>/);
  const p = R("#/p/shop-ops/board");
  assert.match(p, /href="#\/p\/shop-ops\/board" aria-current="page"><span class="g">◎<\/span>PROJECT/);
  assert.match(p, /href="#\/go\/board\/shop-ops"/);
  const c = R("#/go/board/client-portal");
  assert.doesNotMatch(c.slice(c.indexOf('<nav class="tabs"')), /class="bd/, "no badges when nothing waits");
});

test("top bar: LIVE / OFFLINE / FIXTURES, clock, ticker of recent events", () => {
  assert.match(R("#/"), /● SSE LIVE.*<span class="clk">21:42<\/span>/s);
  assert.match(R("#/", { conn: "offline" }), /OFFLINE · 21:42/);
  assert.match(R("#/", { conn: "fixtures" }), /FIXTURES/);
  const t = R("#/");
  assert.match(t, /<div class="tk-in"><span><b>21:40<\/b>109 quiet 16 min/);
  assert.match(t, /106 step &quot;Map columns&quot; started/);
});

test("overview: projects rows, accounts with 20-cell quota bars (no sparkline), needs-you rows", () => {
  const h = R("#/");
  assert.match(h, /href="#\/p\/shop-ops\/board">.*Shop operations.*R<i>3<\/i> Q<i>3<\/i>.*V<i>2<\/i> B<i>1<\/i>/s);
  assert.doesNotMatch(h, /<svg/, "no invented sparklines");
  assert.equal(count(h, 'class="qbar"'), o.accounts.length);
  assert.match(h, /aria-label="52% of 5-hour quota used">(<i class="on"><\/i>){10}<i><\/i>/);
  assert.match(h, /<b class="hot">LIMIT<\/b>5h/, "cooling account shows LIMIT, not 100%");
  assert.match(h, /🔒 W0 is greyed: orchestrator · usage only, never dispatched/);
  assert.match(h, /href="#\/p\/shop-ops\/t\/107-supplier-import\/review">.*REVIEW/);
  assert.match(h, /href="#\/p\/shop-ops\/t\/111-tax-number-rows\/answer">.*ANSWER/);
});

test("needs-you is derived from inboxes, blocked tasks and alerts when OverviewVM.needsYou is missing", () => {
  const h = R("#/", { o: { ...o, needsYou: undefined } });
  assert.match(h, /<span class="n">3<\/span>Needs you/);
  assert.match(h, /href="#\/p\/shop-ops\/t\/107-supplier-import\/review"/);
  assert.doesNotMatch(h, /t\/105-payment-reminders\/review/, "merging items are not waiting for you");
  assert.match(h, /href="#\/p\/shop-ops\/t\/111-tax-number-rows\/answer">.*Import rows without a tax number/s);
  assert.match(h, /t\/110-attendance-history">.*ALERT/s);
});

test("workers: office account greyed with its reason in a personal project; free lanes use capacity.why only", () => {
  const h = R("#/go/workers/shop-ops");
  assert.match(h, /<section class="ac grey" aria-disabled="true">.*W4.*🔒 Claude · office.*Greyed out.<\/b> Office account: never runs personal projects \(account policy\)/s);
  assert.match(h, /🔒 free · no eligible task: 3 queued tasks are personal projects \(account policy\)/);
  assert.match(h, /○ free · waits on ext:pc-ssh \(held by 109\)/);
  assert.match(h, /class="ln spare">.*FREE · 36% to spend before 22:42 · M fits · ⚠ spare 5m/s);
  assert.match(h, /W1.*Shop ops: allowed/s);
  assert.match(h, /class="ln  oos" href="#\/p\/client-portal\/t\/12-login-copy">.*Client · /s, "other projects' lanes are marked");
  assert.match(h, /QUIET 16m/);
  assert.match(h, /<div class="v">1<\/div><div class="l"><span class="led am"[^>]*><\/span>SPARE/);
  assert.match(h, /2 ready \/ target 5/);
  // ALL scope: office is not greyed (it runs office projects); the usage-only orchestrator is
  const a = R("#/go/workers");
  assert.doesNotMatch(a, /Greyed out.<\/b> Office/);
  assert.match(a, /Greyed out.<\/b> orchestrator · usage only, never dispatched/);
  assert.match(a, /RESETS 21:51/);
});

test("board: column counts, open dependency edges, lists with project chips", () => {
  const h = R("#/go/board");
  assert.match(h, /<span class="v">3<\/span><span class="l">READY<\/span>.*<span class="v">5<\/span><span class="l">RUNNING<\/span>.*<span class="v">2<\/span><span class="l">REVIEW<\/span>.*<span class="v">1<\/span><span class="l">BLOCKED<\/span>/s);
  assert.match(h, /<span class="n">2<\/span>Open dependencies/);
  assert.match(h, /<span class="id">107<\/span>REVIEW<\/span><span class="ar"><\/span><span><span class="id">108<\/span>Import suppliers \(full\)/);
  assert.match(h, /⟵ 107 review/);
  assert.match(h, /<span class="cp">Client<\/span>/);
  assert.match(h, /Rows matched 412\/1050/);
  assert.match(h, /BACKLOG · DRAFTS/);
  assert.doesNotMatch(R("#/go/board/shop-ops"), /<span class="cp">Shop ops<\/span>/, "no project chips inside a project");
});

test("review: cards are task roots with done-when, git grid, risks, decision buttons, approve and changes", () => {
  const h = R("#/go/inbox");
  assert.match(h, /<div class="rv task-root" data-p="shop-ops" data-t="107-supplier-import" data-rev="21">/);
  assert.match(h, /<span class="y">\[✓\]<\/span><span>20-row pilot file written<\/span>/);
  assert.match(h, /<b>4<\/b><small>COMMITS<\/small>.*<b>9<\/b><small>FILES<\/small>/s);
  assert.match(h, /▲ RISK · 3 suppliers share one phone number/);
  assert.match(h, /<button class="opt on" data-act="answer" data-decision="0" data-option="inactive">inactive \(default\)<\/button>/);
  assert.match(h, /<button class="act go" data-act="approve">APPROVE ✓<\/button>/);
  assert.match(h, /href="#\/p\/shop-ops\/t\/107-supplier-import\/review">REQUEST CHANGES/);
  assert.match(h, /APPROVED · MERGING · gates 2\/3/, "a merging item shows its stamp instead of buttons");
  assert.ok(h.indexOf("105-payment") < h.indexOf("107-supplier"), "oldest first");
  assert.doesNotMatch(h, /id="note"/, "the list has no note field; the sheet does");
  const sh = R("#/p/shop-ops/t/107-supplier-import/review");
  assert.equal(count(sh, 'id="note"'), 1);
  assert.match(sh, /data-act="changes">SEND CHANGES/);
  assert.match(sh, /data-hold="reject"/);
  assert.match(sh, /<a class="back" href="#\/go\/inbox\/shop-ops">/);
  assert.match(R("#/go/inbox/client-portal"), /Review inbox is empty for this project/);
});

test("project page and ops: header, lanes, accounts allowed here, sub-tabs", () => {
  const p = R("#/p/shop-ops/board");
  assert.match(p, /PERSONAL ACCOUNTS ALLOWED/);
  assert.match(p, /<span class="n">4<\/span>Lanes on this project/);
  assert.match(p, /Demo :3700 down/);
  assert.match(p, /W4 · Claude · office<\/b><small class="no">🔒 Office account: never runs personal projects/);
  assert.equal(kpi(p, "LANES"), "3");
  const ops = R("#/p/shop-ops/timeline");
  assert.match(ops, /<a class="on" href="#\/p\/shop-ops\/timeline">TIMELINE<\/a>/);
  assert.match(ops, /<span class="k" style="--c:var\(--mg\)">REVIEW<\/span>/);
  assert.match(R("#/go/ops/shop-ops"), /<span class="cd" style="--c:var\(--rd\)">POLICY<\/span>/);
  const repo = R("#/p/shop-ops/repo");
  assert.match(repo, /<small>HEAD<\/small><b class="">a1b2c3d<\/b>/);
  assert.match(repo, /ext:pc-ssh<\/span><span>109 since/);
  assert.match(repo, /waiting: 110/);
  const all = R("#/go/ops");
  assert.ok(all.includes("Alerts") && all.includes("Timeline") && all.includes("Repo health"), "ALL scope stacks the three sections");
});

test("server text is escaped everywhere", () => {
  const evil = "<img src=x onerror=alert(1)>";
  const sp = structuredClone(projects["shop-ops"]);
  sp.board.upNext[0].title = evil; sp.inbox[0].summary = evil; sp.inbox[0].risks = [evil]; sp.timeline[0].text = evil; sp.workers[3].reason = evil;
  const oo = structuredClone(o);
  oo.projects[0].headline = evil; oo.accounts[2].capacity.why = evil; oo.alerts[0].text = evil; oo.needsYou[0].title = evil;
  const c = { o: oo, projects: { ...projects, "shop-ops": sp } };
  for (const h of ["#/", "#/go/workers", "#/go/workers/shop-ops", "#/go/board", "#/go/inbox", "#/go/ops", "#/p/shop-ops/t/107-supplier-import/review"]) {
    const out = R(h, c);
    assert.doesNotMatch(out, /<img/, h);
  }
  assert.match(R("#/go/board", c), /&lt;img src=x onerror=alert\(1\)&gt;/);
});
