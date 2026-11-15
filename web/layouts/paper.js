import { workersSection, setWorkerColors, wcStyle } from "../workers.js";
import { mergingText } from "../views/project.js";
// Paper layout, ported from docs/v2/mockups/alt-b/deck.html: calm editorial phone deck (Today · Projects · Accounts ·
// Board · Inbox). render(route, ctx) is pure (view models in, HTML out); task detail and its sheets fall back to the
// shared views (null). A little browser-only glue at the bottom keeps in-page state the router does not carry.
import { html, raw, hhmm, dur, until, num, tnum } from "../fmt.js";
import { freeLane } from "../views/common.js";
import { workersWithUsage } from "../views/project.js";
import { findTask } from "../store.js";

const E = encodeURIComponent;
const tHref = (p, t, sheet) => `#/p/${E(p)}/t/${E(t)}${sheet ? `/${sheet}` : ""}`;
const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
const word = (n, cap = true) => { const w = WORDS[n] ?? String(n); return cap ? w : w.toLowerCase(); };
const nOf = (n, one, many, cap) => `${word(n, cap)} ${n === 1 ? one : many}`;
const UI = { col: 1, seg: "review", note: null }; // in-page state that outlives a re-render

const COLS = [["upNext", "sky", "Ready"], ["running", "sage", "Running"], ["review", "lilac", "Review"], ["needsYou", "peach", "Blocked"]];
const KIND = { review: ["lilac", "Ready for your review"], question: ["peach", "A question for you"], blocked: ["peach", "Waiting on you"], alert: ["rose", "Worth a look"] };
const ALERT = [[/suspect|hung|stale/, "rose", "Looks stuck", "◼"], [/polic/, "rose", "Policy", "⛔"], [/limit/, "sky", "Limit", "⏸"], [/spare|idle/, "butter", "Spare capacity", "◌"]];
const KC = [[/review|handoff/, "lilac"], [/merge|card|step/, "sage"], [/polic/, "rose"], [/limit|suspect|stale|spare/, "butter"], [/lock|domain|quota|usage/, "indigo"], [/plan/, "sky"], [/start|lease/, "teal"]];

// ---- accounts ----
const off = (a) => a.capacity?.whyKind === "policy" || (a.dispatch === false && a.role !== "orchestrator");
const tone = (a) => (off(a) ? "grey" : a.state === "cooling" ? "rose" : (a.pct5 ?? 0) >= 50 ? "butter" : "sage");
const wid = (a) => a.lanes?.[0]?.worker || a.worker || /\((W\d+)\)/.exec(a.label || "")?.[1] || a.label;
const nb = (s) => String(s ?? "").replace(/ /g, "\u00a0"); // keep "Claude · worker" on one line
const cap1 = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
const usagePct = (a) => (a.state === "cooling" ? 100 : a.pct5 ?? 0);

function ring(v, t, size, inner, sub, label) {
  const h = size / 2, r = h - 7, c = 2 * Math.PI * r, w = size > 80 ? 10 : 7;
  return html`<svg class="ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="${label}">
    <circle class="tr" cx="${h}" cy="${h}" r="${r}" fill="none" stroke-width="${w}"/>
    <circle class="arc" cx="${h}" cy="${h}" r="${r}" fill="none" stroke="var(--${t}-i)" stroke-width="${w}" stroke-linecap="round" stroke-dasharray="${((c * Math.min(100, v)) / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 ${h} ${h})"/>
    <text x="50%" y="${sub ? "47%" : "53%"}" text-anchor="middle" dominant-baseline="middle" font-size="${size > 80 ? 26 : String(inner).length > 3 ? 13 : 15}" font-weight="600">${inner}</text>
    ${sub ? html`<text class="sub" x="50%" y="66%" text-anchor="middle" font-size="12">${sub}</text>` : ""}</svg>`;
}

const pill = (cls, text, title) => html`<span class="pill sm p-${cls}"${title ? html` title="${title}"` : ""}>${text}</span>`;
const scopePill = (s) => pill(s === "office" ? "indigo" : "teal", s === "office" ? "Office" : "Personal");
function projPill(ctx, id) {
  const p = (ctx.o.projects || []).find((x) => x.id === id);
  return p ? pill("grey", `${p.icon || ""} ${p.short || p.title}`, p.title) : id ? pill("grey", id) : "";
}

function laneEl(a, l, ctx) {
  const id = `${l.worker}·${l.lane}`, now = ctx.now;
  const t = (inner) => (l.project && l.task ? html`<a class="t" href="${tHref(l.project, l.task)}">${inner}</a>` : html`<span class="t">${inner}</span>`);
  const pt = (ctx.o.projects || []).find((p) => p.id === l.project);
  if (l.state === "running") {
    const quiet = l.lastActiveSec != null && l.lastActiveSec > 600;
    return html`<div class="ln p-${quiet ? "rose" : "sage"}"><span class="n">${id}</span>${t(html`#${tnum({ id: l.task })} ${l.title || ""}<small>${quiet ? `quiet ${dur(l.lastActiveSec)} · looks stuck` : `step ${l.stepIndex ?? "?"} of ${l.stepCount ?? "?"}${l.step ? `, ${String(l.step).toLowerCase()}` : ""}`}${pt ? ` · ${pt.short || pt.title}` : ""}</small>`)}<span class="r">${quiet ? "check" : l.etaMin != null ? `~${l.etaMin} min` : ""}</span></div>`;
  }
  if (l.state === "cooling") {
    return html`<div class="ln p-butter"><span class="n">${id}</span>${t(l.task ? html`#${tnum({ id: l.task })} ${l.title || ""}<small>resumes in the same session after the reset</small>` : html`Resting<small>paused until the reset</small>`)}<span class="r">${until(l.resumeAt || a.resets5, now)}</span></div>`;
  }
  const f = freeLane(a, now);
  if (f.cls === "free") return html`<div class="ln p-butter spare"><span class="n">${id}</span><span class="t">Free lane, quota to spare<small>${f.text}</small></span></div>`;
  if (f.cls === "cool") return html`<div class="ln p-butter"><span class="n">${id}</span><span class="t">Resting<small>${f.text}</small></span></div>`;
  return html`<div class="ln p-grey"><span class="n">${id}</span><span class="t">${f.cls === "pol" ? "🔒 Locked" : "Free"}<small>${f.text}</small></span></div>`;
}

// ---- tasks ----
function tcard(t, pid, ctx, showProj) {
  const s = t.step;
  let line = t.stateReason || "";
  if (t.state === "running" && s) line = `${t.worker ? `${t.worker}${t.lane ? `·${t.lane}` : ""} · ` : ""}step ${s.index} of ${s.count}, ${String(s.title).toLowerCase()}${s.etaMin != null ? ` · about ${s.etaMin} min` : ""}`;
  if (t.state === "merging" && t.resumeAt > ctx.now) line = mergingText(t, ctx.now);
  if (t.state === "limit_paused") line = `${t.worker || ""} · resumes by itself after the reset (${until(t.resumeAt, ctx.now)})`;
  const quiet = t.state === "running" && t.lastActiveSec > 600;
  const m = (t.metrics || []).find((x) => x.total);
  const bar = m ? html`<div class="pbar p-sage"><i style="width:${Math.min(100, Math.round((m.done / m.total) * 100))}%"></i></div><small>${m.label}: ${num(m.done)} of ${num(m.total)}</small>`
    : t.progress != null && t.state === "running" ? html`<div class="pbar p-sage"><i style="width:${Math.round(t.progress * 100)}%"></i></div>` : "";
  const deps = (t.deps || []).map((d) => html`<span class="dep p-${d.state === "done" ? "sage" : "butter"}">after #${tnum({ id: d.id })} · ${d.state === "done" ? "done ✓" : d.state === "review" ? "in review" : d.state.replace("_", " ")}</span>`);
  const st = quiet ? pill("rose", "Looks stuck") : t.state === "limit_paused" ? pill("butter", "Resting") : t.state === "merging" ? pill("sage", "Merging") : t.question ? pill("peach", "Question") : "";
  return html`<a class="tcard"${wcStyle(t.worker)} href="${tHref(pid, t.id)}"><b><span class="num">#${tnum(t)}</span>${t.title}</b><small>${line}</small>${quiet && t.warnings?.[0] ? html`<small class="warn">${t.warnings[0]}</small>` : ""}${bar}
    <span class="meta">${st}${showProj ? projPill(ctx, pid) : ""}${deps}</span></a>`;
}

// ---- data joins ----
const vmsOf = (ctx) => (ctx.o.projects || []).filter((p) => p.enabled !== false).map((p) => ctx.projects?.[p.id]).filter(Boolean);
function needsList(ctx) {
  const o = ctx.o;
  if (Array.isArray(o.needsYou)) return o.needsYou;
  const out = [];
  for (const vm of vmsOf(ctx)) {
    const pid = vm.project.id;
    for (const r of vm.inbox || []) if (r.task.state === "review") out.push({ kind: "review", project: pid, task: r.task.id, num: r.task.num, title: r.task.title, rev: r.task.rev });
    for (const t of vm.board?.needsYou || []) out.push({ kind: t.question ? "question" : "blocked", project: pid, task: t.id, num: t.num, title: t.title, text: t.stateReason, rev: t.rev, options: t.question?.options });
  }
  for (const e of o.alerts || []) out.push({ kind: "alert", project: e.project, task: e.task, title: e.text, text: e.type });
  return out;
}
const reviewsOf = (ctx) => vmsOf(ctx).flatMap((vm) => (vm.inbox || []).map((r) => ({ ...r, pid: vm.project.id }))).sort((a, b) => isMerging(a) - isMerging(b) || (b.waitingSec || 0) - (a.waitingSec || 0));
const isMerging = (r) => (r.task.state === "merging" || r.merging ? 1 : 0);
const pending = (ctx) => reviewsOf(ctx).filter((r) => !isMerging(r)).length;
const trunc = (h, max = 60) => (h && h.length > max ? html`<span title="${h}">${h.slice(0, max - 1)}…</span>` : h);

function needCard(n, ctx) {
  const vm = ctx.projects?.[n.project];
  const [c, k] = KIND[n.kind] || ["grey", n.kind];
  const r = n.kind === "review" ? (vm?.inbox || []).find((x) => x.task.id === n.task) : null;
  const t = vm && n.task ? findTask(vm, n.task) : null;
  const q = n.kind === "question" ? t?.question : null;
  let body = n.text || "";
  if (r) {
    const dw = r.doneWhen || [];
    body = `${[r.worker, r.account].filter(Boolean).join(" · ") || "The worker"} finished it ${dur(r.waitingSec)} ago. ${dw.filter((d) => d.met).length} of ${dw.length} done-when checks pass.`;
  } else if (q?.text) body = q.text;
  const pilot = r ? r.pilot : /pilot/i.test(n.text || "");
  const opts = n.kind === "question" ? q?.options || n.options || [] : [];
  const head = html`<div class="kicker">${k}${pilot ? " · pilot" : ""}</div><h3 title="#${n.num ?? tnum({ id: n.task })}">${n.title}</h3><p>${body}</p>`;
  if (opts.length) {
    const def = q?.default ?? opts[0];
    return html`<div class="card need p-${c} task-root" data-p="${n.project}" data-t="${n.task}" data-rev="${n.rev ?? t?.rev ?? ""}">${head}
      <div class="opts">${opts.map((o) => html`<button class="${o === def ? "on" : ""}" data-act="answer" data-note="${o}">${o}${o === def ? " · default" : ""}</button>`)}</div>
      <div class="meta">${projPill(ctx, n.project)}<a class="lnk" href="${tHref(n.project, n.task, "answer")}">Answer in words ›</a></div></div>`;
  }
  const href = n.kind === "review" ? "#/go/inbox" : n.project && n.task ? tHref(n.project, n.task) : "#/go/ops";
  return html`<a class="card tap need p-${c}" href="${href}">${head}<span class="meta">${projPill(ctx, n.project)}</span></a>`;
}

function alertCard(e, ctx) {
  const m = ALERT.find((x) => x[0].test(e.type || "")) || [0, e.severity === "alert" ? "rose" : "butter", "Alert", "!"];
  return html`<div class="card need p-${m[1]}"><div class="kicker">${m[3]} ${m[2]} · ${hhmm(e.ts)}</div><h3>${e.text}</h3>
    <div class="meta">${projPill(ctx, e.project)}${e.worker ? pill("grey", e.worker) : ""}</div>
    ${e.project && e.task ? html`<div class="acts"><a class="pbtn sm" href="${tHref(e.project, e.task)}">Open #${tnum({ id: e.task })}</a></div>` : ""}</div>`;
}

function feed(list, ctx) {
  const now = ctx.now;
  const day = new Date(now * 1000).toDateString();
  let grp = "";
  return html`<div class="feed">${list.map((e) => {
    const d = new Date(e.ts * 1000), h = d.getHours();
    const g = d.toDateString() !== day ? d.toLocaleDateString("en-GB", { weekday: "long" }) : now - e.ts < 300 ? "Just now" : h >= 17 ? "This evening" : h >= 12 ? "This afternoon" : "This morning";
    const head = g !== grp ? html`<div class="grp">${g}</div>` : "";
    grp = g;
    const k = (KC.find((x) => x[0].test(e.type || "")) || [0, e.severity === "alert" ? "rose" : e.severity === "warn" ? "butter" : "sky"])[1];
    return html`${head}<div class="pev p-${k}"><time>${hhmm(e.ts)}</time>${e.text} ${projPill(ctx, e.project)}</div>`;
  })}</div>`;
}

// ---- chrome ----
const NAV = [["today", "Today", "☼", "#/"], ["projects", "Projects", "◫", "#/go/projects"], ["accounts", "Accounts", "◔", "#/accounts"], ["board", "Board", "▥", "#/go/board"], ["inbox", "Inbox", "✉", "#/go/inbox"]];
function chrome(ctx, tab, body) {
  const { o, now, conn, pref } = ctx;
  const badge = pending(ctx) + (o.alerts || []).length;
  const date = new Date(now * 1000).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }).replace(",", "");
  const live = conn === "offline" ? html`<span class="pill sm p-rose">Offline · last update ${hhmm(o.time)}</span>` : html`<span class="live" aria-label="live"></span>${conn === "fixtures" ? pill("grey", "Sample data") : ""}`;
  const mode = pref?.mode || "auto";
  return html`<div class="pp" data-tab="${tab}"><div class="top"><div class="tools"><button class="theme" data-mode-cycle title="Light / dark / auto">Theme: ${mode}</button><button class="theme" data-layout-cycle aria-label="Next layout" title="Next layout">▦</button><a class="theme" href="#/settings" aria-label="Settings" title="Settings">⚙</a></div>
    <div class="date">${live}<span>${date} · ${hhmm(now)}</span></div></div>
    ${body}
    <nav class="pnav" aria-label="Sections">${NAV.map(([id, label, ic, href]) => html`<a class="${id === tab ? "on" : ""}" href="${href}"${id === tab ? raw(' aria-current="page"') : ""}><span class="ic" aria-hidden="true">${ic}</span>${label}${id === "inbox" && badge ? html`<span class="bd" aria-label="${badge} waiting">${badge}</span>` : ""}</a>`)}</nav></div>`;
}

// ---- screens ----
function today(ctx) {
  const { o, now } = ctx;
  const accts = o.accounts || [];
  const runLanes = accts.flatMap((a) => (a.lanes || []).filter((l) => l.state === "running"));
  const moving = o.machine?.running ?? runLanes.length;
  const across = new Set(runLanes.map((l) => l.project).filter(Boolean)).size;
  const needs = needsList(ctx);
  const rv = needs.filter((n) => n.kind === "review").length;
  const h = new Date(now * 1000).getHours();
  const greet = h < 5 ? "Good night." : h < 12 ? "Good morning." : h < 17 ? "Good afternoon." : "Good evening.";
  const cooling = accts.filter((a) => a.state === "cooling");
  const spare = accts.filter((a) => a.capacity?.spare && !a.capacity?.why);
  const also = [...cooling.map((a) => html`${nb(a.label)} is resting until <b>${hhmm(a.resets5)}</b>`), ...spare.map((a) => html`${nb(a.label)} has a lane free with quota to spare`)];
  const lede = html`${moving === 1 ? "One task is" : `${word(moving)} tasks are`} moving${across ? ` across ${nOf(across, "project", "projects", false)}` : ""}.
    ${rv ? html`<mark>${nOf(rv, "finished piece of work", "finished pieces of work")}</mark> ${rv === 1 ? "is" : "are"} waiting for you.` : "Nothing is waiting for your review."}
    ${also.map((x, i) => html`${i ? (i === also.length - 1 ? ", and " : ", ") : ""}${x}`)}${also.length ? "." : ""}`;
  const greyed = accts.filter(off);
  const tl = vmsOf(ctx).flatMap((vm) => vm.timeline || []).filter((e, i, a) => a.findIndex((x) => x.id === e.id) === i).sort((a, b) => b.ts - a.ts).slice(0, 15);
  const al = o.alerts || [];
  return html`<header class="hello"><h1 class="hd">${greet}</h1><p class="lede">${lede}</p></header>
    <h2>Needs you <small>${nOf(needs.length, "thing", "things", false)}</small></h2>
    ${needs.length ? needs.map((n) => needCard(n, ctx)) : html`<p class="muted">Nothing needs you right now.</p>`}
    <h2>Your accounts <small>5-hour quota</small></h2>
    <div class="card"><div class="rings">${accts.map((a) => html`<a href="#/accounts" class="${off(a) ? "off" : ""}" title="${a.label} · ${a.state === "cooling" ? "at limit" : `${a.pct5 ?? "?"}% used`} · resets ${hhmm(a.resets5)}">${ring(usagePct(a), tone(a), 62, `${usagePct(a)}%`, "", `${a.label}: ${usagePct(a)}% of 5-hour quota used`)}<b>${wid(a)}</b><span>${a.resets5 ? dur(a.resets5 - now) : "—"}</span></a>`)}</div>
      ${greyed.map((a) => html`<p class="note">${a.label} is greyed out: ${a.capacity?.why || "usage only"}.</p>`)}</div>
    <h2>Workers <small>tap to expand</small></h2>${workersSection(o, now, ctx.openWorkers || new Set(), "paper")}
    <h2>Worth a look <small>${nOf(al.length, "alert", "alerts", false)}</small></h2>
    ${al.slice(0, 3).map((e) => alertCard(e, ctx))}${al.length ? html`<a class="back" href="#/go/ops">See all alerts ›</a>` : html`<p class="muted">No alerts.</p>`}
    ${tl.length ? html`<h2>The day so far</h2>${feed(tl, ctx)}` : ""}`;
}

function healthOf(vm) {
  const bad = (vm?.project?.health || []).filter((x) => !x.ok), ch = vm?.project?.repo?.changes || [];
  if (!vm) return null;
  if (bad.length) return ["butter", `${bad.map((x) => x.label).join(", ")} ${bad.length > 1 ? "are" : "is"} down`];
  if (ch.length) return ["butter", `${ch.reduce((s, c) => s + (c.files || 0), 0)} uncommitted files`];
  return ["sage", "Repo healthy"];
}

function projects(ctx) {
  const ps = ctx.o.projects || [];
  const on = ps.filter((p) => p.enabled !== false).length;
  return html`<h1 class="hd">Projects</h1><p class="lede">${nOf(on, "project", "projects")}, each with its own board and task store. Tap one to open its page.</p><div class="sp"></div>
    ${ps.map((p) => {
      const c = p.counts || {}, h = healthOf(ctx.projects?.[p.id]), dis = p.enabled === false;
      const inner = html`<span class="row"><span class="icon p-${p.scope === "office" ? "indigo" : "teal"}" aria-hidden="true">${p.icon || "◆"}</span><span class="grow"><h3>${p.title}</h3>${scopePill(p.scope)}${dis ? pill("grey", "Disabled in config") : ""}</span>${dis ? "" : html`<span class="chev" aria-hidden="true">›</span>`}</span>
        <p>${p.headline || ""}</p>
        <span class="meta">${pill("sage", `${c.running ?? 0} running`)}${pill("sky", `${c.queued ?? 0} ready`)}${pill("lilac", `${c.review ?? 0} in review`)}${pill("peach", `${c.blocked ?? 0} blocked`)}${c.limit_paused ? pill("butter", `${c.limit_paused} resting`) : ""}${c.doneToday ? pill("grey", `${c.doneToday} done today`) : ""}</span>
        ${h ? html`<span class="row hl p-${h[0]}"><span class="dot"></span><span class="muted">${h[1]}</span></span>` : ""}`;
      return dis ? html`<div class="card dis" aria-disabled="true">${inner}</div>` : html`<a class="card tap" href="#/p/${E(p.id)}/board">${inner}</a>`;
    })}
    <p class="muted c">Projects are added or removed in the config file.</p>`;
}

function projectPage(ctx, id) {
  const vm = ctx.projects?.[id];
  const tile = (ctx.o.projects || []).find((p) => p.id === id) || {};
  if (!vm) return html`<a class="back" href="#/go/projects">‹ All projects</a><h1 class="hd">${tile.title || id}</h1><p class="lede">Loading this project…</p>`;
  const p = vm.project, b = vm.board || {}, n = (k) => (b[k] || []).length;
  const accts = Object.fromEntries((ctx.o.accounts || []).map((a) => [a.id, a]));
  const lanes = (vm.lanes || []).map((l) => laneEl(accts[(vm.workers || []).find((w) => w.worker === l.worker)?.account] || {}, l, ctx));
  const h = healthOf(vm), r = p.repo || {}, mg = vm.merge || {};
  const blocked = b.needsYou || [];
  return html`<a class="back" href="#/go/projects">‹ All projects</a>
    <div class="row"><span class="icon p-${p.scope === "office" ? "indigo" : "teal"}" aria-hidden="true">${p.icon || "◆"}</span><h1 class="hd grow">${p.title}</h1></div>
    <div class="meta">${scopePill(p.scope)}${pill("grey", p.allowPersonalAccounts ? "Personal accounts allowed" : "Office accounts only")}</div>
    ${tile.headline ? html`<p class="lede">${tile.headline}</p>` : ""}
    <div class="coltabs">${COLS.map(([k, c, label], i) => html`<a class="p-${c}" href="#/go/board/${E(id)}" data-pv="col:${i}"><b>${n(k)}</b><span>${label}</span></a>`)}</div>
    <h2>Working on it now</h2>${lanes.length ? html`<div class="lns">${lanes}</div>` : html`<p class="muted">Nobody is working on it right now.</p>`}
    <h2>Up next</h2>${n("upNext") ? b.upNext.map((t) => tcard(t, id, ctx)) : html`<p class="muted">Nothing ready.</p>`}
    ${(vm.inbox || []).length || blocked.length ? html`<h2>Waiting for you</h2>${(vm.inbox || []).map((x) => html`<a class="card tap need p-lilac" href="#/go/inbox"><div class="kicker">${x.task.state === "merging" ? "Approved · merging" : "Review"}${x.pilot ? " · pilot" : ""}</div><h3><span class="num">#${tnum(x.task)}</span>${x.task.title}</h3><p>Waiting ${dur(x.waitingSec)} · ${(x.doneWhen || []).filter((d) => d.met).length}/${(x.doneWhen || []).length} checks pass</p></a>`)}
      ${blocked.map((t) => html`<a class="card tap need p-peach" href="${tHref(id, t.id, t.question ? "answer" : "")}"><div class="kicker">${t.question ? "A question for you" : "Blocked"}</div><h3><span class="num">#${tnum(t)}</span>${t.title}</h3><p>${t.question?.text || t.stateReason || ""}</p></a>`)}` : ""}
    ${(vm.alerts || []).length ? html`<h2>Alerts</h2>${vm.alerts.map((e) => alertCard(e, ctx))}` : ""}
    <h2>Repo health</h2><div class="card">${h ? html`<div class="row p-${h[0]}"><span class="dot"></span><b>${h[1]}</b></div>` : ""}
      <dl class="pkv"><dt>Head</dt><dd>${r.commit || "—"}</dd><dt>Working tree</dt><dd>${(r.changes || []).length ? r.changes.map((c) => `${c.area} · ${c.files} files`).join(", ") : "clean"}</dd>
      <dt>Services</dt><dd>${(p.health || []).map((x, i) => html`${i ? " · " : ""}${x.ok ? "🟢" : "🔴"} ${x.label}`)}</dd>
      <dt>Merge</dt><dd>${mg.active ? `#${tnum({ id: mg.active.task })} · gates ${mg.active.gatesDone}/${mg.active.gatesTotal}` : "idle"}${(mg.queue || []).length ? ` · next: ${mg.queue.map((q) => `#${tnum({ id: q.task })}`).join(", ")}` : ""}</dd>
      ${(vm.domains || []).length ? html`<dt>Locks</dt><dd>${vm.domains.map((d, i) => html`${i ? raw("<br>") : ""}${d.name} → ${d.holder ? `#${tnum({ id: d.holder })} since ${hhmm(d.since)}` : "free"}${d.note ? ` · ${d.note}` : ""}${(d.waiting || []).length ? html` <span class="wait">(${d.waiting.map((w) => `#${tnum({ id: w })}`).join(", ")} waiting)</span>` : ""}`)}</dd>` : ""}</dl></div>
    <h2>Accounts for this project</h2><div class="card">${workersWithUsage(vm, ctx.o).map((w) => {
      const ok = w.allowed !== false, f = ok ? freeLane(w, ctx.now) : null;
      const why = ok ? (w.state === "cooling" ? `Allowed. Resting until the ${hhmm(w.resets5)} reset.` : `Allowed${f?.cls === "free" ? ", with a spare lane now." : "."}`) : w.reason || "Not allowed";
      return html`<div class="row elig${ok ? "" : " no"}"><span class="dot p-${ok ? "sage" : "grey"}"></span><span class="grow"><b>${w.worker} · ${w.label}</b>${ok ? "" : " 🔒"}<span class="muted d">${why}</span></span><span class="muted">${w.pct5 != null ? `${w.pct5}%` : ""}</span></div>`;
    })}</div>`;
}

function accounts(ctx) {
  const { o, now } = ctx;
  const accts = o.accounts || [], work = accts.filter((a) => a.dispatch !== false), only = accts.filter((a) => a.dispatch === false);
  const b = o.backlog;
  return html`<h1 class="hd">Accounts</h1><p class="lede">${nOf(accts.length, "account", "accounts")}. ${word(work.length)} take${work.length === 1 ? "s" : ""} work in parallel lanes${only.length ? html`; ${only.map((a) => nb(a.label)).join(", ")} ${only.length > 1 ? "are" : "is"} shown for usage only` : ""}.</p><div class="sp"></div>
    ${accts.map((a) => {
      const g = off(a), p = usagePct(a);
      return html`<div class="card${g ? " acc-off" : ""}"><div class="acc">${ring(p, tone(a), 120, `${p}%`, "5h used", `${a.label}: ${p}% of 5-hour quota used`)}
        <div class="mn"><div class="nm">${a.label}</div><div class="meta">${scopePill(a.scope)}${[...new Set((a.lanes || []).map((l) => l.worker))].map((w) => pill("grey", w))}${a.role === "orchestrator" ? pill("lilac", "Orchestrator") : ""}${a.dispatch === false ? pill("grey", "Usage only") : ""}${a.state === "cooling" ? pill("rose", "At limit") : ""}</div>
          <div class="rs">${a.resets5 ? html`Resets in <b>${dur(a.resets5 - now)}</b> <span class="muted">at ${hhmm(a.resets5)}</span>` : "No reset time yet"}</div>
          ${a.pct7 != null ? html`<div class="wk7">7 days <span class="tr"><i style="width:${Math.min(100, a.pct7)}%"></i></span>${a.pct7}%</div>` : ""}
          ${a.readAgeSec > 600 ? html`<div class="muted">Reading ${dur(a.readAgeSec)} old</div>` : ""}</div>
        <div class="lns">${(a.lanes || []).length ? a.lanes.map((l) => laneEl(a, l, ctx)) : html`<div class="ln p-lilac"><span class="t">${cap1(a.capacity?.why || "No lanes")}</span></div>`}</div>
        ${g ? html`<div class="why"><b>Why it is greyed out.</b> ${cap1(a.capacity?.why || "Usage only.")}</div>` : ""}</div></div>`;
    })}
    ${b ? html`<div class="card p-sky tint"><h3>Backlog</h3><p>${nOf(b.eligible ?? 0, "task is", "tasks are")} ready against a target of ${b.target ?? "—"}.${b.hint ? ` ${b.hint}.` : ""}</p></div>` : ""}`;
}

function board(ctx, pid) {
  const vms = vmsOf(ctx).filter((vm) => !pid || vm.project.id === pid);
  const list = (k) => vms.flatMap((vm) => (vm.board?.[k] || []).map((t) => [t, vm.project.id]));
  const ps = (ctx.o.projects || []).filter((p) => p.enabled !== false);
  return html`<div class="board" data-col="${UI.col}"><h1 class="hd">Board</h1>
    <div class="chips"><a class="${pid ? "" : "on"}" href="#/go/board">All</a>${ps.map((p) => html`<a class="${pid === p.id ? "on" : ""}" href="#/go/board/${E(p.id)}">${p.icon ? `${p.icon} ` : ""}${p.short || p.title}</a>`)}</div>
    <div class="coltabs" role="tablist">${COLS.map(([k, c, label], i) => html`<button role="tab" class="p-${c}" data-pv="col:${i}"><b>${list(k).length}</b><span>${label}</span></button>`)}</div>
    <div class="pages" id="pp-pages">${COLS.map(([k, c, label]) => {
      const l = list(k);
      return html`<section class="pg p-${c}" aria-label="${label}"><h4>${label}<span>${l.length}</span></h4>${l.length ? l.map(([t, p]) => tcard(t, p, ctx, !pid)) : html`<p class="muted">Nothing here.</p>`}</section>`;
    })}</div>
    <div class="dots" aria-hidden="true">${COLS.map(() => html`<i></i>`)}</div>
    <p class="muted c">Swipe sideways between columns. “After #” shows a dependency.</p></div>`;
}

function reviewCard(r, ctx) {
  const t = r.task, g = r.git || {}, dw = r.doneWhen || [];
  const merging = isMerging(r);
  const open = UI.note === t.id;
  return html`<div class="card rv task-root" data-p="${r.pid}" data-t="${t.id}" data-rev="${t.rev ?? ""}">
    <div class="meta">${projPill(ctx, r.pid)}${r.pilot ? pill("butter", "Pilot") : ""}${pill("grey", `waiting ${dur(r.waitingSec)}`)}</div>
    <h3 class="big3"><a href="${tHref(r.pid, t.id)}"><span class="num">#${tnum(t)}</span>${t.title}</a></h3><p class="ink">${r.summary || t.summary || ""}</p>
    ${r.outcome && r.outcome !== "done" ? html`<p class="risk">Outcome: ${r.outcome}</p>` : ""}
    <ul class="dw">${dw.map((d) => html`<li><span class="ck p-${d.met ? "sage" : "rose"}" aria-label="${d.met ? "met" : "not met"}">${d.met ? "✓" : "✕"}</span><span>${d.item}<small>${d.evidence || ""}</small></span></li>`)}</ul>
    <div class="facts"><span>by <b>${[r.worker, r.account].filter(Boolean).join(" · ")}</b></span><span><b>${g.commits ?? 0}</b> commits</span><span><b>${g.filesChanged ?? 0}</b> files</span><span><b>${g.conflictsWithMain ? "conflicts" : "no"}</b>${g.conflictsWithMain ? " with main" : " conflicts"}</span>${g.uncommitted ? html`<span class="wait"><b>${g.uncommitted}</b> uncommitted (not merged)</span>` : ""}</div>
    ${(r.risks || []).map((x) => html`<p class="risk">Worth knowing: ${x}.</p>`)}
    ${(r.decisionsNeeded || []).map((d, i) => html`<p class="ink q"><b>${d.question}</b></p><div class="opts">${(d.options || []).map((o) => html`<button class="${o === d.default ? "on" : ""}" data-act="answer" data-decision="${i}" data-option="${o}">${o}${o === d.default ? " · default" : ""}</button>`)}</div>`)}
    ${merging ? html`<div class="done p-sage">Approved · merging${r.merging ? `, gates ${r.merging.gatesDone} of ${r.merging.gatesTotal}` : ""}.${(r.unlocks || []).length ? ` Then #${r.unlocks.map((u) => tnum({ id: u })).join(", #")} can start.` : ""}</div>`
      : html`<input type="checkbox" class="chgt vh" id="chg-${t.id}" data-note-for="${t.id}"${open ? raw(" checked") : ""}>
      <div class="acts"><label class="pbtn" for="chg-${t.id}">Request changes</label><button class="pbtn go" data-act="approve">Approve${(r.unlocks || []).length ? html` <small>· starts #${r.unlocks.map((u) => tnum({ id: u })).join(", #")}</small>` : ""}</button></div>
      <div class="chg"><textarea class="pnote"${open ? raw(' id="note"') : ""} rows="3" placeholder="What should change? Your note goes to the worker with the next attempt."></textarea><button class="pbtn" data-act="changes">Send changes</button></div>
      <button class="hold" data-hold="reject">Hold to reject</button>`}</div>`;
}

function inbox(ctx, seg) {
  const rs = reviewsOf(ctx), al = ctx.o.alerts || [];
  return html`<div class="inbox" data-seg="${seg}"><h1 class="hd">Inbox</h1>
    <div class="chips"><button data-pv="seg:review">To review · ${pending(ctx)}</button><button data-pv="seg:alerts">Alerts · ${al.length}</button></div>
    <div class="s-review">${rs.length ? rs.map((r) => reviewCard(r, ctx)) : html`<p class="muted">Nothing to review.</p>`}</div>
    <div class="s-alerts">${al.length ? al.map((e) => alertCard(e, ctx)) : html`<p class="muted">No alerts.</p>`}</div></div>`;
}

/** Layout entry point: whole-screen HTML, or null for task detail / sheets (shared views, restyled by tokens). */
export function render(route, ctx) {
  setWorkerColors(ctx?.o);
  if (!ctx?.o || route.view === "task" || route.view === "settings") return null;
  if (route.view === "accounts" || route.page === "workers") return chrome(ctx, "accounts", accounts(ctx));
  if (route.view === "project" || (route.page === "projects" && route.project)) return chrome(ctx, "projects", projectPage(ctx, route.project));
  if (route.page === "projects") return chrome(ctx, "projects", projects(ctx));
  if (route.page === "board") return chrome(ctx, "board", board(ctx, route.project));
  if (route.page === "inbox" || route.page === "ops") return chrome(ctx, "inbox", inbox(ctx, route.page === "ops" ? "alerts" : UI.seg));
  return chrome(ctx, "today", today(ctx));
}

// ---- browser glue (not run under node): board column pages, inbox segment, which review owns #note ----
if (typeof document !== "undefined") {
  const pages = () => document.getElementById("pp-pages");
  const show = (smooth) => {
    const pg = pages();
    const el = pg?.children[UI.col];
    if (el) pg.scrollTo({ left: el.offsetLeft - pg.children[0].offsetLeft, behavior: smooth ? "smooth" : "auto" });
    document.querySelector(".pp .board")?.setAttribute("data-col", UI.col);
  };
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest?.("[data-pv]");
    if (!b) return;
    const [k, v] = b.dataset.pv.split(":");
    if (k === "col") { UI.col = +v; if (pages()) { ev.preventDefault(); show(true); } }
    if (k === "seg") {
      UI.seg = v;
      document.querySelector(".pp .inbox")?.setAttribute("data-seg", v);
      if (location.hash.startsWith("#/go/ops")) history.replaceState(null, "", "#/go/inbox"); // keep the choice across re-renders
    }
  });
  document.addEventListener("scroll", (ev) => {
    const pg = ev.target;
    if (pg?.id !== "pp-pages" || pg.children.length < 2) return;
    const i = Math.round(pg.scrollLeft / (pg.children[1].offsetLeft - pg.children[0].offsetLeft));
    if (i !== UI.col) { UI.col = i; document.querySelector(".pp .board")?.setAttribute("data-col", i); }
  }, true);
  const claimNote = (box) => {
    if (!box) return;
    document.querySelectorAll(".pp textarea.pnote").forEach((x) => x.removeAttribute("id"));
    box.id = "note";
  };
  document.addEventListener("change", (ev) => {
    const c = ev.target;
    if (!c.matches?.(".pp .chgt")) return;
    UI.note = c.checked ? c.dataset.noteFor : UI.note === c.dataset.noteFor ? null : UI.note;
    if (c.checked) { const box = c.closest(".task-root")?.querySelector("textarea.pnote"); claimNote(box); box?.focus(); }
  });
  document.addEventListener("focusin", (ev) => { if (ev.target.matches?.(".pp textarea.pnote")) { claimNote(ev.target); UI.note = ev.target.closest(".task-root")?.dataset.t; } });
  // after each render (app.js sets innerHTML right after render() returns), put the board back on the chosen column
  new MutationObserver(() => { if (pages()) show(false); }).observe(document.getElementById("app") || document.body, { childList: true });
}
