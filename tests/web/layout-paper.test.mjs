// Paper layout (docs/v2/mockups/alt-b/deck.html): every route renders through render(route, ctx) from fixtures.
import { fixture, s, count, WEB } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { render } from "../../web/layouts/paper.js";
import { parseRoute } from "../../web/store.js";

const o = fixture("overview.json");
const projects = { "shop-ops": fixture("project.json"), "client-portal": fixture("project.client-portal.json") };
const ctx = (extra = {}) => ({ o, projects, details: {}, eligible: {}, now: o.time, conn: "live", pref: { layout: "paper", mode: "auto" }, token: "", fixtures: false, ...extra });
const at = (hash, extra) => s(render(parseRoute(hash), ctx(extra)));
const text = (h) => h.replace(/<[^>]+>/g, "").replace(/[ \n\t]+/g, " ");

test("chrome: theme pill cycles mode, layout cycle + settings link, pill nav with five tabs and an Inbox badge", () => {
  const h = at("#/");
  assert.match(h, /<button class="theme" data-mode-cycle[^>]*>Theme: auto<\/button>/);
  assert.match(h, /data-layout-cycle/);
  assert.match(h, /href="#\/settings"/);
  for (const [href, label] of [["#/", "Today"], ["#/go/projects", "Projects"], ["#/accounts", "Accounts"], ["#/go/board", "Board"], ["#/go/inbox", "Inbox"]]) assert.match(h, new RegExp(`href="${href}"[^>]*>.*?${label}`));
  assert.match(h, /class="on" href="#\/" aria-current="page"/);
  // 1 review still waiting (105 is already merging) + 2 alerts
  assert.match(h, /<span class="bd" aria-label="3 waiting">3<\/span>/);
  assert.match(at("#/go/board"), /class="on" href="#\/go\/board"/);
  assert.match(at("#/p/shop-ops/repo"), /class="on" href="#\/go\/projects"/);
});

test("today: greeting by local hour and a lede built from real counts", () => {
  const h = at("#/");
  assert.match(h, /<h1 class="hd">Good evening\.<\/h1>/); // fixture clock is 21:42 IST
  assert.match(at("#/", { now: o.time - 12 * 3600 }), /Good morning\./);
  const lede = text(h.match(/<p class="lede">([\s\S]*?)<\/p>/)[1]);
  assert.match(lede, /Five tasks are moving across two projects\./);
  assert.match(h, /<mark>One finished piece of work<\/mark> is waiting for you\./);
  assert.match(lede, /Claude · worker is resting until 21:51, and Codex · a has a lane free with quota to spare\./);
  assert.match(h, /class="live"/);
});

test("today: needs-you cards carry coloured borders and kickers; questions answer inline", () => {
  const h = at("#/");
  assert.match(h, /Needs you <small>three things<\/small>/);
  assert.match(h, /class="card tap need p-lilac" href="#\/go\/inbox"><div class="kicker">Ready for your review · pilot<\/div>/);
  assert.match(h, /W2 · Codex · b finished it 12m ago\. 3 of 3 done-when checks pass\./);
  assert.match(h, /class="card need p-peach task-root" data-p="shop-ops" data-t="111-tax-number-rows" data-rev="5"><div class="kicker">A question for you/);
  assert.match(h, /<button class="on" data-act="answer" data-note="inactive">inactive · default<\/button>/);
  assert.match(h, /<div class="kicker">Worth a look<\/div>/);
  // without the server list, needs-you is derived from projects' inbox, blocked tasks and alerts
  const d = s(render(parseRoute("#/"), ctx({ o: { ...o, needsYou: undefined } })));
  assert.match(d, /Needs you <small>four things<\/small>/);
});

test("today: one ring per account, office greyed with the planner's reason, alerts and the day's timeline", () => {
  const h = at("#/");
  const rings = h.match(/<div class="rings">([\s\S]*?)<\/div>\s*<p/)[1];
  assert.equal(count(rings, '<svg class="ring"'), o.accounts.length);
  assert.match(rings, /class="off" title="Claude · office/);
  assert.match(rings, /aria-label="Claude · worker: 100% of 5-hour quota used"/);
  assert.match(h, /Claude · office is greyed out: no eligible task: 3 queued tasks are personal projects \(account policy\)\./);
  assert.match(h, /Worth a look <small>two alerts<\/small>/);
  assert.match(h, /◼ Looks stuck · 21:40/);
  assert.match(h, /⛔ Policy · 21:32/);
  assert.match(h, /href="#\/go\/ops">See all alerts ›/);
  assert.match(h, /<h2>The day so far<\/h2><div class="feed">/);
  assert.match(h, /<div class="grp">This evening<\/div>/);
  assert.equal(count(h, 'class="pev '), 7); // shop-ops 6 + client-portal 1, deduped
});

test("projects: one card per project, disabled one is not a link; project page has lanes, next, inbox, alerts, repo, accounts", () => {
  const h = at("#/go/projects");
  assert.match(h, /Two projects, each with its own board/);
  assert.match(h, /class="card tap" href="#\/p\/shop-ops\/board"/);
  assert.match(h, /class="card dis" aria-disabled="true"/);
  assert.doesNotMatch(h, /href="#\/p\/home-lab/);
  assert.match(h, /Demo :3700 is down/);
  const p = at("#/p/shop-ops/board");
  for (const h2 of ["Working on it now", "Up next", "Waiting for you", "Alerts", "Repo health", "Accounts for this project"]) assert.ok(p.includes(`<h2>${h2}</h2>`), h2);
  assert.match(p, /href="#\/go\/projects">‹ All projects/);
  assert.equal(count(p, 'class="ln '), 4);
  assert.match(p, /ext:pc-ssh → #109 since 21:10/);
  assert.match(p, /W4 · Claude · office<\/b> 🔒<span class="muted d">Office account: never runs personal projects \(account policy\)/);
  assert.match(p, /W3 · Claude · worker<\/b><span class="muted d">Allowed\. Resting until the 21:51 reset\./);
  assert.match(at("#/p/nope/board"), /Loading this project/);
});

test("accounts: big ring per account, reset time, lanes; office greyed with its reason", () => {
  const h = at("#/accounts");
  assert.equal(count(h, 'width="120"'), o.accounts.length);
  assert.match(h, /Five accounts\. Four take work in parallel lanes; Claude · main \(W0\) is shown for usage only\./);
  assert.match(h, /Resets in <b>9m<\/b> <span class="muted">at 21:51<\/span>/);
  assert.equal(count(h, "acc-off"), 1);
  assert.match(h, /<b>Why it is greyed out\.<\/b> No eligible task: 3 queued tasks are personal projects \(account policy\)/);
  assert.match(h, /class="ln p-butter spare"><span class="n">W1·3<\/span><span class="t">Free lane, quota to spare<small>FREE · 36% to spend/);
  assert.match(h, /Orchestrator · usage only, never dispatched/);
  assert.match(h, /At limit/);
  assert.match(at("#/go/workers"), /<h1 class="hd">Accounts<\/h1>/);
});

test("board: four column pages with a segmented switcher and project filter chips", () => {
  const h = at("#/go/board");
  assert.match(h, /class="board" data-col="1"/);
  for (const [i, l, n] of [[0, "Ready", 3], [1, "Running", 5], [2, "Review", 2], [3, "Blocked", 1]]) {
    assert.match(h, new RegExp(`data-pv="col:${i}"><b>${n}</b><span>${l}</span>`));
    assert.match(h, new RegExp(`aria-label="${l}"><h4>${l}<span>${n}</span>`));
  }
  assert.match(h, /<a class="on" href="#\/go\/board">All<\/a>/);
  assert.match(h, /after #107 · in review/);
  assert.match(h, /Rows matched: 412 of 1,050/);
  const f = at("#/go/board/client-portal");
  assert.match(f, /class="on" href="#\/go\/board\/client-portal"/);
  assert.match(f, /data-pv="col:1"><b>2<\/b>/);
});

test("inbox: Paper review cards with approve / changes / hold-to-reject inside .task-root, plus every alert", () => {
  const h = at("#/go/inbox");
  assert.match(h, /class="inbox" data-seg="review"/);
  assert.match(h, /To review · 1<\/button><button data-pv="seg:alerts">Alerts · 2/);
  const card = h.match(/<div class="card rv task-root" data-p="shop-ops" data-t="107-supplier-import" data-rev="21">[\s\S]*?Hold to reject<\/button><\/div>/)[0];
  assert.match(card, /data-act="approve"/);
  assert.match(card, /data-act="changes"/);
  assert.match(card, /data-hold="reject"/);
  assert.match(card, /<textarea class="pnote"/);
  assert.match(card, /data-act="answer" data-decision="0" data-option="inactive"/);
  assert.match(card, /Worth knowing: 3 suppliers share one phone number/);
  assert.ok(h.indexOf("107-supplier-import") < h.indexOf("105-payment-reminders"), "waiting reviews before merging ones");
  assert.match(h, /Approved · merging, gates 2 of 3\./);
  assert.equal(count(h.split('class="s-alerts"')[1], 'class="card need'), 2);
  assert.match(at("#/go/ops"), /class="inbox" data-seg="alerts"/);
});

test("server text is escaped; task routes fall back to the shared views; offline is announced", () => {
  const evil = { ...o, alerts: [{ ...o.alerts[0], text: '<img src=x onerror="alert(1)">' }], projects: o.projects.map((p) => ({ ...p, headline: "<b>x</b>" })) };
  const h = s(render(parseRoute("#/"), ctx({ o: evil }))) + s(render(parseRoute("#/go/projects"), ctx({ o: evil })));
  assert.doesNotMatch(h, /<img src=x/);
  assert.match(h, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(h, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.equal(render(parseRoute("#/p/shop-ops/t/107-supplier-import/review"), ctx()), null);
  assert.equal(render(parseRoute("#/p/shop-ops/t/106-match-receipts"), ctx()), null);
  assert.equal(render(parseRoute("#/"), { ...ctx(), o: null }), null);
  assert.match(at("#/", { conn: "offline" }), /Offline · last update 21:42/);
});

test("paper.css: everything nested under the Paper layout, pastel pairs for light and dark, small enough", () => {
  const css = fs.readFileSync(path.join(WEB, "layouts/paper.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  // top-level blocks: the Paper block (rules nested with &), its dark variant, and keyframes only
  let depth = 0, top = [], cur = "";
  for (const ch of css) {
    if (ch === "{" && depth++ === 0) { top.push(cur.trim()); cur = ""; }
    else if (ch === "}") depth--;
    else if (depth === 0) cur += ch;
  }
  for (const sel of top) assert.ok(sel.startsWith(':root[data-layout="paper"]') || sel.startsWith("@keyframes pp-"), sel);
  for (const m of css.matchAll(/\n\s*([^{}\n]+)\{/g)) if (!m[1].trim().startsWith(":root") && !m[1].trim().startsWith("@")) for (const sel of m[1].split(/,(?![^(]*\))/)) assert.match(sel.trim(), /^&/, sel);
  for (const k of ["sage", "sky", "lilac", "peach", "rose", "butter", "teal", "indigo", "grey"]) assert.equal(count(css, `--${k}-i:#`), 2, k);
  assert.doesNotMatch(css, /url\(|@import/, "no remote fonts or assets");
  const js = fs.readFileSync(path.join(WEB, "layouts/paper.js"));
  assert.ok(js.length + Buffer.byteLength(css) < 48 * 1024, `paper.js + paper.css = ${js.length + Buffer.byteLength(css)} bytes`);
});
