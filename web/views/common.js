// Building blocks shared by the deck screens. Every function is pure: view model in, HTML out.
import { wcStyle } from "../workers.js";
import { html, raw, esc, hhmm, dur, until, pct, num, usageColor, scopeWord, stateIcon, stateWord, tnum, ago } from "../fmt.js";

export const scopeBadge = (scope) => html`<span class="sc ${scope === "office" ? "o" : scope === "personal" ? "p" : "u"}">${scopeWord(scope)}</span>`;

export const label = (text, em) => html`<div class="label">${text}<i></i>${em != null && em !== "" ? html`<em>${em}</em>` : ""}</div>`;

/** The LCD block. `conn` is 'live' | 'offline' | 'fixtures'; lastUpdate is unix seconds. */
export function lcd({ left, mid, right, big, bigClass = "big", stats = [], stats2 = [], conn = "live", lastUpdate }) {
  const led = conn === "offline"
    ? html`<span class="off-badge">OFFLINE · last update ${hhmm(lastUpdate)}</span>`
    : conn === "fixtures" ? html`<span class="fx-badge">FIXTURES</span>` : html`<span class="led" aria-label="live"></span>`;
  const row = (s) => (s.length ? html`<div class="stats">${s.map((x) => html`<span class="${x.hot ? "hot" : ""}">${x.b != null ? html`<b>${x.b}</b> ` : ""}${x.t}</span>`)}</div>` : "");
  return html`<header class="lcd">
    <div class="l1">${led}${left}<span>${mid}</span><span>${right}</span><button class="themebtn" data-layout-cycle aria-label="Next layout" title="Next layout">▦</button><button class="themebtn" data-mode-cycle aria-label="Light or dark" title="Light / dark / auto">◐</button><a href="#/settings" aria-label="Settings" title="Settings">⚙</a></div>
    <div class="${bigClass}">${big}</div>${row(stats)}${row(stats2)}</header>`;
}

/** Quota summary for an account header: LIMIT countdown when cooling, never "100% for 24 h" (M4). */
export function quotaText(a, now) {
  if (a.state === "cooling") return { text: `LIMIT → ${hhmm(a.resets5)} (${until(a.resets5, now)})`, hot: true };
  const parts = [`5h ${pct(a.pct5)}`];
  if (a.pct7 != null) parts.push(`7d ${pct(a.pct7)}`);
  if (a.resets5) parts.push(`↻ ${dur(a.resets5 - now)}`);
  const c = a.capacity || {};
  if (a.dispatch === false) parts.push("usage only");
  else if (c.lanesMax) parts.push(`${c.lanesUsed ?? 0}/${c.lanesMax}`);
  if (a.readAgeSec != null && a.readAgeSec > 600) parts.push(`reading ${ago(a.readAgeSec)}`);
  if (a.confidence && a.confidence !== "high") parts.push(`${a.confidence} confidence`);
  return { text: parts.join(" · "), hot: false };
}

export function quotaBar(a) {
  const p = a.state === "cooling" ? 100 : a.pct5;
  const col = a.state === "cooling" ? "var(--bad)" : usageColor(p);
  return html`<span class="bar" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p ?? 0}"><i style="width:${Math.min(100, p ?? 0)}%;background:${raw(col)}"></i></span>`;
}

/**
 * Explain a free lane using only the planner's own output (capacity.why / fitsSize), never UI guesses.
 * Returns {cls, icon, text}: cls 'pol' (policy, grey), 'cool', 'free' (spare, orange) or 'idle'.
 */
export function freeLane(a, now) {
  const c = a.capacity || {};
  if (a.state === "cooling") return { cls: "cool", icon: "⏸", text: `cooling · limit resets ${hhmm(a.resets5)}` };
  if (c.why) {
    const kind = c.whyKind || (/policy/i.test(c.why) ? "policy" : null);
    if (kind === "policy") return { cls: "pol", icon: "🔒", text: `free · ${c.why}` };
    if (kind === "cooling") return { cls: "cool", icon: "⏸", text: c.why };
    return { cls: "idle", icon: "○", text: `free · ${c.why}` };
  }
  if (c.fitsSize) {
    let text = `FREE · ${c.spendable5 ?? "?"}% to spend before ${hhmm(a.resets5)} · ${c.fitsSize} fits`;
    if (c.spare && c.spareSinceSec) text += ` · ⚠ spare ${dur(c.spareSinceSec)}`;
    return { cls: c.spare ? "free" : "idle", icon: "○", text };
  }
  return { cls: "idle", icon: "○", text: "free" };
}

/** One lane row (W1·2 …). projectsById maps id → {short,title} for the project prefix. */
export function laneRow(l, a, now, projectsById = {}) {
  const id = `${l.worker}·${l.lane}`;
  const pname = (p) => (p ? `${projectsById[p]?.short || projectsById[p]?.title || p} · ` : "");
  const link = (inner) => (l.project && l.task ? html`<a class="t" href="#/p/${encodeURIComponent(l.project)}/t/${encodeURIComponent(l.task)}">${inner}</a>` : html`<span class="t">${inner}</span>`);
  if (l.state === "running") {
    const step = l.stepCount ? ` · ${l.stepIndex}/${l.stepCount}` : "";
    const eta = l.etaMin != null ? ` · ~${l.etaMin}m` : "";
    const quiet = l.lastActiveSec != null && l.lastActiveSec > 600 ? ` · quiet ${dur(l.lastActiveSec)}` : "";
    return html`<div class="lane${quiet ? " warn" : ""}"${wcStyle(l.worker)}><span class="id">${id}</span>${link(`▶ ${pname(l.project)}${tnum({ id: l.task })} ${l.title || ""}${step}${eta}${quiet}`)}</div>`;
  }
  if (l.state === "cooling") {
    return html`<div class="lane cool"${wcStyle(l.worker)}><span class="id">${id}</span>${link(`⏸ ${pname(l.project)}${l.task ? `${tnum({ id: l.task })} ${l.title || ""} · ` : ""}resumes ${hhmm(l.resumeAt || a.resets5)} (same session)`)}</div>`;
  }
  const f = freeLane(a, now);
  return html`<div class="lane ${f.cls}"${wcStyle(l.worker)}><span class="id">${id}</span><span class="t">${f.icon} ${f.text}</span></div>`;
}

/** Full account block with every lane (Accounts screen, project Workers tab). */
export function accountBlock(a, now, projectsById, { worker, allowed = true, reason } = {}) {
  const q = quotaText(a, now);
  const name = `${a.label}${worker ? ` · ${worker}` : ""}`;
  return html`<section class="acctcard${allowed ? "" : " off"}" ${allowed ? "" : raw('aria-disabled="true"')}>
    <div class="acct"${wcStyle(a.id)}><span class="n">${allowed ? "" : "🔒 "}${name}${scopeBadge(a.scope)}</span><span class="v${q.hot ? " hot" : ""}">${q.text}</span>${quotaBar(a)}</div>
    ${allowed ? "" : html`<div class="reason">${reason}</div>`}
    ${(a.lanes || []).map((l) => laneRow(l, a, now, projectsById))}
  </section>`;
}

/** Compact account row for the Overview strip: LCD bar + lane chips. */
export function accountStrip(a, now) {
  const q = quotaText(a, now);
  const chips = (a.lanes || []).map((l) => {
    const tip = `${l.worker}·${l.lane} ${l.state === "free" ? freeLane(a, now).text : `${l.task || ""} ${l.title || ""}`}`;
    if (l.state === "running") return html`<i class="r" title="${tip}">${tnum({ id: l.task })}</i>`;
    if (l.state === "cooling") return html`<i class="c" title="${tip}">⏸ ${l.task ? tnum({ id: l.task }) : ""}</i>`;
    const f = freeLane(a, now);
    if (f.cls === "pol") return html`<i class="o" title="${tip}">🔒 POLICY</i>`;
    if (f.cls === "cool") return html`<i class="c" title="${tip}">⏸</i>`;
    if (f.cls === "free") return html`<i class="f" title="${tip}">FREE · ${a.capacity?.fitsSize ?? ""} FITS</i>`;
    return html`<i class="o" title="${tip}">·</i>`;
  });
  return html`<a class="acct" href="#/accounts"${wcStyle(a.id)}><span class="n">${a.label}${scopeBadge(a.scope)}</span><span class="v${q.hot ? " hot" : ""}">${q.text}</span>${quotaBar(a)}${chips.length ? html`<span class="lanes">${chips}</span>` : ""}</a>`;
}

/** A tappable task key (Board columns, lists). */
export function taskKey(t, project, now, extraClass = "") {
  const bits = [];
  if (t.kind === "pilot") bits.push("PILOT");
  if (t.state === "limit_paused" && t.resumeAt) bits.push(`resumes ${hhmm(t.resumeAt)} (${until(t.resumeAt, now)})`);
  else if (t.state === "merging" && t.resumeAt && t.resumeAt > now) bits.push(`retry in ${dur(t.resumeAt - now)}${t.stateReason ? ` · ${t.stateReason.replace(/^merge waits:\s*/i, "").replace(/\s*·\s*retry \d\d:\d\d$/, "")}` : ""}`);
  else if (t.stateReason && !(t.step && /step \d+\/\d+/.test(t.stateReason))) bits.push(t.stateReason);
  if (t.step && t.state === "running") bits.push(`${t.step.index}/${t.step.count} ${t.step.title}${t.step.etaMin != null ? ` · ~${t.step.etaMin}m` : ""}`);
  const m = (t.metrics || [])[0];
  if (m && m.total) bits.push(`${num(m.done)}/${num(m.total)} ${m.label.toLowerCase()}`);
  const cls = t.state === "review" || t.state === "merging" ? "review" : t.state === "blocked" ? "q" : t.state === "hung" || t.state === "failed" ? "alert" : "";
  return html`<a class="key ${cls} ${extraClass}"${wcStyle(t.worker)} href="#/p/${encodeURIComponent(project)}/t/${encodeURIComponent(t.id)}">
    <span class="ico" aria-label="${stateWord(t.state)}">${stateIcon(t.state)}</span><span class="num">${tnum(t)}</span>
    <span class="t"><b>${t.title}</b><small>${t.worker ? html`<span class="wbadge">${t.worker}</span> · ` : ""}${stateWord(t.state)} · ${t.priority || ""} ${t.size || ""}${bits.length ? " · " : ""}${bits.join(" · ")}</small>
    ${t.progress != null && t.state === "running" ? html`<span class="prog"><i style="width:${Math.round(t.progress * 100)}%"></i></span>` : ""}</span></a>`;
}

const SEV = { alert: ["!", "alert"], warn: ["⚠", "alert"], action: ["▣", "review"], info: ["·", ""], debug: ["·", ""] };
/** One event line (alerts list, timeline). */
export function eventRow(e, { showTime = true, key = false } = {}) {
  const [ico, cls] = SEV[e.severity] || ["·", ""];
  const href = e.project && e.task ? `#/p/${encodeURIComponent(e.project)}/t/${encodeURIComponent(e.task)}` : null;
  if (key) {
    return html`<${raw(href ? "a" : "div")} class="key ${cls}" ${href ? raw(`href="${esc(href)}"`) : ""}><span class="ico" aria-label="${e.severity}">${ico}</span>
      <span class="t"><b>${e.text}</b><small>${hhmm(e.ts)} · ${e.type}${e.worker ? ` · ${e.worker}` : ""}</small></span></${raw(href ? "a" : "div")}>`;
  }
  return html`<div class="ev ${e.severity}"${wcStyle(e.worker)}>${showTime ? html`<time>${hhmm(e.ts)}</time>` : ""}<span>${ico}</span><span>${e.text}</span></div>`;
}

export const empty = (text) => html`<div class="empty">${text}</div>`;
