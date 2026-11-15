// Appearance = layout × mode. Three layouts, each with light and dark token sets (themes.css) and its own
// structure (layouts/*.js + *.css); mode "auto" follows prefers-color-scheme. Both choices are persisted.
export const LAYOUTS = [
  { id: "studio", name: "Studio", note: "PhoneDeck hardware look: LCD, chunky keys", defaultMode: "light" },
  { id: "mission", name: "Mission Control", note: "Dense command centre: KPIs, rules, LEDs, ticker", defaultMode: "dark" },
  { id: "paper", name: "Paper", note: "Calm editorial: serif headlines, soft cards, rings", defaultMode: "light" },
];
export const MODES = [
  { id: "auto", name: "Auto" }, { id: "light", name: "Light" }, { id: "dark", name: "Dark" },
];
export const DEFAULT_PREF = { layout: "studio", mode: "auto" };
const KEY = "deck.appearance";
const OLD_KEY = "deck.theme";
// earlier single-theme ids (kept so a saved choice survives the upgrade)
const LEGACY = { deck: ["studio", "auto"], mission: ["mission", "dark"], "paper-light": ["paper", "light"], "paper-dark": ["paper", "dark"], auto: ["paper", "auto"], paper: ["paper", "auto"] };

export function normalise(p) {
  let layout = p?.layout, mode = p?.mode;
  if (!layout && p?.theme && LEGACY[p.theme]) [layout, mode] = LEGACY[p.theme];
  return {
    layout: LAYOUTS.some((l) => l.id === layout) ? layout : DEFAULT_PREF.layout,
    mode: MODES.some((m) => m.id === mode) ? mode : DEFAULT_PREF.mode,
  };
}

/** Quick toggles: ▦ cycles the layout, ◐ cycles the mode (Auto → Light → Dark). */
export function nextLayout(p) {
  const q = normalise(p), ids = LAYOUTS.map((l) => l.id);
  return { ...q, layout: ids[(ids.indexOf(q.layout) + 1) % ids.length] };
}
export function nextMode(p) {
  const q = normalise(p), ids = MODES.map((m) => m.id);
  return { ...q, mode: ids[(ids.indexOf(q.mode) + 1) % ids.length] };
}

/** Attributes for <html>: data-layout picks the structure + token set, data-mode the light/dark variant. */
export function resolve(p, prefersDark = false) {
  const { layout, mode } = normalise(p);
  const dark = mode === "dark" || (mode === "auto" && prefersDark);
  return { "data-layout": layout, "data-mode": dark ? "dark" : "light" };
}

export const label = (p) => {
  const { layout, mode } = normalise(p);
  return `${LAYOUTS.find((l) => l.id === layout).name} · ${MODES.find((m) => m.id === mode).name}`;
};

export function loadPref(storage) {
  try {
    const raw = storage?.getItem(KEY) ?? storage?.getItem(OLD_KEY);
    return normalise(JSON.parse(raw || "null"));
  } catch { return { ...DEFAULT_PREF }; }
}
export function savePref(storage, p) {
  try { storage?.setItem(KEY, JSON.stringify(normalise(p))); } catch { /* private mode: lasts this session */ }
}

/** Apply to the document (browser only). */
export function apply(doc, p, prefersDark) {
  const attrs = resolve(p, prefersDark);
  for (const [k, v] of Object.entries(attrs)) doc.documentElement.setAttribute(k, v);
  doc.documentElement.removeAttribute("data-theme");
  const meta = doc.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", getComputedStyle(doc.documentElement).getPropertyValue("--bg").trim() || "#0f0f10");
  return attrs;
}
