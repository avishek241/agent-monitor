"""T-MIS: replay each original mistake that belongs to B2 and assert the new outcome (design §18).

M1 finished-but-nobody-told, M2 pilot unreviewed, M4 limit misread (also in test_runtime), M6 merge-abort clash
(test_mergeq), M7 hard-coded port (test_mergeq), M8 hung/orphans (test_runtime), M9 limit mid-task manual resume,
M10 bookkeeping. M3 (card keys) and M5 (brief) are B1's; the importer side of M3 is covered here.
"""
import json
from pathlib import Path

from deck import clock
from deck.deckd import Daemon
from deck.import_v1 import import_project
from deck.notify import Notifier
from tests.helpers import T0, DeckCase, account, project, worker, write_exit, write_out

DONE = {"outcome": "done", "summary": "Pilot of 20 suppliers imported and checked.", "doneWhen": [], "tests": [],
        "risks": [], "decisionsNeeded": []}


class Mistakes(DeckCase):
    def base(self, n_lanes=1, **sched):
        self.build([account("cx", max=n_lanes)], [worker("W1", "cx", lanes=n_lanes)], [project("shop")], **sched)
        self.usage("cx", 5, 250)

    def test_m1_detached_finish_is_noticed_and_next_job_starts(self):
        """M1: a worker launched with `&` finished; nobody was told; workers idled 30 min."""
        self.base()
        self.add("shop", "first", createdAt=T0)
        self.add("shop", "second", createdAt=T0 + 1)
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "a"}])
        (rd / "last-message.json").write_text(json.dumps(DONE))
        write_exit(rd, 0)                        # the wrapper's durable exit record — no one has to be watching
        clock.advance(1)
        self.d.on_wake()
        rr = [e for e in self.g.events.rows if e["type"] == "review.requested"]
        self.assertEqual(len(rr), 1)
        self.assertEqual(rr[0]["for"], ["W0"])
        self.assertEqual(rr[0]["severity"], "action")
        self.assertEqual(self.running(), ["second"])                   # re-planned in the same wake, not 30 min later

    def test_m2_pilot_review_overdue_escalates(self):
        """M2: a finished pilot sat unreviewed for 6 h."""
        self.base()
        self.add("shop", "051", kind="pilot")
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "a"}])
        (rd / "last-message.json").write_text(json.dumps(DONE))
        write_exit(rd, 0)
        self.d.tick()
        self.assertIn("PILOT", self.events("review.requested")[0]["text"])
        clock.advance(29 * 60)
        self.d.tick()
        self.assertEqual(self.events("review.overdue"), [])
        clock.advance(2 * 60)
        self.d.tick()
        od = self.events("review.overdue")
        self.assertEqual((len(od), od[0]["severity"]), (1, "action"))
        for _ in range(3):
            clock.advance(60 * 60)
            self.d.tick()
        od = self.events("review.overdue")
        self.assertEqual(len(od), 4)
        self.assertEqual(od[-1]["severity"], "alert")                  # past 2 h it is an alert

    def test_m3_v1_cards_imported_with_visible_warnings(self):
        """M3 (importer side): cards with `name` keys and dict metrics showed nothing on the panel."""
        prog = self.state / "v1progress"
        prog.mkdir()
        (prog / "051-pilot.json").write_text(json.dumps({
            "title": "Supplier pilot", "status": "running",
            "steps": [{"name": "Read export", "status": "done"}, {"label": "Match", "status": "active", "etaMin": 10}],
            "metrics": {"Rows": {"done": 12, "total": 20}}}))
        self.build([account("cx")], [worker("W1", "cx")], [project("shop", legacy={"progressDir": str(prog)})])
        st = self.stores["shop"]
        out = import_project("shop", st, self.cfg.project("shop"), self.state)
        self.assertEqual((out["cards"], out["created"]), (1, 1))
        doc = json.loads((self.state / "legacy" / "shop" / "051-pilot.json").read_text())
        w = " | ".join(doc["card"]["warnings"])
        self.assertIn("'name' renamed to 'title'", w)
        self.assertIn("'label' renamed", w)
        self.assertIn("no etaMin", w)
        self.assertIsInstance(doc["card"]["metrics"], list)
        self.assertEqual(st.get("legacy-051-pilot")["state"], "draft")     # read-only mirror, never dispatched
        import_project("shop", st, self.cfg.project("shop"), self.state)
        self.assertEqual(len(st.list()), 1)                                 # idempotent

    def test_m9_limit_mid_task_resumes_without_anyone_remembering(self):
        """M9: a worker hit its 5-hour limit mid-task; resuming depended on the orchestrator remembering."""
        self.build([account("cw", tool="claude")], [worker("W3", "cw", adapter="claude-headless")], [project("shop")])
        self.usage("cw", 5, 250)
        self.add("shop", "104", workerClass=["claude-headless"], retry={"maxAttempts": 3, "onLimit": "wait",
                                                                        "onHang": "restart"})
        dmn = Daemon(self.cfg, self.g, self.stores, http=False, notifier=Notifier(self.cfg, runner=lambda *a: None,
                                                                                   macos=False),
                     state_dir=self.state, launcher=self.launcher, probe=self.probe, ram=lambda: 16.0,
                     read_usage=lambda a, n: None)
        dmn.start()
        dmn.step()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "system", "subtype": "init", "session_id": "s-104"},
                       {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": T0 + 5400,
                                                                        "rateLimitType": "five_hour"}}])
        write_exit(rd, 1)
        dmn.step()
        self.assertEqual(self.stores["shop"].get("104")["state"], "limit_paused")
        for minute in range(0, 100, 5):                                     # just the daemon's loop, nobody else
            clock.set(T0 + minute * 60)
            dmn.step()
        t = self.stores["shop"].get("104")
        self.assertEqual(t["state"], "starting")
        argv = self.launcher.calls[-1]["spec"].argv
        self.assertEqual(argv[argv.index("--resume") + 1], "s-104")
        self.assertTrue((self.state / "deckd.pid").exists())

    def test_m10_no_bookkeeping_needed_to_dispatch(self):
        """M10: time went into board rows, titles.json and runner scripts. Queued tasks start with no manual step,
        and the planner's own reasons explain every wait."""
        self.build([account("cx", max=3)], [worker("W1", "cx", lanes=3)], [project("shop")])
        self.usage("cx", 5, 250)
        for i in range(3):
            self.add("shop", f"t{i}", createdAt=T0 + i, domains=["ext:pc-ssh"] if i else [])
        started = self.d.tick()
        self.assertEqual(sorted(t for _p, t, _w, _l in started), ["t0", "t1"])
        self.assertIn("ext:pc-ssh held by shop/t1", self.d.notes[("shop", "t2")])
        st = self.g.account_state.get("cx")
        self.assertEqual(st["capacity"]["lanesUsed"], 2)          # third lane free, held back only by the domain
        launch = json.loads((self.launcher.calls[0]["run_dir"] / "launch.json").read_text())
        self.assertEqual(launch["argv"][-1], "<prompt.md>")                # prompt + brief path written by the deck
        self.assertIn("/runs/", (self.launcher.calls[0]["run_dir"] / "prompt.md").read_text())

    def test_notifications_rate_limited_and_notify_command(self):
        sent = []
        self.build([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.cfg.notify_command = "cat > /dev/null"
        n = Notifier(self.cfg, runner=lambda argv, stdin=None: sent.append((argv, stdin)), macos=True)
        n.macos = True
        ev = {"id": 1, "type": "review.requested", "severity": "action", "project": "shop", "task": "t",
              "text": 'Review "t" now'}
        self.assertTrue(n.deliver(ev))
        self.assertFalse(n.deliver(dict(ev, id=2)))                         # same (type, task) within 5 min
        self.assertFalse(n.deliver(dict(ev, id=3, severity="info", type="x")))
        clock.advance(301)
        self.assertTrue(n.deliver(dict(ev, id=4)))
        self.assertEqual(sent[0][0][0], "osascript")
        self.assertIn('\\"t\\"', sent[0][0][2])                             # quotes escaped for AppleScript
        self.assertEqual(json.loads(sent[1][1])["task"], "t")
