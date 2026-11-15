// Project page with tabs WORKERS · BOARD · REVIEW · ALERTS · TIMELINE · REPO (design §13.2 screen 2).
import { html, hhmm, dur, tnum } from "../fmt.js";
import { lcd, label, accountBlock, taskKey, eventRow, empty, scopeBadge } from "./common.js";
import { TABS } from "../store.js";

const TAB_LABEL = { workers: "WORKERS", board: "BOARD", review: "REVIEW", alerts: "ALERTS", timeline: "LOG", repo: "REPO" };
const COLUMNS = [["running", "RUNNING"], ["review", "REVIEW"], ["needsYou", "NEEDS YOU"], ["upNext", "UP NEXT"], ["backlog", "BACKLOG"], ["doneToday", "DONE TODAY"]];

export function projectView(vm, overview, tab, now, conn) {
  const p = vm.project;
  const b = vm.board || {};
  const n = (k) => (b[k] || []).length;
  const pid = Object.fromEntries((overview?.projects || []).map((x) => [x.id, x]));
  const head = lcd({
    left: html`<a href="#/">◀ ALL</a>`, mid: `${p.scope === "office" ? "OFFICE" : "PERSONAL"} · ${(p.short || p.title).toUpperCase()}`,
    right: html`<a href="#" data-swipe="1" aria-label="next project">▶</a>`,
    big: (p.short || p.title).toUpperCase(), bigClass: "proj", conn, lastUpdate: vm.time,
    stats: [{ b: n("running"), t: "RUN" }, { b: n("upNext"), t: "NEXT" }, { b: n("review"), t: "REVIEW", hot: n("review") > 0 },
      { b: n("needsYou"), t: "BLOCKED", hot: n("needsYou") > 0 }, { b: n("doneToday"), t: "DONE TODAY" }],
  });
  const seg = html`<nav class="seg" role="tablist">${TABS.map((t) => html`<a role="tab" aria-selected="${String(t === tab)}" class="${t === tab ? "on" : ""}" href="#/p/${encodeURIComponent(p.id)}/${t}">${TAB_LABEL[t]}${
    t === "review" && (vm.inbox || []).length ? ` ${vm.inbox.length}` : t === "alerts" && (vm.alerts || []).length ? ` ${vm.alerts.length}` : ""}</a>`)}</nav>`;
  const body = { workers: workersTab, board: boardTab, review: reviewTab, alerts: alertsTab, timeline: timelineTab, repo: repoTab }[tab] || boardTab;
  return html`${head}${seg}<div class="tabbody">${body(vm, now, pid, overview)}</div>`;
}

/** B1's WorkerVM has no usage; join it with the Overview's AccountVM so every worker shows its quota bar. */
export function workersWithUsage(vm, overview) {
  const acc = Object.fromEntries((overview?.accounts || []).map((a) => [a.id, a]));
  return (vm.workers || []).map((w) => {
    const a = acc[w.account] || {};
    return { ...a, ...Object.fromEntries(Object.entries(w).filter(([, v]) => v != null)), label: w.label || a.label, lanes: w.lanes?.length ? w.lanes : a.lanes || [] };
  });
}

function workersTab(vm, now, pid, overview) {
  const ws = workersWithUsage(vm, overview);
  if (!ws.length) return empty("No worker is busy on, or eligible for, this project.");
  const ok = ws.filter((w) => w.allowed !== false), off = ws.filter((w) => w.allowed === false);
  return html`${label("ELIGIBLE · PARALLEL LANES", ok.length)}
    ${ok.map((w) => accountBlock(w, now, pid, { worker: w.worker }))}
    ${off.length ? html`${label("NOT ELIGIBLE", off.length)}${off.map((w) => accountBlock(w, now, pid, { worker: w.worker, allowed: false, reason: w.reason }))}` : ""}`;
}

function boardTab(vm, now) {
  const b = vm.board || {};
  const pid = vm.project.id;
  return html`${COLUMNS.map(([k, title]) => {
    const items = b[k] || [];
    if (!items.length) return "";
    if (k === "doneToday") {
      return html`<div class="col">${title} · ${items.length}</div><div class="done-row">${items.map((t) => html`<a href="#/p/${encodeURIComponent(pid)}/t/${encodeURIComponent(t.id)}">✓ ${tnum(t)} ${t.title}</a>`)}</div>`;
    }
    return html`<div class="col">${title} · ${items.length}</div><div class="list">${items.map((t) => taskKey(t, pid, now))}</div>`;
  })}${Object.values(b).every((c) => !(c || []).length) ? empty("The board is empty.") : ""}`;
}

/** Review inbox: one key per ReviewVM, tap opens the review sheet. */
function reviewTab(vm) {
  const inbox = vm.inbox || [];
  if (!inbox.length) return empty("Review inbox is empty.");
  const pid = vm.project.id;
  return html`<div class="list">${inbox.map((r) => {
    const met = (r.doneWhen || []).filter((d) => d.met).length;
    const tests = (r.tests || []).reduce((a, t) => ({ p: a.p + (t.passed || 0), f: a.f + (t.failed || 0) }), { p: 0, f: 0 });
    const bits = [r.pilot ? "PILOT" : null, r.task.state === "merging" ? `merging${r.merging ? ` · gates ${r.merging.gatesDone}/${r.merging.gatesTotal}` : ""}` : `waiting ${dur(r.waitingSec)}`,
      `${met}/${(r.doneWhen || []).length} done-when ✓`, `tests ${tests.p}${tests.f ? ` · ${tests.f} failed` : ""}`,
      r.git?.uncommitted ? `⚠ ${r.git.uncommitted} uncommitted` : null, r.decisionsNeeded?.length ? `${r.decisionsNeeded.length} decision` : null].filter(Boolean);
    return html`<a class="key review${r.git?.uncommitted ? " amber" : ""}" href="#/p/${encodeURIComponent(pid)}/t/${encodeURIComponent(r.task.id)}/review">
      <span class="ico">${r.task.state === "merging" ? "⇄" : "▣"}</span><span class="num">${tnum(r.task)}</span>
      <span class="t"><b>${r.task.title}</b><small>${bits.join(" · ")}</small></span></a>`;
  })}</div>`;
}

function alertsTab(vm) {
  const a = vm.alerts || [];
  return a.length ? html`<div class="list">${a.map((e) => eventRow(e, { key: true }))}</div>` : empty("No alerts for this project.");
}

function timelineTab(vm) {
  const t = [...(vm.timeline || [])].sort((a, b) => b.ts - a.ts);
  return t.length ? html`<div class="evs">${t.map((e) => eventRow(e))}</div>` : empty("Nothing has happened yet.");
}

function repoTab(vm, now) {
  const p = vm.project;
  const repo = p.repo || {};
  const groups = {};
  for (const h of p.health || []) (groups[h.group || ""] ||= []).push(h);
  const m = vm.merge || {};
  return html`${label("REPO")}
    <div class="panel-box"><div class="mono">${repo.commit || "—"}</div>
      ${(repo.changes || []).length ? repo.changes.map((c) => html`<div class="kv"><span>${c.area}</span><span class="hot">${c.files} files changed</span></div>`) : html`<div class="muted">working tree clean</div>`}</div>
    ${label("HEALTH")}
    <div class="panel-box">${Object.values(groups).map((hs) => html`<div class="health">${hs.map((h) => { const up = h.ok ?? h.up; return html`<span class="${up ? "up" : up === false ? "down" : "muted"}">${up ? "●" : up === false ? "✗" : "?"} ${h.label}${up == null ? " (not checked)" : ""}</span>`; })}</div>`)}${(p.health || []).length ? "" : html`<span class="muted">no checks configured</span>`}</div>
    ${label("DOMAINS & MERGE")}
    <div class="panel-box">${(vm.domains || []).map((d) => html`<div class="kv"><span class="mono">${d.name}</span><span>${d.holder ? `${tnum({ id: d.holder })} since ${hhmm(d.since)}` : d.note || "free"}${
      (d.waiting || []).length ? ` · ${d.waiting.map((w) => tnum({ id: w })).join(", ")} waiting` : ""}${d.reserved ? " · reserved" : ""}</span></div>`)}
      <div class="kv"><span class="mono">merge</span><span>${m.active ? `${tnum({ id: m.active.task })} (gates ${m.active.gatesDone}/${m.active.gatesTotal})` : "idle"} · queue: ${(m.queue || []).length ? m.queue.map((q) => tnum({ id: q.task || q })).join(", ") : "—"}</span></div></div>
    ${label("SCOPE")}<div class="panel-box">${scopeBadge(p.scope)} ${p.scope === "office" ? (p.allowPersonalAccounts ? "personal accounts allowed" : "office accounts only") : "office accounts never run this project"}</div>`;
}
