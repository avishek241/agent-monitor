"""T-CARD: card ops, rejects (exit 2), merge never removes steps, continuation appends, append-only history,
v1 normaliser warnings, CLI worker flow (card → ask → handoff submit)."""
import json
import os
import sqlite3
import unittest

from deck import cards, clock, schema
from deck.errors import CardRejected
from tests.helpers import T0, CliCase, DeckCase


class TestOps(DeckCase):
    def setUp(self):
        super().setUp()
        self.t, self.att = self.running()
        self.s = self.ps()

    def op(self, op, **args):
        return self.s.card_op(self.t["id"], self.att["n"], op, args, self.att["token"])

    def test_steps_and_metrics(self):
        self.op("step.add", title="Read sources", eta=15)
        self.op("step.add", title="Map columns", eta=20, id="map")
        clock.advance(60)
        c = self.op("step.start", id="read-sources")
        self.assertEqual(c["steps"][0]["status"], "active")
        self.assertEqual(c["steps"][0]["startedAt"], T0 + 60)
        c = self.op("step.done", id="Read sources")
        self.assertEqual(c["steps"][0]["status"], "done")
        self.op("step.eta", id="map", eta=30)
        self.op("step.skip", id="map")
        self.op("metric", label="Files processed", done=120, total=500)
        c = self.op("metric", label="Files processed", done=130)
        self.assertEqual(c["metrics"], [{"label": "Files processed", "kind": "count", "done": 130, "total": 500,
                                          "updatedAt": T0 + 60}])
        self.op("metric", label="Errors", done=2, kind="bad")
        c = self.op("note", text="Profiling batch 3/8")
        self.assertEqual(c["note"], "Profiling batch 3/8")
        self.assertEqual(schema.validate(c, "card"), [])
        self.assertEqual(c["attempt"], 1)
        self.assertEqual(c["steps"][1]["etaMin"], 30)

    def test_duplicate_title_is_noop(self):
        self.op("step.add", title="Read sources", eta=15)
        rev = self.s.card(self.t["id"])["rev"]
        c = self.op("step.add", title="Read sources", eta=99)
        self.assertEqual(len(c["steps"]), 1)
        self.assertEqual(c["rev"], rev)

    def test_rejects(self):
        bad = [
            ("step.add", {"title": "Read sources"}),                 # missing eta
            ("step.add", {"title": "Read sources", "eta": 0}),
            ("step.add", {"title": "Read sources", "eta": -5}),
            ("step.add", {"title": "x" * 71, "eta": 5}),              # title over 70 chars
            ("step.add", {"title": "Ok title", "eta": 5, "colour": "red"}),   # unknown option
            ("step.start", {"id": "nope"}),
            ("metric", {"label": "Files", "done": -1}),
            ("metric", {"label": "Files", "done": 1, "total": 0}),
            ("metric", {"label": "Files", "done": "many"}),
            ("metric", {"label": "Files", "done": 1, "kind": "weird"}),
            ("note", {"text": "x" * 201}),
            ("rename", {"title": "x"}),                                # unknown op
        ]
        for op, args in bad:
            with self.assertRaises(CardRejected, msg=(op, args)) as cm:
                self.s.card_op(self.t["id"], self.att["n"], op, args, self.att["token"])
            self.assertEqual(cm.exception.exit_code, 2)
        self.assertEqual(len([e for e in self.events() if e["type"] == "card.rejected"]), len(bad))
        self.assertIsNone(self.s.card(self.t["id"]))

    def test_token_required(self):
        with self.assertRaises(CardRejected):
            self.s.card_op(self.t["id"], self.att["n"], "note", {"text": "hi"}, None)
        with self.assertRaises(CardRejected):
            self.s.card_op(self.t["id"], self.att["n"], "note", {"text": "hi"}, "forged")

    def test_limits(self):
        for i in range(20):
            self.op("step.add", title=f"Step number {i}", eta=5)
        with self.assertRaises(CardRejected):
            self.op("step.add", title="One too many", eta=5)
        for i in range(8):
            self.op("metric", label=f"Metric {i}", done=1)
        with self.assertRaises(CardRejected):
            self.op("metric", label="Metric 9", done=1)

    def test_card_op_is_heartbeat(self):
        clock.advance(300)
        self.op("note", text="still here")
        self.assertEqual(self.s.current_attempt(self.t["id"])["lastHeartbeat"], T0 + 300)

    def test_continuation_appends_never_resets(self):
        self.op("step.add", title="Read sources", eta=15)
        self.op("step.done", id="read-sources")
        self.s.transition(self.t["id"], "limit_paused", actor="ADP", resets_at=T0 + 10)
        clock.advance(200)
        self.s.transition(self.t["id"], "queued", actor="TMR")
        att2 = self.s.lease(self.t["id"], "W1", 1, "codex-a", slot=None, domains=[])
        self.assertEqual(att2["continuationOf"], 1)
        c = self.s.card_op(self.t["id"], 2, "step.add", {"title": "Map columns", "eta": 10}, att2["token"])
        self.assertEqual([(x["title"], x["status"], x.get("attempt")) for x in c["steps"]],
                         [("Read sources", "done", 1), ("Map columns", "pending", 2)])
        self.assertEqual(c["attempt"], 2)
        with self.assertRaises(CardRejected):    # the old attempt's token no longer writes
            self.s.card_op(self.t["id"], 2, "note", {"text": "x"}, self.att["token"])

    def test_history_append_only(self):
        self.op("step.add", title="Read sources", eta=15)
        self.op("note", text="hello")
        h = self.s.card_history(self.t["id"])
        self.assertEqual([x["op"] for x in h], ["step.add", "note"])
        with self.assertRaises(sqlite3.DatabaseError):
            self.s.conn.execute("UPDATE card_history SET op='x'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.s.conn.execute("DELETE FROM card_history")
        with self.assertRaises(sqlite3.DatabaseError):
            self.s.conn.execute("DELETE FROM events")
        with self.assertRaises(sqlite3.DatabaseError):
            self.g.conn.execute("UPDATE events SET text='x'")

    def test_ask_and_answer(self):
        c = self.op("ask", question="Import rows without GST as inactive?")
        self.assertEqual(c["question"], "Import rows without GST as inactive?")
        ev = [e for e in self.events() if e["type"] == "question.asked"][-1]
        self.assertEqual(ev["severity"], "action")
        self.assertEqual(ev["for"], ["W0"])
        self.s.transition(self.t["id"], "blocked", actor="ADP")
        self.s.transition(self.t["id"], "queued", actor="W0", answer="yes")
        self.assertIsNone(self.s.card(self.t["id"])["question"])


class TestNormaliser(unittest.TestCase):
    def test_v1_shapes(self):
        raw = {"steps": [{"name": "Plan", "status": "done", "etaMin": 10},
                         {"label": "Build", "status": "in-progress", "startedAt": 5},
                         {"title": "Test", "status": "pending", "etaMin": 15}],
               "metrics": {"Files": {"done": 3, "total": 10}, "Errors": 2},
               "note": "hello"}
        c = cards.normalize_v1(raw, "051-pilot")
        self.assertEqual([s["title"] for s in c["steps"]], ["Plan", "Build", "Test"])
        self.assertEqual(c["steps"][1]["status"], "active")
        self.assertIsNone(c["steps"][1]["etaMin"])
        self.assertEqual(c["metrics"][0], {"label": "Files", "done": 3.0, "total": 10.0, "kind": "count"})
        self.assertEqual(c["metrics"][1]["done"], 2.0)
        w = " | ".join(c["warnings"])
        self.assertIn("step key 'name' renamed to 'title'", w)
        self.assertIn("step key 'label' renamed to 'title'", w)
        self.assertIn("metrics was an object", w)
        self.assertIn("has no etaMin", w)

    def test_clean_v1_card_has_no_warnings(self):
        c = cards.normalize_v1({"steps": [{"title": "Plan", "status": "done", "etaMin": 5}],
                                "metrics": [{"label": "Files", "done": 1, "total": 2}]}, "1-x")
        self.assertEqual(c["warnings"], [])


class TestCliWorkerFlow(CliCase):
    """deck new → ready → assign → (lease) → card → ask/handoff submit → approve (design §17 Done-when)."""

    def test_end_to_end_with_fake_worker(self):
        rc, out, err = self.cli("new", "Import the supplier list", "--summary", "Load suppliers so bills link",
                                "--size", "S", "--domains", "area:apps/api/**", "--workers", "interactive,codex-exec",
                                "--no-edit")
        self.assertEqual(rc, 0, err)
        tid = self.ps().list()[0]["id"]
        self.write_brief("shop-ops", self.ps().get(tid))
        self.assertEqual(self.cli("ready", tid)[0], 0)
        self.assertEqual(self.cli("assign", tid, "W1")[0], 0)
        # the dispatcher (B2) leases; here we lease as it would
        att = self.ps().lease(tid, "W1", 1, "codex-a", slot=None, domains=[])
        self.ps().transition(tid, "running", actor="ADP")
        os.environ.update(DECK_PROJECT="shop-ops", DECK_TASK=tid, DECK_ATTEMPT=f"shop-ops:{tid}:1", DECK_TOKEN=att["token"])
        self.assertEqual(self.cli("card", "step", "add", "Read sources", "--eta", "15", project=None)[0], 0)
        self.assertEqual(self.cli("card", "step", "start", "read-sources", project=None)[0], 0)
        self.assertEqual(self.cli("card", "metric", "Files processed", "120", "--total", "500", project=None)[0], 0)
        self.assertEqual(self.cli("card", "note", "Profiling", "batch", "3/8", project=None)[0], 0)
        rc, out, err = self.cli("card", "step", "add", "No eta here", project=None)
        self.assertEqual(rc, 2, "missing --eta must exit 2")
        self.assertIn("--eta", err)
        rc, out, err = self.cli("hb", "--turn-end", project=None)
        self.assertEqual(rc, 0, err)
        card = self.ps().card(tid)
        self.assertEqual(card["steps"][0]["status"], "active")
        self.assertEqual(card["note"], "Profiling batch 3/8")
        # invalid handoff → exit 2, nothing changes
        rc, out, err = self.cli("handoff", "submit", "-", project=None, stdin=json.dumps({"outcome": "done"}))
        self.assertEqual(rc, 2)
        rc, out, err = self.cli("handoff", "submit", "-", project=None, stdin=json.dumps(self.good_handoff()))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(tid)["state"], "running", "headless adapters end the attempt themselves (B2)")
        self.ps().transition(tid, "review", actor="SUP")
        for k in ("DECK_PROJECT", "DECK_TASK", "DECK_ATTEMPT", "DECK_TOKEN"):
            os.environ.pop(k)
        rc, out, err = self.cli("review", "list")
        self.assertIn(tid, out)
        rc, out, err = self.cli("approve", tid)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(tid)["state"], "merging")
        rc, out, err = self.cli("merge", "status")
        self.assertIn(tid, out)

    def test_interactive_claim_ask_and_handoff(self):
        t = self.queued(workerClass=["interactive"])
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg.account("claude-worker").home
        rc, out, err = self.cli("claim", t["id"], "--worker", "W0")
        self.assertEqual(rc, 0, err)
        token = out.split("DECK_TOKEN=")[1].split()[0]
        os.environ.update(DECK_PROJECT="shop-ops", DECK_TASK=t["id"], DECK_ATTEMPT=f"shop-ops:{t['id']}:1", DECK_TOKEN=token)
        rc, out, err = self.cli("ask", "Import", "rows", "without", "GST?", project=None)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.cli("handoff", "submit", "-", project=None, stdin=json.dumps(self.good_handoff("blocked")))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(t["id"])["state"], "blocked")
        for k in ("DECK_PROJECT", "DECK_TASK", "DECK_ATTEMPT", "DECK_TOKEN"):
            os.environ.pop(k)
        rc, out, err = self.cli("answer", t["id"], "yes", "as", "inactive")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.ps().get(t["id"])["state"], "queued")

    def test_card_without_attempt_env_is_refused(self):
        rc, out, err = self.cli("card", "note", "hello", project=None)
        self.assertEqual(rc, 5)


if __name__ == "__main__":
    unittest.main()
