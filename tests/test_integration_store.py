"""B2 runtime against B1's REAL SQLite store (deck.store). Skipped until B1 is merged into this tree."""
import importlib.util
import json
import unittest

from deck import clock
from deck.dispatcher import Dispatcher
from tests.b2_helpers import T0, DeckCase, FakeLauncher, FakeProbe, account, make_cfg, project, worker, write_exit, write_out

HAVE_STORE = importlib.util.find_spec("deck.store") is not None


@unittest.skipUnless(HAVE_STORE, "B1 deck.store not present in this tree")
class RealStore(DeckCase):
    def build_real(self, accounts, workers, projects, **sched):
        from deck import store
        self.cfg = make_cfg(accounts, workers, projects, self.state, **sched)
        self.g = store.open_global(self.cfg)
        self.stores = {pid: store.open_project(pid, self.cfg) for pid in self.cfg.projects}
        self.launcher, self.probe = FakeLauncher(), FakeProbe()
        self.d = Dispatcher(self.cfg, self.g, self.stores, state_dir=self.state, launcher=self.launcher,
                            probe=self.probe, ram=lambda: 16.0, read_usage=lambda a, n: None)

    def usage(self, aid, pct5, resets_min):
        self.g.usage.put({"account": aid, "read_at": clock.now(), "source": "test", "pct5": pct5, "pct7": 10,
                          "resets5": clock.now() + resets_min * 60, "resets7": clock.now() + 5 * 86400,
                          "confidence": "high"})

    def ready(self, pid, title, **f):
        st = self.stores[pid]
        f.setdefault("isolation", "none")
        f.setdefault("summary", "A plain summary of the work")
        t = st.create_task({"title": title, **f}, by="W0")
        st.transition(t["id"], "ready", actor="W0", brief={"sha256": "abc", "snapshot": "/tmp/brief.md"})
        return t["id"]

    def gevents(self, type):
        return [e for e in self.g.events_since(0, limit=5000) if e["type"] == type]

    def test_happy_path_to_review_and_done(self):
        self.build_real([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        tid = self.ready("shop", "Import the supplier list", size="S", workerClass=["codex-exec"])
        self.d.tick()
        st = self.stores["shop"]
        self.assertEqual(st.get(tid)["state"], "starting")
        rd = self.launcher.calls[0]["run_dir"]
        self.assertEqual(st.current_attempt(tid)["runDir"], str(rd))
        write_out(rd, [{"type": "thread.started", "thread_id": "th-1"}])
        self.d.tick()
        self.assertEqual(st.get(tid)["state"], "running")
        self.assertEqual(st.current_attempt(tid)["sessionId"], "th-1")
        (rd / "last-message.json").write_text(json.dumps({
            "outcome": "done", "summary": "Loaded every supplier and linked the bills.", "doneWhen": [], "tests": [],
            "risks": [], "decisionsNeeded": [], "followUps": [{"title": "Clean supplier names", "summary": "Trim spaces"}],
            "migrations": None, "servicesStarted": None, "nextStep": None}))
        write_exit(rd, 0)
        self.d.tick()
        t = st.get(tid)
        self.assertEqual(t["state"], "review")
        self.assertTrue(st.handoff(tid)["valid"])
        self.assertEqual(len(self.gevents("review.requested")), 1)       # emitted once (by the store)
        self.assertEqual([x["title"] for x in st.list(["draft"])], ["Clean supplier names"])
        st.review(tid, "approve", by="W0")
        self.assertEqual(st.get(tid)["state"], "done")

    def test_crash_backoff_then_retry_with_resume(self):
        self.build_real([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        tid = self.ready("shop", "Map receipt columns", size="S", workerClass=["codex-exec"])
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th-9"}])
        self.d.tick()
        write_exit(rd, 1)
        self.d.tick()
        t = self.stores["shop"].get(tid)
        self.assertEqual(t["state"], "queued")
        self.assertGreater(t["resumeAt"], clock.now())
        clock.advance(3 * 60)
        self.d.tick()
        self.assertEqual(self.launcher.calls[-1]["spec"].argv[:4], ["codex", "exec", "resume", "th-9"])

    def test_limit_pause_resume_real_store(self):
        self.build_real([account("cw", tool="claude")], [worker("W3", "cw", adapter="claude-headless")], [project("shop")])
        self.usage("cw", 5, 250)
        tid = self.ready("shop", "Write the import script", size="S", workerClass=["claude-headless"],
                         retry={"maxAttempts": 3, "onLimit": "wait", "onHang": "restart"})
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "system", "subtype": "init", "session_id": "s-1"},
                       {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": T0 + 7200,
                                                                        "rateLimitType": "five_hour"}}])
        write_exit(rd, 1)
        self.d.tick()
        t = self.stores["shop"].get(tid)
        self.assertEqual((t["state"], t["resumeAt"]), ("limit_paused", T0 + 7290))
        self.assertEqual(len(self.gevents("limit.hit")), 1)
        clock.set(T0 + 7300)
        self.d.tick()
        argv = self.launcher.calls[-1]["spec"].argv
        self.assertEqual(argv[argv.index("--resume") + 1], "s-1")

    def test_hang_restart_real_store(self):
        self.build_real([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        tid = self.ready("shop", "Profile the stock costs", size="S", workerClass=["codex-exec"],
                         timeouts={"attemptMin": 600, "taskHours": 48, "quietMin": 15})
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th-h"}])
        fake = 50001
        (rd / "pid.json").write_text(json.dumps({"pid": fake, "pgid": fake, "start": "x", "wrapper": None}))
        self.probe.procs[fake] = {"alive": True, "start": "x", "cpu": 0.0, "run_dir": rd}
        self.d.tick()
        clock.advance(31 * 60)
        self.d.tick()
        self.assertEqual(len(self.gevents("attempt.hung")), 1)
        self.d.tick()
        t = self.stores["shop"].get(tid)
        self.assertEqual([a["outcome"] for a in t["attempts"]], ["hung", "running"])

    def test_ext_domain_across_projects_and_policy(self):
        self.build_real([account("cx", max=3), account("off", scope="office")],
                        [worker("W1", "cx"), worker("W4", "off")], [project("shop"), project("portal")])
        self.usage("cx", 5, 250)
        self.usage("off", 0, 290)
        a = self.ready("shop", "Pull bills from the PC", domains=["ext:pc-ssh"], workerClass=["codex-exec"])
        b = self.ready("portal", "Pull stock from the PC", domains=["ext:pc-ssh"], workerClass=["codex-exec"])
        self.d.tick()
        states = {self.stores["shop"].get(a)["state"], self.stores["portal"].get(b)["state"]}
        self.assertEqual(states, {"starting", "queued"})
        for c in self.launcher.calls:
            self.assertEqual(c["spec"].env["CODEX_HOME"], self.cfg.account("cx").home)     # never the office account
        self.assertEqual(self.gevents("policy.denied"), [])

    def test_reconcile_after_restart(self):
        self.build_real([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        tid = self.ready("shop", "Rebuild the price list", size="S", workerClass=["codex-exec"])
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th-r"}])
        self.d.tick()
        (rd / "last-message.json").write_text(json.dumps({"outcome": "partial", "summary": "Half the list is rebuilt.",
                                                          "doneWhen": [], "tests": [], "risks": [], "decisionsNeeded": []}))
        write_exit(rd, 0)
        d2 = Dispatcher(self.cfg, self.g, self.stores, state_dir=self.state, launcher=self.launcher, probe=self.probe,
                        ram=lambda: 16.0, read_usage=lambda a, n: None)
        self.assertEqual(d2.reconcile()["finished"], 1)
        self.assertEqual(self.stores["shop"].get(tid)["state"], "review")


    def test_pause_kills_real_store(self):
        self.build_real([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        tid = self.ready("shop", "Pause me please", size="S", workerClass=["codex-exec"])
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th"}])
        (rd / "pid.json").write_text(json.dumps({"pid": 50009, "pgid": 50009, "start": "x", "wrapper": None}))
        self.probe.procs[50009] = {"alive": True, "start": "x", "cpu": 0.0, "run_dir": rd}
        self.d.tick()
        self.stores["shop"].transition(tid, "blocked", actor="W0", reason="paused")
        self.d.tick()
        self.assertEqual(self.probe.killed, [50009])


if __name__ == "__main__":
    unittest.main()
