"""Shared fixtures: a temp state dir, a neutral machine config, fake clock."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from deck import clock
from deck.config import Config
from deck.store import GlobalStore, ProjectStore

T0 = 1794760000

GOOD_BRIEF = """# Import the supplier list
## Goal
Load the supplier list so purchase bills can link to suppliers.
## Rules
Follow the project rules. No live writes.
## Done when
- [ ] `python3 -m unittest` passes with 0 failures
- [ ] 53 suppliers exist in the staging table
## Stop point
After the 20-record pilot, before any live write.
"""


def machine(tmp):
    return {
        "kind": "machine", "stateDir": str(Path(tmp) / "state"),
        "accounts": [
            {"id": "codex-a", "label": "Codex · a", "tool": "codex", "scope": "personal", "home": str(Path(tmp) / "homes/codex-a")},
            {"id": "claude-worker", "label": "Claude · worker", "tool": "claude", "scope": "personal", "home": str(Path(tmp) / "homes/claude-worker")},
            {"id": "claude-office", "label": "Claude · office", "tool": "claude", "scope": "office", "home": str(Path(tmp) / "homes/claude-office"),
             "parallel": {"max": 2}},
            {"id": "claude-untagged", "label": "Claude · ?", "tool": "claude", "home": str(Path(tmp) / "homes/claude-untagged")},
        ],
        "workers": [
            {"id": "W0", "account": "claude-worker", "adapter": "interactive", "role": "orchestrator", "capabilities": ["code", "data", "review", "research"]},
            {"id": "W1", "account": "codex-a", "adapter": "codex-exec", "capabilities": ["code", "data", "ui"], "lanes": 3},
            {"id": "W3", "account": "claude-worker", "adapter": "claude-headless", "capabilities": ["code", "data", "ui", "research"], "lanes": 2},
            {"id": "W4", "account": "claude-office", "adapter": "claude-headless", "capabilities": ["code", "data", "ui"], "lanes": 2},
            {"id": "W6", "account": "claude-office", "adapter": "interactive", "capabilities": ["code", "data"]},
            {"id": "W5", "account": "claude-untagged", "adapter": "claude-headless", "capabilities": ["code"]},
        ],
        "projects": [
            {"id": "shop-ops", "config": "", "scope": "personal", "color": "#ff5a1f", "icon": "S"},
            {"id": "client-portal", "config": "", "scope": "office", "color": "#4f7cff"},
            {"id": "office-open", "config": "", "scope": "office", "allowPersonalAccounts": True},
            {"id": "no-scope", "config": ""},
        ],
    }


def project_file(tmp, pid):
    repo = Path(tmp) / "repos" / pid
    (repo / "docs/tasks").mkdir(parents=True, exist_ok=True)
    (repo / "apps/api").mkdir(parents=True, exist_ok=True)
    return {"kind": "project", "title": pid.replace("-", " ").title(), "repo": str(repo),
            "domains": {"seq:db-migrations": {"capacity": 0}, "ext:pc-ssh": {"capacity": 1}, "res:demo-db": {"capacity": 2}},
            "slots": {"count": 3}}


class DeckCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="deck-test-")
        clock.set(T0)
        self.m = machine(self.tmp)
        for a in self.m["accounts"]:
            Path(a["home"]).mkdir(parents=True, exist_ok=True)
        self.pf = {p["id"]: project_file(self.tmp, p["id"]) for p in self.m["projects"]}
        self.cfg = Config(self.m, self.pf)
        self.g = GlobalStore(self.cfg)
        self.stores = {}

    def tearDown(self):
        for s in self.stores.values():
            s.close()
        self.g.close()
        clock.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ps(self, pid="shop-ops"):
        if pid not in self.stores:
            self.stores[pid] = ProjectStore(pid, self.cfg, self.g)
        return self.stores[pid]

    def write_brief(self, pid, task, text=GOOD_BRIEF):
        p = Path(self.cfg.project(pid).repo) / task["brief"]["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def new_task(self, pid="shop-ops", **kw):
        f = {"title": "Import the supplier list", "summary": "Load suppliers so bills can link to them",
             "capability": "code", "size": "S", "priority": "P2", "domains": ["area:apps/api/**"]}
        f.update(kw)
        return self.ps(pid).create_task(f, by="W0")

    def ready_fake(self, pid, tid):
        """draft → ready without the brief validator (state-machine tests)."""
        return self.ps(pid).transition(tid, "ready", actor="W0", brief={"sha256": "x" * 64, "snapshot": "/tmp/snap.md"})

    def queued(self, pid="shop-ops", **kw):
        t = self.new_task(pid, **kw)
        self.ready_fake(pid, t["id"])
        return self.ps(pid).transition(t["id"], "queued", actor="DSP")

    def running(self, pid="shop-ops", worker="W1", account="codex-a", lane=1, **kw):
        t = self.queued(pid, **kw)
        att = self.ps(pid).lease(t["id"], worker, lane, account, slot=None, domains=[])
        t = self.ps(pid).transition(t["id"], "running", actor="ADP")
        return t, att

    def events(self, pid="shop-ops", type=None):
        evs = self.ps(pid).events.since(0, 10000)
        return [e for e in evs if type is None or e["type"] == type]

    def good_handoff(self, outcome="done"):
        return {"outcome": outcome, "summary": "Imported fifty-three suppliers into staging; tests pass.",
                "doneWhen": [{"item": "tests pass", "met": True, "evidence": "python3 -m unittest: OK"}],
                "tests": [{"command": "python3 -m unittest", "result": "pass", "passed": 10, "failed": 0}],
                "risks": [], "decisionsNeeded": []}


class CliCase(DeckCase):
    """DeckCase plus on-disk config files so `deck.cli.main` can run in-process."""

    def setUp(self):
        super().setUp()
        import copy
        m = copy.deepcopy(self.m)
        for p in m["projects"]:
            pf = Path(self.tmp) / f"{p['id']}.project.json"
            pf.write_text(json.dumps(self.pf[p["id"]]))
            p["config"] = str(pf)
        self.cfg_path = str(Path(self.tmp) / "deck.json")
        Path(self.cfg_path).write_text(json.dumps(m))
        self._env = {k: os.environ.get(k) for k in ("DECK_PROJECT", "DECK_TASK", "DECK_ATTEMPT", "DECK_TOKEN",
                                                    "DECK_WORKER", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "DECK_ACCOUNT")}
        for k in self._env:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def cli(self, *args, project="shop-ops", stdin=None):
        import contextlib
        import io
        import sys
        from deck import cli
        out, err = io.StringIO(), io.StringIO()
        old_in = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                pre = ["--config", self.cfg_path] + (["-p", project] if project else [])
                try:
                    rc = cli.main(pre + list(args))
                except SystemExit as e:
                    rc = e.code
        finally:
            sys.stdin = old_in
        return rc, out.getvalue(), err.getvalue()

    def cli_store(self, pid="shop-ops"):
        """A store on the same state dir as the CLI (fresh connection each time)."""
        from deck.config import Config
        cfg = Config.load(self.cfg_path)
        return ProjectStore(pid, cfg, GlobalStore(cfg))
