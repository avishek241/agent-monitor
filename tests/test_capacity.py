"""T-CAP: capacity / parallel jobs per account (design §6.2, §16)."""
import random
import unittest

from deck import capacity, quota
from tests.helpers import T0, DeckCase, account, project, worker


def ai(eff5, resets_min, jobs=(), pct7=10.0, **kw):
    return capacity.AccountInput(id=kw.pop("id", "a"), eff5=eff5, pct7=pct7,
                                 resets5=None if resets_min is None else T0 + resets_min * 60, jobs=list(jobs), **kw)


class CapacityMaths(unittest.TestCase):
    def test_about_to_reset_preference_numbers(self):
        # (c) A at 80 % resetting in 10 min: R5 = 92-5-80 = 7; L burn 40/180 ≈ 0.222 %/min × 10 = 2.2 ≤ 7
        a = ai(80, 10)
        cap = capacity.account_capacity(a, T0)
        self.assertAlmostEqual(cap.R5, 7)
        ok, _ = capacity.admit(cap, a, 40, 180)
        self.assertTrue(ok)
        b = ai(40, 240, id="b")
        capb = capacity.account_capacity(b, T0)
        self.assertAlmostEqual(cap.urgency, 7 / 15, places=3)
        self.assertAlmostEqual(capb.urgency, 47 / 240, places=3)
        self.assertGreater(cap.urgency, capb.urgency)

    def test_nearly_full_account_refuses(self):
        a = ai(90, 60)                     # R5 = -3
        cap = capacity.account_capacity(a, T0)
        ok, why = capacity.admit(cap, a, 40, 180)
        self.assertFalse(ok)
        self.assertIn("5-hour quota", why)

    def test_only_spend_before_reset_counts(self):
        # a running L job with 170 min left only commits burn × T5 when the reset is 20 min away
        a = ai(50, 20, jobs=[capacity.Job(40, 180, 10)])
        cap = capacity.account_capacity(a, T0)
        self.assertAlmostEqual(cap.committed5, 40 / 180 * 20, places=3)

    def test_weekly_ceiling_and_p1_grace(self):
        a = ai(10, 200, pct7=93.0)          # R7 = 95 - 93 = 2; L needs 40 × 0.12 = 4.8
        cap = capacity.account_capacity(a, T0)
        self.assertFalse(capacity.admit(cap, a, 40, 180, "P2")[0])
        self.assertTrue(capacity.admit(cap, a, 40, 180, "P1")[0])          # 4.8 ≤ 2 + 5

    def test_caps_parallel_max_machine_ram(self):
        a = ai(10, 200, jobs=[capacity.Job(8, 30)] * 3, parallel_max=3)
        cap = capacity.account_capacity(a, T0)
        self.assertIn("lanes busy", capacity.admit(cap, a, 8, 30)[1])
        b = ai(10, 200)
        capb = capacity.account_capacity(b, T0)
        self.assertIn("machine cap", capacity.admit(capb, b, 8, 30, machine=capacity.Machine(6, 6, 16, 2))[1])
        self.assertIn("free RAM", capacity.admit(capb, b, 8, 30, machine=capacity.Machine(0, 6, 1.0, 2))[1])

    def test_unknown_reading_allows_one_job(self):
        a = ai(None, None)
        cap = capacity.account_capacity(a, T0)
        self.assertTrue(cap.unknown)
        self.assertTrue(capacity.admit(cap, a, 8, 30)[0])
        a2 = ai(None, None, jobs=[capacity.Job(8, 30)])
        self.assertIn("unknown", capacity.admit(capacity.account_capacity(a2, T0), a2, 8, 30)[1])

    def test_cooling_and_ceiling_hard_stops(self):
        a = ai(100, 30, cooling=True)
        self.assertIn("cooling", capacity.admit(capacity.account_capacity(a, T0), a, 1, 1)[1])
        b = ai(93, 30)
        self.assertIn("ceiling", capacity.admit(capacity.account_capacity(b, T0), b, 1, 1)[1])

    def test_learned_estimates_and_defaults(self):
        self.assertEqual(quota.estimate("L", "codex", []), (40.0, 180.0))
        hist = [{"account_tool": "codex", "size": "S", "delta5": d, "minutes": m, "ts": i}
                for i, (d, m) in enumerate([(4, 20), (6, 25), (5, 22), (30, 200)])]
        self.assertEqual(quota.estimate("S", "codex", hist), (5.5, 23.5))
        self.assertEqual(quota.estimate("S", "claude", hist), (8.0, 30.0))         # other tool → defaults
        self.assertEqual(quota.estimate("S", "codex", hist, {"pct5": 3, "minutes": None}), (3.0, 23.5))

    def test_property_never_overcommits(self):
        rnd = random.Random(7)
        for _ in range(400):
            jobs = []
            a = ai(rnd.uniform(0, 100), rnd.choice([None, 5, 30, 120, 290]), parallel_max=rnd.randint(1, 4),
                   pct7=rnd.uniform(0, 100))
            if a.resets5 is None:
                a.eff5 = rnd.choice([None, a.eff5])
            for _ in range(10):
                cap = capacity.account_capacity(a, T0)
                size = rnd.choice(capacity.SIZES)
                est, mins = quota.estimate(size, "codex")
                ok, _ = capacity.admit(cap, a, est, mins)
                if ok:
                    self.assertLess(len(a.jobs), a.parallel_max)
                    self.assertLessEqual(cap.committed5 + est / mins * min(cap.T5, mins), cap.R5 + 1e-6)
                    a.jobs.append(capacity.Job(est, mins))
                    jobs.append(size)
            self.assertLessEqual(len(a.jobs), a.parallel_max)


class ParallelPlanner(DeckCase):
    def setup_one(self, pct5, resets_min, n_tasks=3, size="S", max_=3):
        self.build([account("cx", max=max_)], [worker("W1", "cx", lanes=3)], [project("shop")])
        self.usage("cx", pct5, resets_min)
        for i in range(n_tasks):
            self.add("shop", f"t{i}", size=size, workerClass=["codex-exec"])

    def test_a_three_parallel_jobs_at_20pct(self):
        self.setup_one(20, 200)
        started = self.d.plan()
        self.assertEqual(len(started), 3)
        self.assertEqual(sorted(l for _p, _t, _w, l in started), [1, 2, 3])
        self.assertEqual(len(self.launcher.calls), 3)

    def test_b_80pct_admits_only_what_fits(self):
        # R5 = 92-5-80 = 7 ; S burn 8/30 × min(T5=200, 30) = 8 > 7 → none; at 75 % R5 = 12 → one (8), second 16 > 12
        self.setup_one(80, 200)
        self.assertEqual(self.d.plan(), [])
        self.assertIn("5-hour quota", self.stores["shop"].get("t0")["stateReason"])
        self.setup_one(75, 200)
        self.assertEqual(len(self.d.plan()), 1)

    def test_c_next_L_goes_to_about_to_reset_account(self):
        self.build([account("acc-a"), account("acc-b")],
                   [worker("W1", "acc-a"), worker("W2", "acc-b")], [project("shop")])
        self.usage("acc-a", 80, 10)
        self.usage("acc-b", 40, 240)
        self.add("shop", "big", size="L", workerClass=["codex-exec"])
        started = self.d.plan()
        self.assertEqual(started[0][2], "W1")
        # a 4th L task on A at 90 % with 60 min to reset is refused (R5 = -3) — B takes it instead
        self.build([account("acc-a"), account("acc-b")],
                   [worker("W1", "acc-a"), worker("W2", "acc-b")], [project("shop")])
        self.usage("acc-a", 90, 60)
        self.usage("acc-b", 40, 240)
        self.add("shop", "big2", size="L", workerClass=["codex-exec"])
        self.assertEqual(self.d.plan()[0][2], "W2")

    def test_e_parallel_max_and_machine_cap(self):
        self.setup_one(10, 200, n_tasks=5, max_=2)
        self.assertEqual(len(self.d.plan()), 2)
        self.build([account("cx", max=3)], [worker("W1", "cx")], [project("shop")], maxParallelTotal=1)
        self.usage("cx", 10, 200)
        for i in range(3):
            self.add("shop", f"m{i}", size="S", workerClass=["codex-exec"])
        self.assertEqual(len(self.d.plan()), 1)

    def test_e_low_ram_blocks(self):
        self.build([account("cx")], [worker("W1", "cx")], [project("shop")], ram=lambda: 0.5, minFreeRamGb=2)
        self.usage("cx", 10, 200)
        self.add("shop", "t", size="S", workerClass=["codex-exec"])
        self.assertEqual(self.d.plan(), [])
        self.assertIn("free RAM", self.stores["shop"].get("t")["stateReason"])

    def test_f_unknown_reading_one_job(self):
        self.build([account("cx")], [worker("W1", "cx")], [project("shop")])
        for i in range(3):
            self.add("shop", f"u{i}", size="S", workerClass=["codex-exec"])
        self.assertEqual(len(self.d.plan()), 1)

    def test_h_limit_on_one_lane_cools_account_others_untouched(self):
        self.setup_one(10, 200, n_tasks=4, max_=3)
        self.d.plan()
        self.assertEqual(len(self.running()), 3)
        self.g.limits.add("cx", "five_hour", T0, T0 + 3600, "adapter")
        self.assertEqual(self.d.plan(), [])                          # no new admits while cooling
        self.assertEqual(len(self.running()), 3)                     # running lanes untouched
        self.assertEqual(self.g.account_state.get("cx")["state"], "cooling")

    def test_i_property_random_accounts_never_exceed_caps(self):
        import random as r
        rnd = r.Random(3)
        for _round in range(25):
            n_acc = rnd.randint(1, 3)
            accs = [account(f"a{i}", max=rnd.randint(1, 3)) for i in range(n_acc)]
            wks = [worker(f"W{i + 1}", f"a{i}", lanes=rnd.randint(1, 3)) for i in range(n_acc)]
            self.build(accs, wks, [project("shop")])
            for i in range(n_acc):
                if rnd.random() < 0.8:
                    self.usage(f"a{i}", rnd.uniform(0, 95), rnd.choice([5, 60, 200, 290]))
            for k in range(rnd.randint(1, 8)):
                self.add("shop", f"r{k}", size=rnd.choice("SML"), workerClass=["codex-exec"])
            self.d.plan()
            for i, a in enumerate(accs):
                n = sum(1 for t in self.stores["shop"].list(["starting"]) if t["attempts"][-1]["account"] == a["id"])
                self.assertLessEqual(n, min(a["parallel"]["max"], wks[i]["lanes"]))


if __name__ == "__main__":
    unittest.main()
