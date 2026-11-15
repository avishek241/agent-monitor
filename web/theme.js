// Theme choice: tokens live in themes.css; this module only picks which token set is active and remembers it.
export const THEMES = [
  { id: "deck", name: "Deck", note: "Studio hardware look (default)" },
  { id: "mission", name: "Mission Control", note: "Dense, dark, neon on charcoal" },
  { id: "paper", name: "Paper", note: "Calm editorial; light, dark or auto" },
];
export const MODES = ["auto", "light", "dark"];
export const DEFAULT_PREF = { theme: "deck", mode: "auto" };
const KEY = "deck.theme";

/** Validate a stored preference; anything unknown falls back to the default. */
export function normalise(p) {
  const theme = THEMES.some((t) => t.id === p?.theme) ? p.theme : DEFAULT_PREF.theme;
  const mode = MODES.includes(p?.mode) ? p.mode : DEFAULT_PREF.mode;
  return { theme, mode };
}

/** Quick toggle order: Deck → Mission Control → Paper light → Paper dark → Deck. */
export function nextPref(p) {
  const { theme, mode } = normalise(p);
  if (theme === "deck") return { theme: "mission", mode };
  if (theme === "mission") return { theme: "paper", mode: "light" };
  if (theme === "paper" && mode !== "dark") return { theme: "paper", mode: "dark" };
  return { theme: "deck", mode: "auto" };
}

/** The attributes to put on <html>. Only Paper has a dark variant; "auto" follows the OS. */
export function resolve(p, prefersDark = false) {
  const { theme, mode } = normalise(p);
  const dark = theme === "paper" && (mode === "dark" || (mode === "auto" && prefersDark));
  return { "data-theme": theme, "data-mode": dark ? "dark" : "light" };
}

export const label = (p) => {
  const { theme, mode } = normalise(p);
  const t = THEMES.find((x) => x.id === theme).name;
  return theme === "paper" ? `${t} · ${mode}` : t;
};

export function loadPref(storage) {
  try { return normalise(JSON.parse(storage?.getItem(KEY) || "null")); } catch { return { ...DEFAULT_PREF }; }
}
export function savePref(storage, p) {
  try { storage?.setItem(KEY, JSON.stringify(normalise(p))); } catch { /* private mode: choice lasts this session */ }
}

/** Apply to the document (browser only). */
export function apply(doc, p, prefersDark) {
  const attrs = resolve(p, prefersDark);
  for (const [k, v] of Object.entries(attrs)) doc.documentElement.setAttribute(k, v);
  const meta = doc.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", getComputedStyle(doc.documentElement).getPropertyValue("--lcd-bg").trim() || "#0f0f10");
  return attrs;
}
