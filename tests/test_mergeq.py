"""T-MQ: merge queue on real temporary git repos (design §6.8; M6, M7)."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from deck.mergeq import MergeQueue, git_dir
from tests.helpers import DeckCase, account, project, worker

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

RENUMBER = textwrap.dedent("""
    import os, re, subprocess, sys
    target = sys.argv[1]
    def names(args):
        out = subprocess.run(["git", *args], capture_output=True, text=True).stdout.split()
        return [n for n in out if n.startswith("migrations/")]
    mine = names(["diff", "--name-only", "--diff-filter=A", f"{target}...HEAD"])
    theirs = names(["ls-tree", "--name-only", "-r", target])
    top = max([int(re.match(r"migrations/(\\d+)", n).group(1)) for n in theirs if n not in mine] or [0])
    for n in sorted(mine):
        num = int(re.match(r"migrations/(\\d+)", n).group(1))
        if num <= top:
            top += 1
            new = re.sub(r"migrations/\\d+", f"migrations/{top:04d}", n)
            subprocess.run(["git", "mv", n, new], check=True)
""")


def sh(cwd, *args):
    r = subprocess.run(list(args), cwd=str(cwd), env=ENV, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"{args}: {r.stdout}{r.stderr}")
    return r.stdout


class MergeQueueTests(DeckCase):
    def setUp(self):
        super().setUp()
        os.environ.update({k: ENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                               "GIT_COMMITTER_EMAIL", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")})
        self.repo = self.state / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main")
        (self.repo / "app.txt").write_text("line one\n")
        (self.repo / "migrations").mkdir()
        (self.repo / "migrations" / "0029_base.sql").write_text("-- base\n")
        (self.repo / "renumber.py").write_text(RENUMBER)
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "init")

    def setup_mq(self, **pf):
        pf.setdefault("repo", str(self.repo))
        pf.setdefault("worktreesDir", str(self.state / "wt"))
        self.build([account("cx")], [worker("W1", "cx")], [project("shop", **pf)])
        self.mq = MergeQueue(self.d)
        self.d.mergeq = self.mq

    def task_branch(self, tid, files, slot=1, priority="P2"):
        wt = self.state / "wt" / tid
        sh(self.repo, "git", "worktree", "add", "-q", "-b", f"task/{tid}", str(wt), "main")
        for rel, text in files.items():
            p = wt / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        sh(wt, "git", "add", "-A")
        sh(wt, "git", "commit", "-q", "-m", f"{tid} work")
        self.add("shop", tid, state="merging", isolation="worktree", priority=priority,
                 workspace={"path": str(wt), "branch": f"task/{tid}", "base": "main", "slot": slot},
                 attempts=[{"n": 1, "worker": "W1", "adapter": "codex-exec", "account": "cx", "outcome": "handoff",
                            "slot": slot}])
        return wt

    def test_clean_merge_removes_worktree_and_branch(self):
        self.setup_mq()
        wt = self.task_branch("t1", {"new.txt": "hello\n"})
        self.assertEqual(self.mq.step("shop", self.stores["shop"]), ("t1", "done"))
        self.assertEqual(self.stores["shop"].get("t1")["state"], "done")
        self.assertTrue((self.repo / "new.txt").exists())
        self.assertFalse(wt.exists())
        self.assertEqual(sh(self.repo, "git", "branch", "--list", "task/t1").strip(), "")
        self.assertEqual(self.stores["shop"].domains.held_by("merge-queue"), [])

    def test_conflict_aborted_in_worktree_only(self):
        self.setup_mq()
        wt = self.task_branch("t1", {"app.txt": "line from task\n"})
        (self.repo / "app.txt").write_text("line from main\n")
        sh(self.repo, "git", "commit", "-qam", "main moved")
        self.assertEqual(self.mq.step("shop", self.stores["shop"])[1], "changes")
        t = self.stores["shop"].get("t1")
        self.assertEqual(t["state"], "changes_requested")
        self.assertIn("app.txt", t["review"]["note"])
        self.assertFalse((git_dir(wt) / "MERGE_HEAD").exists())        # our worktree merge aborted
        self.assertEqual((self.repo / "app.txt").read_text(), "line from main\n")
        self.assertEqual(len(self.events("merge.conflict")), 1)

    def test_foreign_merge_head_in_main_is_never_touched(self):
        """M6: a runner's `merge --abort` aborted the orchestrator's merge. The queue must leave it alone."""
        self.setup_mq()
        self.task_branch("t1", {"new.txt": "hello\n"})
        sh(self.repo, "git", "checkout", "-q", "-b", "side")
        (self.repo / "app.txt").write_text("side\n")
        sh(self.repo, "git", "commit", "-qam", "side")
        sh(self.repo, "git", "checkout", "-q", "main")
        (self.repo / "app.txt").write_text("main\n")
        sh(self.repo, "git", "commit", "-qam", "main")
        subprocess.run(["git", "merge", "side"], cwd=self.repo, env=ENV, capture_output=True)   # W0's own merge, in conflict
        mh = git_dir(self.repo) / "MERGE_HEAD"
        self.assertTrue(mh.exists())
        before = mh.read_text()
        self.assertEqual(self.mq.step("shop", self.stores["shop"])[1], "busy")
        self.assertTrue(mh.exists())
        self.assertEqual(mh.read_text(), before)
        self.assertEqual(self.stores["shop"].get("t1")["state"], "merging")
        self.assertIn("busy", self.events("merge.failed")[-1]["text"])
        self.assertIsNone(self.mq.step("shop", self.stores["shop"]))            # waits 2 min, no hammering

    def test_two_tasks_same_migration_number_second_renumbered(self):
        self.setup_mq(migrationRule={"paths": ["migrations/*"], "regenerate": f"{sys.executable} ../../repo/renumber.py main"})
        self.task_branch("ta", {"migrations/0030_a.sql": "-- a\n"})
        self.task_branch("tb", {"migrations/0030_b.sql": "-- b\n"})
        st = self.stores["shop"]
        self.assertEqual(self.mq.step("shop", st), ("ta", "done"))
        self.assertEqual(self.mq.step("shop", st), ("tb", "done"))
        names = sorted(p.name for p in (self.repo / "migrations").iterdir())
        self.assertEqual(names, ["0029_base.sql", "0030_a.sql", "0031_b.sql"])

    def test_gate_runs_in_fresh_slot_and_catches_hard_coded_port(self):
        """M7: a test hard-coded to the attempt's port/DB fails at the gate, not on someone else's machine."""
        gate = f"{sys.executable} -c \"import os,sys; sys.exit(0 if os.environ['API_PORT'] != '4110' else 0)\""
        hard = f"{sys.executable} -c \"import os,sys; print('API_PORT', os.environ['API_PORT']); sys.exit(0 if os.environ['API_PORT'] == '4110' else 1)\""
        slots = {"count": 4, "portBase": "4{slot}1", "env": {"API_PORT": "{portBase}0", "TEST_DB": "shop_test_{slot}"},
                 "canonicalPorts": [4000]}
        self.setup_mq(gates=[gate, hard], slots=slots)
        self.task_branch("t1", {"tests/api.test.ts": "fetch('http://localhost:4000/x')\n"}, slot=1)
        self.assertEqual(self.mq.step("shop", self.stores["shop"])[1], "changes")
        note = self.stores["shop"].get("t1")["review"]["note"]
        self.assertIn("failed in slot", note)
        self.assertNotIn("slot 1:", note)                                 # never the attempt's own slot
        warn = [e for e in self.events("merge.gate") if "hard-code" in e["text"]]
        self.assertEqual(len(warn), 1)
        self.assertIn("4000", warn[0]["text"])

    def test_p1_jumps_queue(self):
        self.setup_mq()
        self.task_branch("slow", {"a.txt": "a\n"}, priority="P3")
        self.task_branch("urgent", {"b.txt": "b\n"}, priority="P1")
        self.assertEqual(self.mq.step("shop", self.stores["shop"])[0], "urgent")

    def test_dirty_overlap_in_main_waits(self):
        self.setup_mq()
        self.task_branch("t1", {"app.txt": "task\n"})
        (self.repo / "app.txt").write_text("uncommitted edit by W0\n")
        self.assertEqual(self.mq.step("shop", self.stores["shop"])[1], "busy")
        self.assertEqual((self.repo / "app.txt").read_text(), "uncommitted edit by W0\n")
