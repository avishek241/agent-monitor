import { WEB, fixture, s } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import http from "node:http";
import crypto from "node:crypto";
import { LAYOUTS, MODES, normalise, nextLayout, nextMode, resolve, loadPref, savePref, label } from "../../web/theme.js";
import { settingsView } from "../../web/views/settings.js";

/** Parse themes.css into the six token sets (layout × mode); variants inherit their layout's base block. */
function themeTokens() {
  const css = fs.readFileSync(path.join(WEB, "themes.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const raw = {};
  for (const m of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const sel = m[1].trim();
    const vars = Object.fromEntries([...m[2].matchAll(/--([\w-]+):\s*([^;]+);/g)].map((x) => [x[1], x[2].trim()]));
    const layout = sel.match(/data-layout="(\w+)"/)?.[1] || "studio";
    const mode = sel.match(/data-mode="(\w+)"/)?.[1] || "base";
    raw[`${layout}-${mode}`] = vars;
  }
  const base = raw["studio-base"];
  const lay = (l) => ({ ...base, ...(raw[`${l}-base`] || {}) });
  return {
    "studio-light": lay("studio"), "studio-dark": { ...lay("studio"), ...raw["studio-dark"] },
    "mission-dark": lay("mission"), "mission-light": { ...lay("mission"), ...raw["mission-light"] },
    "paper-light": lay("paper"), "paper-dark": { ...lay("paper"), ...raw["paper-dark"] },
  };
}
const rgb = (hex) => { const h = hex.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const mix = (c, pct) => c.map((v) => Math.round((v * pct) / 100)); // color-mix(c pct%, #000)

// text token on background token: every pair the components actually render
const PAIRS = [
  ["tx", "bg"], ["tx", "bg2"], ["tx", "card"], ["muted", "bg"], ["muted", "bg2"], ["muted", "card"], ["accent-tx", "card"], ["accent-tx", "bg"],
  ["on-accent", "accent"], ["on-ok", "ok"], ["ok-tx", "card"], ["bad-tx", "card"], ["bad-tx", "bg"], ["warn-tx", "card"], ["on-bad", "bad"],
  ["on-scope", "office"], ["on-scope", "personal"], ["on-chip-on", "chip-on"],
  ["lcd-tx", "lcd-bg"], ["lcd-muted", "lcd-bg"], ["lcd-hot", "lcd-bg"], ["on-ok", "ok"],
  ["tx", "tint-review"], ["muted", "tint-review"], ["tx", "tint-q"], ["muted", "tint-q"], ["tx", "tint-alert"], ["muted", "tint-alert"], ["bad-tx", "tint-alert"],
  ["tint-free-tx", "tint-free"], ["tint-cool-tx", "tint-cool"], ["tint-off-tx", "tint-off"],
];

const T = themeTokens();
const IDS = ["studio-light", "studio-dark", "mission-dark", "mission-light", "paper-light", "paper-dark"];

test("every token set defines every token the Studio set defines", () => {
  for (const id of IDS) for (const k of Object.keys(T["studio-light"])) assert.ok(T[id][k], `${id} --${k}`);
});

for (const id of IDS) {
  test(`WCAG AA (4.5:1) for every text/background pair in theme ${id}`, () => {
    const bad = [];
    for (const [fg, bg] of PAIRS) {
      const a = T[id][fg], b = T[id][bg];
      assert.match(a, /^#[0-9a-f]{6}$/i, `${id} --${fg}`); assert.match(b, /^#[0-9a-f]{6}$/i, `${id} --${bg}`);
      const r = contrast(rgb(a), rgb(b));
      if (r < 4.5) bad.push(`--${fg} on --${bg} = ${r.toFixed(2)}`);
    }
    assert.deepEqual(bad, []);
  });
  test(`project tiles keep white text at AA on every fixture colour in theme ${id}`, () => {
    const pct = parseInt(T[id]["tile-mix"], 10);
    for (const p of fixture("overview.json").projects) {
      const r = contrast([255, 255, 255], mix(rgb(p.color), pct));
      assert.ok(r >= 4.5, `${p.id} ${p.color} at ${pct}% = ${r.toFixed(2)}`);
    }
  });
}

test("readability tokens: phone body ≥ 15 px, line-height ≈ 1.45, 4/8 spacing scale, tap targets ≥ 44 px", () => {
  for (const id of IDS) {
    assert.ok(parseFloat(T[id]["fs-body"]) >= 15, `${id} fs-body`);
    assert.ok(Math.abs(parseFloat(T[id].lh) - 1.45) < 0.051, `${id} lh`);
    assert.ok(parseFloat(T[id].tap) >= 44, `${id} tap`);
    for (const k of ["s1", "s2", "s3", "s4", "s5", "s6", "gap-card"]) assert.equal(parseFloat(T[id][k]) % 4, 0, `${id} --${k} on the 4 px grid`);
  }
  const css = fs.readFileSync(path.join(WEB, "styles.css"), "utf8");
  assert.doesNotMatch(css, /(?:^|[;{\s])(?:color|background):\s*#(?!fff\b)/i, "components use colour tokens, not literals");
  assert.doesNotMatch(css, /\.key \.t b[^}]*nowrap/, "key titles wrap instead of truncating");
});

test("appearance preference: layout and mode chosen separately, cycled, resolved, persisted, migrated", () => {
  assert.deepEqual(LAYOUTS.map((l) => l.name), ["Studio", "Mission Control", "Paper"]);
  assert.deepEqual(MODES.map((m) => m.name), ["Auto", "Light", "Dark"]);
  assert.deepEqual(normalise({ layout: "nope", mode: "x" }), { layout: "studio", mode: "auto" });
  assert.deepEqual(normalise({ theme: "paper-dark" }), { layout: "paper", mode: "dark" }, "old single-theme value migrates");
  assert.deepEqual(nextLayout({ layout: "paper", mode: "dark" }), { layout: "studio", mode: "dark" });
  assert.deepEqual(nextMode({ layout: "mission", mode: "dark" }), { layout: "mission", mode: "auto" });
  assert.deepEqual(resolve({ layout: "mission", mode: "auto" }, false), { "data-layout": "mission", "data-mode": "light" });
  assert.deepEqual(resolve({ layout: "studio", mode: "auto" }, true), { "data-layout": "studio", "data-mode": "dark" });
  assert.deepEqual(resolve({ layout: "paper", mode: "light" }, true), { "data-layout": "paper", "data-mode": "light" });
  assert.equal(label({ layout: "mission", mode: "light" }), "Mission Control · Light");
  const mem = new Map();
  const store = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  savePref(store, { layout: "paper", mode: "dark" });
  assert.deepEqual(loadPref(store), { layout: "paper", mode: "dark" });
  mem.clear(); mem.set("deck.theme", JSON.stringify({ theme: "mission" }));
  assert.deepEqual(loadPref(store), { layout: "mission", mode: "dark" });
  assert.deepEqual(loadPref({ getItem: () => { throw new Error("blocked"); } }), { layout: "studio", mode: "auto" });
});

test("settings view: picks layout and mode separately and marks both", () => {
  const h = s(settingsView({ layout: "paper", mode: "dark" }));
  for (const l of LAYOUTS) assert.match(h, new RegExp(`data-layout-set="${l.id}" aria-pressed="${l.id === "paper"}"`));
  for (const m of MODES) assert.match(h, new RegExp(`data-mode-set="${m.id}" aria-pressed="${m.id === "dark"}"`));
  for (const n of ["Studio", "Mission Control", "Paper", "Auto", "Light", "Dark"]) assert.ok(h.includes(`>${n}<`), n);
});

// Screenshot check per theme in headless Chrome (skipped when Chrome is not installed).
const CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].find((p) => fs.existsSync(p));
test("screenshot per layout × mode: each renders the overview and looks different", { skip: !CHROME && "no Chrome" , timeout: 120000 }, async () => {
  const srv = http.createServer((req, res) => {
    const f = path.join(WEB, decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (!f.startsWith(WEB) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end(); }
    const type = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".json": "application/json", ".woff2": "font/woff2" }[path.extname(f)] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": type }); res.end(fs.readFileSync(f));
  });
  await new Promise((ok) => srv.listen(0, "127.0.0.1", ok));
  const port = srv.address().port;
  const out = fs.mkdtempSync(path.join(os.tmpdir(), "deck-shots-"));
  const hashes = new Set();
  try {
    for (const [t, m] of [["studio", "light"], ["studio", "dark"], ["mission", "dark"], ["mission", "light"], ["paper", "light"], ["paper", "dark"]]) {
      const png = path.join(out, `${t}-${m}.png`);
      const url = `http://127.0.0.1:${port}/index.html?fixtures=1&layout=${t}&mode=${m}#/`;
      // Chrome runs async in the test runner, so spawn it through a promise-friendly sync call per shot
      await new Promise((ok) => setImmediate(ok));
      const r = await new Promise((ok) => {
        import("node:child_process").then(({ execFile }) => execFile(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--user-data-dir=${path.join(out, `p-${t}-${m}`)}`,
          "--window-size=500,1200", "--virtual-time-budget=3000", `--screenshot=${png}`, url], { timeout: 60000 }, (err) => ok(err)));
      });
      assert.ok(fs.existsSync(png), `${t}/${m} screenshot (${r?.message || "ok"})`);
      const buf = fs.readFileSync(png);
      assert.ok(buf.length > 20000, `${t}/${m} screenshot is not blank (${buf.length} bytes)`);
      hashes.add(crypto.createHash("sha1").update(buf).digest("hex"));
    }
    assert.equal(hashes.size, 6, "all six layout × mode renders differ");
    if (process.env.DECK_SHOTS) for (const f of fs.readdirSync(out)) if (f.endsWith(".png")) fs.copyFileSync(path.join(out, f), path.join(process.env.DECK_SHOTS, f));
  } finally { srv.close(); fs.rmSync(out, { recursive: true, force: true }); }
});
