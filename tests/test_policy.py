"""T-POL (store, CLI and API parts): the office account must never run personal-project tasks."""
import random
import unittest

from deck import policy
from deck.config import AccountCfg, ProjectCfg
from deck.errors import IllegalTransition, PolicyDenied
from tests.helpers import CliCase, DeckCase


class TestMatrix(unittest.TestCase):
    """Full §2.3 table, as dicts and as config objects."""

    TABLE = [
        # account scope, project scope, allowPersonalAccounts, allowed, reason
        ("personal", "personal", False, True, None),
        ("personal", "personal", True, True, None),
        ("personal", "office", True, True, None),
        ("personal", "office", False, False, policy.NO_PERSONAL_ACCOUNTS),
        ("office", "office", False, True, None),
        ("office", "office", True, True, None),
        ("office", "personal", False, False, policy.OFFICE_NEVER_PERSONAL),
        ("office", "personal", True, False, policy.OFFICE_NEVER_PERSONAL),   # no flag can override the hard row
        (None, "personal", False, False, policy.ACCOUNT_UNTAGGED),
        (None, "office", True, False, policy.ACCOUNT_UNTAGGED),
        ("", "office", False, False, policy.ACCOUNT_UNTAGGED),
        ("Office", "office", False, False, policy.ACCOUNT_UNTAGGED),         # tags are exact, fail closed
        ("personal", None, False, False, policy.PROJECT_UNTAGGED),
        ("office", None, False, False, policy.PROJECT_UNTAGGED),
        ("personal", "work", False, False, policy.PROJECT_UNTAGGED),
    ]

    def test_dicts(self):
        for a, p, allow_personal, ok, reason in self.TABLE:
            acc = {"id": "x", "scope": a} if a is not None else {"id": "x"}
            prj = {"id": "p", "scope": p, "allowPersonalAccounts": allow_personal}
            got = policy.allow(acc, prj)
            self.assertEqual(got[0], ok, (a, p, allow_personal, got))
            if reason:
                self.assertEqual(got[1], reason)

    def test_objects(self):
        for a, p, allow_personal, ok, reason in self.TABLE:
            acc = AccountCfg(id="x", label="x", tool="claude", scope=a, home="/h")
            prj = ProjectCfg(id="p", scope=p, allowPersonalAccounts=allow_personal)
            self.assertEqual(policy.allow(acc, prj)[0], ok)

    def test_missing_account(self):
        self.assertEqual(policy.allow(None, {"scope": "office"}), (False, policy.NO_ACCOUNT))

    def test_allow_personal_must_be_true_not_truthy(self):
        self.assertFalse(policy.allow({"scope": "personal"}, {"scope": "office", "allowPersonalAccounts": "yes"})[0])


class TestStoreEnforcement(DeckCase):
    def alerts(self, pid="shop-ops"):
        return [e for e in self.events(pid, "policy.denied") if e["severity"] == "alert"]

    def test_lease_denies_office_on_personal_even_when_called_directly(self):
        t = self.queued("shop-ops")
        with self.assertRaises(PolicyDenied) as cm:
            self.ps().lease(t["id"], "W4", 1, "claude-office", slot=None, domains=[])
        self.assertEqual(cm.exception.exit_code, 3)
        self.assertEqual(self.ps().attempts(t["id"]), [])
        self.assertEqual(self.ps().get(t["id"])["state"], "queued")
        al = self.alerts()
        self.assertEqual(len(al), 1)
        self.assertEqual(al[0]["data"]["layer"], "lease")
        self.assertIn("office account may never run personal projects", al[0]["text"])
        self.assertTrue(any(e["type"] == "policy.denied" for e in self.g.events_since()), "mirrored globally")

    def test_lease_worker_account_mismatch_denied(self):
        t = self.queued("shop-ops")
        with self.assertRaises(PolicyDenied):   # W4 is office, claims to be codex-a
            self.ps().lease(t["id"], "W4", 1, "codex-a", slot=None, domains=[])
        self.assertEqual(self.ps().attempts(t["id"]), [])

    def test_lease_untagged_account_and_project(self):
        t = self.queued("shop-ops")
        with self.assertRaises(PolicyDenied):
            self.ps().lease(t["id"], "W5", 1, "claude-untagged", slot=None, domains=[])
        t2 = self.queued("no-scope")
        with self.assertRaises(PolicyDenied):
            self.ps("no-scope").lease(t2["id"], "W1", 1, "codex-a", slot=None, domains=[])

    def test_office_project_rules(self):
        t = self.queued("client-portal")
        with self.assertRaises(PolicyDenied):
            self.ps("client-portal").lease(t["id"], "W1", 1, "codex-a", slot=None, domains=[])
        att = self.ps("client-portal").lease(t["id"], "W4", 1, "claude-office", slot=None, domains=[])
        self.assertEqual(att["account"], "claude-office")
        t2 = self.queued("office-open")
        self.assertEqual(self.ps("office-open").lease(t2["id"], "W1", 1, "codex-a", slot=None, domains=[])["worker"], "W1")

    def test_pin_and_reassign_checked(self):
        t = self.new_task()
        with self.assertRaises(PolicyDenied):
            self.ps().edit(t["id"], {"pinWorker": "W4"}, by="W0")
        self.assertIsNone(self.ps().get(t["id"]).get("pinWorker"))
        run, _ = self.running()
        with self.assertRaises(PolicyDenied):
            self.ps().transition(run["id"], "queued", actor="W0", worker="W4", t="T30")
        self.assertEqual(self.ps().get(run["id"])["state"], "running")
        layers = sorted(e["data"]["layer"] for e in self.alerts())
        self.assertEqual(layers, ["pin", "reassign"])
        self.assertTrue(all("owner" in e["for"] for e in self.alerts()))

    def test_claim_from_office_home_into_personal_denied(self):
        t = self.queued("shop-ops")
        with self.assertRaises(PolicyDenied):
            self.ps().claim(t["id"], self.cfg.account("claude-office").home, "W6")
        self.assertEqual(self.ps().attempts(t["id"]), [])
        with self.assertRaises(PolicyDenied):   # unknown home
            self.ps().claim(t["id"], "/nowhere", "W0")
        with self.assertRaises(PolicyDenied):   # worker not on the session account
            self.ps().claim(t["id"], self.cfg.account("claude-worker").home, "W6")
        self.assertEqual(len(self.alerts()), 3)

    def test_claim_allowed(self):
        t = self.queued("client-portal", workerClass=["interactive", "claude-headless"])
        att = self.ps("client-portal").claim(t["id"], self.cfg.account("claude-office").home, "W6")
        self.assertEqual(self.ps("client-portal").get(t["id"])["state"], "running")
        self.assertEqual(att["account"], "claude-office")
        self.assertTrue(att["token"])

    def test_claim_offered_task(self):
        t = self.queued("shop-ops", workerClass=["interactive"])
        self.ps().lease(t["id"], "W0", 1, "claude-worker", slot=None, domains=[])   # offer
        with self.assertRaises(IllegalTransition):
            self.ps().claim(t["id"], self.cfg.account("codex-a").home, "W1")
        att = self.ps().claim(t["id"], self.cfg.account("claude-worker").home, "W0")
        self.assertEqual(self.ps().get(t["id"])["state"], "running")
        self.assertEqual(att["n"], 1)

    def test_eligible_lists_every_worker_with_reason(self):
        t = self.new_task("shop-ops")
        el = {e["worker"]: e for e in self.ps().eligible(t["id"])}
        self.assertEqual(set(el), set(self.cfg.workers))
        self.assertFalse(el["W4"]["allowed"])
        self.assertEqual(el["W4"]["reason"], policy.OFFICE_NEVER_PERSONAL)
        self.assertEqual(el["W4"]["scope"], "office")
        self.assertFalse(el["W5"]["allowed"])
        self.assertTrue(el["W1"]["allowed"])
        self.assertIsNone(el["W1"]["reason"])
        t2 = self.new_task("client-portal")
        el2 = {e["worker"]: e for e in self.ps("client-portal").eligible(t2["id"])}
        self.assertEqual(el2["W1"]["reason"], policy.NO_PERSONAL_ACCOUNTS)
        self.assertTrue(el2["W4"]["allowed"])

    def test_property_no_office_attempt_on_personal_project(self):
        rnd = random.Random(1234)
        pids = ["shop-ops", "client-portal", "office-open", "no-scope"]
        workers = list(self.cfg.workers.values())
        accounts = list(self.cfg.accounts)
        for i in range(160):
            pid = rnd.choice(pids)
            w = rnd.choice(workers)
            acct = w.account if rnd.random() < 0.7 else rnd.choice(accounts)   # sometimes lie about the account
            t = self.queued(pid, capability=rnd.choice(["code", "data"]), domains=[f"area:x{i}/**"],
                            workerClass=["claude-headless", "codex-exec", "interactive"])
            try:
                self.ps(pid).lease(t["id"], w.id, rnd.randint(1, 3), acct, slot=None, domains=[])
            except (PolicyDenied, IllegalTransition):
                pass
            try:
                self.ps(pid).claim(t["id"], self.cfg.account(rnd.choice(accounts)).home, w.id)
            except (PolicyDenied, IllegalTransition):
                pass
        bad = []
        n = 0
        for pid in pids:
            pscope = self.cfg.project(pid).scope
            for r in self.ps(pid).conn.execute("SELECT account, worker FROM attempts"):
                n += 1
                acc = self.cfg.account(r["account"])
                if acc.scope == "office" and pscope != "office":
                    bad.append((pid, r["worker"], r["account"]))
                self.assertTrue(policy.allow(acc, self.cfg.project(pid))[0])
                self.assertEqual(self.cfg.worker(r["worker"]).account, r["account"])
        self.assertFalse(bad)
        self.assertGreater(n, 10, "the property test must also produce allowed attempts")


class TestCliEnforcement(CliCase):
    def test_assign_office_worker_to_personal_exit_3(self):
        rc, out, err = self.cli("new", "Import the supplier list", "--summary", "Load suppliers so bills link", "--no-edit")
        self.assertEqual(rc, 0, err)
        tid = self.ps().list()[0]["id"]
        rc, out, err = self.cli("assign", tid, "W4")
        self.assertEqual(rc, 3)
        self.assertIn("office account may never run personal projects", err)
        rc, out, err = self.cli("edit", tid, "--pin", "W4")
        self.assertEqual(rc, 3)
        al = [e for e in self.ps().events.since(0) if e["type"] == "policy.denied"]
        self.assertEqual(len(al), 2)
        self.assertTrue(all(e["severity"] == "alert" and "owner" in e["for"] for e in al))
        rc, out, err = self.cli("assign", tid, "W1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(tid)["pinWorker"], "W1")

    def test_reassign_running_task_to_office_exit_3(self):
        t, att = self.running()
        rc, out, err = self.cli("reassign", t["id"], "W4")
        self.assertEqual(rc, 3, err)
        self.assertEqual(self.ps().get(t["id"])["state"], "running")
        rc, out, err = self.cli("reassign", t["id"], "W3")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(t["id"])["pinWorker"], "W3")

    def test_claim_from_office_session_exit_3(self):
        t = self.queued()
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg.account("claude-office").home
        rc, out, err = self.cli("claim", t["id"], "--worker", "W6")
        self.assertEqual(rc, 3)
        self.assertEqual(self.ps().attempts(t["id"]), [])


import os  # noqa: E402  (used by TestCliEnforcement)

if __name__ == "__main__":
    unittest.main()
