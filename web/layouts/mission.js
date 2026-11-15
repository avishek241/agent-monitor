// Mission Control layout (docs/v2/mockups/alt-a/deck.html): top bar, LIVE ticker, scope strip, OVR·WORKERS·BOARD·REVIEW·OPS.
// Pure: view models in, HTML out. Task detail + reassign/answer sheets fall back to shared views (null). No sparklines:
// the VMs carry no usage history, so quota is a 20-cell bar.
import { html, hhmm, hhmmss, dur, pct, tnum, stateWord as sw } from "../fmt.js";
import { freeLane } from "../views/common.js";

const E = encodeURIComponent;
const led = (c, b) => html`<span class="led ${c}${b ? " blink" : ""}" aria-hidden="true"></span>`;
const tag = (s) => html`<span class="tag ${s === "office" ? "office" : "personal"}">${s === "office" ? "OFFC" : "PERS"}</span>`;
const h = (n, t) => html`<div class="h">${n != null ? html`<span class="n">${n}</span>` : ""}${t}</div>`;
const emp = (t) => html`<div class="emp">${t}</div>`;
const num = (id) => html`<span class="id">${tnum({ id })}</span>`;
const SC = { running: "gr", starting: "gr", limit_paused: "am", hung: "rd", failed: "rd", ready: "cy", queued: "cy", review: "mg", merging: "mg", changes_requested: "am", blocked: "or" };
const SEV = { alert: "rd", warn: "am", action: "mg", info: "cy" };
const KC = { card: "gr", attempt: "cy", review: "mg", limit: "am", merge: "gr", policy: "rd", lock: "vi", quota: "vi" };
const quiet = (t) => t.state === "running" && t.lastActiveSec > 600;
const pc = (a) => (a.state === "cooling" ? "rd" : a.pct5 >= 50 ? "am" : "gr");
const tH = (p, t, s) => `#/p/${E(p)}/t/${E(t)}${s ? `/${s}` : ""}`;
const PG = { wrk: "workers", brd: "board", rev: "inbox", ops: "ops" };
const href = (tab, id) => (tab === "ovr" ? (id ? `#/p/${E(id)}/board` : "#/") : `#/go/${PG[tab]}${id ? `/${E(id)}` : ""}`);
const sum = (l, f) => l.reduce((n, x) => n + (f(x) || 0), 0);
const run = (c) => (c.running || 0) + (c.limit_paused || 0) + (c.hung || 0);
const left = (ts, now) => (ts ? dur(ts - now) : "—");
const qbar = (a) => {
  const v = a.state === "cooling" ? 100 : a.pct5 ?? 0;
  return html`<div class="qbar" style="--c:var(--${pc(a)})" role="img" aria-label="${v}% of 5-hour quota used">${Array.from({ length: 20 }, (_, i) => html`<i${i < Math.round(v / 5) ? html` class="on"` : ""}></i>`)}</div>`;
};
const kgrid = (cells) => html`<div class="kpis">${cells.map(([v, l, c]) => html`<div class="kpi" style="--c:var(--${c})"><div class="v">${v}</div><div class="l">${led(c)}${l}</div></div>`)}</div>`;

/** Route → [tab, ops sub-tab]; null → shared views. */
function screen(r) {
  if (r.view === "overview") return ["ovr"];
  if (r.view === "accounts") return ["wrk"];
  if (r.view === "page") return [{ projects: "ovr", workers: "wrk", board: "brd", inbox: "rev", ops: "ops" }[r.page]];
  if (r.view === "project") return { workers: ["wrk"], board: ["ovr"], review: ["rev"] }[r.tab] || ["ops", r.tab];
  return r.view === "task" && r.sheet === "review" ? ["rev"] : null;
}

export function render(route, ctx) {
  const scr = screen(route);
  if (!scr || !ctx?.o) return null;
  const { o, projects = {} } = ctx, scope = route.project || null;
  if (scope && !projects[scope]) return null;
  const P = o.projects || [];
  const vms = scope ? [projects[scope]] : P.filter((p) => p.enabled !== false).map((p) => projects[p.id]).filter(Boolean);
  const m = { ...ctx, P, scope, vms, wmap: {}, accts: o.accounts || [] };
  for (const vm of Object.values(projects)) for (const w of vm.workers || []) m.wmap[w.account] = w.worker;
  m.short = (id) => P.find((p) => p.id === id)?.short || id;
  m.alerts = scope ? projects[scope].alerts || [] : o.alerts || [];
  m.inbox = vms.flatMap((vm) => (vm.inbox || []).map((r) => ({ ...r, p: vm.project?.id })));
  m.tasks = vms.flatMap((vm) => Object.entries(vm.board || {}).flatMap(([col, ts]) => (ts || []).map((t) => ({ ...t, col, p: vm.project?.id }))));
  m.plc = (p) => {
    const sev = (o.alerts || []).filter((a) => a.project === p.id).map((a) => a.severity);
    return p.enabled === false ? "off" : sev.includes("alert") ? "rd" : sev.length || (projects[p.id]?.project?.health || []).some((x) => !x.ok) ? "am" : "gr";
  };
  const [tab, sub] = scr;
  let body;
  if (route.view === "task") {
    const r = m.inbox.find((x) => x.task?.id === route.task);
    if (!r) return null;
    body = html`<a class="back" href="${href("rev", scope)}">◀ REVIEW INBOX</a>${review(r, m, true)}`;
  } else body = { ovr: scope ? projectPage : overview, wrk: workers, brd: board, rev: reviews, ops }[tab](m, sub);
  return html`<div class="mc">${chrome(m, tab)}<div class="mn">${body}</div>${tabs(m, tab)}</div>`;
}

function chrome(m, tab) {
  const { conn, o } = m, seen = new Set();
  const link = conn === "offline" ? html`<span class="link off">OFFLINE · ${hhmm(o.time)}</span>` : html`<span class="link${conn === "fixtures" ? " fx" : ""}">${conn === "fixtures" ? "FIXTURES" : "● SSE LIVE"}</span>`;
  const tick = [...(o.alerts || []), ...m.vms.flatMap((vm) => vm.timeline || [])].filter((e) => !seen.has(e.id) && seen.add(e.id)).sort((a, b) => b.ts - a.ts).slice(0, 8);
  const items = tick.map((e) => html`<span><b>${hhmm(e.ts)}</b>${e.text}</span>`);
  const sc = m.P.map((p) => {
    const al = p.alerts || 0, off = p.enabled === false;
    const inner = html`<span class="nm">${led(m.plc(p))}${p.short || p.title}</span><span class="sub">${off ? "disabled" : `${run(p.counts || {})} run · ${al} alert${al === 1 ? "" : "s"}`}</span>`;
    return off ? html`<span class="dis" title="${p.title} is disabled">${inner}</span>` : html`<a class="${m.scope === p.id ? "on" : ""}" href="${href(tab, p.id)}" title="${p.title}">${inner}</a>`;
  });
  return html`<header class="hdr"><div class="top">${led(conn === "offline" ? "rd" : "gr", conn === "live")}<span class="brand">Agent<b>/</b>Deck</span>${link}<span class="clk">${hhmm(m.now)}</span>
    <button class="ib" data-layout-cycle aria-label="Next layout" title="Next layout">▦</button><button class="ib" data-mode-cycle aria-label="Light or dark" title="Light / dark / auto">◐</button><a class="ib" href="#/settings" aria-label="Settings" title="Settings">⚙</a></div>
    <div class="ticker" aria-label="Live events"><div class="tk-in">${items.length ? html`${items}<span aria-hidden="true">${items}</span>` : "No events yet"}</div></div>
    <nav class="scope" aria-label="Project scope" style="grid-template-columns:repeat(${sc.length + 1},minmax(96px,1fr))"><a class="${m.scope ? "" : "on"}" href="${href(tab)}"><span class="nm">ALL</span><span class="sub">${m.P.length} projects</span></a>${sc}</nav></header>`;
}

const inboxN = (m) => (m.scope ? m.inbox.length : m.o.inbox?.review ?? m.inbox.length);
function tabs(m, tab) {
  const T = [["ovr", m.scope ? "PROJECT" : "OVR", "◎"], ["wrk", "WORKERS", "▤"], ["brd", "BOARD", "▦"], ["rev", "REVIEW", "✓", inboxN(m), "mg"], ["ops", "OPS", "!", m.alerts.length, ""]];
  return html`<nav class="tabs" aria-label="Views">${T.map(([id, l, g, n, c]) => html`<a class="${tab === id ? "on" : ""}" href="${href(id, m.scope)}"${tab === id ? html` aria-current="page"` : ""}><span class="g">${g}</span>${l}${n ? html`<span class="bd ${c}" aria-label="${n} waiting">${n}</span>` : ""}</a>`)}</nav>`;
}

function kpis(m) {
  const cs = (m.scope ? m.P.filter((p) => p.id === m.scope) : m.P).map((p) => p.counts || {}), k = (f) => sum(cs, f), mc = m.o.machine || {};
  const lanes = m.scope ? (m.projects[m.scope].lanes || []).filter((l) => l.state === "running").length : html`${mc.running ?? 0}<small>/${mc.lanesTotal ?? 0}</small>`;
  return kgrid([[k(run), "RUNNING", "gr"], [k((c) => c.queued), "READY", "cy"], [k((c) => c.review), "REVIEW", "mg"], [k((c) => c.blocked), "BLOCKED", "or"],
    [m.alerts.length, "ALERTS", "rd"], [lanes, "LANES", "cy"], [k((c) => c.doneToday), "DONE TODAY", "gr"], [inboxN(m), "INBOX", "mg"]]);
}

const NK = { review: ["mg", "REVIEW", "review"], question: ["or", "ANSWER", "answer"], alert: ["rd", "ALERT"], blocked: ["or", "INPUT"] };
/** NEEDS YOU: OverviewVM.needsYou when present, else derived from inboxes, blocked tasks and alert-severity events. */
function needs(m) {
  let it = m.o.needsYou || [...m.inbox.filter((r) => !r.merging).map((r) => ({ kind: "review", project: r.p, task: r.task.id, title: r.task.title, text: `${r.worker || ""} · ${(r.doneWhen || []).filter((d) => d.met).length}/${(r.doneWhen || []).length} done-when · waiting ${dur(r.waitingSec)}` })),
    ...m.tasks.filter((t) => t.col === "needsYou").map((t) => ({ kind: t.question ? "question" : "blocked", project: t.p, task: t.id, title: t.title, text: t.question?.text || t.stateReason })),
    ...m.alerts.filter((a) => a.severity === "alert").map((a) => ({ kind: "alert", project: a.project, task: a.task, title: a.text, text: a.type }))];
  if (m.scope) it = it.filter((x) => x.project === m.scope);
  return html`${h(it.length, "Needs you")}${it.length ? it.map((x) => {
    const [c, w, s] = NK[x.kind] || NK.blocked;
    const inner = html`${led(c, x.kind === "alert")}<div class="tt"><b>${x.task ? num(x.task) : ""}${x.title}</b><small>${m.short(x.project)} · ${x.text}</small></div><div class="r"><b>${w}</b></div>`;
    return x.task ? html`<a class="row" href="${tH(x.project, x.task, s)}">${inner}</a>` : html`<div class="row">${inner}</div>`;
  }) : emp("Nothing waits for you.")}`;
}

const wid = (a, m) => m.wmap[a.id] || a.lanes?.[0]?.worker || "";
function overview(m) {
  const grey = m.accts.filter((a) => a.dispatch === false);
  return html`${kpis(m)}${h(m.P.length, "Projects")}${m.P.map((p) => {
    const c = p.counts || {};
    const inner = html`${led(m.plc(p))}<div class="tt"><b>${p.icon || ""} ${p.title} ${tag(p.scope)}</b><small>${p.headline}</small></div>
      <div class="r cnts"><span>R<i>${run(c)}</i> Q<i>${c.queued || 0}</i></span><span>V<i>${c.review || 0}</i> B<i>${c.blocked || 0}</i></span></div>`;
    return p.enabled === false ? html`<div class="row dim" title="disabled">${inner}</div>` : html`<a class="row" href="${href("ovr", p.id)}">${inner}</a>`;
  })}
  ${h(m.accts.length, "Accounts · 5h quota")}${m.accts.map((a) => {
    const g = a.dispatch === false, cool = a.state === "cooling";
    return html`<a class="row acr${g ? " grey" : ""}" href="#/go/workers">${led(g ? "off" : pc(a), cool)}<div class="tt"><b>${wid(a, m)} ${a.scope === "office" ? tag("office") : ""}</b><small>${a.label}</small></div>
      ${qbar(a)}<div class="r"><b class="${cool ? "hot" : ""}">${cool ? "LIMIT" : pct(a.pct5)}</b>5h</div><div class="r"><b>${left(a.resets5, m.now)}</b>reset</div></a>`;
  })}${grey.map((a) => html`<div class="row">${led("off")}<div class="tt"><small>🔒 ${wid(a, m) || a.label} is greyed: ${a.capacity?.why || "usage only"}</small></div></div>`)}${needs(m)}`;
}

function lane(l, a, m) {
  const other = m.scope && l.project && l.project !== m.scope, pre = other ? `${m.short(l.project)} · ` : "";
  let cls = "", lt, lr = "—";
  if (l.state === "running") {
    const q = l.lastActiveSec > 600;
    cls = q ? "hung" : ""; lt = html`▶ ${pre}${num(l.task)}${l.title || ""}${l.stepCount ? ` · ${l.stepIndex}/${l.stepCount}` : ""}`;
    lr = q ? `QUIET ${dur(l.lastActiveSec)}` : l.etaMin != null ? `~${l.etaMin}m` : "—";
  } else if (l.state === "cooling") {
    const at = l.resumeAt || a.resets5;
    cls = "cooling"; lt = html`⏸ ${pre}${l.task ? num(l.task) : ""}${l.title || ""} · resumes ${hhmm(at)}`; lr = left(at, m.now);
  } else {
    const f = freeLane(a, m.now);
    cls = { free: "spare", pol: "locked", cool: "cooling" }[f.cls] || "idle"; lt = `${f.icon} ${f.text}`; lr = f.cls === "free" ? "⚠" : "—";
  }
  const inner = html`<span class="lid">${l.worker}·${l.lane}</span><span class="lt">${lt}</span><span class="lr">${lr}</span>`, c = `ln ${cls}${other ? " oos" : ""}`;
  return l.project && l.task ? html`<a class="${c}" href="${tH(l.project, l.task)}">${inner}</a>` : html`<div class="${c}">${inner}</div>`;
}

/** WORKERS account block. w = the project's WorkerVM in project scope (allowed/reason), else greyed when usage-only. */
function acct(a, m, w) {
  const grey = w ? !w.allowed : a.dispatch === false, c = a.capacity || {}, cool = a.state === "cooling";
  const role = a.role === "orchestrator" ? "ORCHESTRATOR" : a.dispatch === false ? "USAGE ONLY" : cool ? "AT LIMIT" : `${c.lanesUsed ?? 0}/${c.lanesMax ?? 0} LANES`;
  return html`<section class="ac${grey ? " grey" : ""}"${grey ? html` aria-disabled="true"` : ""}><div class="ah">${led(grey ? "off" : pc(a), cool)}<span class="wid">${w?.worker || wid(a, m)}</span><b>${grey ? "🔒 " : ""}${a.label}</b>${tag(a.scope)}<span class="role">${role}</span></div>
    <div class="ag"><div class="pct" style="color:var(--${pc(a)})">${cool ? "LIMIT" : pct(a.pct5)}<small>5H USED${cool ? " · 100%" : ""}</small></div><div class="sp">${c.spendable5 != null ? `${c.spendable5}% to spend` : ""}${c.committed5 ? ` · ${c.committed5}% committed` : ""}</div><div class="rs"><b>${left(a.resets5, m.now)}</b><small>RESETS ${hhmm(a.resets5)}</small></div></div>
    ${qbar(a)}<div class="q7">7D<span class="tr"><i style="width:${Math.min(100, a.pct7 ?? 0)}%"></i></span>${pct(a.pct7)}${a.readAgeSec != null ? ` · read ${dur(a.readAgeSec)} ago` : ""}${a.confidence && a.confidence !== "high" ? ` · ${a.confidence} confidence` : ""}</div>
    ${(a.lanes || []).length ? html`<div class="lns">${a.lanes.map((l) => lane(l, a, m))}</div>` : !grey && c.why ? html`<div class="elig">${c.why}</div>` : ""}
    ${grey ? html`<div class="why">🔒 <span><b>Greyed out.</b> ${(w ? w.reason : c.why) || "not allowed here"}</span></div>` : w ? html`<div class="elig ok">${m.short(m.scope)}: allowed</div>` : ""}</section>`;
}

function workers(m) {
  const mc = m.o.machine || {}, bl = m.o.backlog, ln = m.accts.flatMap((a) => (a.lanes || []).map((l) => [l, a]));
  const ws = m.scope ? m.projects[m.scope].workers || [] : null;
  return html`${kgrid([[html`${mc.running ?? 0}<small>/${mc.lanesTotal ?? 0}</small>`, "LANES BUSY", "gr"], [ln.filter(([l, a]) => l.state === "free" && freeLane(a, m.now).cls === "free").length, "SPARE", "am"],
    [ln.filter(([l]) => l.state === "cooling").length, "COOLING", "am"], [mc.maxParallelTotal ?? "—", "MACHINE CAP", "cy"]])}
    ${bl ? html`${h(null, "Backlog")}<div class="bl${bl.low ? " warn" : ""}">${bl.eligible} ready / target ${bl.target}${bl.hint ? ` · ${bl.hint}` : ""}</div>` : ""}
    ${m.accts.map((a) => acct(a, m, ws && (ws.find((w) => w.account === a.id) || { allowed: false, reason: "Not a worker for this project" })))}`;
}

function trow(t, m) {
  const wk = `${t.worker || ""}${t.worker && t.lane ? `·${t.lane}` : ""}`, q = quiet(t), r = m.inbox.find((x) => x.task?.id === t.id);
  const meta = t.state === "running" ? `${wk}${t.step ? ` · ${t.step.index}/${t.step.count} ${t.step.title}` : ""}${q ? ` · ${t.stateReason}` : ""}` : t.state === "limit_paused" ? `${wk} · ${t.stateReason}` : t.stateReason || sw(t.state);
  const right = q ? html`<b class="hot">${dur(t.lastActiveSec)}</b>quiet`
    : t.state === "running" ? html`<b>${t.step?.etaMin != null ? `~${t.step.etaMin}m` : "—"}</b>${t.size}·${t.priority}`
    : t.state === "limit_paused" ? html`<b class="am">${left(t.resumeAt, m.now)}</b>to reset`
    : t.state === "merging" ? html`<b>MERGE</b>${r?.merging ? `gates ${r.merging.gatesDone}/${r.merging.gatesTotal}` : ""}`
    : t.state === "review" && r ? html`<b>${dur(r.waitingSec)}</b>waiting` : html`<b>${t.size || ""}</b>${t.priority || ""}`;
  const x = [...(t.deps || []).map((d) => html`<span class="cp ${d.state === "done" ? "ok" : "wait"}">⟵ ${tnum(d)} ${sw(d.state)}</span>`), m.scope ? "" : html`<span class="cp">${m.short(t.p)}</span>`,
    ...(t.metrics || []).filter((x) => x.total).slice(0, 1).map((x) => html`<span class="cp">${x.label} ${x.done}/${x.total}</span>`)].filter(Boolean);
  return html`<a class="row" href="${tH(t.p, t.id)}">${led(q ? "rd" : SC[t.state] || "off", q)}<div class="tt"><b>${num(t.id)}${t.title}</b><small>${meta}</small></div><div class="r">${right}</div>${x.length ? html`<div class="x">${x}</div>` : ""}</a>`;
}

function projectPage(m) {
  const vm = m.projects[m.scope], p = vm.project || {}, lanes = vm.lanes || [];
  const down = (p.health || []).filter((x) => !x.ok), ts = m.tasks.filter((t) => t.col === "running" || t.col === "upNext");
  const A = (id) => m.accts.find((a) => a.id === id) || {};
  return html`<div class="ph"><div class="pt">${tag(p.scope)}<span>${p.allowPersonalAccounts === false ? "OFFICE ACCOUNTS ONLY" : "PERSONAL ACCOUNTS ALLOWED"}</span></div>
      <div class="pn">${p.icon || ""} ${p.title}</div><div class="pm">${led(m.plc(p))} ${p.repo?.commit || ""}${down.length ? ` · ${down.map((x) => x.label).join(", ")} down` : ""}</div></div>
    ${kpis(m)}${h(lanes.length, "Lanes on this project")}<div class="pad">${lanes.length ? html`<div class="lns">${lanes.map((l) => lane(l, m.accts.find((a) => (a.lanes || []).some((x) => x.worker === l.worker)) || {}, m))}</div>` : emp("No lanes running here.")}</div>
    ${h(ts.length, "Board · now and next")}${ts.map((t) => trow(t, m))}${needs(m)}
    ${h(null, "Accounts allowed here")}${(vm.workers || []).map((w) => html`<div class="row">${led(w.allowed ? "gr" : "off")}<div class="tt"><b>${w.worker} · ${w.label}</b><small class="${w.allowed ? "" : "no"}">${w.allowed ? `Allowed · ${w.state}` : `🔒 ${w.reason}`}</small></div><div class="r">${pct(A(w.account).pct5)}</div></div>`)}`;
}

function board(m) {
  const C = [["upNext", "READY", "cy"], ["running", "RUNNING", "gr"], ["review", "REVIEW", "mg"], ["needsYou", "BLOCKED", "or"], ["backlog", "BACKLOG · DRAFTS"]];
  const col = (c) => m.tasks.filter((t) => t.col === c);
  const edges = m.tasks.filter((t) => t.col !== "doneToday").flatMap((t) => (t.deps || []).filter((d) => d.state !== "done").map((d) => [d, t]));
  return html`<div class="cols">${C.slice(0, 4).map(([c, l, k]) => html`<div style="--c:var(--${k})"><span class="v">${col(c).length}</span><span class="l">${l}</span></div>`)}</div>
    ${h(edges.length, "Open dependencies")}<div class="deps">${edges.length ? edges.map(([d, t]) => html`<div class="edge">${led(SC[d.state] || "off")}<span>${num(d.id)}${sw(d.state).toUpperCase()}</span><span class="ar"></span><span>${num(t.id)}${t.title}</span></div>`) : emp("None.")}</div>
    ${C.map(([c, l, k]) => (k || col(c).length ? html`${h(col(c).length, l)}${col(c).length ? col(c).map((t) => trow(t, m)) : emp("Empty.")}` : ""))}`;
}

function review(r, m, sheet) {
  const t = r.task || {}, g = r.git || {}, dw = r.doneWhen || [];
  const foot = r.merging ? html`<div class="stamp">⇄ APPROVED · MERGING · gates ${r.merging.gatesDone}/${r.merging.gatesTotal}</div>`
    : sheet ? html`<textarea id="note" placeholder="What should change? (sent to the worker as the next attempt's brief note)" rows="3"></textarea>
      <div class="acts"><button class="act ch" data-act="changes">SEND CHANGES</button><button class="act go" data-act="approve">APPROVE ✓</button></div><div class="acts"><button class="act bad" data-hold="reject">HOLD TO REJECT</button></div>`
    : html`<div class="acts"><a class="act ch" href="${tH(r.p, t.id, "review")}">REQUEST CHANGES</a><button class="act go" data-act="approve">APPROVE ✓</button></div>`;
  return html`<div class="rv task-root" data-p="${r.p}" data-t="${t.id}" data-rev="${t.rev ?? ""}"><div class="hd">${led("mg")}${num(t.id)}<b>${t.title}</b>${r.pilot ? html`<span class="st">PILOT</span>` : ""}${tag(m.P.find((p) => p.id === r.p)?.scope)}</div>
    <div class="mt">${m.short(r.p)} · ${r.worker || t.worker || ""}${r.account ? ` · ${r.account}` : ""} · waiting ${dur(r.waitingSec)}${(r.unlocks || []).length ? ` · approving unlocks ${r.unlocks.map((u) => tnum({ id: u })).join(", ")}` : ""}</div>
    <p>${r.summary}</p>${r.outcome && r.outcome !== "done" ? html`<div class="risk">▲ OUTCOME · ${r.outcome}</div>` : ""}
    ${h(`${dw.filter((d) => d.met).length}/${dw.length}`, "Done when")}<div class="dw">${dw.map((d) => html`<span class="${d.met ? "y" : "n"}">[${d.met ? "✓" : "✗"}]</span><span>${d.item}</span><small>${d.evidence}</small>`)}</div>
    <div class="gitg"><div><b>${g.commits ?? 0}</b><small>COMMITS</small></div><div><b>${g.filesChanged ?? 0}</b><small>FILES</small></div><div><b class="${g.uncommitted ? "am" : ""}">${g.uncommitted ?? 0}</b><small>UNCOMMIT</small></div><div><b class="${g.conflictsWithMain ? "hot" : "gr"}">${g.conflictsWithMain ? "YES" : "0"}</b><small>CONFLICTS</small></div></div>
    ${(r.risks || []).map((x) => html`<div class="risk">▲ RISK · ${x}</div>`)}
    ${(r.decisionsNeeded || []).map((d, i) => html`<div class="q">◆ ${d.question}</div><div class="opts">${(d.options || []).map((o) => html`<button class="opt${o === d.default ? " on" : ""}" data-act="answer" data-decision="${i}" data-option="${o}">${o}${o === d.default ? " (default)" : ""}</button>`)}</div>`)}${foot}</div>`;
}

function reviews(m) {
  const it = m.inbox.slice().sort((a, b) => (b.waitingSec || 0) - (a.waitingSec || 0));
  return it.length ? html`${h(it.length, "Review inbox · oldest first")}${it.map((r) => review(r, m))}` : emp(`Review inbox is empty${m.scope ? " for this project" : ""}.`);
}

const CODE = { "attempt.suspect": "QUIET", "policy.denied": "POLICY", "limit.hit": "LIMIT" };
const alerts = (m) => (m.alerts.length ? m.alerts.map((a) => html`<div class="al"><span class="cd" style="--c:var(--${SEV[a.severity] || "cy"})">${CODE[a.type] || String(a.type || "").split(".").pop().toUpperCase()}</span>
  <div class="tt"><b>${a.text}</b><div class="mt">${hhmm(a.ts)} · ${m.short(a.project)}${a.worker ? ` · ${a.worker}` : ""} · severity ${a.severity}</div></div>
  ${a.project && a.task ? html`<div class="acts"><a class="act sm" href="${tH(a.project, a.task)}">OPEN ${tnum({ id: a.task })}</a></div>` : ""}</div>`) : emp("No alerts."));
function timeline(m) {
  const seen = new Set(), ev = m.vms.flatMap((vm) => vm.timeline || []).filter((e) => !seen.has(e.id) && seen.add(e.id)).sort((a, b) => b.ts - a.ts);
  return ev.length ? html`<div class="log">${ev.map((e) => { const k = String(e.type || "").split(".")[0]; return html`<div><span class="t">${hhmmss(e.ts)}</span><span class="k" style="--c:var(--${KC[k] || "cy"})">${k.toUpperCase()}</span><span>${e.text}</span></div>`; })}</div>` : emp("No events yet.");
}
const cell = (l, v, c = "") => html`<div><small>${l}</small><b class="${c}">${v}</b></div>`;
const repo = (m) => m.vms.map((vm) => {
  const p = vm.project || {}, [sha, msg, age] = String(p.repo?.commit || "").split(" · "), hl = p.health || [], down = hl.filter((x) => !x.ok);
  const ch = sum(p.repo?.changes || [], (c) => c.files), ds = vm.domains || [], ma = vm.merge?.active, wt = sum(ds, (d) => (d.waiting || []).length);
  return html`<div class="rp"><div class="hd">${led(m.plc(p))}<b>${p.title}</b>${tag(p.scope)}<span class="age">${age || ""}</span></div>
    <div class="rpg">${cell("HEAD", sha || "—")}${cell("CHANGES", ch ? `${ch} files` : "clean", ch ? "am" : "gr")}${cell("SERVICES", `${hl.length - down.length}/${hl.length}`, down.length ? "hot" : "gr")}
      ${cell("LOCKS", ds.filter((d) => d.holder).length)}${cell("WAITING", wt, wt ? "am" : "")}${cell("MERGE Q", (vm.merge?.queue || []).length)}</div>
    ${hl.length ? html`<div class="svc">${hl.map((x) => html`<span>${led(x.ok ? "gr" : "rd")}${x.label}${x.ok ? "" : " down"}</span>`)}</div>` : ""}
    <div class="dom"><span class="dk">merge</span><span>${ma ? `${tnum({ id: ma.task })} · gates ${ma.gatesDone}/${ma.gatesTotal}` : "idle"}</span>${(vm.merge?.queue || []).map((q) => html`<span class="w">queue: ${tnum({ id: q.task })} ${q.state || ""}</span>`)}
      ${ds.map((d) => html`<span class="dk">${d.name}</span><span>${d.holder ? `${tnum({ id: d.holder })}${d.since ? ` since ${hhmm(d.since)}` : ""}` : `free${d.note ? ` · ${d.note}` : ""}`}</span>${(d.waiting || []).length ? html`<span class="w">waiting: ${d.waiting.map((x) => tnum({ id: x })).join(", ")}</span>` : ""}`)}</div>
    ${msg ? html`<div class="nt">${msg}</div>` : ""}</div>`;
});

/** OPS: project scope has Alerts/Timeline/Repo sub-tabs (#/p/<id>/<sub>); ALL has no sub-tab route, so they stack. */
function ops(m, sub) {
  if (!m.scope) return html`${h(m.alerts.length, "Alerts")}${alerts(m)}${h(null, "Timeline")}${timeline(m)}${h(m.vms.length, "Repo health")}${repo(m)}`;
  const cur = ["timeline", "repo"].includes(sub) ? sub : "alerts";
  return html`<nav class="sub2">${["alerts", "timeline", "repo"].map((s) => html`<a class="${cur === s ? "on" : ""}" href="#/p/${E(m.scope)}/${s}">${s.toUpperCase()}</a>`)}</nav>${{ alerts, timeline, repo }[cur](m)}`;
}
