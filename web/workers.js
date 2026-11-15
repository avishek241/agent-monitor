// Worker colours and the expandable Workers section (shared by every layout).
// Colours: Okabe–Ito order (colour-blind safe), one per account in overview order; AccountVM.color (config) overrides.
// Each theme defines --w0..--w6 in themes.css, tuned per light/dark so stripes and graph lines keep ≥ 3:1 on cards.
import { html, raw, esc, hhmm, dur, pct, tnum } from "./fmt.js";
import { freeLane } from "./views/common.js";

export const PALETTE_SIZE = 7;
let byWorker = {}, byAccount = {};

/** Register colours for this render: account i → var(--w{i}) unless the account has its own colour. */
export function setWorkerColors(o) {
  byWorker = {}; byAccount = {};
  (o?.accounts || []).forEach((a, i) => {
    const c = a.color && /^#[0-9a-f]{6}$/i.test(a.color) ? a.color : `var(--w${i % PALETTE_SIZE})`;
    byAccount[a.id] = c;
    for (const l of a.lanes || []) byWorker[l.worker] = c;
  });
}
/** CSS colour for a worker id (W1) or account id; null when unknown. */
export const wc = (workerOrAccount) => byWorker[workerOrAccount] || byAccount[workerOrAccount] || null;
/** style attribute value setting --wc, or "" (escaped for attributes). */
export const wcStyle = (id) => (wc(id) ? raw(` style="--wc:${esc(wc(id))}"`) : "");

/** 5 h (solid) + 7 d (dashed) usage graph with reset markers, as inline SVG. */
export function usageGraph(a, now, { w = 320, h = 90 } = {}) {
  const pts = a.usage?.points || [];
  if (pts.length < 2) return "";
  const t0 = pts[0].ts, t1 = Math.max(now, pts[pts.length - 1].ts), span = Math.max(1, t1 - t0);
  const x = (ts) => ((ts - t0) / span) * w, y = (v) => h - (Math.min(100, Math.max(0, v ?? 0)) / 100) * h;
  const line = (k) => pts.map((p) => `${x(p.ts).toFixed(1)},${y(p[k]).toFixed(1)}`).join(" ");
  const resets = (a.usage.resets || []).filter((r) => r >= t0 && r <= t1);
  return html`<svg class="ugraph" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
    aria-label="${a.label}: 5-hour usage ${pct(a.pct5)}, 7-day ${pct(a.pct7)} over the last ${dur(span)}">
    <line class="grid" x1="0" x2="${w}" y1="${h / 2}" y2="${h / 2}"/><line class="grid" x1="0" x2="${w}" y1="1" y2="1"/>
    ${resets.map((r) => html`<line class="reset" x1="${x(r).toFixed(1)}" x2="${x(r).toFixed(1)}" y1="0" y2="${h}"><title>reset ${hhmm(r)}</title></line>`)}
    <polyline class="p7" points="${line("pct7")}"/><polyline class="p5" points="${line("pct5")}"/></svg>`;
}

/** Studio: the same series as an LCD dot-matrix (columns = samples, lit dots = 5 h %, hollow = 7 d level). */
export function dotMatrix(a, now, { rows = 10 } = {}) {
  const pts = a.usage?.points || [];
  if (pts.length < 2) return "";
  const cols = pts.slice(-32), d = 8;
  const resets = new Set((a.usage.resets || []).map((r) => cols.findIndex((p) => p.ts >= r)).filter((i) => i >= 0));
  let dots = "";
  cols.forEach((p, c) => {
    const lit = Math.round(((p.pct5 ?? 0) / 100) * rows), l7 = Math.round(((p.pct7 ?? 0) / 100) * rows);
    for (let r = 0; r < rows; r++) {
      const cls = r < lit ? "on" : r === l7 - 1 ? "w7" : "off";
      dots += `<circle class="${cls}" cx="${c * d + 4}" cy="${(rows - 1 - r) * d + 4}" r="2.6"/>`;
    }
    if (resets.has(c)) dots += `<line class="reset" x1="${c * d}" x2="${c * d}" y1="0" y2="${rows * d}"/>`;
  });
  return html`<svg class="ugraph dm" viewBox="0 0 ${cols.length * d} ${rows * d}" role="img"
    aria-label="${a.label}: 5-hour usage ${pct(a.pct5)}, 7-day ${pct(a.pct7)}">${raw(dots)}</svg>`;
}

function summaryLine(a) {
  const c = a.capacity || {};
  const lanes = c.lanesMax ? `${c.lanesUsed ?? 0}/${c.lanesMax} lanes` : a.dispatch === false ? "usage only" : "";
  return [a.state === "cooling" ? `LIMIT → ${hhmm(a.resets5)}` : `5h ${pct(a.pct5)}`, lanes].filter(Boolean).join(" · ");
}

/** One worker: summary row (a toggle) and, when open, the detail panel. */
function workerItem(a, now, open, layout) {
  const id = a.id, u = a.usage || {};
  const workers = [...new Set((a.lanes || []).map((l) => l.worker))].join(" ");
  const running = (a.lanes || []).filter((l) => l.state === "running");
  const spare = (a.lanes || []).some((l) => l.state === "free") ? freeLane(a, now) : null;
  const detail = open ? html`<div class="wdetail" id="wd-${id}">
      ${layout === "studio" ? dotMatrix(a, now) : usageGraph(a, now)}
      <div class="wlegend small"><span class="k5">— 5 h</span> <span class="k7">- - 7 d</span> <span class="kr">┆ reset</span></div>
      <dl class="wfacts">
        ${u.burn5PerHour != null ? html`<dt>Burn</dt><dd>${u.burn5PerHour}%/h</dd>` : ""}
        ${u.minutesToLimit != null ? html`<dt>To limit</dt><dd>~${dur(u.minutesToLimit * 60)}${a.resets5 && now + u.minutesToLimit * 60 > a.resets5 ? " (resets first)" : ""}</dd>` : ""}
        ${a.state === "cooling" ? html`<dt>Limit</dt><dd>resets ${hhmm(a.resets5)}</dd>` : ""}
        ${a.pct7 != null ? html`<dt>7 days</dt><dd>${pct(a.pct7)}</dd>` : ""}
        ${a.doneToday != null ? html`<dt>Done today</dt><dd>${a.doneToday}</dd>` : ""}
      </dl>
      ${running.length ? html`<div class="wsub">Running now</div>${running.map((l) => html`<a class="wlane" href="#/p/${encodeURIComponent(l.project)}/t/${encodeURIComponent(l.task)}">${l.worker}·${l.lane} ▶ ${tnum({ id: l.task })} ${l.title || ""}${l.etaMin != null ? ` · ~${l.etaMin}m` : ""}</a>`)}` : ""}
      ${spare ? html`<div class="wspare ${spare.cls}">${spare.icon} ${spare.text}</div>` : ""}
      ${(a.queued || []).length ? html`<div class="wsub">Queued for this worker</div>${a.queued.map((q) => html`<a class="wlane q" href="#/p/${encodeURIComponent(q.project)}/t/${encodeURIComponent(q.task)}">⏳ ${tnum({ id: q.task })} ${q.title || ""}</a>`)}` : ""}
    </div>` : "";
  return html`<div class="witem${open ? " open" : ""}"${wcStyle(id)}>
    <button class="wrow" data-wexpand="${id}" aria-expanded="${String(open)}" aria-controls="wd-${id}">
      <span class="wdot" aria-hidden="true"></span><span class="wname">${a.label}${workers ? html` <small>${workers}</small>` : ""}</span>
      <span class="wsum">${summaryLine(a)}</span><span class="wchev" aria-hidden="true">${open ? "▾" : "▸"}</span></button>${detail}</div>`;
}

/**
 * The Workers section. `open` is a Set of account ids that are expanded ("*" = all). Same markup in every layout;
 * layouts restyle it through tokens, Studio swaps the graph for a dot matrix.
 */
export function workersSection(o, now, open = new Set(), layout = "studio") {
  const accts = o?.accounts || [];
  if (!accts.length) return "";
  setWorkerColors(o);
  const all = open.has("*");
  return html`<section class="wsec" aria-label="Workers">
    <button class="whead" data-wexpand="*" aria-expanded="${String(all)}"><span>WORKERS · ${accts.length}</span>
      <span class="wlegend">${accts.map((a) => html`<span${wcStyle(a.id)}><i class="wdot"></i>${a.label}</span>`)}</span><span class="wchev">${all ? "▾" : "▸"}</span></button>
    ${accts.map((a) => workerItem(a, now, all || open.has(a.id), layout))}
  </section>`;
}
