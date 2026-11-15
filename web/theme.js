// Theme choice: tokens live in themes.css; this module only picks which token set is active and remembers it.
// Four themes plus Auto. Auto follows prefers-color-scheme: Paper light by day, Paper dark by night
// (Paper is the only theme designed in both; reversible decision, see docs/v2/B3-handoff.md).
export const THEMES = [
  { id: "deck", name: "Deck", note: "Studio hardware look (default)" },
  { id: "mission", name: "Mission Control", note: "Dense, dark, neon on charcoal" },
  { id: "paper-light", name: "Paper light", note: "Calm editorial, light" },
  { id: "paper-dark", name: "Paper dark", note: "Calm editorial, dark" },
  { id: "auto", name: "Auto", note: "Follows the system: Paper light or Paper dark" },
];
export const DEFAULT_PREF = { theme: "deck" };
const KEY = "deck.theme";
const LEGACY = { paper: "paper-light" };

/** Validate a stored preference; anything unknown falls back to the default. */
export function normalise(p) {
  let theme = p?.theme;
  if (theme === "paper" && p?.mode === "dark") theme = "paper-dark";
  theme = LEGACY[theme] || theme;
  return { theme: THEMES.some((t) => t.id === theme) ? theme : DEFAULT_PREF.theme };
}

/** Quick toggle order: Deck → Mission Control → Paper light → Paper dark → Auto → Deck. */
export function nextPref(p) {
  const ids = THEMES.map((t) => t.id);
  return { theme: ids[(ids.indexOf(normalise(p).theme) + 1) % ids.length] };
}

/** The attributes to put on <html>: data-theme is the token set, data-mode picks Paper's dark variant. */
export function resolve(p, prefersDark = false) {
  const { theme } = normalise(p);
  if (theme === "paper-light") return { "data-theme": "paper", "data-mode": "light" };
  if (theme === "paper-dark") return { "data-theme": "paper", "data-mode": "dark" };
  if (theme === "auto") return { "data-theme": "paper", "data-mode": prefersDark ? "dark" : "light" };
  return { "data-theme": theme, "data-mode": "light" };
}

export const label = (p) => THEMES.find((x) => x.id === normalise(p).theme).name;

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
