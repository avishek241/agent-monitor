"""T-API: every endpoint vs the view-model contract, SSE resume by Last-Event-ID, token rules, error shapes."""
import json
import threading
import unittest
import urllib.error
import urllib.request

from deck import server, views_types
from tests.helpers import DeckCase


class ApiCase(DeckCase):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server(self.cfg, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        self.token = server.local_token(self.cfg.state_dir)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.srv.app.g.close()
        for s in self.srv.app.views.stores.values():
            s.close()
        super().tearDown()

    def req(self, method, path, body=None, token=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw) if "json" in ctype else raw.decode())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body, **kw):
        return self.req("POST", path, body, **kw)


class TestViews(ApiCase):
    def test_overview(self):
        self.running()
        st, vm = self.get("/api/overview")
        self.assertEqual(st, 200)
        self.assertEqual(views_types.check_vm("OverviewVM", vm), [])
        acc = {a["id"]: a for a in vm["accounts"]}
        self.assertIn("claude-office", acc, "the office account is always shown")
        self.assertEqual(acc["claude-office"]["scope"], "office")
        self.assertEqual(acc["codex-a"]["capacity"]["lanesUsed"], 1)
        lane = next(l for l in acc["codex-a"]["lanes"] if l["state"] == "running")
        self.assertEqual(lane["project"], "shop-ops")
        self.assertEqual(lane["title"], "Import the supplier list")
        tile = next(p for p in vm["projects"] if p["id"] == "shop-ops")
        self.assertEqual(tile["counts"]["running"], 1)
        self.assertEqual(vm["machine"]["running"], 1)

    def test_accounts_usage_and_cooling(self):
        self.g.usage.put({"account": "codex-a", "read_at": 1794760000 - 60, "source": "rollout", "pct5": 52, "pct7": 40,
                          "resets5": 1794763600, "resets7": 1795000000, "confidence": "high"})
        self.g.limits.add("claude-worker", "5h", 1794759000, 1794771060, "adapter")
        st, rows = self.get("/api/accounts")
        self.assertEqual(st, 200)
        acc = {a["id"]: a for a in rows}
        for a in rows:
            self.assertEqual(views_types.check_vm("AccountVM", a), [])
        self.assertEqual(acc["codex-a"]["pct5"], 52)
        self.assertEqual(acc["codex-a"]["readAgeSec"], 60)
        self.assertEqual(acc["codex-a"]["confidence"], "high")
        self.assertEqual(acc["claude-worker"]["state"], "cooling")
        self.assertEqual(acc["claude-worker"]["pct5"], 100)
        self.assertTrue(all(l["state"] == "cooling" for l in acc["claude-worker"]["lanes"]))
        self.assertIsNone(acc["claude-office"]["pct5"], "no reading is unknown, never 0 %")
        self.assertEqual(acc["claude-office"]["confidence"], "unknown")

    def test_project_page(self):
        t, att = self.running()
        q = self.queued()
        st, vm = self.get("/api/projects/shop-ops")
        self.assertEqual(st, 200)
        self.assertEqual(views_types.check_vm("ProjectVM", vm), [])
        self.assertEqual([x["id"] for x in vm["board"]["running"]], [t["id"]])
        self.assertEqual([x["id"] for x in vm["board"]["upNext"]], [q["id"]])
        w = {x["worker"]: x for x in vm["workers"]}
        self.assertFalse(w["W4"]["allowed"])
        self.assertEqual(w["W4"]["reason"], "office account may never run personal projects")
        self.assertTrue(any(d["name"] == "area:apps/api/**" and d["holder"] == t["id"] for d in vm["domains"]))

    def test_review_inbox(self):
        t, att = self.running(kind="pilot")
        s = self.ps()
        s.put_handoff(t["id"], 1, self.good_handoff())
        s.transition(t["id"], "review", actor="SUP")
        st, vm = self.get("/api/projects/shop-ops")
        self.assertEqual(len(vm["inbox"]), 1)
        r = vm["inbox"][0]
        self.assertEqual(views_types.check_vm("ReviewVM", r), [])
        self.assertTrue(r["pilot"])
        self.assertEqual(r["outcome"], "done")
        st, ov = self.get("/api/overview")
        self.assertEqual(ov["inbox"]["review"], 1)
        self.assertIn("pilot ready to review", next(p for p in ov["projects"] if p["id"] == "shop-ops")["headline"])

    def test_task_detail_and_eligible(self):
        t, att = self.running()
        self.ps().card_op(t["id"], 1, "step.add", {"title": "Read sources", "eta": 10}, att["token"])
        st, vm = self.get(f"/api/projects/shop-ops/tasks/{t['id']}")
        self.assertEqual(st, 200)
        self.assertEqual(views_types.check_vm("TaskDetailVM", vm), [])
        self.assertEqual(vm["card"]["steps"][0]["title"], "Read sources")
        self.assertEqual(views_types.check_vm("TaskVM", vm["view"]), [])
        st, el = self.get(f"/api/projects/shop-ops/tasks/{t['id']}/eligible-workers")
        self.assertEqual(st, 200)
        row = next(e for e in el if e["worker"] == "W4")
        self.assertEqual(views_types.check_vm("EligibleVM", row), [])
        self.assertEqual((row["allowed"], row["scope"]), (False, "office"))
        self.assertIn("never run personal", row["reason"])
        st, err = self.get("/api/projects/shop-ops/tasks/999-nope")
        self.assertEqual((st, err["code"]), (404, "not_found"))
        st, err = self.get("/api/projects/ghost")
        self.assertEqual(st, 404)

    def test_static_root(self):
        st, body = self.get("/")
        self.assertEqual(st, 200)
        self.assertIn("Agent Deck", body)
        st, _ = self.get("/../../etc/passwd")
        self.assertEqual(st, 404)


class TestEvents(ApiCase):
    def test_json_since(self):
        self.new_task()
        st, evs = self.get("/api/events")
        self.assertEqual(st, 200)
        self.assertTrue(evs)
        last = evs[-1]["id"]
        self.new_task()
        st, evs2 = self.get(f"/api/events?since={last}")
        self.assertTrue(all(e["id"] > last for e in evs2))
        self.assertEqual(evs2[0]["type"], "task.created")
        st, al = self.get("/api/events?min=alert")
        self.assertEqual(al, [])
        st, pr = self.get("/api/events?project=client-portal")
        self.assertEqual(pr, [])

    def test_sse_resume_by_last_event_id(self):
        self.new_task()
        self.new_task()
        st, evs = self.get("/api/events")
        first = evs[0]["id"]
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/events?timeout=0.8",
                                   headers={"Accept": "text/event-stream", "Last-Event-ID": str(first)})
        with urllib.request.urlopen(r, timeout=10) as resp:
            self.assertEqual(resp.headers["Content-Type"], "text/event-stream")
            body = resp.read().decode()
        ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
        self.assertEqual(ids, [e["id"] for e in evs if e["id"] > first])
        self.assertIn("event: deck", body)
        data = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        self.assertEqual(data[0]["type"], evs[1]["type"])


class TestAuth(ApiCase):
    def test_post_needs_token(self):
        t = self.queued()
        st, err = self.post(f"/api/projects/shop-ops/tasks/{t['id']}/actions", {"action": "priority", "priority": "P1"})
        self.assertEqual((st, err["code"]), (401, "unauthorized"))
        st, err = self.post(f"/api/projects/shop-ops/tasks/{t['id']}/actions", {"action": "priority", "priority": "P1"},
                            token="wrong")
        self.assertEqual(st, 401)
        st, res = self.post(f"/api/projects/shop-ops/tasks/{t['id']}/actions", {"action": "priority", "priority": "P1"},
                            token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual(res["task"]["priority"], "P1")
        st, res = self.post(f"/api/projects/shop-ops/tasks/{t['id']}/actions", {"action": "priority", "priority": "P0"},
                            token=self.token)
        self.assertEqual((st, res["task"]["priority"]), (200, "P0"))                       # W0: P0 allowed
        st, err = self.post(f"/api/projects/shop-ops/tasks/{t['id']}/actions", {"action": "priority", "priority": "P4"},
                            token=self.token)
        self.assertEqual((st, err["code"]), (400, "invalid"))

    def test_host_header_checked(self):
        st, err = self.get("/api/overview", headers={"Host": "evil.example"})
        self.assertEqual(st, 403)

    def test_remote_needs_device_token(self):
        old = server.LOOPBACK
        server.LOOPBACK = ()          # every client now counts as remote (Tailscale)
        try:
            st, err = self.get("/api/overview")
            self.assertEqual(st, 401)
            st, err = self.get("/api/overview", token=self.token)      # the local token is not a device token
            self.assertEqual(st, 401)
            code = server.start_pairing(self.cfg.state_dir)
            st, err = self.post("/api/pair", {"code": "000000" if code != "000000" else "111111", "name": "phone"})
            self.assertEqual(st, 403)
            st, res = self.post("/api/pair", {"code": code, "name": "phone"})
            self.assertEqual(st, 200)
            dev = res["token"]
            st, err = self.post("/api/pair", {"code": code, "name": "again"})
            self.assertEqual(st, 403, "a pairing code works once")
            st, vm = self.get("/api/overview", token=dev)
            self.assertEqual(st, 200)
            stored = (self.cfg.state_dir / "devices.json").read_text()
            self.assertNotIn(dev, stored, "device tokens are stored hashed")
        finally:
            server.LOOPBACK = old


class TestActions(ApiCase):
    def act(self, tid, body, token=True):
        return self.post(f"/api/projects/shop-ops/tasks/{tid}/actions", body, token=self.token if token else None)

    def test_policy_error_shape(self):
        t, att = self.running()
        st, res = self.act(t["id"], {"action": "reassign", "worker": "W4"})
        self.assertEqual(st, 403)
        self.assertEqual(res, {"ok": False, "code": "policy_denied", "reason": "office account may never run personal projects"})
        st, res = self.act(t["id"], {"action": "reassign", "worker": "W3"})
        self.assertEqual((st, res["ok"], res["task"]["pinWorker"]), (200, True, "W3"))

    def test_illegal_and_stale(self):
        t = self.queued()
        st, res = self.act(t["id"], {"action": "approve"})
        self.assertEqual((st, res["code"]), (409, "illegal"))
        st, res = self.act(t["id"], {"action": "priority", "priority": "P1", "rev": 1})
        self.assertEqual((st, res["code"]), (409, "stale"))
        st, res = self.act(t["id"], {"action": "explode"})
        self.assertEqual((st, res["code"]), (400, "invalid"))

    def test_review_actions(self):
        t, att = self.running()
        s = self.ps()
        s.put_handoff(t["id"], 1, self.good_handoff())
        s.transition(t["id"], "review", actor="SUP")
        rev = s.get(t["id"])["rev"]
        st, res = self.act(t["id"], {"action": "changes", "note": "", "rev": rev})
        self.assertEqual(st, 409)
        st, res = self.act(t["id"], {"action": "approve", "rev": rev})
        self.assertEqual((st, res["task"]["state"]), (200, "merging"))
        self.assertEqual(res["task"]["review"]["by"], "owner")

    def test_pause_resume_cancel_answer(self):
        t, att = self.running()
        st, res = self.act(t["id"], {"action": "pause"})
        self.assertEqual(res["task"]["state"], "blocked")
        st, res = self.act(t["id"], {"action": "answer", "note": "go ahead"})
        self.assertEqual(res["task"]["state"], "queued")
        st, res = self.act(t["id"], {"action": "cancel"})
        self.assertEqual(res["task"]["state"], "cancelled")

    def test_card_post_with_attempt_token(self):
        t, att = self.running()
        path = f"/api/projects/shop-ops/tasks/{t['id']}/card"
        st, res = self.post(path, {"op": "step.add", "title": "Read sources", "eta": 10}, token=att["token"])
        self.assertEqual((st, res["ok"]), (200, True))
        self.assertEqual(res["card"]["steps"][0]["id"], "read-sources")
        st, res = self.post(path, {"op": "step.add", "title": "No eta"}, token=att["token"])
        self.assertEqual((st, res["code"]), (400, "invalid"))
        st, res = self.post(path, {"op": "note", "text": "x"}, token=self.token)   # the panel token cannot write cards
        self.assertEqual(st, 400)
        st, res = self.post(path, {"op": "note", "text": "x"})
        self.assertEqual(st, 401)


if __name__ == "__main__":
    unittest.main()
