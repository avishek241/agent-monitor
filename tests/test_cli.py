"""CLI views and bookkeeping: snapshot validates against views_types, export, doctor, events/watch, init."""
import json
import os
import unittest
from pathlib import Path

from deck import views_types
from tests.helpers import CliCase


class TestCliViews(CliCase):
    def test_snapshot_overview_and_project(self):
        self.running()
        rc, out, err = self.cli("snapshot", "--overview", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(views_types.check_vm("OverviewVM", json.loads(out)), [])
        rc, out, err = self.cli("snapshot", "--project", "shop-ops", "--json", project=None)
        self.assertEqual(rc, 0, err)
        vm = json.loads(out)
        self.assertEqual(views_types.check_vm("ProjectVM", vm), [])
        self.assertEqual(vm["project"]["id"], "shop-ops")

    def test_list_board_show_export(self):
        t, _ = self.running()
        q = self.queued()
        rc, out, err = self.cli("list")
        self.assertIn(t["id"], out)
        self.assertIn("running", out)
        rc, out, err = self.cli("list", "--all-projects", "--json")
        self.assertEqual({x["id"] for x in json.loads(out)}, {t["id"], q["id"]})
        rc, out, err = self.cli("board")
        self.assertIn("== RUNNING (1)", out)
        self.assertIn("== UPNEXT (1)", out)
        rc, out, err = self.cli("show", t["id"], "--json")
        self.assertEqual(json.loads(out)["state"], "running")
        rc, out, err = self.cli("show", "999")
        self.assertEqual(rc, 5)
        rc, out, err = self.cli("export", "board", "--md")
        self.assertIn(f"| {t['id']} |", out)
        rc, out, err = self.cli("export", "titles")
        self.assertEqual(json.loads(out)[t["id"]]["title"], "Import the supplier list")

    def test_edit_priority_deps_domains(self):
        a = self.new_task()
        b = self.new_task()
        rc, out, err = self.cli("edit", b["id"], "--prio", "P1", "--deps", f"+{a['id']}", "--domains=-area:apps/api/**,+area:docs/**")
        self.assertEqual(rc, 0, err)
        t = self.ps().get(b["id"])
        self.assertEqual((t["priority"], t["deps"], t["domains"]), ("P1", [a["id"]], ["area:docs/**"]))
        rc, out, err = self.cli("new", "Urgent supplier fix", "--summary", "Fix the supplier import now", "--prio", "P0")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().list(["draft"])[-1]["priority"], "P0")
        rc, out, err = self.cli("edit", b["id"], "--prio", "P3", "--rev", "1")
        self.assertEqual(rc, 4)
        rc, out, err = self.cli("edit", b["id"], "--prio", "P9")
        self.assertEqual(rc, 2)

    def test_events_and_watch(self):
        self.new_task()
        rc, out, err = self.cli("events", "--json")
        self.assertEqual(json.loads(out)[0]["type"], "task.created")
        t, _ = self.running()
        self.ps().transition(t["id"], "review", actor="SUP")
        rc, out, err = self.cli("watch", "--for", "W0", "--since", "0", "--once")
        self.assertEqual(rc, 0, err)
        self.assertIn("ready to review", out)
        self.assertIn("ended without a valid handoff", out)
        inbox = Path(self.cfg.state_dir) / "inbox" / "W0.jsonl"
        self.assertTrue(any(json.loads(l)["type"] == "review.requested" for l in inbox.read_text().splitlines()))

    def test_doctor(self):
        rc, out, err = self.cli("doctor")
        self.assertEqual(rc, 2)
        self.assertIn("account claude-untagged has no scope tag", out)
        self.assertIn("project no-scope has no scope tag", out)

    def test_retry_with_attempts(self):
        tid = self.new_task()["id"]
        self.ps().conn.execute("UPDATE tasks SET state='failed' WHERE id=?", (tid,))
        rc, out, err = self.cli("retry", tid, "--attempts", "+2")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(tid)["retry"]["maxAttempts"], 5)

    def test_workers_and_accounts(self):
        rc, out, err = self.cli("workers")
        self.assertEqual(rc, 0, err)
        w4 = next(l for l in out.splitlines() if l.startswith("W4"))
        self.assertIn("may run: client-portal, office-open", w4)
        self.assertNotIn("shop-ops", w4)
        rc, out, err = self.cli("usage", "--json")
        self.assertIn("claude-office", {a["id"] for a in json.loads(out)})


class TestInit(CliCase):
    def test_init_and_project_add(self):
        cfg = Path(self.tmp) / "fresh" / "deck.json"
        os.environ["DECK_STATE"] = str(Path(self.tmp) / "fresh-state")
        try:
            rc, out, err = self.cli("--config", str(cfg), "init", project=None)
            self.assertEqual(rc, 0, err)
            self.assertTrue(cfg.exists())
            self.assertEqual(oct(cfg.stat().st_mode & 0o777), "0o600")
            tok = Path(self.tmp) / "fresh-state" / "token"
            self.assertEqual(oct(tok.stat().st_mode & 0o777), "0o600")
            pf = Path(self.tmp) / "p.json"
            pf.write_text(json.dumps({"kind": "project", "title": "Demo", "repo": self.tmp}))
            rc, out, err = self.cli("--config", str(cfg), "project", "add", "demo", "--config", str(pf), "--scope", "personal",
                                    project=None)
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(cfg.read_text())["projects"][0]["scope"], "personal")
        finally:
            os.environ.pop("DECK_STATE", None)


if __name__ == "__main__":
    unittest.main()
