"""T-DOM (B1 part): glob overlap table, all-or-nothing sorted acquisition, ext: across projects, seq/res
capacities, merge reserved, anti-starvation reservation after 30 min, lease uses domains, CLI domain cmds."""
import unittest

from deck import clock
from deck.domains import overlaps
from deck.errors import DomainBusy
from tests.helpers import T0, CliCase, DeckCase


class TestOverlap(unittest.TestCase):
    TABLE = [
        ("area:apps/api/**", "area:apps/api/src/x/**", True),
        ("area:apps/api/**", "area:apps/web/**", False),
        ("area:**", "area:apps/web/**", True),
        ("area:**", "area:**", True),
        ("area:apps/api/**", "area:apps/api/**", True),
        ("area:apps/api", "area:apps/api-old/**", False),
        ("area:apps/api/x.ts", "area:apps/api/**", True),
        ("area:apps/api/x.ts", "area:apps/api/y.ts", False),
        ("area:packages/db/*.sql", "area:packages/db/migrations/**", True),
        ("area:apps/a*", "area:apps/web/**", True),          # conservative: unsure means overlap
        ("area:docs/**", "area:apps/**", False),
        ("area:./apps/api/**", "area:apps/api/src/**", True),
    ]

    def test_table(self):
        for a, b, want in self.TABLE:
            self.assertEqual(overlaps(a, b), want, (a, b))
            self.assertEqual(overlaps(b, a), want, (b, a))


class TestAcquire(DeckCase):
    def test_area_exclusive_on_overlap(self):
        d = self.ps().domains
        self.assertEqual(d.try_acquire(["area:apps/api/**"], "101"), (True, None))
        ok, b = d.try_acquire(["area:apps/api/src/x/**"], "102")
        self.assertFalse(ok)
        self.assertEqual(b["holder"], "101")
        self.assertIn("held by 101", b["reason"])
        self.assertTrue(d.try_acquire(["area:apps/web/**"], "103")[0])
        d.release_all("101")
        self.assertTrue(d.try_acquire(["area:apps/api/src/x/**"], "102")[0])

    def test_all_or_nothing(self):
        d = self.ps().domains
        d.try_acquire(["res:demo-db"], "a")
        d.try_acquire(["res:demo-db"], "b")      # capacity 2
        ok, b = d.try_acquire(["area:apps/web/**", "res:demo-db"], "c")
        self.assertFalse(ok)
        self.assertEqual(d.holding("c"), [], "nothing acquired when one domain is busy")

    def test_opposite_order_no_deadlock(self):
        d = self.ps().domains
        ok1, _ = d.try_acquire(["area:x/**", "area:y/**"], "t1")
        ok2, _ = d.try_acquire(["area:y/**", "area:x/**"], "t2")
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(d.holding("t2"), [])
        d.release_all("t1")
        self.assertTrue(d.try_acquire(["area:y/**", "area:x/**"], "t2")[0])

    def test_ext_exclusive_across_projects(self):
        a = self.ps("shop-ops").domains
        b = self.ps("office-open").domains
        self.assertTrue(a.try_acquire(["ext:pc-ssh"], "101")[0])
        ok, blk = b.try_acquire(["ext:pc-ssh"], "101")     # same task id, other project: still busy
        self.assertFalse(ok)
        self.assertEqual(blk["holder"], "shop-ops/101")
        a.release_all("101")
        self.assertTrue(b.try_acquire(["ext:pc-ssh"], "101")[0])

    def test_ext_failure_keeps_local_free(self):
        a = self.ps("shop-ops").domains
        self.ps("office-open").domains.try_acquire(["ext:pc-ssh"], "9")
        ok, _ = a.try_acquire(["area:apps/**", "ext:pc-ssh"], "101")
        self.assertFalse(ok)
        self.assertEqual(a.holding("101"), [])

    def test_seq_parallel_by_default_and_strict_mode(self):
        d = self.ps().domains
        self.assertTrue(d.try_acquire(["seq:db-migrations"], "1")[0])
        self.assertTrue(d.try_acquire(["seq:db-migrations"], "2")[0])
        self.cfg.projects["shop-ops"].domains["seq:db-migrations"] = {"capacity": 1}
        from deck.store import ProjectStore
        strict = ProjectStore("shop-ops", self.cfg, self.g)
        try:
            strict.domains.release_all("2")
            self.assertFalse(strict.domains.try_acquire(["seq:db-migrations"], "3")[0])
        finally:
            strict.close()

    def test_merge_reserved_capacity_one(self):
        d = self.ps().domains
        self.assertTrue(d.try_acquire(["merge"], "101")[0])
        self.assertFalse(d.try_acquire(["merge"], "102")[0])

    def test_ttl_expiry_and_renew(self):
        d = self.ps().domains
        d.try_acquire(["area:a/**"], "1", ttl=10)
        clock.advance(5 * 60)
        d.renew("1", ttl=10)
        clock.advance(8 * 60)
        self.assertFalse(d.try_acquire(["area:a/**"], "2")[0])
        clock.advance(3 * 60)
        self.assertTrue(d.try_acquire(["area:a/**"], "2")[0], "expired holdings are ignored")

    def test_anti_starvation_reservation(self):
        d = self.ps().domains
        d.try_acquire(["area:apps/api/**"], "holder")
        self.assertFalse(d.try_acquire(["area:apps/api/**"], "waiter")[0])
        clock.advance(31 * 60)
        self.assertFalse(d.try_acquire(["area:apps/api/**"], "waiter")[0])   # now reserved
        self.assertTrue(any(e["type"] == "domain.waiting" for e in self.events()))
        held = {x["name"]: x for x in d.held()}
        self.assertTrue(held["area:apps/api/**"]["reserved"])
        self.assertEqual(held["area:apps/api/**"]["waiting"], ["waiter"])
        d.release_all("holder")
        ok, b = d.try_acquire(["area:apps/api/src/**"], "newcomer")
        self.assertFalse(ok, "a newcomer cannot jump the reserved waiter")
        self.assertIn("reserved by waiter", b["reason"])
        self.assertTrue(d.try_acquire(["area:apps/api/**"], "waiter")[0])
        held = {x["name"]: x for x in d.held()}
        self.assertEqual(held["area:apps/api/**"]["holder"], "waiter")
        self.assertEqual(held["area:apps/api/src/**"]["waiting"], ["newcomer"])

    def test_lease_acquires_task_domains(self):
        a = self.queued(domains=["area:apps/api/**"])
        b = self.queued(domains=["area:apps/api/src/**"])
        self.ps().lease(a["id"], "W1", 1, "codex-a", slot=None, domains=[])
        with self.assertRaises(DomainBusy) as cm:
            self.ps().lease(b["id"], "W1", 2, "codex-a", slot=None, domains=[])
        self.assertEqual(cm.exception.exit_code, 6)
        self.assertEqual(self.ps().get(b["id"])["state"], "queued")
        self.assertEqual(self.ps().attempts(b["id"]), [])

    def test_slots(self):
        s = self.ps()
        self.assertEqual([s.slots.take(x) for x in ("a", "b", "c")], [1, 2, 3])
        self.assertIsNone(s.slots.take("d"))          # slots.count = 3
        self.assertEqual(s.slots.take("a"), 1)       # idempotent
        s.slots.release("b")
        self.assertEqual(s.slots.take("d"), 2)


class TestCliDomains(CliCase):
    def test_domain_run_and_list(self):
        rc, out, err = self.cli("domain", "run", "ext:pc-ssh", "--no-wait", "--", "true")
        self.assertEqual(rc, 0, err)
        self.g.ext_domains.try_acquire(["ext:pc-ssh"], "other/1")
        rc, out, err = self.cli("domain", "run", "ext:pc-ssh", "--no-wait", "--", "true")
        self.assertEqual(rc, 6)
        rc, out, err = self.cli("domain", "list")
        self.assertIn("ext:pc-ssh", out)
        rc, out, err = self.cli("domain", "release", "ext:pc-ssh")
        self.assertEqual(rc, 2)
        rc, out, err = self.cli("domain", "release", "ext:pc-ssh", "--force")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.g.ext_domains.held(), [])


if __name__ == "__main__":
    unittest.main()
