"""T-VAL: brief validator rules V1–V13 (pass + fail), snapshot/sha, edit-after-ready → T2."""
import os
import unittest
from pathlib import Path

from deck import brief
from deck.errors import IllegalTransition, ValidationError
from tests.helpers import GOOD_BRIEF, CliCase, DeckCase


def rules(res, kind="errors"):
    return {r for r, _ in res[kind]}


class TestValidator(DeckCase):
    def task(self, text=GOOD_BRIEF, pid="shop-ops", **kw):
        t = self.new_task(pid, **kw)
        if text is not None:
            self.write_brief(pid, t, text)
        return t

    def check(self, t, pid="shop-ops"):
        return brief.validate(self.ps(pid), self.ps(pid).get(t["id"]))

    def test_good_brief_passes(self):
        res = self.check(self.task())
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["doneWhen"]), 2)
        self.assertIn("20-record pilot", res["stopPoint"])

    def test_V1_missing_or_outside_repo(self):
        res = self.check(self.task(text=None))
        self.assertIn("V1", rules(res))
        outside = Path(self.tmp) / "elsewhere.md"
        outside.write_text(GOOD_BRIEF)
        t = self.new_task(brief={"path": str(outside)})
        self.assertIn("V1", rules(self.check(t)))
        t2 = self.new_task(brief={"path": str(outside)}, isolation="none", capability="research", domains=[])
        self.assertNotIn("V1", rules(self.check(t2)))

    def test_V2_sections(self):
        for sec in ("Goal", "Rules", "Done when", "Stop point"):
            text = GOOD_BRIEF.replace(f"## {sec}", "## Something else")
            self.assertIn("V2", rules(self.check(self.task(text))), sec)
        empty_rules = GOOD_BRIEF.replace("Follow the project rules. No live writes.", "")
        self.assertIn("V2", rules(self.check(self.task(empty_rules))))
        self.assertNotIn("V2", rules(self.check(self.task(GOOD_BRIEF.replace("## Done when", "## DONE WHEN (checklist)")))))

    def test_V3_done_when_items(self):
        no_items = GOOD_BRIEF.replace("- [ ] `python3 -m unittest` passes with 0 failures\n- [ ] 53 suppliers exist in the staging table",
                                      "Everything should be fine.")
        self.assertIn("V3", rules(self.check(self.task(no_items))))
        vague = GOOD_BRIEF.replace("- [ ] 53 suppliers exist in the staging table", "- [ ] works")
        self.assertIn("V3", rules(self.check(self.task(vague))))
        long = GOOD_BRIEF.replace("- [ ] 53 suppliers exist in the staging table", "- [ ] " + "x" * 201)
        self.assertIn("V3", rules(self.check(self.task(long))))

    def test_V4_title_summary(self):
        t = self.task()
        s = self.ps()
        bad = dict(s.get(t["id"]), title="Fix `apps/api/x.ts`")
        self.assertIn("V4", rules(brief.validate(s, bad)))
        bad = dict(s.get(t["id"]), summary="short")
        self.assertIn("V4", rules(brief.validate(s, bad)))
        self.assertNotIn("V4", rules(self.check(t)))

    def test_V5_deps_exist_and_no_cycle(self):
        t = self.task()
        s = self.ps()
        self.assertIn("V5", rules(brief.validate(s, dict(s.get(t["id"]), deps=["999-nope"]))))
        a = self.task()
        b = self.task(deps=[a["id"]])
        s.conn.execute("UPDATE tasks SET data=json_set(data, '$.deps', json_array(?)) WHERE id=?", (b["id"], a["id"]))
        self.assertIn("V5", rules(self.check(a)))
        self.assertNotIn("V5", rules(self.check(self.task(deps=[t["id"]]))))

    def test_V6_domains(self):
        self.assertIn("V6", rules(self.check(self.task(domains=["area:apps/api/**", "ext:unknown-pc"]))))
        self.assertIn("V6", rules(self.check(self.task(domains=["area:/etc/**"]))))
        self.assertIn("V6", rules(self.check(self.task(domains=["area:../other/**"]))))
        ok = self.check(self.task(domains=["area:apps/api/**", "ext:pc-ssh", "seq:db-migrations", "res:demo-db"]))
        self.assertNotIn("V6", rules(ok))
        with self.assertRaises(ValidationError):   # 'merge' is reserved: the schema refuses it outright
            self.new_task(domains=["merge"])

    def test_V7_some_worker_can_run_it(self):
        self.assertIn("V7", rules(self.check(self.task(capability="browser"))))
        self.assertIn("V7", rules(self.check(self.task(pinWorker="W4"))))      # office pin on a personal project
        self.assertIn("V7", rules(self.check(self.task(pid="no-scope"), pid="no-scope")))
        self.assertNotIn("V7", rules(self.check(self.task())))
        # an office project with dispatch-able office worker passes; personal workers do not count
        self.assertNotIn("V7", rules(self.check(self.task(pid="client-portal"), pid="client-portal")))

    def test_V8_scale_needs_pilot(self):
        pilot = self.task(kind="pilot")
        t = self.task(kind="scale", pilotOf=pilot["id"])
        self.assertNotIn("V8", rules(self.check(t)))
        self.assertIn(pilot["id"], self.ps().get(t["id"])["deps"], "pilot added to deps")
        self.assertIn("V8", rules(self.check(self.task(kind="scale"))))
        self.assertIn("V8", rules(self.check(self.task(kind="scale", pilotOf="998-gone"))))

    def test_V9_secrets(self):
        for secret in ("AKIA" + "ABCDEFGHIJKLMNOP", "password: hunter2xyz", "sk-" + "a" * 30,
                       "-----BEGIN RSA PRIVATE KEY-----", "A" * 60):
            text = GOOD_BRIEF.replace("No live writes.", f"No live writes. {secret}")
            self.assertIn("V9", rules(self.check(self.task(text))), secret)

    def test_V10_paths_warn(self):
        text = GOOD_BRIEF.replace("No live writes.", "See `apps/api/missing.ts` and `apps/api`.")
        res = self.check(self.task(text))
        self.assertIn("V10", rules(res, "warnings"))
        self.assertEqual([m for r, m in res["warnings"] if r == "V10"], ["path `apps/api/missing.ts` does not exist in the repo"])
        self.assertEqual(res["errors"], [])

    def test_V11_size_and_restated_rules(self):
        big = GOOD_BRIEF + "\n" + ("filler line of context\n" * 1400)
        self.assertIn("V11", rules(self.check(self.task(big)), "warnings"))
        repo = Path(self.cfg.project("shop-ops").repo)
        lines = ["Every request body is parsed with a zod schema from contracts.",
                 "Every non-public route has a permission decorator on it."]
        (repo / "AGENTS.md").write_text("\n".join(lines) + "\n")
        text = GOOD_BRIEF.replace("Follow the project rules. No live writes.", "\n".join(lines))
        self.assertIn("V11", rules(self.check(self.task(text)), "warnings"))

    def test_V12_xl(self):
        self.assertIn("V12", rules(self.check(self.task(size="XL")), "warnings"))
        self.assertNotIn("V12", rules(self.check(self.task(size="L")), "warnings"))

    def test_V13_area_derivation(self):
        text = GOOD_BRIEF.replace("No live writes.", "Change `apps/web/src/page.tsx` and `packages/db/schema.ts`.")
        res = self.check(self.task(text, domains=[]))
        self.assertIn("V13", rules(res, "warnings"))
        self.assertEqual(sorted(res["domains"]), ["area:apps/web/**", "area:packages/db/**"])
        res2 = self.check(self.task(domains=[]))
        self.assertEqual(res2["domains"], ["area:**"])
        self.assertIn("declare areas", res2["warnings"][-1][1])
        res3 = self.check(self.task(domains=[], capability="data"))
        self.assertNotIn("V13", rules(res3, "warnings"))


class TestReady(DeckCase):
    def test_ready_snapshot_and_sha(self):
        t = self.new_task()
        p = self.write_brief("shop-ops", t)
        r = brief.ready(self.ps(), t["id"])
        self.assertEqual(r["state"], "ready")
        self.assertEqual(r["brief"]["sha256"], brief.sha256(GOOD_BRIEF))
        snap = Path(r["brief"]["snapshot"])
        self.assertTrue(snap.is_file())
        self.assertEqual(snap.read_text(), GOOD_BRIEF)
        self.assertTrue(str(snap).startswith(str(self.cfg.state_dir)))
        self.assertEqual(r["doneWhen"], ["`python3 -m unittest` passes with 0 failures", "53 suppliers exist in the staging table"])
        self.assertTrue(p.is_file())

    def test_ready_failure_records_T1x(self):
        t = self.new_task()
        self.write_brief("shop-ops", t, "# Nothing here\n")
        with self.assertRaises(ValidationError) as cm:
            brief.ready(self.ps(), t["id"])
        self.assertEqual(cm.exception.exit_code, 2)
        self.assertEqual(self.ps().get(t["id"])["state"], "draft")
        ev = [e for e in self.events() if e["type"] == "task.invalid"]
        self.assertTrue(any("V2" in x for x in ev[-1]["data"]["errors"]))

    def test_ready_only_from_draft(self):
        t = self.new_task()
        self.write_brief("shop-ops", t)
        brief.ready(self.ps(), t["id"])
        with self.assertRaises(IllegalTransition):
            brief.ready(self.ps(), t["id"])

    def test_edit_after_ready_goes_back_to_draft(self):
        t = self.new_task()
        p = self.write_brief("shop-ops", t)
        brief.ready(self.ps(), t["id"])
        self.assertIsNone(brief.check_changed(self.ps(), t["id"]))
        p.write_text(GOOD_BRIEF + "\nOne more rule.\n")
        self.assertEqual(brief.check_changed(self.ps(), t["id"]), "draft")
        t2 = self.ps().get(t["id"])
        self.assertEqual(t2["state"], "draft")
        self.assertEqual(t2["stateReason"], "brief edited after validation")

    def test_edit_while_running_only_warns(self):
        t, att = self.running()
        s = self.ps()
        s.conn.execute("UPDATE tasks SET data=json_set(data, '$.brief.sha256', 'old') WHERE id=?", (t["id"],))
        self.write_brief("shop-ops", t)
        self.assertEqual(brief.check_changed(s, t["id"]), "warned")
        self.assertEqual(s.get(t["id"])["state"], "running")

    def test_template(self):
        t = self.new_task()
        text = brief.render(t)
        sec = brief.sections(text)
        for k in ("goal", "rules", "done when", "stop point"):
            self.assertIn(k, sec)
        self.assertIn(t["id"], text.splitlines()[0])


class TestCliBriefFlow(CliCase):
    def test_new_validate_ready(self):
        rc, out, err = self.cli("new", "Import the supplier list", "--summary", "Load suppliers so bills link",
                                "--size", "S", "--domains", "area:apps/api/**", "--no-edit")
        self.assertEqual(rc, 0, err)
        tid = self.ps().list()[0]["id"]
        self.assertTrue(tid.startswith("001-"))
        rc, out, err = self.cli("validate", tid)
        self.assertEqual(rc, 2)
        self.assertIn("V2", out)
        self.write_brief("shop-ops", self.ps().get(tid))
        rc, out, err = self.cli("ready", tid)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(tid)["state"], "ready")
        rc, out, err = self.cli("unready", tid)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(tid)["state"], "draft")

    def test_numbers_never_reused(self):
        for i in range(3):
            self.cli("new", f"Task number {i} here", "--summary", "A summary of the task", "--no-edit")
        ids = [t["id"] for t in self.ps().list()]
        self.assertEqual([i.split("-")[0] for i in ids], ["001", "002", "003"])
        self.cli("cancel", ids[-1])
        self.cli("new", "Another task here", "--summary", "A summary of the task", "--no-edit")
        self.assertEqual(self.ps().list()[-1]["id"].split("-")[0], "004")


if __name__ == "__main__":
    unittest.main()
