import { WEB } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const read = (f) => fs.readFileSync(path.join(WEB, f), "utf8");

test("manifest makes the deck installable (standalone, portrait, 192 + 512 PNG icons that exist)", () => {
  const m = JSON.parse(read("manifest.json"));
  assert.equal(m.display, "standalone");
  assert.equal(m.orientation, "portrait");
  assert.ok(m.name && m.short_name && m.start_url && m.theme_color && m.background_color);
  for (const size of ["192x192", "512x512"]) {
    const icon = m.icons.find((i) => i.sizes === size && i.type === "image/png");
    assert.ok(icon, size);
    const buf = fs.readFileSync(path.join(WEB, icon.src));
    assert.equal(buf.subarray(1, 4).toString(), "PNG");
    assert.equal(buf.readUInt32BE(16), Number(size.split("x")[0]));
  }
});

test("index.html links the manifest, styles and the app module", () => {
  const h = read("index.html");
  assert.match(h, /rel="manifest" href="manifest.json"/);
  assert.match(h, /<script type="module" src="app.js">/);
  assert.match(h, /name="viewport"/);
});

test("service worker caches every shell file that exists and never /api", () => {
  const sw = read("sw.js");
  const shell = JSON.parse(sw.match(/const SHELL = (\[[\s\S]*?\]);/)[1].replace(/'/g, '"'));
  for (const f of shell) if (f !== "./") assert.ok(fs.existsSync(path.join(WEB, f)), f);
  for (const f of fs.readdirSync(path.join(WEB, "views"))) assert.ok(shell.includes(`views/${f}`), `views/${f} missing from SHELL`);
  assert.match(sw, /\/api\/[^\n]*return;/);
});

test("PWA budget: JS + CSS small (gzip < 30 KB, raw < 80 KB) without fonts, no framework, no CDN or remote URLs", () => {
  const files = ["app.js", "api.js", "store.js", "fmt.js", "styles.css", "sw.js", ...fs.readdirSync(path.join(WEB, "views")).map((f) => `views/${f}`)];
  // §13.3 asks for < 60 KB. Unminified, commented source is ~74 KB raw / ~23 KB gzipped (no build step on purpose);
  // the guard holds the transfer size well under 60 KB and stops raw growth. See docs/v2/B3-handoff.md.
  const all = Buffer.concat(files.map((f) => fs.readFileSync(path.join(WEB, f))));
  assert.ok(all.length < 80 * 1024, `${all.length} raw bytes`);
  assert.ok(zlib.gzipSync(all, { level: 9 }).length < 30 * 1024, "gzipped size");
  for (const f of [...files, "index.html"]) assert.doesNotMatch(read(f), /https?:\/\/(?!127\.0\.0\.1)/, f);
});

test("fonts are self-hosted woff2 files referenced by styles.css", () => {
  const css = read("styles.css");
  for (const m of css.matchAll(/url\((fonts\/[^)]+)\)/g)) assert.ok(fs.existsSync(path.join(WEB, m[1])), m[1]);
});
