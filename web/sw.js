// Agent Deck service worker: cache the app shell only. Never cache /api (live state, actions, tokens).
// Network first for the shell so an update shows at once; the cache keeps the deck openable when offline,
// where it shows the OFFLINE badge instead of stale numbers dressed up as live.
const CACHE = "agent-deck-shell-v4";
const SHELL = ["./", "index.html", "styles.css", "themes.css", "theme.js", "workers.js", "app.js", "api.js", "store.js", "fmt.js",
  "views/common.js", "views/overview.js", "views/project.js", "views/task.js", "views/settings.js", "layouts/index.js", "layouts/mission.js", "layouts/paper.js", "layouts/mission.css", "layouts/paper.css",
  "manifest.json", "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png",
  "fonts/Doto.woff2", "fonts/JetBrainsMono.woff2", "fonts/SpaceGrotesk.woff2"];
self.addEventListener("install", (e) => e.waitUntil((async () => {
  const cache = await caches.open(CACHE);
  try { await cache.addAll(SHELL); } catch { /* partial shell is fine; fetch fills it */ }
  await self.skipWaiting();
})()));
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k.startsWith("agent-deck-shell-") && k !== CACHE) await caches.delete(k);
  await self.clients.claim();
})()));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin || e.request.method !== "GET") return;
  if (url.pathname.includes("/api/") || url.pathname.includes("/fixtures/")) return; // always live
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const key = e.request.mode === "navigate" ? "./" : e.request;
    try {
      const r = await fetch(e.request, { cache: "no-store" });
      if (r.ok) cache.put(key, r.clone());
      return r;
    } catch {
      return (await cache.match(key)) || (await cache.match(e.request, { ignoreSearch: true })) || Response.error();
    }
  })());
});
