"""T-ADP adapters (parse fixtures, argv builders, schema transforms, hooks) and T-QUO quota."""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from deck import quota
from deck.adapters import base as ab
from deck.adapters import schemas
from deck.adapters.interactive import install_claude_hooks, install_codex_notify
from deck.config import AccountCfg, ProjectCfg, WorkerCfg
from tests.helpers import T0

FIX = Path(__file__).parent / "fixtures"
SCHEMA = Path(__file__).resolve().parent.parent / "deck" / "schemas" / "handoff.schema.json"


def lines(name):
    return (FIX / name).read_text().splitlines()


def ctx(tmp, adapter="claude-headless", resume=None, permission=None, model=None, task=None):
    return ab.LaunchContext(
        project=ProjectCfg("shop", "personal", title="Shop"), task=task or {"id": "107-x", "title": "X"}, attempt=1,
        worker=WorkerCfg("W2", "acc", adapter, permission=permission, model=model),
        account=AccountCfg("acc", "Acc", "claude" if "claude" in adapter else "codex", "personal", "/tmp/home-acc"),
        workspace=Path(tmp), slot_env={"API_PORT": "4210"}, run_dir=Path(tmp), prompt="PROMPT",
        handoff_schema=SCHEMA, resume_session=resume, deck_env={"DECK_TASK": "107-x", "DECK_ATTEMPT": "1"})


class ClaudeAdapter(unittest.TestCase):
    def setUp(self):
        self.a = ab.get("claude-headless")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_parse_success_structured_output(self):
        st = {}
        sig = self.a.parse(lines("claude/success.jsonl"), T0, st)
        kinds = [type(s).__name__ for s in sig]
        self.assertEqual(kinds[0], "SessionId")
        self.assertIn("Usage", kinds)
        self.assertIn("Activity", kinds)
        final = [s for s in sig if isinstance(s, ab.Final)][0]
        self.assertEqual(final.handoff["outcome"], "done")
        self.assertFalse(final.is_error)
        u = [s for s in sig if isinstance(s, ab.Usage)][0]
        self.assertEqual((u.pct5, u.resets5), (25.0, T0 + 7200))

    def test_parse_limit_uses_absolute_resets_at(self):
        sig = self.a.parse(lines("claude/limit.jsonl"), T0, {})
        lims = [s for s in sig if isinstance(s, ab.Limit)]
        self.assertEqual(len(lims), 1)                          # one Limit, not one per line
        self.assertEqual(lims[0].resets_at, T0 + 3 * 3600)      # from rate_limit_event, not from "1:50am" text
        self.assertEqual(lims[0].kind, "five_hour")

    def test_parse_api_retry_exhausted_is_limit(self):
        sig = self.a.parse(lines("claude/api_retry.jsonl"), T0, {})
        lims = [s for s in sig if isinstance(s, ab.Limit)]
        self.assertEqual(len(lims), 1)
        self.assertIsNone(lims[0].resets_at)

    def test_plain_limit_text_fallback(self):
        sig = self.a.parse(["You've hit your 5-hour limit · resets 8:50pm"], T0, {})
        self.assertIsInstance(sig[0], ab.Limit)
        self.assertEqual(time.localtime(sig[0].resets_at).tm_hour, 20)

    def test_incremental_parse_keeps_state(self):
        st = {}
        ls = lines("claude/limit.jsonl")
        a = self.a.parse(ls[:2], T0, st)
        b = self.a.parse(ls[2:], T0, st)
        self.assertEqual(sum(isinstance(s, ab.Limit) for s in a + b), 1)

    def test_argv_new_session(self):
        spec = self.a.launch_spec(ctx(self.tmp, permission="bypass", model="opus"))
        argv = spec.argv
        self.assertEqual(argv[:5], ["claude", "-p", "--output-format", "stream-json", "--verbose"])
        self.assertIn("--session-id", argv)
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        self.assertNotIn("$schema", schema)                     # spike 1: claude rejects draft 2020-12 $schema
        self.assertNotIn("git", schema["properties"])           # daemon-only fields stripped
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "bypassPermissions")
        self.assertEqual(argv[argv.index("--permission-prompts") + 1], "none")
        self.assertEqual(argv[-1], "PROMPT")
        self.assertEqual(spec.env["CLAUDE_CONFIG_DIR"], "/tmp/home-acc")
        self.assertEqual(spec.env["CI"], "1")
        self.assertEqual(spec.env["API_PORT"], "4210")
        self.assertNotIn("CODEX_HOME", spec.env)

    def test_argv_resume_interrupted(self):
        c = ctx(self.tmp, resume="sess-1")
        c.interrupted = True
        spec = self.a.launch_spec(c)
        self.assertEqual(spec.argv[spec.argv.index("--resume") + 1], "sess-1")
        self.assertNotIn("--session-id", spec.argv)
        self.assertEqual(spec.env["CLAUDE_CODE_RESUME_INTERRUPTED_TURN"], "1")

    def test_cross_account_resume_copies_transcript(self):
        home = Path(self.tmp) / "home-a"
        (home / "projects" / "-tmp-ws").mkdir(parents=True)
        (home / "projects" / "-tmp-ws" / "sess-9.jsonl").write_text('{"type":"user"}\n')
        run = Path(self.tmp) / "run"
        run.mkdir()
        p = self.a.prepare_cross_account_resume(str(home), "sess-9", run)
        self.assertEqual(p, str(run / "sess-9.jsonl"))
        self.assertTrue(Path(p).exists())
        self.assertIsNone(self.a.prepare_cross_account_resume(str(home), "nope", run))


class CodexAdapter(unittest.TestCase):
    def setUp(self):
        self.a = ab.get("codex-exec")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_parse_success(self):
        sig = self.a.parse(lines("codex/success.jsonl"), T0, {})
        self.assertEqual(sig[0], ab.SessionId("01a1e023-7c00-79c2-b257-456dd539dc3e"))
        acts = [s for s in sig if isinstance(s, ab.Activity)]
        self.assertTrue(any("unittest" in a.text for a in acts))
        self.assertTrue(any("src/app.py" in a.text for a in acts))
        self.assertEqual([s.total for s in sig if isinstance(s, ab.Tokens)], [21644])

    def test_parse_limit_try_again_at(self):
        sig = self.a.parse(lines("codex/limit.jsonl"), T0, {})
        lims = [s for s in sig if isinstance(s, ab.Limit)]
        self.assertEqual(len(lims), 1)
        self.assertEqual(time.localtime(lims[0].resets_at).tm_hour, 20)

    def test_final_from_last_message_strips_nulls(self):
        shutil.copy(FIX / "codex/last-message.json", Path(self.tmp) / "last-message.json")
        f = self.a.final(Path(self.tmp), {})
        self.assertNotIn("nextStep", f.handoff)
        h = json.loads(SCHEMA.read_text())
        self.assertEqual(schemas.validate(f.handoff, h), [])

    def test_argv_new_and_resume(self):
        spec = self.a.launch_spec(ctx(self.tmp, "codex-exec"))
        argv = spec.argv
        self.assertEqual(argv[:3], ["codex", "exec", "--json"])
        self.assertIn("-C", argv)
        self.assertEqual(argv[argv.index("-s") + 1], "workspace-write")
        self.assertEqual(argv[argv.index("-o") + 1], str(Path(self.tmp) / "last-message.json"))
        strict = json.loads((Path(self.tmp) / "handoff.schema.json").read_text())
        self.assertEqual(sorted(strict["required"]), sorted(strict["properties"]))     # spike 2: strict mode
        self.assertEqual(spec.env["CODEX_HOME"], "/tmp/home-acc")
        r = self.a.launch_spec(ctx(self.tmp, "codex-exec", resume="th-1")).argv
        self.assertEqual(r[:4], ["codex", "exec", "resume", "th-1"])
        self.assertNotIn("-s", r)                                # resume rejects -s and -C (spike 2)
        self.assertNotIn("-C", r)
        self.assertIn('sandbox_mode="workspace-write"', r)
        self.assertIn("--output-schema", r)

    def test_strict_schema_nested_objects(self):
        s = schemas.for_codex(json.loads(SCHEMA.read_text()))
        dn = s["properties"]["decisionsNeeded"]["items"]
        self.assertEqual(sorted(dn["required"]), ["default", "options", "question"])
        self.assertIn("null", s["properties"]["nextStep"]["type"])
        self.assertFalse(s["additionalProperties"])


class OtherAdapters(unittest.TestCase):
    def test_interactive_offers_only(self):
        self.assertIsNone(ab.get("interactive").launch_spec(None))

    def test_shell_argv_and_synthetic_final(self):
        tmp = tempfile.mkdtemp()
        try:
            c = ctx(tmp, "shell", task={"id": "9-x", "title": "x", "command": "python3 scripts/run.py --all"})
            spec = ab.get("shell").launch_spec(c)
            self.assertEqual(spec.argv, ["python3", "scripts/run.py", "--all"])
            (Path(tmp) / "exit.json").write_text('{"code": 0}')
            self.assertEqual(ab.get("shell").final(Path(tmp), {}).handoff["outcome"], "partial")
        finally:
            shutil.rmtree(tmp)

    def test_hooks_install_idempotent_and_keeps_others(self):
        tmp = tempfile.mkdtemp()
        try:
            (Path(tmp) / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [
                {"type": "command", "command": "say done"}]}]}, "model": "x"}))
            install_claude_hooks(tmp)
            install_claude_hooks(tmp)
            s = json.loads((Path(tmp) / "settings.json").read_text())
            stop_cmds = [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]]
            self.assertEqual(stop_cmds, ["say done", "deck hb --turn-end"])
            self.assertEqual(s["model"], "x")
            self.assertEqual(len(s["hooks"]["PostToolUse"]), 1)
            (Path(tmp) / "config.toml").write_text('model = "o3"\n[profiles.x]\nmodel = "y"\n')
            install_codex_notify(tmp)
            install_codex_notify(tmp)
            t = (Path(tmp) / "config.toml").read_text()
            self.assertEqual(t.count("notify ="), 1)
            self.assertLess(t.index("notify ="), t.index("[profiles.x]"))
        finally:
            shutil.rmtree(tmp)


class Quota(unittest.TestCase):
    def test_claude_statusline_reading(self):
        r = quota.claude_status_reading("cl", str(FIX / "claude/statusline.json"), T0)
        self.assertEqual((r.pct5, r.pct7, r.resets5), (41.0, 22.0, T0 + 3600))

    def test_codex_rollout_newest_codex_reading(self):
        tmp = tempfile.mkdtemp()
        try:
            d = Path(tmp) / "sessions/2026/11/15"
            d.mkdir(parents=True)
            shutil.copy(FIX / "codex/rollout.jsonl", d / "rollout-x.jsonl")
            r = quota.codex_rollout_reading("cx", tmp, int(time.time()))
            self.assertEqual((r.pct5, r.pct7), (62.0, 55.0))      # newest 'codex' line, other models ignored
        finally:
            shutil.rmtree(tmp)

    def test_effective_rules(self):
        rd = {"read_at": T0 - 30, "pct5": 60, "pct7": 30, "resets5": T0 + 600, "resets7": T0 + 9000}
        e = quota.effective(rd, [], T0)
        self.assertEqual((e["eff5"], e["confidence"]), (60.0, "high"))
        self.assertEqual(quota.effective(dict(rd, read_at=T0 - 900), [], T0)["confidence"], "stale")
        rolled = quota.effective(dict(rd, resets5=T0 - 1), [], T0)
        self.assertEqual((rolled["eff5"], rolled["confidence"]), (0.0, "low"))
        self.assertEqual(quota.effective(None, [], T0)["confidence"], "unknown")
        self.assertIsNone(quota.effective(None, [], T0)["eff5"])           # never shown as 0 %
        lim = [{"resets_at": T0 + 100, "cleared_at": None}]
        self.assertTrue(quota.effective(rd, lim, T0)["cooling"])
        self.assertFalse(quota.effective(rd, lim, T0 + 101)["cooling"])

    def test_limit_time_converted_once_across_midnight(self):
        # detected at 23:30 local, "resets 1:50am" → tomorrow 01:50, never "today 01:50" (in the past)
        t = time.localtime(T0)
        detected = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 23, 30, 0, 0, 0, -1)))
        kind, at, _raw = quota.parse_limit_text("You've hit your 5-hour limit · resets 1:50am (Asia/Calcutta)", detected)
        self.assertEqual(kind, "five_hour")
        self.assertEqual(at - detected, 2 * 3600 + 20 * 60)

    def test_reading_clears_limit_only_if_newer(self):
        lim = {"detected_at": T0, "resets_at": T0 + 3600}
        self.assertFalse(quota.limit_cleared_by(lim, {"pct5": 10, "read_at": T0 - 5}))
        self.assertTrue(quota.limit_cleared_by(lim, {"pct5": 10, "read_at": T0 + 5}))
        self.assertFalse(quota.limit_cleared_by(lim, {"pct5": 100, "read_at": T0 + 5}))


if __name__ == "__main__":
    unittest.main()
