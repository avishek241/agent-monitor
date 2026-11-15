// Agent Deck PWA: DOM glue. Data → views/*.js (pure) → #app. Only api.js talks to the network.
import { createApi, actionBody } from "./api.js";
import { parseRoute, routeHash, neighbourProject, findTask, serverNow } from "./store.js";
import { html, esc } from "./fmt.js";
import { overviewView, accountsView } from "./views/overview.js";
import { projectView } from "./views/project.js";
import { taskView, reviewSheet, reassignSheet, answerSheet } from "./views/task.js";
import { settingsView } from "./views/settings.js";
import * as theme from "./theme.js";

const qs = new URLSearchParams(location.search);
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const FIXTURES = qs.get("fixtures") === "1";
const base = location.pathname.replace(/[^/]*$/, "");
let api = createApi({ base, fixtures: FIXTURES, token: store.get("deck.token") });

// Theme: tokens in themes.css; the choice is persisted per device and Paper "auto" follows the OS.
const darkQuery = window.matchMedia?.("(prefers-color-scheme: dark)");
let themePref = theme.loadPref(window.localStorage);
function setTheme(p) {
  themePref = theme.normalise(p);
  theme.savePref(window.localStorage, themePref);
  theme.apply(document, themePref, !!darkQuery?.matches);
  if (parseRoute(location.hash).view === "settings") render();
}
// ?theme=mission&mode=dark previews a theme (screenshots, sharing a link) and remembers it.
if (qs.get("theme")) { themePref = theme.normalise({ theme: qs.get("theme"), mode: qs.get("mode") || themePref.mode }); theme.savePref(window.localStorage, themePref); }
theme.apply(document, themePref, !!darkQuery?.matches);
darkQuery?.addEventListener?.("change", () => theme.apply(document, themePref, darkQuery.matches));

const S = { overview: null, ovAt: 0, projects: {}, details: {}, eligible: {}, conn: FIXTURES ? "fixtures" : "live", lastEventId: 0 };
const $app = document.getElementById("app");
const nowLocal = () => Date.now() / 1000;
const now = () => (S.overview ? serverNow(S.overview.time, S.ovAt) : Math.round(nowLocal()));

function toast(text, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.className = `toast show ${kind}`;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.className = "toast"), kind === "err" ? 6000 : 3000);
}

async function load(route, force = false) {
  const jobs = [];
  if (force || !S.overview) jobs.push(api.overview().then((o) => { S.overview = o; S.ovAt = nowLocal(); }));
  const p = route.project;
  if (p && (force || !S.projects[p])) jobs.push(api.project(p).then((vm) => { S.projects[p] = vm; }));
  if (route.view === "task") {
    const k = `${p}/${route.task}`;
    if (force || !S.details[k]) jobs.push(api.task(p, route.task).then((d) => { S.details[k] = d; }));
    if (route.sheet === "reassign" && (force || !S.eligible[k])) jobs.push(api.eligible(p, route.task).then((e) => { S.eligible[k] = e; }));
  }
  await Promise.all(jobs);
}

function view(route) {
  const t = now();
  const o = S.overview;
  if (route.view === "settings") return settingsView(themePref, { token: store.get("deck.token") || "", fixtures: FIXTURES, conn: S.conn });
  if (!o) return html`<div class="empty">Loading…</div>`;
  if (route.view === "accounts") return accountsView(o, t, S.conn);
  const vm = S.projects[route.project];
  if (route.view === "project") {
    if (!vm) return html`<div class="empty">No data for project ${route.project}. <a href="#/">Back to all projects</a></div>`;
    return projectView(vm, o, route.tab, t, S.conn);
  }
  if (route.view === "task") {
    const k = `${route.project}/${route.task}`;
    const tvm = findTask(vm, route.task);
    const detail = S.details[k];
    const merged = { ...(detail?.task || {}), ...(tvm || {}), project: route.project };
    if (detail?.task?.rev != null) merged.rev = detail.task.rev;
    if (route.sheet === "review") return reviewSheet((vm?.inbox || []).find((r) => r.task.id === route.task), vm, t, S.conn);
    if (route.sheet === "reassign") return reassignSheet(merged, S.eligible[k], vm, t, S.conn);
    if (route.sheet === "answer") return answerSheet(merged, vm, t, S.conn);
    return taskView(detail, tvm, vm, t, S.conn);
  }
  return overviewView(o, t, S.conn);
}

let renderSeq = 0;
async function render(force = false, top = false) {
  const route = parseRoute(location.hash);
  const seq = ++renderSeq;
  try {
    await load(route, force);
  } catch (e) {
    if (e.code === "auth" && route.view !== "settings") { location.hash = "#/settings"; return; }
    if (S.overview) setConn("offline", false);
    else { $app.innerHTML = String(html`<div class="empty">Can't reach the deck (${e.message}). Retrying…</div>`); return; }
  }
  if (seq !== renderSeq) return;
  const keepNote = document.getElementById("note")?.value;
  const scroll = window.scrollY;
  $app.innerHTML = String(view(route));
  if (keepNote) { const n = document.getElementById("note"); if (n) n.value = keepNote; }
  window.scrollTo(0, top ? 0 : scroll);
  document.body.dataset.view = route.view;
}

function setConn(c, rerender = true) {
  if (S.conn === c) return;
  S.conn = c;
  if (rerender) render();
}

// ---- actions -------------------------------------------------------------------------------------
async function runAction(el, act) {
  const root = el.closest(".task-root");
  if (!root) return;
  const { p, t } = root.dataset;
  const rev = Number(root.dataset.rev);
  const note = document.getElementById("note")?.value.trim();
  const extra = { ...el.dataset };
  delete extra.act; delete extra.hold;
  if (extra.decision != null) extra.decision = Number(extra.decision);
  if ((act === "changes" || act === "answer" || act === "reject") && !extra.note && note) extra.note = note;
  let body;
  try { body = actionBody(act, rev, extra); } catch (e) { toast(e.message, "err"); document.getElementById("note")?.focus(); return; }
  el.disabled = true;
  const res = await api.action(p, t, body);
  el.disabled = false;
  if (res.simulated) { toast(`Fixtures: would POST ${JSON.stringify(body)}`); console.info("deck action", p, t, body); return; }
  if (!res.ok) { toast(res.reason || res.code || "Refused", "err"); if (res.code === "stale") render(true); return; }
  toast(`${act} ✓`);
  if (act === "approve" || act === "changes" || act === "reject" || act === "reassign") location.hash = routeHash({ view: "project", project: p, tab: "board" });
  render(true);
}

document.addEventListener("click", (ev) => {
  const swipe = ev.target.closest("[data-swipe]");
  if (swipe) { ev.preventDefault(); goProject(+1); return; }
  const th = ev.target.closest("[data-theme-cycle],[data-theme-set],[data-mode-set]");
  if (th) {
    const d = th.dataset;
    const next = d.themeSet ? { ...themePref, theme: d.themeSet } : d.modeSet ? { ...themePref, mode: d.modeSet } : theme.nextPref(themePref);
    setTheme(next);
    toast(`Theme: ${theme.label(next)}`);
    return;
  }
  const pair = ev.target.closest("[data-pair]");
  if (pair) { pairAction(pair.dataset.pair); return; }
  const el = ev.target.closest("[data-act]");
  if (el && !el.closest("[aria-disabled=true]")) { ev.preventDefault(); runAction(el, el.dataset.act); }
});

function pairAction(what) {
  if (what === "save") { store.set("deck.token", document.getElementById("tok").value.trim() || null); toast("Saved"); }
  if (what === "clear") { store.set("deck.token", null); toast("Forgotten"); }
  if (what === "notify" && "Notification" in window) Notification.requestPermission().then((p) => toast(`Notifications: ${p}`));
  api = createApi({ base, fixtures: FIXTURES, token: store.get("deck.token") });
  if (what === "save" || what === "clear") location.hash = "#/";
}

// Hold-to-confirm for destructive keys (cancel, reject): 1.2 s press, released early = nothing.
let hold = null;
document.addEventListener("pointerdown", (ev) => {
  const el = ev.target.closest("[data-hold]");
  if (!el) return;
  el.classList.add("holding");
  hold = { el, timer: setTimeout(() => { el.classList.remove("holding"); hold = null; runAction(el, el.dataset.hold); }, 1200) };
});
const release = () => { if (hold) { clearTimeout(hold.timer); hold.el.classList.remove("holding"); hold = null; } };
["pointerup", "pointerleave", "pointercancel"].forEach((e) => document.addEventListener(e, release));

// Swipe left/right on a project page moves to the next/previous project.
function goProject(dir) {
  const r = parseRoute(location.hash);
  const next = neighbourProject(S.overview?.projects, r.project, dir);
  if (next && next !== r.project) location.hash = routeHash({ view: "project", project: next, tab: r.tab || "board" });
}
let touch = null;
document.addEventListener("touchstart", (e) => { const p = e.touches[0]; touch = { x: p.clientX, y: p.clientY }; }, { passive: true });
document.addEventListener("touchend", (e) => {
  if (!touch || parseRoute(location.hash).view !== "project") return;
  const p = e.changedTouches[0];
  const dx = p.clientX - touch.x, dy = p.clientY - touch.y;
  touch = null;
  if (Math.abs(dx) > 70 && Math.abs(dy) < 50) goProject(dx < 0 ? +1 : -1);
});

// ---- live updates ----------------------------------------------------------------------------------
let pending = null;
function onEvent(e) {
  S.lastEventId = Math.max(S.lastEventId, e.id || 0);
  if ((e.severity === "action" || e.severity === "alert") && "Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification(`Agent Deck${e.project ? ` · ${e.project}` : ""}`, { body: e.text, tag: `${e.type}:${e.task || ""}` }); } catch { /* unsupported */ }
  }
  clearTimeout(pending);
  pending = setTimeout(() => render(true), 300); // coalesce bursts
}
const ctl = new AbortController();
api.stream({ since: 0, onEvent, onState: (s) => setConn(s), signal: ctl.signal });
setInterval(() => render(S.conn !== "live" && !FIXTURES), 15000); // countdowns tick; poll while SSE is down
window.addEventListener("hashchange", () => render(false, true));
render(false, true);

if ("serviceWorker" in navigator && !FIXTURES) navigator.serviceWorker.register("sw.js").catch(() => {});
window.__deck = { S, esc }; // for debugging in the console
