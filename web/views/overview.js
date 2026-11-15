// Overview (all projects) and Accounts & parallel lanes screens (design §13.2 screens 1 and 6).
import { html, hhmm, dayLabel, tnum } from "../fmt.js";
import { workersSection, setWorkerColors } from "../workers.js";
import { lcd, label, accountStrip, accountBlock, eventRow, empty, scopeBadge } from "./common.js";

const projectsById = (o) => Object.fromEntries((o.projects || []).map((p) => [p.id, p]));

/** Soonest reset worth announcing: a cooling account first, else the soonest 5-h reset. */
export function nextReset(o, now) {
  const accts = (o.accounts || []).filter((a) => a.resets5 && a.resets5 > now);
  const cooling = accts.filter((a) => a.state === "cooling").sort((a, b) => a.resets5 - b.resets5)[0];
  const pick = cooling || accts.sort((a, b) => a.resets5 - b.resets5)[0];
  if (!pick) return null;
  const lane = (pick.lanes || []).find((l) => l.state === "cooling" && l.task);
  return `NEXT RESET · ${pick.label.toUpperCase()} ${hhmm(pick.resets5)}${lane ? ` → resumes ${tnum({ id: lane.task })}` : ""}`;
}

/** "Needs you": server list when present (reviews, questions, alerts), else alerts only. */
export function needsYou(o) {
  if (Array.isArray(o.needsYou)) return o.needsYou;
  return (o.alerts || []).map((e) => ({ kind: "alert", project: e.project, task: e.task, title: e.text, text: e.type, eventId: e.id }));
}

export function needsKey(n) {
  const ico = { review: "▣", question: "?", alert: "!" }[n.kind] || "·";
  const cls = { review: "review", question: "q", alert: "alert" }[n.kind] || "";
  const sheet = n.kind === "review" ? "/review" : n.kind === "question" ? "/answer" : "";
  const href = n.project && n.task ? `#/p/${encodeURIComponent(n.project)}/t/${encodeURIComponent(n.task)}${sheet}` : "#/";
  return html`<a class="key ${cls}" href="${href}"><span class="ico" aria-label="${n.kind}">${ico}</span>${n.task ? html`<span class="num">${n.num ?? tnum({ id: n.task })}</span>` : ""}
    <span class="t"><b>${n.title}</b><small>${n.text || ""}</small></span></a>`;
}

function tile(p) {
  const c = p.counts || {};
  const cnt = [["▶", c.running], ["⏳", c.queued], ["▣", c.review], ["✋", c.blocked], ["⏸", c.limit_paused], ["!", c.hung], ["✓", c.doneToday]]
    .filter(([, v]) => v).map(([i, v]) => html`<span>${i}${v}</span>`);
  const inner = html`<span class="scope">${p.scope === "office" ? "OFFICE" : p.scope === "personal" ? "PERSONAL" : "UNTAGGED"}</span>
    <span class="ic" aria-hidden="true">${p.icon || "◆"}</span><b>${p.short || p.title}</b><small>${p.headline || ""}</small>
    <div class="cnt">${cnt.length ? cnt : html`<span>—</span>`}${p.alerts ? html`<span class="hot">!${p.alerts}</span>` : ""}</div>`;
  return p.enabled === false
    ? html`<div class="tile disabled" style="--c:${p.color || "#888"}" aria-disabled="true">${inner}</div>`
    : html`<a class="tile" style="--c:${p.color || "#888"}" href="#/p/${encodeURIComponent(p.id)}/board">${inner}</a>`;
}

export function overviewView(o, now, conn, openWorkers = new Set()) {
  setWorkerColors(o);
  const accts = o.accounts || [];
  const running = o.machine?.running ?? accts.reduce((s, a) => s + (a.lanes || []).filter((l) => l.state === "running").length, 0);
  const lanesTotal = o.machine?.lanesTotal ?? accts.reduce((s, a) => s + (a.capacity?.lanesMax || 0), 0);
  const ib = o.inbox || {};
  const reset = nextReset(o, now);
  const needs = needsYou(o);
  const head = lcd({
    left: "", mid: "AGENT DECK", right: dayLabel(now), big: hhmm(now), conn, lastUpdate: o.time,
    stats: [{ b: running, t: "RUNNING" }, { b: ib.review || 0, t: "REVIEW", hot: ib.review > 0 }, { b: ib.questions || 0, t: "?" }, { b: ib.alerts || 0, t: "!", hot: ib.alerts > 0 }],
    stats2: reset ? [{ t: reset }] : [],
  });
  return html`<div class="ov">
    <div class="ov-a">${head}
      ${label("ACCOUNTS · LANES", `${running}/${lanesTotal} JOBS`)}
      <div class="accts">${accts.map((a) => accountStrip(a, now))}</div>
      ${backlogBar(o)}
      ${workersSection(o, now, openWorkers, "studio")}
    </div>
    <div class="ov-b">${label("PROJECTS", (o.projects || []).filter((p) => p.enabled !== false).length)}
      <div class="tiles">${(o.projects || []).map(tile)}<div class="tile ghost" aria-hidden="true"><b>＋ add in config</b></div></div>
    </div>
    <div class="ov-c">${label("NEEDS YOU", needs.length)}
      <div class="list">${needs.length ? needs.map(needsKey) : empty("Nothing needs you right now.")}</div>
    </div>
  </div>`;
}

export function backlogBar(o) {
  const b = o.backlog;
  if (!b) return "";
  return html`<div class="backlog${b.low ? " low" : ""}"><span>${b.low ? "▲ " : ""}BACKLOG</span><b>${b.eligible} ready / target ${b.target}</b>${b.hint ? html`<small>${b.hint}</small>` : ""}</div>`;
}

export function accountsView(o, now, conn, openWorkers = new Set()) {
  setWorkerColors(o);
  const accts = o.accounts || [];
  const m = o.machine || {};
  const running = m.running ?? 0;
  const total = m.lanesTotal ?? accts.reduce((s, a) => s + (a.capacity?.lanesMax || 0), 0);
  const spare = accts.filter((a) => a.capacity?.spare && !a.capacity?.why);
  const spareLanes = spare.reduce((s, a) => s + Math.max(0, (a.capacity.lanesMax || 0) - (a.capacity.lanesUsed || 0)), 0);
  const head = lcd({
    left: html`<a href="#/">◀ ALL</a>`, mid: "CAPACITY", right: m.plannedAt ? `RE-PLANNED ${hhmm(m.plannedAt)}` : "",
    big: `${running}/${total} JOBS`, bigClass: "big mid", conn, lastUpdate: o.time,
    stats: [{ t: `MACHINE CAP ${m.maxParallelTotal ?? "—"}` }, { t: `RAM ${m.freeRamGb ?? "—"} GB FREE` }, { b: spareLanes, t: "SPARE LANES", hot: spareLanes > 0 }],
  });
  const pid = projectsById(o);
  return html`${head}
    ${accts.map((a) => accountBlock(a, now, pid, { worker: [...new Set((a.lanes || []).map((l) => l.worker))].join(" ") }))}
    ${workersSection(o, now, openWorkers, "studio")}
    ${backlogBar(o)}
    <div class="list">${spare.map((a) => html`<div class="key q"><span class="ico">⚡</span><span class="t"><b>Spare: ${a.label} · ${a.capacity.fitsSize || "?"} fits</b>
      <small>${a.capacity.spendable5 ?? "?"}% to spend before ${hhmm(a.resets5)}${o.backlog?.hint ? ` · ${o.backlog.hint}` : ""}</small></span></div>`)}</div>
    ${label("ALERTS", (o.alerts || []).length)}
    <div class="list">${(o.alerts || []).length ? o.alerts.map((e) => eventRow(e, { key: true })) : empty("No alerts.")}</div>`;
}

export { scopeBadge };
