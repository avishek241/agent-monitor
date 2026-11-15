// Task detail and its sheets: Review, Reassign, Answer (design §13.2 screens 3–5).
// Action keys carry data-act; app.js turns them into api.actionBody(...) POSTs with the task's rev.
import { html, hhmm, dur, until, num, bumpPriority, stateWord, stateIcon, tnum, rateText } from "../fmt.js";
import { lcd, label, eventRow, empty } from "./common.js";

const backTo = (p, title) => html`<a href="#/p/${encodeURIComponent(p)}/board">◀ ${String(title).toUpperCase()}</a>`;

/** Which action keys make sense for a state. The server is still the judge (illegal → its reason is shown). */
export function actionsFor(state, { question = false } = {}) {
  const a = [];
  if (question) a.push("answer");
  if (["running", "starting", "queued", "ready"].includes(state)) a.push("pause");
  if (["blocked", "limit_paused"].includes(state) && !question) a.push("resume");
  if (["hung", "failed", "changes_requested"].includes(state)) a.push("retry");
  if (["review", "merging"].includes(state)) a.push("review");
  if (!["done", "cancelled", "merging", "review"].includes(state)) a.push("reassign", "prio-up", "prio-down", "cancel");
  return a;
}

function actionKeys(t, pid, acts) {
  const k = [];
  const link = (sheet, text, cls = "") => html`<a class="btn ${cls}" href="#/p/${encodeURIComponent(pid)}/t/${encodeURIComponent(t.id)}/${sheet}">${text}</a>`;
  for (const a of acts) {
    if (a === "pause") k.push(html`<button class="btn" data-act="pause">PAUSE</button>`);
    if (a === "resume") k.push(html`<button class="btn go" data-act="resume">RESUME</button>`);
    if (a === "retry") k.push(html`<button class="btn warn" data-act="retry">RETRY</button>`);
    if (a === "answer") k.push(link("answer", "ANSWER…", "warn"));
    if (a === "review") k.push(link("review", "REVIEW…", "warn"));
    if (a === "reassign") k.push(link("reassign", "REASSIGN"));
    if (a === "prio-up" && t.priority !== "P0") k.push(html`<button class="btn" data-act="priority" data-priority="${bumpPriority(t.priority, +1)}">PRIO ▲</button>`);
    if (a === "prio-down" && t.priority !== "P3") k.push(html`<button class="btn" data-act="priority" data-priority="${bumpPriority(t.priority, -1)}">PRIO ▼</button>`);
  }
  const main = html`<div class="btns">${k}</div>`;
  return acts.includes("cancel") ? html`${main}<div class="btns"><button class="btn danger hold" data-hold="cancel">HOLD TO CANCEL</button></div>` : main;
}

/** Merge the TaskVM (from the board) with the detail endpoint's task + card. */
function taskInfo(detail, tvm) {
  const t = { ...(detail?.task || {}), ...(tvm || {}) };
  if (detail?.task?.rev != null) t.rev = detail.task.rev; // detail is fresher than the board
  return t;
}

export function taskView(detail, tvm, vm, now, conn) {
  tvm = tvm || detail?.view; // B1 sends the TaskVM inside the detail as `view`
  const t = taskInfo(detail, tvm);
  if (!t.id) return html`${lcd({ left: "", mid: "TASK", right: "", big: "NOT FOUND", bigClass: "proj", conn })}${empty("This task is not in the deck any more.")}`;
  const p = vm?.project || { id: t.project, title: t.project };
  const card = detail?.card;
  const attempts = detail?.attempts || [];
  const cur = attempts[attempts.length - 1];
  const stats = [{ t: `${stateIcon(t.state)} ${stateWord(t.state).toUpperCase()}${t.stateSince ? ` ${dur(now - t.stateSince)}` : ""}` }];
  if (t.etaFinish) stats.push({ t: `ETA ~${dur(t.etaFinish - now)} (${hhmm(t.etaFinish)})`, hot: true });
  if (t.state === "limit_paused" && t.resumeAt) stats.push({ t: `RESUMES ${hhmm(t.resumeAt)} ${until(t.resumeAt, now)}`, hot: true });
  if (cur) stats.push({ t: `ATTEMPT ${cur.n}` });
  const head = lcd({
    left: backTo(p.id, p.short || p.title), mid: `${tnum(t)} · ${t.priority || ""} · ${t.size || ""}${t.kind === "pilot" ? " · PILOT" : ""}`,
    right: t.worker || "", big: String(t.title || "").toUpperCase(), bigClass: "proj small", conn, lastUpdate: vm?.time, stats,
  });
  const steps = card?.steps || [];
  const doneN = steps.filter((s) => s.status === "done").length;
  const added = steps.filter((s) => cur && s.attempt === cur.n && cur.n > 1).length;
  const stepRow = (s) => {
    const cls = { done: "d", active: "a", failed: "x", skipped: "p" }[s.status] || "p";
    const ico = { done: "✓", active: "◉", failed: "✗", skipped: "–" }[s.status] || "○";
    let tail = `~${s.etaMin}m`;
    if (s.status === "done" && s.startedAt && s.endedAt) tail = dur(s.endedAt - s.startedAt);
    if (s.status === "active" && s.startedAt) tail = `~${dur(Math.max(0, s.etaMin * 60 - (now - s.startedAt)))} left`;
    return html`<div class="${cls}">${ico} ${s.title} · ${tail}</div>`;
  };
  const metrics = card?.metrics?.length ? card.metrics : (t.metrics || []);
  const vmMetric = (label) => (tvm?.metrics || []).find((m) => m.label === label) || {};
  const q = t.question || (card?.question ? { text: card.question, options: [] } : null);
  return html`<div class="task-root" data-p="${p.id}" data-t="${t.id}" data-rev="${t.rev ?? ""}">${head}
    ${t.summary ? html`<p class="summary">${t.summary}</p>` : ""}
    ${q ? html`<a class="key q" href="#/p/${encodeURIComponent(p.id)}/t/${encodeURIComponent(t.id)}/answer"><span class="ico">?</span><span class="t"><b>${q.text}</b><small>tap to answer</small></span></a>` : ""}
    ${(t.warnings || []).map((w) => html`<div class="warnline">⚠ ${w}</div>`)}
    ${steps.length ? html`${label("STEPS", `${doneN}/${steps.length}`)}<div class="steps">${steps.map(stepRow)}
      ${added ? html`<div class="p small">${added} step${added > 1 ? "s" : ""} added by attempt ${cur.n}${attempts[cur.n - 2]?.outcome === "limit" ? " (resumed after limit)" : ""}</div>` : ""}</div>` : ""}
    ${metrics.length ? html`${label("NUMBERS")}${metrics.map((m) => {
      const x = { ...vmMetric(m.label), ...m };
      const bad = x.kind === "bad" && x.done > 0;
      return html`<div class="acct"><span class="n">${x.label}</span><span class="v${bad ? " hot" : ""}">${num(x.done)}${x.total ? ` / ${num(x.total)}` : ""}${rateText(x)}</span>
        ${x.total ? html`<span class="bar"><i style="width:${Math.min(100, Math.round((x.done / x.total) * 100))}%"></i></span>` : ""}</div>`;
    })}` : ""}
    ${card?.note ? html`<div class="note">“${card.note}”</div>` : ""}
    ${(t.deps || []).length ? html`${label("DEPENDS ON")}<div class="steps">${t.deps.map((d) => html`<div>${stateIcon(d.state)} ${tnum({ id: d.id })} · ${stateWord(d.state)}</div>`)}</div>` : ""}
    ${attempts.length ? html`${label("ATTEMPTS", attempts.length)}<div class="steps small">${attempts.map((a) => html`<div>#${a.n} ${a.worker} · ${a.account || ""} · ${hhmm(a.startedAt)}${a.endedAt ? `–${hhmm(a.endedAt)}` : " → now"}${a.outcome ? ` · ${a.outcome}` : ""}</div>`)}</div>` : ""}
    ${(detail?.events || []).length ? html`${label("LAST EVENTS")}<div class="evs">${detail.events.slice(0, 8).map((e) => eventRow(e))}</div>` : ""}
    ${actionKeys(t, p.id, actionsFor(t.state, { question: !!q }))}
  </div>`;
}

/** Review sheet (ReviewVM): Done-when with evidence, tests, risks, one-tap decisions, APPROVE / CHANGES. */
export function reviewSheet(r, vm, now, conn) {
  if (!r) return html`${lcd({ mid: "REVIEW", big: "NOTHING TO REVIEW", bigClass: "proj small", conn })}${empty("This task has no handoff waiting for review.")}`;
  const t = r.task;
  const p = vm.project;
  const met = (r.doneWhen || []).filter((d) => d.met).length;
  const g = r.git || {};
  const head = lcd({
    left: html`<a href="#/p/${encodeURIComponent(p.id)}/review">◀ REVIEW</a>`, mid: `${tnum(t)}${r.pilot ? " · PILOT" : ""}`, right: `WAITING ${dur(r.waitingSec).toUpperCase()}`,
    big: String(t.title).toUpperCase(), bigClass: "proj small", conn, lastUpdate: vm.time,
    stats: [{ t: `${r.worker || t.worker || ""} ${r.account ? r.account.toUpperCase() : ""}` }, { t: `${g.commits ?? 0} COMMITS` }, { t: `${g.filesChanged ?? 0} FILES` },
      { t: `${g.uncommitted ?? 0} UNCOMMITTED`, hot: !!g.uncommitted }],
  });
  return html`<div class="task-root sheet-root" data-p="${p.id}" data-t="${t.id}" data-rev="${t.rev ?? ""}">${head}
    <p class="summary">${r.summary}</p>
    ${r.outcome !== "done" ? html`<div class="warnline">Outcome: ${r.outcome}</div>` : ""}
    ${g.uncommitted ? html`<div class="warnline amber">${g.uncommitted} uncommitted change${g.uncommitted > 1 ? "s" : ""} will not be merged</div>` : ""}
    ${g.conflictsWithMain ? html`<div class="warnline">Conflicts with main — merge will need a rebase</div>` : ""}
    ${label("DONE WHEN", `${met}/${(r.doneWhen || []).length}`)}
    <div class="chk">${(r.doneWhen || []).map((d) => html`<span class="${d.met ? "y" : "n"}">${d.met ? "✓" : "✗"}</span><span>${d.item}</span><small>${d.evidence}</small>`)}</div>
    ${(r.tests || []).length ? html`${label("TESTS")}<div class="chk">${r.tests.map((x) => html`<span class="${x.result === "pass" ? "y" : x.result === "fail" ? "n" : ""}">${x.result === "pass" ? "✓" : x.result === "fail" ? "✗" : "–"}</span><span class="mono">${x.command}</span><small>${x.result}${x.passed != null ? ` · ${x.passed} passed` : ""}${x.failed ? ` · ${x.failed} failed` : ""}${x.note ? ` · ${x.note}` : ""}</small>`)}</div>` : ""}
    ${(r.risks || []).length ? html`${label("RISKS")}<ul class="risks">${r.risks.map((x) => html`<li>${x}</li>`)}</ul>` : ""}
    ${(r.decisionsNeeded || []).length ? html`${label("DECISIONS NEEDED", r.decisionsNeeded.length)}${r.decisionsNeeded.map((d, i) => html`<div class="decision"><div>${d.question}</div>
      <div>${(d.options || []).map((o) => html`<button class="chip${o === d.default ? " on" : ""}" data-act="answer" data-decision="${i}" data-option="${o}">${o}${o === d.default ? " (default)" : ""}</button>`)}</div></div>`)}` : ""}
    <textarea id="note" class="notefield" placeholder="What should change? (needed for CHANGES)" rows="2"></textarea>
    <div class="btns"><button class="btn warn" data-act="changes">CHANGES…</button><button class="btn go" data-act="approve">APPROVE ✓</button></div>
    <div class="btns"><button class="btn danger hold" data-hold="reject">HOLD TO REJECT</button></div>
    ${(r.unlocks || []).length ? html`<div class="hint">Approving queues the merge and unlocks ${r.unlocks.map((u) => tnum({ id: u })).join(", ")}</div>` : ""}
  </div>`;
}

/**
 * Reassign sheet: every worker is a key. Disallowed workers stay visible, greyed, with a lock and the
 * server's reason verbatim; they carry no data-act, so they can't be tapped (design §2.3 point 6).
 */
export function reassignSheet(t, eligible, vm, now, conn) {
  const p = vm?.project || { id: t?.project, title: t?.project };
  const head = lcd({
    left: html`<a href="#/p/${encodeURIComponent(p.id)}/t/${encodeURIComponent(t.id)}">◀ ${tnum(t)}</a>`, mid: `${p.scope === "office" ? "OFFICE" : "PERSONAL"} PROJECT`,
    right: (p.short || p.title || "").toUpperCase(), big: String(t.title || "").toUpperCase(), bigClass: "proj small", conn, lastUpdate: vm?.time,
  });
  const ico = (w) => (w.allowed ? (w.best ? "🟢" : "🟡") : w.kind === "cooling" ? "⏸" : "🔒");
  return html`<div class="task-root sheet-root" data-p="${p.id}" data-t="${t.id}" data-rev="${t.rev ?? ""}">${head}
    <div class="sheet">${label("RUN ON WHICH WORKER?")}
      ${(eligible || []).map((w) => w.allowed
        ? html`<button class="wk" data-act="reassign" data-worker="${w.worker}"><span class="lock">${ico(w)}</span><span class="t"><b>${w.worker} · ${w.label}</b><small>${w.scope === "office" ? "OFFICE" : "PERSONAL"}${w.detail ? ` · ${w.detail}` : ""}</small></span><span>›</span></button>`
        : html`<div class="wk off" aria-disabled="true"><span class="lock">${ico(w)}</span><span class="t"><b>${w.worker} · ${w.label}</b><small>${w.reason}</small></span></div>`)}
      <div class="hint">Greyed rows can't be tapped. Trying through the CLI or API is refused and logged as an alert.</div>
    </div></div>`;
}

/** Answer sheet for a worker question: one-tap options plus free text. */
export function answerSheet(t, vm, now, conn) {
  const p = vm?.project || { id: t.project, title: t.project };
  const q = t.question || { text: t.stateReason || "Question", options: [] };
  const head = lcd({
    left: html`<a href="#/p/${encodeURIComponent(p.id)}/t/${encodeURIComponent(t.id)}">◀ ${tnum(t)}</a>`, mid: "QUESTION", right: t.worker || "",
    big: String(t.title || "").toUpperCase(), bigClass: "proj small", conn, lastUpdate: vm?.time,
  });
  return html`<div class="task-root sheet-root" data-p="${p.id}" data-t="${t.id}" data-rev="${t.rev ?? ""}">${head}
    <div class="sheet">${label(`${t.worker || "WORKER"} ASKS`)}<p class="summary">${q.text}</p>
      <div>${(q.options || []).map((o) => html`<button class="chip${o === q.default ? " on" : ""}" data-act="answer" data-note="${o}">${o}${o === q.default ? " (default)" : ""}</button>`)}</div>
      <textarea id="note" class="notefield" placeholder="Or type an answer" rows="2"></textarea>
      <div class="btns"><button class="btn go" data-act="answer">SEND ANSWER</button></div></div></div>`;
}
