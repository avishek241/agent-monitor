"""Append-only event log (design §10): project table + global mirror + per-worker inbox files.

Project events have project-local ids. The global mirror (``global.db events``) has machine-wide
ids; SSE and ``/api/events?since=`` use the GLOBAL ids.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

from . import clock

SEVERITIES = ("debug", "info", "warn", "action", "alert")
RANK = {s: i for i, s in enumerate(SEVERITIES)}


def rank(sev: Optional[str]) -> int:
    return RANK.get(sev or "debug", 0)


def row_to_event(r, project=None) -> dict:
    d = dict(r)
    ev = {"id": d["id"], "ts": d["ts"], "project": d.get("project", project), "type": d["type"],
          "severity": d["severity"], "task": d["task"], "attempt": d["attempt"], "worker": d["worker"],
          "for": json.loads(d["for_"] or "[]"), "text": d["text"], "data": json.loads(d["data"] or "{}")}
    return ev


def append_inbox(state_dir: Path, ev: dict):
    for who in ev.get("for") or []:
        p = Path(state_dir) / "inbox" / f"{who}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps(ev, separators=(",", ":")) + "\n")


def touch_wake(state_dir: Path):
    p = Path(state_dir) / "wake"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a"):
            os.utime(p, None)
    except OSError:
        pass


class Events:
    """Events of one store. ``mirror(ev)`` (optional) copies to the global log after commit."""

    def __init__(self, conn, tx, project: Optional[str], state_dir: Path, mirror=None):
        self.conn, self.tx, self.project, self.state_dir, self.mirror = conn, tx, project, Path(state_dir), mirror

    def emit(self, type: str, severity: str, text: str, task: Optional[str] = None,
             attempt: Optional[int] = None, worker: Optional[str] = None, for_: Iterable[str] = (),
             data: Optional[dict] = None) -> dict:
        assert severity in RANK, severity
        ts = clock.now()
        text = (text or "")[:200]
        for_ = list(for_ or ())
        with self.tx() as c:
            cols = "ts, type, severity, task, attempt, worker, for_, text, data"
            vals = [ts, type, severity, task, attempt, worker, json.dumps(for_), text, json.dumps(data or {})]
            if self.mirror is None and self.project is None:   # this IS the global log
                cols += ", project"
                vals.append(None)
            cur = c.execute(f"INSERT INTO events({cols}) VALUES({','.join('?' * len(vals))})", vals)
            ev = {"id": cur.lastrowid, "ts": ts, "project": self.project, "type": type, "severity": severity,
                  "task": task, "attempt": attempt, "worker": worker, "for": for_, "text": text, "data": data or {}}

            def after(ev=ev):
                if self.mirror:
                    self.mirror(ev)
                append_inbox(self.state_dir, ev)
                touch_wake(self.state_dir)
            self.tx.on_commit(after)
        return ev

    def since(self, since_id: int = 0, limit: int = 500, min_severity: Optional[str] = None,
              task: Optional[str] = None, types: Optional[Iterable[str]] = None) -> list:
        q = "SELECT * FROM events WHERE id > ?"
        args: list = [since_id]
        if task:
            q += " AND task = ?"
            args.append(task)
        if min_severity:
            sevs = [s for s in SEVERITIES if rank(s) >= rank(min_severity)]
            q += f" AND severity IN ({','.join('?' * len(sevs))})"
            args += sevs
        if types:
            types = list(types)
            q += f" AND type IN ({','.join('?' * len(types))})"
            args += types
        q += " ORDER BY id LIMIT ?"
        args.append(limit)
        return [row_to_event(r, self.project) for r in self.conn.execute(q, args)]

    def last(self, n: int = 50, task: Optional[str] = None, min_severity: Optional[str] = None) -> list:
        q, args = "SELECT * FROM events WHERE 1=1", []
        if task:
            q += " AND task=?"
            args.append(task)
        if min_severity:
            sevs = [s for s in SEVERITIES if rank(s) >= rank(min_severity)]
            q += f" AND severity IN ({','.join('?' * len(sevs))})"
            args += sevs
        q += " ORDER BY id DESC LIMIT ?"
        args.append(n)
        return list(reversed([row_to_event(r, self.project) for r in self.conn.execute(q, args)]))
