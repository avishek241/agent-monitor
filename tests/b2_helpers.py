"""Shared test scaffolding: config builder, fake launcher, fake process probe, fake clock."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from deck import clock
from deck.config import Config
from deck.dispatcher import Dispatcher
from tests.fakes.store import make_stores

T0 = 1794760000          # a fixed "now" for fake-clock tests


def account(id, tool="codex", scope="personal", max=3, dispatch=True, home=None, **usage):
    return {"id": id, "label": id, "tool": tool, "scope": scope, "home": home or f"/tmp/deck-test-homes/{id}",
            "dispatch": dispatch, "parallel": {"max": max, "reservePct": 5}, "usage": usage}


def worker(id, account, adapter="codex-exec", lanes=3, caps=("code", "data", "ui", "infra", "research"), **kw):
    return {"id": id, "account": account, "adapter": adapter, "lanes": lanes, "capabilities": list(caps), **kw}


def project(id, scope="personal", allow_personal=False, weight=1, **pf):
    return ({"id": id, "config": f"/nonexistent/{id}.json", "scope": scope, "allowPersonalAccounts": allow_personal,
             "weight": weight}, {"kind": "project", "title": id.title(), "repo": "", **pf})


def make_cfg(accounts, workers, projects, state_dir, **sched):
    machine = {"kind": "machine", "stateDir": str(state_dir), "accounts": accounts, "workers": workers,
               "projects": [p for p, _f in projects], "scheduler": {"minFreeRamGb": 0, **sched}}
    return Config(machine, project_files={p["id"]: f for p, f in projects})


class FakeLauncher:
    def __init__(self):
        self.calls = []

    def __call__(self, spec, run_dir, run_id, ports):
        self.calls.append({"spec": spec, "run_dir": Path(run_dir), "run_id": run_id, "ports": ports})
        return 4242


class FakeProbe:
    """Processes are dicts: pid -> {alive, start, cpu}. kill() marks them dead and writes exit.json if asked."""

    def __init__(self):
        self.procs, self.killed = {}, []

    def alive(self, pid):
        return self.procs.get(pid, {}).get("alive", False)

    def process_start(self, pid):
        return self.procs.get(pid, {}).get("start")

    def group_cpu_secs(self, pgid):
        return self.procs.get(pgid, {}).get("cpu", 0.0)

    def deepest_child(self, pgid):
        return ("curl https://example.invalid/slow", 840)

    def group_pids(self, pgid):
        return [pgid] if self.alive(pgid) else []

    def kill(self, pgid, block=False):
        self.killed.append(pgid)
        if pgid in self.procs:
            self.procs[pgid]["alive"] = False
            rd = self.procs[pgid].get("run_dir")
            if rd:
                write_exit(rd, None, signal=2)
        return "SIGINT"


def write_exit(run_dir, code, signal=None, orphans=0):
    p = Path(run_dir) / "exit.json"
    p.write_text(json.dumps({"code": code, "signal": signal, "endedAt": clock.now(), "orphansKilled": orphans}))


def write_out(run_dir, lines):
    with open(Path(run_dir) / "out.jsonl", "a") as fh:
        for ln in lines:
            fh.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


def reading(account, pct5, resets_in_min, pct7=10.0, now=None, read_age=0):
    now = clock.now() if now is None else now
    return {"account": account, "read_at": now - read_age, "source": "test", "pct5": pct5, "pct7": pct7,
            "resets5": now + int(resets_in_min * 60), "resets7": now + 5 * 86400, "confidence": "high"}


class DeckCase(unittest.TestCase):
    """Fake clock + temp state dir + stores + dispatcher with fake launcher/probe."""

    def setUp(self):
        clock.set(T0)
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        clock.reset()
        self.tmp.cleanup()

    def build(self, accounts, workers, projects, read_usage=None, ram=lambda: 16.0, **sched):
        self.cfg = make_cfg(accounts, workers, projects, self.state, **sched)
        self.g, self.stores = make_stores(self.cfg)
        self.launcher = FakeLauncher()
        self.probe = FakeProbe()
        self.d = Dispatcher(self.cfg, self.g, self.stores, state_dir=self.state, launcher=self.launcher,
                            probe=self.probe, ram=ram, read_usage=read_usage or (lambda a, now: None))
        return self.d

    def add(self, pid, tid, state="queued", **fields):
        st = self.stores[pid]
        st.create_task({"id": tid, "title": f"Task {tid}", **fields})
        st.tasks[tid]["state"] = state
        return st.get(tid)

    def usage(self, aid, pct5, resets_in_min, **kw):
        self.g.usage.put(reading(aid, pct5, resets_in_min, **kw))

    def running(self, pid=None):
        out = []
        for p, st in self.stores.items():
            if pid and p != pid:
                continue
            out += [t["id"] for t in st.list(["starting", "running"])]
        return sorted(out)

    def start_proc(self, pid, tid, alive=True, cpu=0.0):
        """Simulate deck-run having started the agent: pid.json + probe entry."""
        t = self.stores[pid].get(tid)
        att = t["attempts"][-1]
        rd = Path(att["runDir"])
        fake_pid = 50000 + len(self.probe.procs)
        (rd / "pid.json").write_text(json.dumps({"pid": fake_pid, "pgid": fake_pid, "start": "Sun Nov 15 22:00:00 2026",
                                                 "wrapper": None}))
        self.probe.procs[fake_pid] = {"alive": alive, "start": "Sun Nov 15 22:00:00 2026", "cpu": cpu, "run_dir": rd}
        return fake_pid, rd

    def events(self, type):
        out = [e for e in self.g.events.rows if e["type"] == type]
        return out


os.environ.setdefault("DECK_TEST", "1")
