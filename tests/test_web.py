"""W0 integration with B3: deckd serves web/ (types, gzip, ETag, budget) and the optional view-model fields the
Workers section and the needs-you list use (usage history, burn, time to limit, done today, queued, needsYou)."""
import gzip
import json
import urllib.error
import urllib.request
from pathlib import Path

from deck import clock, server, views, views_types
from tests.helpers import T0
from tests.test_api import ApiCase

WEB = Path(__file__).resolve().parent.parent / "web"
BUDGET = 60 * 1024


def eager_files():
    """JS + CSS a first load fetches: index.html's stylesheets plus the module graph from app.js, without the
    lazily imported layout renderers (mission.js / paper.js load only when that layout is chosen)."""
    import re
    html = (WEB / "index.html").read_text()
    css = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    import posixpath
    seen, todo = set(), ["app.js"]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        src = (WEB / f).read_text()
        for m in re.findall(r'^\s*import\s[^;]*?from\s+"(\.[^"]+)"', src, flags=re.M):
            todo.append(posixpath.normpath(posixpath.join(posixpath.dirname(f), m)))
    return sorted(css), sorted(seen)


class StaticFiles(ApiCase):
    def fetch(self, path, gz=True, headers=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if gz:
            r.add_header("Accept-Encoding", "gzip")
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()

    def test_types_gzip_and_etag(self):
        want = {"/": "text/html", "/app.js": "text/javascript", "/layouts/mission.js": "text/javascript",
                "/styles.css": "text/css", "/manifest.json": "application/manifest+json", "/sw.js": "text/javascript",
                "/fonts/IBMPlexMono-Regular.woff2": "font/woff2", "/icons/icon.svg": "image/svg+xml",
                "/icons/icon-192.png": "image/png", "/fixtures/overview.json": "application/json"}
        for path, ctype in want.items():
            st, h, body = self.fetch(path)
            self.assertEqual(st, 200, path)
            self.assertTrue(h["Content-Type"].startswith(ctype), (path, h["Content-Type"]))
            self.assertTrue(h.get("ETag"), path)
            src = (WEB / (path.lstrip("/") or "index.html")).read_bytes()
            if ctype.startswith(("font/", "image/png")):
                self.assertNotIn("Content-Encoding", h, path)              # already compressed
                self.assertEqual(body, src)
            elif len(src) >= server.GZIP_MIN:
                self.assertEqual(h.get("Content-Encoding"), "gzip", path)
                self.assertEqual(h.get("Vary"), "Accept-Encoding")
                self.assertEqual(gzip.decompress(body), src, path)
        st, h, body = self.fetch("/app.js", gz=False)                       # no Accept-Encoding: plain
        self.assertNotIn("Content-Encoding", h)
        self.assertEqual(body, (WEB / "app.js").read_bytes())
        st, h2, _ = self.fetch("/app.js", headers={"If-None-Match": h["ETag"]})
        self.assertEqual(st, 304)

    def test_traversal_and_missing(self):
        self.assertEqual(self.fetch("/../deck/store.py")[0], 404)
        self.assertEqual(self.fetch("/nope.js")[0], 404)

    def test_transfer_budget_per_layout_under_60kb(self):
        """§13.3: what deckd sends (gzip per response) for a first load stays under 60 KB of JS + CSS in each
        layout. Studio loads no renderer; Mission or Paper adds one lazily imported module."""
        css, js = eager_files()
        self.assertNotIn("layouts/mission.js", js)
        self.assertNotIn("layouts/paper.js", js)
        sizes = {}
        for f in css + js + ["layouts/mission.js", "layouts/paper.js"]:
            st, h, body = self.fetch("/" + f)
            self.assertEqual((st, h.get("Content-Encoding")), (200, "gzip"), f)
            sizes[f] = len(body)
        base = sum(sizes[f] for f in css + js)
        for layout, extra in (("studio", []), ("mission", ["layouts/mission.js"]), ("paper", ["layouts/paper.js"])):
            total = base + sum(sizes[f] for f in extra)
            self.assertLess(total, BUDGET, f"{layout}: {total} bytes gzipped")

    def test_every_font_referenced_by_css_is_served(self):
        import re
        css = (WEB / "styles.css").read_text()
        fonts = re.findall(r"url\((fonts/[^)]+\.woff2)\)", css)
        for fam in ("IBM Plex Mono", "Barlow Semi Condensed", "Fraunces", "Source Sans 3", "Space Grotesk"):
            self.assertIn(f'font-family: "{fam}"', css)
        for f in fonts:
            st, h, body = self.fetch("/" + f)
            self.assertEqual(st, 200, f)
            self.assertEqual(body[:4], b"wOF2", f)


class UsageSeries(ApiCase):
    def test_points_resets_burn_and_time_to_limit(self):
        now = T0
        rows = []
        # yesterday's window, then a reset, then the current window rising 12 points an hour
        for i in range(0, 20 * 60, 5):                       # every 5 min for 20 h, ending at now
            ts = now - 20 * 3600 + i * 60
            if ts < now - 3 * 3600:
                rows.append({"read_at": ts, "pct5": 50, "pct7": 30, "resets5": now - 3 * 3600})
            else:
                rows.append({"read_at": ts, "pct5": 12 * (ts - (now - 3 * 3600)) / 3600, "pct7": 31,
                             "resets5": now + 2 * 3600})
        rows.append({"read_at": now, "pct5": 36.0, "pct7": 31, "resets5": now + 2 * 3600})
        u = views.usage_series(rows, [], now)
        self.assertEqual(views_types.check_vm("UsageVM", u), [])
        self.assertLessEqual(len(u["points"]), 97)
        self.assertEqual(u["points"][-1]["ts"], now)
        self.assertEqual(u["resets"], [now - 3 * 3600, now + 2 * 3600])
        self.assertAlmostEqual(u["burn5PerHour"], 12.0, delta=0.2)
        self.assertEqual(u["minutesToLimit"], round((100 - 36) / u["burn5PerHour"] * 60))

    def test_unknown_burn(self):
        u = views.usage_series([{"read_at": T0 - 60, "pct5": 10, "pct7": 1, "resets5": T0 + 600}], [], T0)
        self.assertIsNone(u["burn5PerHour"])
        self.assertIsNone(u["minutesToLimit"])
        rolled = views.usage_series([{"read_at": T0 - 900, "pct5": 10, "resets5": T0 - 60},
                                     {"read_at": T0 - 60, "pct5": 40, "resets5": T0 - 60}], [], T0)
        self.assertIsNone(rolled["burn5PerHour"])                          # window rolled over: no estimate


class AccountExtras(ApiCase):
    def test_overview_serves_usage_done_today_queued_and_needs_you(self):
        for i, pct in enumerate((10, 16, 22)):
            self.g.usage.put({"account": "codex-a", "read_at": T0 - 1800 + i * 900, "source": "test", "pct5": pct,
                              "pct7": 20, "resets5": T0 + 7200, "resets7": T0 + 86400 * 3, "confidence": "high"})
        # a finished job today on codex-a
        t, att = self.running()
        self.ps().put_handoff(t["id"], att["n"], self.good_handoff())
        self.ps().transition(t["id"], "review", actor="ADP")
        self.ps().end_attempt(t["id"], att["n"], "handoff", 0)
        # a question and a queued task the planner listed for codex-a
        q, qa = self.running(worker="W1", lane=2, title="Map receipt columns", domains=["area:docs/**"])
        self.ps().card_op(q["id"], qa["n"], "ask", {"question": "Rows without a tax number: skip or keep?"}, qa["token"])
        self.ps().transition(q["id"], "blocked", actor="W1")
        w = self.queued(title="Clean supplier names", domains=["area:apps/web/**"])
        cap = {"lanesUsed": 0, "lanesMax": 3, "spare": False, "why": None,
               "queued": [{"project": "shop-ops", "task": w["id"], "num": int(w["id"][:3]), "title": w["title"],
                           "worker": "W1", "why": "waiting: no free lane on its workers"}]}
        self.g.account_state.put("codex-a", "ok", cap)
        st, vm = self.get("/api/overview")
        self.assertEqual(st, 200)
        self.assertEqual(views_types.check_vm("OverviewVM", vm), [])
        acc = {a["id"]: a for a in vm["accounts"]}
        a = acc["codex-a"]
        self.assertEqual(len(a["usage"]["points"]), 3)
        self.assertAlmostEqual(a["usage"]["burn5PerHour"], 24.0)            # 12 points in 30 min
        self.assertEqual(a["usage"]["minutesToLimit"], round((100 - 22) / 24 * 60))
        self.assertEqual(a["usage"]["resets"], [T0 + 7200])
        self.assertEqual(a["doneToday"], 1)
        self.assertEqual([x["task"] for x in a["queued"]], [w["id"]])
        self.assertNotIn("queued", a["capacity"])
        self.assertEqual(acc["claude-office"]["doneToday"], 0)
        self.assertIsNone(acc["claude-office"]["usage"])                   # no readings: hidden, not invented
        kinds = [(n["kind"], n["task"]) for n in vm["needsYou"]]
        self.assertEqual(kinds[0], ("question", q["id"]))
        self.assertIn(("review", t["id"]), kinds)
        rev = next(n for n in vm["needsYou"] if n["kind"] == "review")
        self.assertIn("1/1 done-when", rev["text"])
        self.assertIsInstance(rev["rev"], int)
        # TaskVM carries rev
        st, p = self.get("/api/projects/shop-ops")
        self.assertTrue(all(isinstance(x.get("rev"), int) for col in p["board"].values() for x in col))

    def test_cooling_account_has_no_burn_estimate(self):
        for i, pct in enumerate((60, 80, 95)):
            self.g.usage.put({"account": "claude-worker", "read_at": T0 - 1800 + i * 900, "source": "test",
                              "pct5": pct, "pct7": 20, "resets5": T0 + 3600, "resets7": T0 + 86400,
                              "confidence": "high"})
        self.g.limits.add("claude-worker", "5h", T0 - 60, T0 + 3600, "adapter")
        st, rows = self.get("/api/accounts")
        a = next(x for x in rows if x["id"] == "claude-worker")
        self.assertEqual(a["state"], "cooling")
        self.assertIsNone(a["usage"]["burn5PerHour"])
        self.assertIsNone(a["usage"]["minutesToLimit"])
        self.assertIn(T0 + 3600, a["usage"]["resets"])


if __name__ == "__main__":
    import unittest
    unittest.main()
