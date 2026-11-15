"""T-SM: every row of design §5.2 with its guard satisfied and violated; every unlisted pair; rev; terminals."""
import json
import unittest

from deck import clock, states
from deck.errors import IllegalTransition, PolicyDenied, StaleRev
from tests.helpers import T0, DeckCase


class SM(DeckCase):
    """Helpers to put a task straight into any state (test-only, bypasses the machine)."""

    def mk(self, state, attempt=None, worker="W1", hb=None, pid="shop-ops", **fields):
        s = self.ps(pid)
        t = self.new_task(pid, **fields)
        tid = t["id"]
        if attempt:
            s.conn.execute("INSERT INTO attempts(task, n, worker, account, adapter, started_at, last_heartbeat, outcome, lane)"
                           " VALUES(?,?,?,?,?,?,?,?,?)",
                           (tid, 1, worker, self.cfg.worker(worker).account, self.cfg.worker(worker).adapter,
                            clock.now(), clock.now() if hb is None else hb, attempt, 1))
        s.conn.execute("UPDATE tasks SET state=?, state_since=? WHERE id=?", (state, clock.now(), tid))
        return tid

    def setdata(self, tid, pid="shop-ops", **kv):
        s = self.ps(pid)
        row = s.conn.execute("SELECT data, resume_at FROM tasks WHERE id=?", (tid,)).fetchone()
        d = json.loads(row["data"])
        resume = kv.pop("resumeAt", row["resume_at"])
        d.update(kv)
        s.conn.execute("UPDATE tasks SET data=?, resume_at=? WHERE id=?", (json.dumps(d), resume, tid))

    def handoff(self, tid, outcome="done", valid=True):
        self.ps().put_handoff(tid, 1, self.good_handoff(outcome), valid=valid)

    def ok(self, tid, to, actor, **eff):
        t = self.ps().transition(tid, to, actor=actor, **eff)
        self.assertEqual(t["state"], to)
        return t

    def bad(self, tid, to, actor, exc=IllegalTransition, **eff):
        before = self.ps().get(tid)
        with self.assertRaises(exc):
            self.ps().transition(tid, to, actor=actor, **eff)
        after = self.ps().get(tid)
        self.assertEqual(before["state"], after["state"], "a refused transition must write nothing")
        self.assertEqual(before["rev"], after["rev"])


class TestRows(SM):
    def test_T1_validated(self):
        tid = self.mk("draft")
        t = self.ok(tid, "ready", "W0", brief={"sha256": "a" * 64, "snapshot": "/s.md"}, doneWhen=["x"])
        self.assertEqual(t["brief"]["sha256"], "a" * 64)
        self.assertEqual(t["doneWhen"], ["x"])
        self.bad(self.mk("draft"), "ready", "W0")
        self.bad(self.mk("draft"), "ready", "DSP", brief={"sha256": "a", "snapshot": "b"})

    def test_T1x_invalid_stays_draft(self):
        tid = self.mk("draft")
        self.ok(tid, "draft", "W0", errors=["V2: x"])
        self.assertTrue(self.events(type="task.invalid"))
        self.bad(tid, "draft", "SUP")

    def test_T2_unready(self):
        self.ok(self.mk("ready"), "draft", "W0")
        self.ok(self.mk("queued"), "draft", "SUP")
        self.bad(self.mk("queued", attempt="running"), "draft", "W0")

    def test_T3_deps_done(self):
        dep = self.mk("done")
        tid = self.mk("ready", deps=[dep])
        self.ok(tid, "queued", "DSP")
        dep2 = self.mk("running", attempt="running")
        self.bad(self.mk("ready", deps=[dep2]), "queued", "DSP")
        self.bad(self.mk("ready"), "queued", "W0")

    def test_T3_waiting_for_and_disabled_project(self):
        tid = self.mk("ready")
        self.setdata(tid, waitingFor="owner answers Q7")
        self.bad(tid, "queued", "DSP")
        self.cfg.projects["shop-ops"].enabled = False
        self.bad(self.mk("ready"), "queued", "DSP")

    def test_T4_dep_failed(self):
        dep = self.mk("failed")
        t = self.ok(self.mk("ready", deps=[dep]), "blocked", "DSP")
        self.assertEqual(t["state"], "blocked")
        self.ok(self.mk("queued", deps=[self.mk("cancelled")]), "blocked", "DSP")
        self.bad(self.mk("ready", deps=[self.mk("done")]), "blocked", "DSP")

    def test_T5_only_through_lease(self):
        tid = self.mk("queued")
        self.bad(tid, "starting", "DSP")
        att = self.ps().lease(tid, "W1", 1, "codex-a", slot=None, domains=[])
        self.assertEqual(self.ps().get(tid)["state"], "starting")
        self.assertEqual(att["n"], 1)
        self.assertTrue(att["token"])
        self.assertEqual(self.ps().get(tid)["worker"], "W1")

    def test_T5_guards(self):
        tid = self.mk("queued", retry={"maxAttempts": 1})
        self.ps().conn.execute("INSERT INTO attempts(task, n, worker, outcome) VALUES(?,?,?,?)", (tid, 1, "W1", "crashed"))
        with self.assertRaises(IllegalTransition):
            self.ps().lease(tid, "W1", 1, "codex-a", slot=None, domains=[])
        with self.assertRaises(IllegalTransition):  # capability
            self.ps().lease(self.mk("queued", capability="browser"), "W1", 1, "codex-a", slot=None, domains=[])
        with self.assertRaises(IllegalTransition):  # workerClass
            self.ps().lease(self.mk("queued", workerClass=["claude-headless"]), "W1", 1, "codex-a", slot=None, domains=[])
        with self.assertRaises(IllegalTransition):  # pin
            self.ps().lease(self.mk("queued", pinWorker="W3"), "W1", 1, "codex-a", slot=None, domains=[])
        with self.assertRaises(IllegalTransition):  # not queued
            self.ps().lease(self.mk("ready"), "W1", 1, "codex-a", slot=None, domains=[])

    def test_T5_lane_busy(self):
        a = self.mk("queued", domains=["area:a/**"])
        b = self.mk("queued", domains=["area:b/**"])
        self.ps().lease(a, "W1", 1, "codex-a", slot=None, domains=[])
        with self.assertRaises(IllegalTransition):
            self.ps().lease(b, "W1", 1, "codex-a", slot=None, domains=[])
        self.ps().lease(b, "W1", 2, "codex-a", slot=None, domains=[])

    def test_T6_first_output(self):
        self.ok(self.mk("starting", attempt="running"), "running", "ADP")
        self.bad(self.mk("starting"), "running", "ADP")

    def test_T7_spawn_failed(self):
        tid = self.mk("starting", attempt="running")
        t = self.ok(tid, "queued", "SUP")
        self.assertEqual(self.ps().current_attempt(tid)["outcome"], "crashed")
        self.assertEqual(t["resumeAt"], T0 + 60)
        self.bad(self.mk("starting", attempt="running", retry={"maxAttempts": 1}), "queued", "SUP")

    def test_T8_valid_handoff(self):
        tid = self.mk("running", attempt="running")
        self.handoff(tid, "done")
        t = self.ok(tid, "review", "SUP", t="T8")
        self.assertEqual(t["review"]["requestedAt"], T0)
        self.assertFalse(self.events(type="handoff.invalid"))
        self.assertTrue(self.events(type="review.requested"))
        self.bad(self.mk("running", attempt="running"), "review", "SUP", t="T8")

    def test_T9_no_valid_handoff(self):
        tid = self.mk("running", attempt="running")
        t = self.ok(tid, "review", "SUP")
        h = self.ps().handoff(tid)
        self.assertEqual(h["data"]["outcome"], "unknown")
        self.assertFalse(h["valid"])
        self.assertTrue(self.events(type="handoff.invalid"))
        tid2 = self.mk("running", attempt="running")
        self.handoff(tid2, "done")
        self.bad(tid2, "review", "SUP", t="T9")

    def test_T10_question_or_blocked_handoff(self):
        tid = self.mk("running", attempt="running")
        self.ps().card_op(tid, 1, "ask", {"question": "Import rows without GST as inactive?"}, None)
        t = self.ok(tid, "blocked", "ADP")
        self.assertIn("question", t["stateReason"])
        tid2 = self.mk("running", attempt="running")
        self.handoff(tid2, "blocked")
        self.ok(tid2, "blocked", "W1")
        self.bad(self.mk("running", attempt="running"), "blocked", "ADP")

    def test_T11_limit(self):
        tid = self.mk("running", attempt="running")
        self.ps().slots.take(tid)
        t = self.ok(tid, "limit_paused", "ADP", resets_at=T0 + 3000)
        self.assertEqual(t["resumeAt"], T0 + 3090)
        self.assertEqual(self.ps().current_attempt(tid)["outcome"], "limit")
        self.assertIsNotNone(self.ps().slots.of(tid), "slot is kept on limit")
        t2 = self.ok(self.mk("starting", attempt="running"), "limit_paused", "SUP")
        self.assertEqual(t2["resumeAt"], T0 + 3600 + 90)
        self.bad(self.mk("running", attempt="running"), "limit_paused", "W0")

    def test_T12_hung(self):
        tid = self.mk("running", attempt="running", hb=T0 - 31 * 60)
        self.ok(tid, "hung", "SUP", cpu_delta=0.2)
        self.assertTrue(self.events(type="attempt.hung"))
        self.bad(self.mk("running", attempt="running", hb=T0 - 31 * 60), "hung", "SUP", cpu_delta=5)
        self.bad(self.mk("running", attempt="running", hb=T0 - 60), "hung", "SUP")

    def test_T13_recovered(self):
        self.ok(self.mk("hung", attempt="running", hb=T0 - 30), "running", "SUP")
        self.bad(self.mk("hung", attempt="running", hb=T0 - 3600), "running", "SUP")

    def test_T14_restart(self):
        tid = self.mk("hung", attempt="running")
        self.ok(tid, "queued", "SUP", killed=True)
        self.assertEqual(self.ps().current_attempt(tid)["outcome"], "hung")
        self.bad(self.mk("hung", attempt="running"), "queued", "SUP")
        self.bad(self.mk("hung", attempt="running", retry={"onHang": "block"}), "queued", "SUP", killed=True)

    def test_T15_hang_block(self):
        self.ok(self.mk("hung", attempt="running", retry={"onHang": "block"}), "blocked", "SUP")
        self.ok(self.mk("hung", attempt="running", retry={"maxAttempts": 1}), "blocked", "SUP")
        self.bad(self.mk("hung", attempt="running"), "blocked", "SUP")

    def test_T16_crash_retry(self):
        tid = self.mk("running", attempt="running")
        t = self.ok(tid, "queued", "SUP", outcome="crashed")
        self.assertEqual(t["resumeAt"], T0 + 120)
        self.bad(self.mk("running", attempt="running", retry={"maxAttempts": 1}), "queued", "SUP")

    def test_T17_failed(self):
        self.ok(self.mk("running", attempt="running", retry={"maxAttempts": 1}), "failed", "SUP")
        self.ok(self.mk("hung", attempt="running", retry={"maxAttempts": 1}), "failed", "SUP")
        self.ok(self.mk("starting", attempt="running", retry={"maxAttempts": 1}), "failed", "SUP")
        tid = self.mk("running", attempt="running", timeouts={"taskHours": 1})
        clock.advance(3601)
        self.ok(tid, "failed", "SUP")
        self.bad(self.mk("running", attempt="running"), "failed", "SUP")

    def test_T18_resume_after_limit(self):
        tid = self.mk("limit_paused")
        self.setdata(tid, resumeAt=T0 + 100)
        self.bad(tid, "queued", "TMR")
        clock.advance(100)
        self.ok(tid, "queued", "TMR")
        tid2 = self.mk("limit_paused", pinWorker="W3")
        self.setdata(tid2, resumeAt=T0 + 10_000)
        t = self.ok(tid2, "queued", "DSP", failover=True)
        self.assertIsNone(t.get("pinWorker"))

    def test_T19_unblock(self):
        tid = self.mk("blocked")
        self.ok(tid, "queued", "W0", answer="yes, inactive")
        self.assertEqual(self.events(type="question.answered")[-1]["data"]["answer"], "yes, inactive")
        self.ok(self.mk("blocked"), "queued", "owner")
        self.bad(self.mk("blocked"), "queued", "DSP")

    def test_T20_pause(self):
        t = self.ok(self.mk("running", attempt="running"), "blocked", "owner")
        self.assertIn("paused", t["stateReason"])
        self.bad(self.mk("running", attempt="running"), "blocked", "SUP")

    def test_T21_approve_merge(self):
        tid = self.mk("review", attempt="handoff")
        self.handoff(tid)
        self.ok(tid, "merging", "W0")
        self.assertEqual(self.ps().merge_queue()[0]["task"], tid)
        own = self.mk("review", attempt="handoff", worker="W0")
        self.handoff(own)
        self.bad(own, "merging", "W0")      # reviewer == worker
        self.bad(self.mk("review", attempt="handoff"), "merging", "W0")   # no handoff
        auto = self.mk("review", attempt="handoff", kind="chore", merge={"auto": True})
        self.handoff(auto)
        self.ok(auto, "merging", "DSP")
        notauto = self.mk("review", attempt="handoff")
        self.handoff(notauto)
        self.bad(notauto, "merging", "DSP")

    def test_T22_approve_no_branch(self):
        tid = self.mk("review", attempt="handoff", isolation="none")
        self.ok(tid, "done", "W0")
        self.ok(self.mk("review", attempt="handoff", capability="research", domains=[]), "done", "owner")
        self.bad(self.mk("review", attempt="handoff"), "done", "W0")

    def test_T23_changes(self):
        tid = self.mk("review", attempt="handoff")
        t = self.ok(tid, "changes_requested", "W0", note="add the GST column")
        self.assertEqual(t["review"]["decision"], "changes")
        self.bad(self.mk("review", attempt="handoff"), "changes_requested", "W0", note="  ")

    def test_T24_T25_T26_merge_queue(self):
        a = self.mk("merging")
        self.ok(a, "done", "MQ")
        self.bad(self.mk("merging"), "done", "W0")
        b = self.mk("merging")
        t = self.ok(b, "changes_requested", "MQ", note="conflict in apps/api/x.ts")
        self.assertEqual(t["review"]["note"], "conflict in apps/api/x.ts")
        self.bad(self.mk("merging"), "changes_requested", "W0", note="x")
        c = self.mk("merging")
        t = self.ok(c, "review", "MQ", reason="main checkout busy")
        self.assertEqual(t["resumeAt"], T0 + 120)
        self.bad(self.mk("merging"), "review", "W0")

    def test_T27_rework(self):
        tid = self.mk("changes_requested")
        self.ok(tid, "queued", "DSP")
        tid2 = self.mk("changes_requested")
        self.setdata(tid2, resumeAt=T0 + 60)
        self.bad(tid2, "queued", "DSP")

    def test_T27_review_round_grants_an_attempt(self):
        tid = self.mk("review", attempt="handoff", retry={"maxAttempts": 1})
        self.ps().review(tid, "changes", by="W0", note="fix the totals")
        self.ok(tid, "queued", "DSP")

    def test_T28_retry(self):
        tid = self.mk("failed", attempt="crashed", retry={"maxAttempts": 1})
        t = self.ok(tid, "queued", "W0")
        self.assertEqual(t["retry"]["maxAttempts"], 2)
        t2 = self.ok(self.mk("failed"), "queued", "W0", attempts_plus=3)
        self.assertEqual(t2["retry"]["maxAttempts"], 6)
        self.bad(self.mk("failed"), "queued", "owner")

    def test_T29_cancel_any_non_terminal(self):
        for st in states.NON_TERMINAL:
            tid = self.mk(st, attempt="running" if st in states.ACTIVE_ATTEMPT else None)
            self.ok(tid, "cancelled", "W0")
        for st in states.TERMINAL:
            self.bad(self.mk(st), "cancelled", "W0")
        self.bad(self.mk("queued"), "cancelled", "DSP")

    def test_T30_reassign(self):
        tid = self.mk("running", attempt="running")
        t = self.ok(tid, "queued", "W0", worker="W3", t="T30")
        self.assertEqual(t["pinWorker"], "W3")
        self.assertEqual(self.ps().current_attempt(tid)["outcome"], "cancelled")
        self.bad(self.mk("running", attempt="running"), "queued", "W0", exc=PolicyDenied, worker="W4", t="T30")


class TestTable(SM):
    def test_every_row_is_tested(self):
        names = {n for n in dir(TestRows) if n.startswith("test_T")}
        covered = set()
        for n in names:
            for part in n.split("_")[1:]:
                if part.startswith("T") and part[1:].rstrip("x").isdigit():
                    covered.add(part)
        missing = {r.id for r in states.ROWS} - covered
        self.assertFalse(missing, f"rows without a test: {missing}")

    def test_unlisted_pairs_raise(self):
        legal = states.legal_pairs()
        checked = 0
        for f in states.STATES:
            tid = self.mk(f, attempt="running" if f in states.ACTIVE_ATTEMPT else None)
            for to in states.STATES:
                if (f, to) in legal:
                    continue
                for actor in ("W0", "DSP", "SUP", "MQ"):
                    with self.assertRaises(IllegalTransition, msg=f"{f}->{to} by {actor}"):
                        self.ps().transition(tid, to, actor=actor)
                checked += 1
            self.assertEqual(self.ps().get(tid)["state"], f)
        self.assertGreater(checked, 100)

    def test_unknown_state(self):
        with self.assertRaises(IllegalTransition):
            self.ps().transition(self.mk("draft"), "exploded", actor="W0")

    def test_stale_rev(self):
        tid = self.mk("blocked")
        rev = self.ps().get(tid)["rev"]
        with self.assertRaises(StaleRev) as cm:
            self.ps().transition(tid, "queued", actor="W0", rev=rev - 1)
        self.assertEqual(cm.exception.exit_code, 4)
        t = self.ps().transition(tid, "queued", actor="W0", rev=rev)
        self.assertEqual(t["rev"], rev + 1)

    def test_every_transition_bumps_rev_and_logs(self):
        tid = self.mk("blocked")
        r0 = self.ps().get(tid)["rev"]
        self.ps().transition(tid, "queued", actor="W0")
        self.assertEqual(self.ps().get(tid)["rev"], r0 + 1)
        ev = [e for e in self.events(type="task.state") if e["task"] == tid][-1]
        self.assertEqual(ev["data"], {"from": "blocked", "to": "queued", "t": "T19", "by": "W0"})

    def test_terminal_states(self):
        for st in ("done", "cancelled"):
            tid = self.mk(st)
            for to in states.STATES:
                with self.assertRaises(IllegalTransition):
                    self.ps().transition(tid, to, actor="W0")
        tid = self.mk("failed")
        self.ps().transition(tid, "queued", actor="W0")   # T28 is the only way out

    def test_holdings_released(self):
        t, att = self.running(domains=["area:apps/api/**", "ext:pc-ssh"])
        s = self.ps()
        self.assertEqual(sorted(s.domains.holding(t["id"])), ["area:apps/api/**"])
        self.assertEqual(self.g.ext_domains.holding(f"shop-ops/{t['id']}"), ["ext:pc-ssh"])
        self.assertEqual(self.g.lanes.of("W1")[0]["state"], "running")
        s.transition(t["id"], "review", actor="SUP")
        self.assertEqual(s.domains.holding(t["id"]), [])
        self.assertEqual(self.g.ext_domains.holding(f"shop-ops/{t['id']}"), [])
        self.assertIsNone(s.slots.of(t["id"]))
        self.assertEqual(self.g.lanes.of("W1")[0]["state"], "free")

    def test_full_happy_path(self):
        t, att = self.running()
        s = self.ps()
        s.card_op(t["id"], att["n"], "step.add", {"title": "Read sources", "eta": 10}, att["token"])
        s.put_handoff(t["id"], att["n"], self.good_handoff())
        s.end_attempt(t["id"], att["n"], "handoff", 0)
        s.transition(t["id"], "review", actor="SUP")
        s.review(t["id"], "approve", by="W0")
        t = s.transition(t["id"], "done", actor="MQ")
        self.assertEqual(t["state"], "done")
        self.assertEqual([x["outcome"] for x in t["attempts"]], ["handoff"])


if __name__ == "__main__":
    unittest.main()
