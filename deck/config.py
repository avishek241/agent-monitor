"""Private configuration (config.schema.json) as typed objects.

Frozen contract: ``AccountCfg``, ``WorkerCfg``, ``ProjectCfg`` and ``Config`` are what the
adapters (B2) receive in ``LaunchContext`` and what ``policy.allow`` takes.

The machine config lives at ``$DECK_CONFIG`` or ``~/.config/agent-deck/deck.json``; each project
entry points at its own project config file. Neither is ever in git.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG = "~/.config/agent-deck/deck.json"
DEFAULT_STATE = "~/.local/share/agent-deck"


def _x(p):
    return str(Path(os.path.expanduser(str(p))))


@dataclass
class AccountCfg:
    id: str
    label: str
    tool: str                       # 'claude' | 'codex'
    scope: Optional[str]            # 'office' | 'personal' | None (untagged: fail closed)
    home: str
    usage: dict = field(default_factory=dict)
    dispatch: bool = True
    parallel_max: int = 3
    reserve_pct: float = 5
    raw: dict = field(default_factory=dict)

    @property
    def ceiling_pct(self):
        return self.usage.get("ceilingPct", 92)

    @property
    def weekly_ceiling_pct(self):
        return self.usage.get("weeklyCeilingPct", 95)


@dataclass
class WorkerCfg:
    id: str
    account: str
    adapter: str
    role: str = "worker"
    capabilities: list = field(default_factory=list)
    permission: Optional[str] = None
    model: Optional[str] = None
    lanes: int = 3
    raw: dict = field(default_factory=dict)


@dataclass
class ProjectCfg:
    id: str
    scope: Optional[str]
    config_path: Optional[str] = None
    allowPersonalAccounts: bool = False
    weight: float = 1
    enabled: bool = True
    color: Optional[str] = None
    icon: Optional[str] = None
    # from the project file (kind=project)
    title: str = ""
    repo: str = ""
    mainBranch: str = "main"
    worktreesDir: Optional[str] = None
    briefsDir: str = "docs/tasks"
    rulesFiles: list = field(default_factory=lambda: ["AGENTS.md"])
    taskNumberStart: int = 1
    slots: dict = field(default_factory=dict)
    gates: list = field(default_factory=list)
    health: list = field(default_factory=list)
    legacy: dict = field(default_factory=dict)
    domains: dict = field(default_factory=dict)
    migrationRule: dict = field(default_factory=dict)
    setup: list = field(default_factory=list)
    worktreeInclude: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def slot_count(self):
        return int(self.slots.get("count", 6))


class Config:
    """Machine config + every project config, resolved."""

    def __init__(self, machine: dict, project_files: Optional[dict] = None, path: Optional[str] = None):
        self.path = path
        self.machine = machine
        self.state_dir = Path(_x(machine.get("stateDir") or os.environ.get("DECK_STATE") or DEFAULT_STATE))
        self.listen = {"host": "127.0.0.1", "port": 7788, "tailscale": False, **machine.get("listen", {})}
        self.scheduler = {"maxParallelTotal": 6, "minFreeRamGb": 2, "backlogTarget": 0, "spareAlertMin": 5,
                          "replanDebounceMs": 500, **machine.get("scheduler", {})}
        self.notify_command = machine.get("notifyCommand")
        self.accounts: dict[str, AccountCfg] = {}
        for a in machine.get("accounts", []):
            par = a.get("parallel", {})
            self.accounts[a["id"]] = AccountCfg(
                id=a["id"], label=a.get("label", a["id"]), tool=a.get("tool", "claude"), scope=a.get("scope"),
                home=_x(a.get("home", "")), usage=a.get("usage", {}), dispatch=a.get("dispatch", True),
                parallel_max=par.get("max", 3), reserve_pct=par.get("reservePct", 5), raw=a)
        self.workers: dict[str, WorkerCfg] = {}
        for w in machine.get("workers", []):
            self.workers[w["id"]] = WorkerCfg(
                id=w["id"], account=w["account"], adapter=w["adapter"], role=w.get("role", "worker"),
                capabilities=list(w.get("capabilities", [])), permission=w.get("permission"),
                model=w.get("model"), lanes=w.get("lanes", 3), raw=w)
        self.projects: dict[str, ProjectCfg] = {}
        project_files = project_files or {}
        for p in machine.get("projects", []):
            pf = project_files.get(p["id"])
            if pf is None and p.get("config"):
                try:
                    pf = json.loads(Path(_x(p["config"])).read_text())
                except (OSError, ValueError):
                    pf = {}
            pf = pf or {}
            known = {k: pf[k] for k in ProjectCfg.__dataclass_fields__ if k in pf and k not in ("id", "scope", "raw")}
            if "repo" in known:
                known["repo"] = _x(known["repo"])
            self.projects[p["id"]] = ProjectCfg(
                id=p["id"], scope=p.get("scope"), config_path=p.get("config"),
                allowPersonalAccounts=p.get("allowPersonalAccounts", False), weight=p.get("weight", 1),
                enabled=p.get("enabled", True), color=p.get("color"), icon=p.get("icon"),
                **known, raw=pf)
            if not self.projects[p["id"]].title:
                self.projects[p["id"]].title = p["id"]

    # ---- loading ----
    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        path = _x(path or os.environ.get("DECK_CONFIG") or DEFAULT_CONFIG)
        try:
            machine = json.loads(Path(path).read_text())
        except FileNotFoundError:
            machine = {"kind": "machine", "accounts": [], "workers": [], "projects": []}
        return cls(machine, path=path)

    def save(self):
        """Write the machine config back (only used by `deck init` / `deck project add`)."""
        if not self.path:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path + ".tmp"
        Path(tmp).write_text(json.dumps(self.machine, indent=2) + "\n")
        os.replace(tmp, self.path)

    # ---- lookups ----
    def account(self, aid) -> Optional[AccountCfg]:
        return self.accounts.get(aid)

    def worker(self, wid) -> Optional[WorkerCfg]:
        return self.workers.get(wid)

    def project(self, pid) -> Optional[ProjectCfg]:
        return self.projects.get(pid)

    def account_of_worker(self, wid) -> Optional[AccountCfg]:
        w = self.workers.get(wid)
        return self.accounts.get(w.account) if w else None

    def account_for_home(self, home: Optional[str]) -> Optional[AccountCfg]:
        """Identity from the credential home path (design §2.2), never from a label."""
        if not home:
            return None
        rp = os.path.realpath(_x(home))
        for a in self.accounts.values():
            if a.home and os.path.realpath(a.home) == rp:
                return a
        return None

    def account_from_env(self, env=None) -> Optional[AccountCfg]:
        env = os.environ if env is None else env
        acct = None
        for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            if env.get(var):
                acct = self.account_for_home(env[var])
                if acct:
                    break
        claimed = env.get("DECK_ACCOUNT")
        if claimed and (acct is None or acct.id != claimed):
            return None  # DECK_ACCOUNT only accepted when it agrees with the home mapping
        return acct

    def project_for_cwd(self, cwd: str) -> Optional[ProjectCfg]:
        cwd = os.path.realpath(cwd)
        best = None
        for p in self.projects.values():
            if p.repo:
                r = os.path.realpath(p.repo)
                if (cwd == r or cwd.startswith(r + os.sep)) and (best is None or len(r) > len(best.repo)):
                    best = p
        return best
