"""W0 integration decisions, against B1's real SQLite store and B2's dispatcher.

- set_reason: a non-transition reason shown on the board, written by the dispatcher.
- workspace path and branch persisted on the attempt.
- learned job cost per account and size (global usage_cost), used by the planner instead of the defaults.
(T26 merging → merging and T28 by the owner are covered in test_states and test_mergeq.)
"""
import json

from deck import clock, quota, store
from deck.dispatcher import Dispatcher
from tests.b2_helpers import DeckCase as B2Case
from tests.b2_helpers import FakeLauncher, FakeProbe, account, make_cfg, project, worker, write_exit, write_out
from tests.helpers import DeckCase


class SetReason(DeckCase):
    def test_reason_is_not_a_transition(self):
        t = self.queued()
        tid, rev, since = t["id"], t["rev"], t["stateSince"]
        n_events = len(self.events())
        clock.advance(40)
        self.assertTrue(self.ps().set_reason(tid, "waiting: area:apps/api/** held by T-12"))
        t2 = self.ps().get(tid)
        self.assertEqual(t2["stateReason"], "waiting: area:apps/api/** held by T-12")
        self.assertEqual((t2["state"], t2["rev"], t2["stateSince"]), ("queued", rev, since))
        self.assertEqual(len(self.events()), n_events)                    # no event, safe to call every tick
        self.assertFalse(self.ps().set_reason(tid, "waiting: area:apps/api/** held by T-12"))   # unchanged
        # the next transition replaces it with its own reason
        t3 = self.ps().transition(tid, "draft", actor="W0", reason="unready")
        self.assertEqual(t3["stateReason"], "unready")

    def test_terminal_tasks_keep_their_reason_and_text_is_capped(self):
        t = self.queued()
        self.ps().transition(t["id"], "cancelled", actor="W0", reason="not needed")
        self.assertFalse(self.ps().set_reason(t["id"], "waiting: x"))
        self.assertEqual(self.ps().get(t["id"])["stateReason"], "not needed")
        t2 = self.queued()
        self.ps().set_reason(t2["id"], "x" * 500)
        self.assertEqual(len(self.ps().get(t2["id"])["stateReason"]), 300)
        self.ps().set_reason(t2["id"], None)
        self.assertNotIn("stateReason", {k for k, v in self.ps().get(t2["id"]).items() if v})


class UsageCost(DeckCase):
    def test_put_recent_filters_and_pruning(self):
        uc = self.g.usage_cost
        for i in range(55):
            uc.put({"account": "codex-a", "account_tool": "codex", "size": "M", "delta5": 10 + i % 3, "delta7": None,
                    "minutes": 40, "ts": clock.now() + i})
        uc.put({"account": "claude-worker", "account_tool": "claude", "size": "M", "delta5": 30, "minutes": 90,
                "ts": clock.now() + 100})
        self.assertEqual(len(uc.recent(account="codex-a", size="M")), uc.KEEP)     # oldest pruned
        rows = uc.recent()
        self.assertEqual(rows[-1]["account"], "claude-worker")                      # newest last
        self.assertEqual(len(uc.recent(tool="claude")), 1)

    def test_estimate_prefers_the_accounts_own_numbers(self):
        hist = [{"account": "a1", "account_tool": "codex", "size": "M", "delta5": 4, "minutes": 20, "ts": i}
                for i in range(3)]
        hist += [{"account": "a2", "account_tool": "codex", "size": "M", "delta5": 30, "minutes": 100, "ts": i}
                 for i in range(5)]
        self.assertEqual(quota.estimate("M", "codex", hist, account="a1"), (4.0, 20.0))
        # an account without enough rows of its own falls back to the tool's median
        est, mins = quota.estimate("M", "codex", hist, account="a3")
        self.assertEqual(est, 30.0)
        # no rows for the size at all: defaults
        self.assertEqual(quota.estimate("XL", "codex", hist, account="a1"), quota.estimate("XL", "codex", []))


class RealRuntime(B2Case):
    def build_real(self, accounts, workers, projects, **sched):
        self.cfg = make_cfg(accounts, workers, projects, self.state, **sched)
        self.g = store.open_global(self.cfg)
        self.stores = {pid: store.open_project(pid, self.cfg, self.g) for pid in self.cfg.projects}
        self.launcher, self.probe = FakeLauncher(), FakeProbe()
        self.d = Dispatcher(self.cfg, self.g, self.stores, state_dir=self.state, launcher=self.launcher,
                            probe=self.probe, ram=lambda: 16.0, read_usage=lambda a, n: None)

    def tearDown(self):
        for s in getattr(self, "stores", {}).values():
            s.close()
        if getattr(self, "g", None) is not None:
            self.g.close()
        super().tearDown()

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

    def test_conflicting_task_waits_with_a_reason_on_the_task(self):
        self.build_real([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        a = self.ready("shop", "Edit the API", size="S", workerClass=["codex-exec"], domains=["area:apps/api/**"])
        b = self.ready("shop", "Edit the API routes", size="S", workerClass=["codex-exec"],
                       domains=["area:apps/api/routes/**"])
        c = self.ready("shop", "Edit the web app", size="S", workerClass=["codex-exec"], domains=["area:apps/web/**"])
        self.d.tick()
        st = self.stores["shop"]
        states = {t: st.get(t)["state"] for t in (a, b, c)}
        self.assertEqual(sorted(states.values()), ["queued", "starting", "starting"])
        waiting = [t for t, s in states.items() if s == "queued"][0]
        self.assertIn(waiting, (a, b))
        reason = st.get(waiting)["stateReason"]
        self.assertTrue(reason.startswith("waiting:"), reason)
        self.assertIn("area:apps/api", reason)
        holder = a if waiting == b else b
        self.assertIn(holder, reason)

    def test_workspace_on_attempt_and_cost_learned_after_a_job(self):
        self.build_real([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        tid = self.ready("shop", "Import the supplier list", size="S", workerClass=["codex-exec"])
        self.d.tick()
        st = self.stores["shop"]
        att = st.current_attempt(tid)
        self.assertTrue(att["workspace"])                                    # persisted on the attempt
        self.assertEqual(self.d.workspace_of({"id": tid, "project": "shop"})["path"], att["workspace"])
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th-1"}])
        self.d.tick()
        clock.advance(25 * 60)
        self.usage("cx", 12, 225)                                            # 7 points used by this job
        (rd / "last-message.json").write_text(json.dumps({
            "outcome": "done", "summary": "Loaded every supplier and linked the bills.", "doneWhen": [], "tests": [],
            "risks": [], "decisionsNeeded": [], "followUps": None, "migrations": None, "servicesStarted": None,
            "nextStep": None}))
        write_exit(rd, 0)
        self.d.tick()
        self.assertEqual(st.get(tid)["state"], "review")
        rows = self.g.usage_cost.recent(account="cx", size="S")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["delta5"], 7.0)
        self.assertAlmostEqual(rows[0]["minutes"], 25.0, delta=0.5)
        self.assertEqual(rows[0]["account_tool"], "codex")
        # the planner now reads learned numbers (tool-level until the account has 3 of its own)
        self.assertEqual(quota.estimate("S", "codex", self.d._history(), account="cx")[0], 7.0)
