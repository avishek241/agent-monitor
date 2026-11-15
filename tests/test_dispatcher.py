"""T-DSP planner, T-DOM (planner side), T-SPARE, T-POL (dispatcher/launch/failover parts)."""
import random
import unittest

from deck import clock, policy
from tests.b2_helpers import T0, DeckCase, account, project, worker, write_exit, write_out


class Planner(DeckCase):
    def two_projects(self, **kw):
        self.build([account("cx", max=4)], [worker("W1", "cx", lanes=4)],
                   [project("shop", **kw), project("portal")])
        self.usage("cx", 5, 250)

    def test_candidate_order_resume_priority_unblocks_age(self):
        self.build([account("cx", max=1)], [worker("W1", "cx", lanes=1)], [project("shop")])
        self.usage("cx", 5, 250)
        self.add("shop", "old-p3", priority="P3", createdAt=T0 - 900)
        self.add("shop", "p1", priority="P1", createdAt=T0)
        self.add("shop", "p2-unblocks", priority="P2", createdAt=T0)
        self.add("shop", "p2-plain", priority="P2", createdAt=T0 - 100)
        self.add("shop", "child", state="ready", deps=["p2-unblocks"])
        res = self.add("shop", "resume", priority="P3")
        self.stores["shop"].tasks["resume"]["attempts"] = [{"n": 1, "worker": "W1", "adapter": "codex-exec",
                                                            "account": "cx", "outcome": "limit", "sessionId": "s1"}]
        order = [t for _p, t in [(p, t["id"]) for p, t in self.d.candidates(T0)]]
        self.assertEqual(order, ["resume", "p1", "p2-unblocks", "p2-plain", "old-p3"])

    def test_fair_share_across_projects(self):
        self.two_projects()
        for i in range(3):
            self.add("shop", f"s{i}", createdAt=T0 - 100 + i)
            self.add("portal", f"p{i}", createdAt=T0 + i)
        started = self.d.plan()
        projs = [p for p, *_ in started]
        self.assertEqual(sorted(projs), ["portal", "portal", "shop", "shop"])

    def test_capability_workerclass_and_pin(self):
        self.build([account("cx"), account("cl", tool="claude")],
                   [worker("W1", "cx", caps=["code"]), worker("W2", "cl", adapter="claude-headless", caps=["data"])],
                   [project("shop")])
        self.usage("cx", 5, 250)
        self.usage("cl", 5, 250)
        self.add("shop", "data-job", capability="data")
        self.add("shop", "code-claude-only", capability="code", workerClass=["claude-headless"])
        self.add("shop", "pinned", capability="code", pinWorker="W1")
        started = {t: w for _p, t, w, _l in self.d.plan()}
        self.assertEqual(started, {"data-job": "W2", "pinned": "W1"})
        self.assertIn("no configured worker", self.d.notes[("shop", "code-claude-only")])

    def test_one_job_per_account_per_round(self):
        self.build([account("a1"), account("a2")], [worker("W1", "a1"), worker("W2", "a2")], [project("shop")])
        self.usage("a1", 5, 250)
        self.usage("a2", 5, 250)
        for i in range(4):
            self.add("shop", f"t{i}", size="S", createdAt=T0 + i)
        started = self.d.plan()
        workers = [w for _p, _t, w, _l in started]
        self.assertEqual(len(started), 4)
        self.assertNotEqual(workers[0], workers[1])           # round 1 fills one lane on each account
        self.assertEqual(sorted(workers[:2]), ["W1", "W2"])

    def test_resume_prefers_own_worker(self):
        self.build([account("a1"), account("a2")], [worker("W1", "a1"), worker("W2", "a2")], [project("shop")])
        self.usage("a1", 5, 250)
        self.usage("a2", 50, 250)
        self.add("shop", "back")
        # the task last ran on W2 (account a2): within a2 the affinity picks W2; a1 is more urgent, so pin-free
        # resumes still go where quota is, but the resume session is only used on the same account
        self.stores["shop"].tasks["back"]["attempts"] = [{"n": 1, "worker": "W2", "adapter": "codex-exec",
                                                          "account": "a2", "outcome": "limit", "sessionId": "th-1",
                                                          "runDir": str(self.state)}]
        self.stores["shop"].tasks["back"]["pinWorker"] = "W2"
        started = self.d.plan()
        self.assertEqual(started[0][2], "W2")
        argv = self.launcher.calls[0]["spec"].argv
        self.assertEqual(argv[:4], ["codex", "exec", "resume", "th-1"])

    def test_replan_on_completion_within_one_tick(self):
        self.build([account("cx", max=1)], [worker("W1", "cx", lanes=1)], [project("shop")])
        self.usage("cx", 5, 250)
        self.add("shop", "first", createdAt=T0)
        self.add("shop", "second", createdAt=T0 + 1)
        self.d.tick()
        self.assertEqual(self.running(), ["first"])
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th"}])
        (rd / "last-message.json").write_text('{"outcome":"done","summary":"Imported everything as asked, see tests.",'
                                              '"doneWhen":[],"tests":[],"risks":[],"decisionsNeeded":[]}')
        write_exit(rd, 0)
        clock.advance(1)                                           # < 1 s later, no 5 s safety tick needed
        self.d.on_wake()
        self.assertEqual(self.stores["shop"].get("first")["state"], "review")
        self.assertEqual(self.running(), ["second"])


class Domains(DeckCase):
    def setUp(self):
        super().setUp()
        self.build([account("cx", max=5)], [worker("W1", "cx", lanes=5)],
                   [project("shop", domains={"seq:db-migrations": {"capacity": 0}, "res:demo-db": {"capacity": 2}}),
                    project("portal")])
        self.usage("cx", 5, 250)

    def test_overlapping_areas_serialise_disjoint_run_parallel(self):
        self.add("shop", "api", domains=["area:apps/api/**"], createdAt=T0)
        self.add("shop", "invoices", domains=["area:apps/api/src/invoices/**"], createdAt=T0 + 1)
        self.add("shop", "web", domains=["area:apps/web/**"], createdAt=T0 + 2)
        started = sorted(t for _p, t, _w, _l in self.d.plan())
        self.assertEqual(started, ["api", "web"])
        self.assertIn("area:apps/api/src/invoices/** held by api", self.d.notes[("shop", "invoices")])

    def test_no_area_blocks_every_code_task(self):
        self.add("shop", "everything", domains=["area:**"], createdAt=T0)
        self.add("shop", "web", domains=["area:apps/web/**"], createdAt=T0 + 1)
        self.assertEqual([t for _p, t, _w, _l in self.d.plan()], ["everything"])

    def test_ext_domain_exclusive_across_projects(self):
        self.add("shop", "pc-a", domains=["ext:pc-ssh"], createdAt=T0)
        self.add("portal", "pc-b", domains=["ext:pc-ssh"], createdAt=T0 + 1)
        self.assertEqual([t for _p, t, _w, _l in self.d.plan()], ["pc-a"])
        self.assertIn("ext:pc-ssh held by shop/pc-a", self.d.notes[("portal", "pc-b")])

    def test_seq_parallel_by_default_res_counted(self):
        for i in range(2):
            self.add("shop", f"mig{i}", domains=["seq:db-migrations"], createdAt=T0 + i)
        for i in range(3):
            self.add("shop", f"demo{i}", domains=["res:demo-db"], createdAt=T0 + 10 + i)
        started = sorted(t for _p, t, _w, _l in self.d.plan())
        self.assertEqual(started, ["demo0", "demo1", "mig0", "mig1"])

    def test_seq_strict_capacity_one(self):
        self.build([account("cx", max=5)], [worker("W1", "cx", lanes=5)],
                   [project("shop", domains={"seq:db-migrations": {"capacity": 1}})])
        self.usage("cx", 5, 250)
        for i in range(2):
            self.add("shop", f"mig{i}", domains=["seq:db-migrations"], createdAt=T0 + i)
        self.assertEqual(len(self.d.plan()), 1)

    def test_all_or_nothing_release_on_failure(self):
        self.add("shop", "holder", domains=["ext:pc-ssh"], createdAt=T0)
        self.d.plan()
        self.add("shop", "both", domains=["area:docs/**", "ext:pc-ssh"], createdAt=T0 + 1)
        self.d.plan()
        self.assertEqual(self.stores["shop"].domains.held_by("both"), [])     # area not kept when ext failed

    def test_released_domain_lets_waiter_run_after_completion(self):
        self.add("shop", "a", domains=["area:apps/**"], createdAt=T0)
        self.add("shop", "b", domains=["area:apps/web/**"], createdAt=T0 + 1)
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_exit(rd, 0)
        (rd / "last-message.json").write_text('{"outcome":"partial","summary":"Half done, the rest needs a call.",'
                                              '"doneWhen":[],"tests":[],"risks":[],"decisionsNeeded":[]}')
        self.d.tick()
        self.assertEqual(self.running(), ["b"])

    def test_anti_starvation_reservation(self):
        self.add("shop", "long", domains=["area:apps/**"], createdAt=T0)
        self.d.plan()
        self.add("shop", "waiter", domains=["area:apps/api/**"], priority="P2", createdAt=T0 + 1)
        self.d.plan()                                    # waiter starts waiting
        clock.advance(31 * 60)
        # the long task ends; a NEW overlapping task with higher priority must not jump the 30-min waiter
        self.stores["shop"].domains.release_all("long")
        self.stores["shop"].tasks["long"]["state"] = "review"
        self.stores["shop"].tasks["long"]["attempts"][-1]["outcome"] = "handoff"
        self.g.lanes.release("W1", 1)
        self.add("shop", "jumper", domains=["area:apps/api/x/**"], priority="P1", createdAt=T0 + 2000)
        started = [t for _p, t, _w, _l in self.d.plan()]
        self.assertIn("waiter", started)
        self.assertNotIn("jumper", started)


class SpareAndBacklog(DeckCase):
    def test_capacity_and_free_task_just_starts(self):
        self.build([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        self.add("shop", "t", size="S")
        self.d.tick()
        clock.advance(10 * 60)
        self.d.tick()
        self.assertEqual(self.events("account.spare"), [])

    def test_spare_alert_names_blocking_domain(self):
        self.build([account("cx"), account("cy")], [worker("W1", "cx"), worker("W2", "cy")], [project("shop")],
                   spareAlertMin=5)
        self.usage("cx", 5, 250)
        self.usage("cy", 90, 250)                 # cy is full
        self.g.ext_domains.try_acquire(["ext:pc-ssh"], "other/holder")
        self.add("shop", "t", domains=["ext:pc-ssh"])
        self.d.tick()
        self.assertEqual(self.events("account.spare"), [])
        clock.advance(6 * 60)
        self.d.tick()
        ev = self.events("account.spare")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["severity"], "action")
        self.assertIn("ext:pc-ssh", ev[0]["text"])
        clock.advance(6 * 60)
        self.d.tick()
        self.assertEqual(len(self.events("account.spare")), 1)          # once per spare period
        self.g.ext_domains.release_all("other/holder")
        self.d.tick()                                                    # job starts → alert state clears
        self.assertEqual(self.running(), ["t"])
        self.assertIsNone(self.d._spare["cx"]["since"])

    def test_office_idle_on_personal_work_is_info_not_action(self):
        self.build([account("office", tool="claude", scope="office")],
                   [worker("W4", "office", adapter="claude-headless")], [project("shop", scope="personal")])
        self.usage("office", 5, 250)
        self.add("shop", "t", workerClass=["claude-headless"])
        self.d.tick()
        clock.advance(6 * 60)
        self.d.tick()
        ev = self.events("account.spare")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["severity"], "info")
        self.assertIn("account policy", ev[0]["text"])
        self.assertEqual(self.running(), [])

    def test_backlog_low_once_per_30_min_with_size_hint(self):
        self.build([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        self.d.tick()
        ev = self.events("backlog.low")
        self.assertEqual(len(ev), 1)
        self.assertIn("XL fits", ev[0]["text"])
        clock.advance(10 * 60)
        self.d.tick()
        self.assertEqual(len(self.events("backlog.low")), 1)
        clock.advance(25 * 60)
        self.d.tick()
        self.assertEqual(len(self.events("backlog.low")), 2)


class PolicyRuntime(DeckCase):
    def test_matrix(self):
        from deck.config import AccountCfg, ProjectCfg
        A = lambda s: AccountCfg("a", "a", "claude", s, "/x")
        P = lambda s, allow=False: ProjectCfg("p", s, allowPersonalAccounts=allow)
        self.assertFalse(policy.allow(A("office"), P("personal"))[0])
        self.assertTrue(policy.allow(A("office"), P("office"))[0])
        self.assertTrue(policy.allow(A("personal"), P("personal"))[0])
        self.assertFalse(policy.allow(A("personal"), P("office"))[0])
        self.assertTrue(policy.allow(A("personal"), P("office", True))[0])
        self.assertFalse(policy.allow(A(None), P("office"))[0])
        self.assertFalse(policy.allow(A("personal"), P(None))[0])

    def test_dispatcher_never_leases_office_to_personal(self):
        self.build([account("office", tool="claude", scope="office"), account("mine")],
                   [worker("W4", "office", adapter="claude-headless"), worker("W1", "mine")],
                   [project("shop", scope="personal")])
        self.usage("office", 0, 290)          # most urgent account by far
        self.usage("mine", 60, 250)
        self.add("shop", "t", workerClass=["claude-headless", "codex-exec"], size="S")
        started = self.d.plan()
        self.assertEqual(started[0][2], "W1")
        self.assertEqual(self.events("policy.denied"), [])          # filtered before lease, no bug alert

    def test_launch_recheck_aborts_when_account_swapped(self):
        self.build([account("mine"), account("office", scope="office")], [worker("W1", "mine")],
                   [project("shop", scope="personal")])
        self.usage("mine", 5, 250)
        self.add("shop", "t", size="S")
        real = self.cfg.account_of_worker
        self.cfg.account_of_worker = lambda wid: self.cfg.account("office")    # config reloaded between lease+launch
        self.d.plan()
        self.cfg.account_of_worker = real
        ev = self.events("policy.denied")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["data"]["layer"], "launch")
        self.assertEqual(self.launcher.calls, [])
        t = self.stores["shop"].get("t")
        self.assertEqual(t["state"], "queued")
        self.assertEqual(t["attempts"][-1]["outcome"], "crashed")

    def test_lease_direct_call_denies_and_alerts(self):
        from deck.errors import PolicyDenied
        self.build([account("office", scope="office")], [worker("W4", "office")], [project("shop", scope="personal")])
        self.add("shop", "t")
        with self.assertRaises(PolicyDenied):
            self.stores["shop"].lease("t", "W4", 1, "office", slot=1, domains=[])
        self.assertEqual(self.events("policy.denied")[0]["severity"], "alert")

    def test_limit_failover_never_picks_office_for_personal(self):
        self.build([account("mine", tool="claude"), account("office", tool="claude", scope="office")],
                   [worker("W3", "mine", adapter="claude-headless"), worker("W4", "office", adapter="claude-headless")],
                   [project("shop", scope="personal")])
        self.usage("mine", 5, 250)
        self.usage("office", 0, 290)
        self.add("shop", "t", workerClass=["claude-headless"], size="S")
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "system", "subtype": "init", "session_id": "sess-1"},
                       {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": T0 + 4 * 3600,
                                                                        "rateLimitType": "five_hour"}}])
        write_exit(rd, 1)
        self.d.tick()
        t = self.stores["shop"].get("t")
        self.assertEqual(t["state"], "limit_paused")             # no failover: the only other account is office
        self.assertEqual(len(self.launcher.calls), 1)

    def test_property_random_configs_never_office_on_personal(self):
        rnd = random.Random(11)
        for _ in range(30):
            accs, wks = [], []
            for i in range(rnd.randint(1, 4)):
                accs.append(account(f"a{i}", tool="codex", scope=rnd.choice(["office", "personal", None])))
                wks.append(worker(f"W{i + 1}", f"a{i}"))
            projs = [project("p1", scope=rnd.choice(["office", "personal"]), allow_personal=rnd.random() < .5),
                     project("p2", scope=rnd.choice(["office", "personal", None]))]
            self.build(accs, wks, projs)
            for a in accs:
                self.usage(a["id"], rnd.uniform(0, 60), 200)
            for k in range(6):
                pid = rnd.choice(["p1", "p2"])
                self.add(pid, f"x{k}", pinWorker=rnd.choice([None, None, f"W{rnd.randint(1, len(wks))}"]))
            self.d.plan()
            for pid, st in self.stores.items():
                pscope = self.cfg.project(pid).scope
                for t in st.list():
                    for a in t["attempts"]:
                        ascope = self.cfg.account(a["account"]).scope
                        self.assertFalse(ascope == "office" and pscope == "personal")
                        self.assertIn(ascope, ("office", "personal"))


if __name__ == "__main__":
    unittest.main()
