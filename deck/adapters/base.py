"""Adapter interface (design §7.1). B2 owns this file; the dataclass and method signatures are frozen.

Additions beyond §7.1 (additive only, no frozen signature changed):
- `Usage(Signal)`: a fresh usage reading seen in the agent's own stream (Claude `rate_limit_event`).
- `Adapter.final(run_dir, state)`: read a Final from a file after exit (Codex `-o last-message.json`).
- `UsageReading`: the shape `usage()` returns and `GlobalStore.usage.put()` takes.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Every agent process runs with these (design §6.5 "Prevention"): never wait for a human on stdin/editor/pager.
NONINTERACTIVE_ENV = {
    "CI": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "true",
    "EDITOR": "true",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "npm_config_yes": "true",
}


@dataclass
class LaunchContext:
    project: object                 # ProjectCfg
    task: dict
    attempt: int
    worker: object                  # WorkerCfg
    account: object                 # AccountCfg
    workspace: Path
    slot_env: dict
    run_dir: Path
    prompt: str                     # full prompt (§7.2), also written to run_dir/prompt.md
    handoff_schema: Path            # schemas/handoff.schema.json
    resume_session: Optional[str]   # session/thread id (or, for Claude cross-account, a transcript path)
    deck_env: dict                  # DECK_PROJECT, DECK_TASK, DECK_ATTEMPT, DECK_TOKEN, DECK_URL, DECK_STATE
    interrupted: bool = False       # previous attempt was interrupted (pause, hang) — Claude resumes the turn


@dataclass
class LaunchSpec:
    argv: list
    env: dict
    cwd: Path


class Signal:
    """Base of everything `parse()` returns."""


@dataclass
class SessionId(Signal):
    id: str


@dataclass
class Activity(Signal):
    kind: str
    text: str


@dataclass
class Tokens(Signal):
    total: int


@dataclass
class Limit(Signal):
    resets_at: Optional[int]        # absolute epoch, computed ONCE at detection, or None
    kind: str                       # 'five_hour' | 'weekly' | 'usage' | 'rate'
    raw: str


@dataclass
class Waiting(Signal):
    reason: str


@dataclass
class Final(Signal):
    handoff: Optional[dict]
    text: str
    is_error: bool


@dataclass
class ApiError(Signal):
    kind: str
    text: str


@dataclass
class Usage(Signal):
    pct5: Optional[float]
    pct7: Optional[float]
    resets5: Optional[int]
    resets7: Optional[int]


@dataclass
class UsageReading:
    account: str
    read_at: int
    source: str
    pct5: Optional[float]
    pct7: Optional[float]
    resets5: Optional[int]
    resets7: Optional[int]
    confidence: str = "high"

    def as_dict(self):
        return dict(account=self.account, read_at=self.read_at, source=self.source, pct5=self.pct5,
                    pct7=self.pct7, resets5=self.resets5, resets7=self.resets7, confidence=self.confidence)


class Adapter:
    name = "base"
    binary = None

    def check(self, account) -> tuple:
        if self.binary and not shutil.which(self.binary):
            return False, f"{self.binary} not found on PATH"
        home = getattr(account, "home", "")
        if home and not os.path.isdir(home):
            return False, f"account home {home} does not exist"
        return True, "ok"

    def launch_spec(self, ctx: LaunchContext) -> Optional[LaunchSpec]:
        raise NotImplementedError

    def parse(self, lines: list, now: int, state: dict) -> list:
        return []

    def usage(self, account, now: int) -> Optional[UsageReading]:
        return None

    def final(self, run_dir: Path, state: dict) -> Optional[Final]:
        return None

    # ---- helpers shared by adapters ----
    @staticmethod
    def base_env(ctx: LaunchContext) -> dict:
        env = dict(os.environ)
        # never let one account's identity leak into another's process
        for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "DECK_ACCOUNT", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            env.pop(k, None)
        env.update(NONINTERACTIVE_ENV)
        env.update({k: str(v) for k, v in (ctx.slot_env or {}).items()})
        env.update({k: str(v) for k, v in (ctx.deck_env or {}).items()})
        env["DECK_WORKER"] = ctx.worker.id
        env["DECK_ACCOUNT"] = ctx.account.id
        return env


_REGISTRY = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Adapter:
    from . import claude_headless, codex_exec, interactive, shell  # noqa: F401  (registers)
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(f"unknown adapter {name!r}") from None


def names():
    get("shell")
    return sorted(_REGISTRY)
