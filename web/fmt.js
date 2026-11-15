// Pure formatting helpers shared by every view. No DOM, so node --test can import them.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Escape any value for HTML text or attribute use. null/undefined → "". */
export const esc = (v) => (v == null ? "" : String(v).replace(/[&<>"']/g, (c) => ESC[c]));

/** Tagged template that escapes interpolations unless they are wrapped with raw(). */
export function html(strings, ...vals) {
  let out = strings[0];
  vals.forEach((v, i) => {
    out += Array.isArray(v) ? v.map(unwrap).join("") : unwrap(v);
    out += strings[i + 1];
  });
  return new Raw(out);
}
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(String(s ?? ""));
const unwrap = (v) => (v instanceof Raw ? v.s : v === false || v == null ? "" : esc(v));

const pad = (n) => String(n).padStart(2, "0");
/** Local wall-clock HH:MM for a unix-seconds epoch. */
export const hhmm = (ts) => {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
export const hhmmss = (ts) => {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
/** Compact duration from seconds: 45s, 9m, 3h20, 2d4h. */
export function dur(sec) {
  if (sec == null || !isFinite(sec)) return "—";
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec}s`;
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), mm = m % 60;
  if (h < 48) return mm ? `${h}h${pad(mm)}` : `${h}h`;
  return `${Math.floor(h / 24)}d${h % 24}h`;
}
/** "in 9m" / "9m ago" relative to now. */
export const until = (ts, now) => (ts == null ? "—" : ts >= now ? `in ${dur(ts - now)}` : `${dur(now - ts)} ago`);
export const ago = (sec) => (sec == null ? "—" : `${dur(sec)} ago`);
export const dayLabel = (ts) =>
  new Date(ts * 1000).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" }).toUpperCase().replace(",", "");

/** Number with thousands separators; decimals kept to one place. */
export const num = (v) => (v == null ? "—" : Number.isInteger(v) ? v.toLocaleString("en-US") : v.toFixed(1));
export const pct = (v) => (v == null ? "—" : `${Math.round(v)}%`);
/** Usage bar colour: green < 60, amber < 90, red ≥ 90 (same thresholds as v1). */
export const usageColor = (p) => (p == null ? "var(--muted)" : p >= 90 ? "var(--bad)" : p >= 60 ? "var(--warn)" : "var(--ok)");

export const SCOPE = { office: { short: "O", word: "OFFICE" }, personal: { short: "P", word: "PERSONAL" } };
export const scopeWord = (s) => SCOPE[s]?.word ?? "UNTAGGED";

/** State → icon + word (colour is never the only cue, §13.3). */
export const STATE = {
  draft: ["✎", "draft"], ready: ["⏳", "ready"], queued: ["⏳", "queued"], starting: ["▶", "starting"],
  running: ["▶", "running"], blocked: ["✋", "blocked"], limit_paused: ["⏸", "limit"], hung: ["!", "hung"],
  review: ["▣", "review"], changes_requested: ["↺", "changes"], merging: ["⇄", "merging"], done: ["✓", "done"],
  failed: ["✗", "failed"], cancelled: ["–", "cancelled"],
};
export const stateIcon = (s) => STATE[s]?.[0] ?? "·";
export const stateWord = (s) => STATE[s]?.[1] ?? s;

/** Next priority up/down: P0 is highest. */
export function bumpPriority(p, dir) {
  const n = Math.min(3, Math.max(0, Number(String(p || "P2").slice(1)) + (dir > 0 ? -1 : 1)));
  return `P${n}`;
}
/** Short task number for chips: num if present, else the id prefix before the first dash. */
export const tnum = (t) => (t?.num != null ? String(t.num) : String(t?.id ?? t?.task ?? "").split("-")[0]);
