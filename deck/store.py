"""SQLite store (design §3, §5). The single choke point for every state change and every lease.

``transition()`` and ``lease()`` run inside ``BEGIN IMMEDIATE``; the account policy is checked
inside ``lease()`` and ``claim()`` so no code path can skip it.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import cards, clock, policy, schema, states
from .config import Config
from .db import Tx, connect, migrate
from .domains import Domains, kind_of
from .errors import (CardRejected, DomainBusy, IllegalTransition, NotFound, PolicyDenied, StaleRev,
                     ValidationError)
from .events import Events, row_to_event, rank
from .store_api import GlobalStoreAPI, ProjectStoreAPI

LEASE_SEC = 120
DEFAULTS = {"priority": "P2", "size": "M", "capability": "code", "kind": "normal",
            "workerClass": ["claude-headless", "codex-exec"], "deps": [], "domains": [], "tags": []}
READ_ONLY = {"brief.sha256", "brief.snapshot", "brief.validatedAt", "doneWhen", "stopPoint", "state", "stateSince",
             "stateReason", "resumeAt", "worker", "attempts", "workspace", "review", "createdAt", "updatedAt", "rev"}
EDITABLE = {"title", "summary", "tags", "priority", "size", "kind", "pilotOf", "deps", "capability", "workerClass",
            "pinWorker", "domains", "estimate", "isolation", "timeouts", "retry", "merge", "waitingFor", "brief"}
ATTEMPT_COLS = ("worker", "account", "adapter", "session_id", "pid", "pgid", "pid_start", "run_dir", "slot",
                "started_at", "ended_at", "lease_until", "last_heartbeat", "cpu_secs", "out_offset", "exit_code",
                "outcome", "usage_start", "usage_end", "tokens", "continuation_of", "token_hash", "lane")
CAMEL = {"session_id": "sessionId", "pid_start": "pidStart", "run_dir": "runDir", "started_at": "startedAt",
         "ended_at": "endedAt", "lease_until": "leaseUntil", "last_heartbeat": "lastHeartbeat",
         "exit_code": "exitCode", "usage_start": "usageStart", "usage_end": "usageEnd",
         "continuation_of": "continuationOf", "cpu_secs": "cpuSecs", "out_offset": "outOffset"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _hm(ts: Optional[int]) -> str:
    import time
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "?"


def attempt_dict(row) -> dict:
    d = dict(row)
    out = {"task": d["task"], "n": d["n"]}
    for col in ATTEMPT_COLS:
        if col == "token_hash":
            continue
        out[CAMEL.get(col, col)] = d.get(col)
    return out


def _schema_attempt(a: dict) -> dict:
    keys = schema.load("task")["$defs"]["attempt"]["properties"].keys()
    return {k: a[k] for k in keys if k in a and (a[k] is not None or k in ("endedAt", "exitCode"))}


# ---------------------------------------------------------------- global store
class _Usage:
    def __init__(self, g):
        self.g = g

    def put(self, reading) -> None:
        r = asdict(reading) if is_dataclass(reading) else dict(reading)
        with self.g.tx() as c:
            c.execute("INSERT OR REPLACE INTO usage(account, read_at, source, pct5, pct7, resets5, resets7, confidence)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (r["account"], r.get("read_at") or clock.now(), r.get("source"), r.get("pct5"), r.get("pct7"),
                       r.get("resets5"), r.get("resets7"), r.get("confidence")))

    def latest(self, account: str) -> Optional[dict]:
        row = self.g.conn.execute("SELECT * FROM usage WHERE account=? ORDER BY read_at DESC LIMIT 1", (account,)).fetchone()
        return dict(row) if row else None


class _Limits:
    def __init__(self, g):
        self.g = g

    def add(self, account, kind, detected_at, resets_at, source, project=None, task=None, attempt=None, raw=None) -> int:
        with self.g.tx() as c:
            return c.execute("INSERT INTO limits(account, kind, detected_at, resets_at, source, project, task, attempt, raw)"
                             " VALUES(?,?,?,?,?,?,?,?,?)",
                             (account, kind, detected_at, resets_at, source, project, task, attempt, raw)).lastrowid

    def active(self, account, now=None) -> list:
        now = clock.now() if now is None else now
        return [dict(r) for r in self.g.conn.execute(
            "SELECT * FROM limits WHERE account=? AND cleared_at IS NULL AND (resets_at IS NULL OR resets_at > ?)"
            " ORDER BY detected_at", (account, now))]

    def clear(self, limit_id, at=None):
        with self.g.tx() as c:
            c.execute("UPDATE limits SET cleared_at=? WHERE id=? AND cleared_at IS NULL", (at or clock.now(), limit_id))


class _Lanes:
    def __init__(self, g):
        self.g = g

    def running(self, account: Optional[str] = None) -> list:
        rows = [dict(r) for r in self.g.conn.execute("SELECT * FROM lanes WHERE state='running'")]
        if account:
            ws = {w.id for w in self.g.cfg.workers.values() if w.account == account}
            rows = [r for r in rows if r["worker"] in ws]
        return rows

    def take(self, worker, project=None, task=None, attempt=None) -> Optional[int]:
        w = self.g.cfg.worker(worker)
        if not w:
            return None
        acct = self.g.cfg.account(w.account)
        with self.g.tx() as c:
            if acct and len(self.running(acct.id)) >= acct.parallel_max:
                return None
            busy = {r["lane"] for r in c.execute("SELECT lane FROM lanes WHERE worker=? AND state='running'", (worker,))}
            for lane in range(1, w.lanes + 1):
                if lane not in busy:
                    self.mark(worker, lane, "running", project, task, attempt)
                    return lane
        return None

    def mark(self, worker, lane, state, project=None, task=None, attempt=None):
        with self.g.tx() as c:
            c.execute("INSERT OR REPLACE INTO lanes(worker, lane, state, project, task, attempt, since) VALUES(?,?,?,?,?,?,?)",
                      (worker, lane, state, project, task, attempt, clock.now()))

    def release(self, worker, lane) -> None:
        with self.g.tx() as c:
            c.execute("UPDATE lanes SET state='free', project=NULL, task=NULL, attempt=NULL, since=? WHERE worker=? AND lane=?",
                      (clock.now(), worker, lane))

    def of(self, worker) -> list:
        return [dict(r) for r in self.g.conn.execute("SELECT * FROM lanes WHERE worker=? ORDER BY lane", (worker,))]


class _AccountState:
    def __init__(self, g):
        self.g = g

    def put(self, account, state, capacity) -> None:
        with self.g.tx() as c:
            prev = c.execute("SELECT spare_since FROM account_state WHERE account=?", (account,)).fetchone()
            spare_since = prev["spare_since"] if prev else None
            if capacity and capacity.get("spare"):
                spare_since = spare_since or clock.now()
            else:
                spare_since = None
            c.execute("INSERT OR REPLACE INTO account_state(account, state, capacity_json, spare_since, updated_at)"
                      " VALUES(?,?,?,?,?)", (account, state, json.dumps(capacity or {}), spare_since, clock.now()))

    def get(self, account) -> Optional[dict]:
        r = self.g.conn.execute("SELECT * FROM account_state WHERE account=?", (account,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["capacity"] = json.loads(d.pop("capacity_json") or "{}")
        return d


class GlobalStore(GlobalStoreAPI):
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state_dir = Path(cfg.state_dir)
        self.conn = connect(self.state_dir / "global.db")
        migrate(self.conn, "global")
        self.tx = Tx(self.conn)
        self.usage = _Usage(self)
        self.limits = _Limits(self)
        self.lanes = _Lanes(self)
        self.account_state = _AccountState(self)
        self.events = Events(self.conn, self.tx, None, self.state_dir)
        self.ext_domains = Domains(self.conn, self.tx, "ext_domains", "ext_domain_waits",
                                   capacity=lambda name: 1, emit=self.events.emit, extra=("project", None))

    def mirror(self, project: str, ev: dict) -> int:
        with self.tx() as c:
            gid = c.execute("INSERT INTO events(ts, type, severity, task, attempt, worker, for_, text, data, project, src_id)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (ev["ts"], ev["type"], ev["severity"], ev["task"], ev["attempt"], ev["worker"],
                             json.dumps(ev.get("for") or []), ev["text"], json.dumps(ev.get("data") or {}), project,
                             ev["id"])).lastrowid
        ev["gid"] = gid
        return gid

    def events_since(self, since_id=0, project=None, min_severity=None, limit=500, for_=None) -> list:
        q, args = "SELECT * FROM events WHERE id > ?", [since_id]
        if project:
            q += " AND project=?"
            args.append(project)
        rows = [row_to_event(r) for r in self.conn.execute(q + " ORDER BY id LIMIT ?", args + [limit * 4 if (min_severity or for_) else limit])]
        if min_severity:
            rows = [e for e in rows if rank(e["severity"]) >= rank(min_severity)]
        if for_:
            rows = [e for e in rows if for_ in (e["for"] or [])]
        return rows[:limit]

    def max_event_id(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def sync_config(self):
        """Snapshot accounts and workers into the DB (for UI history and lane/worker rows)."""
        with self.tx() as c:
            for a in self.cfg.accounts.values():
                c.execute("INSERT OR REPLACE INTO accounts(id, data) VALUES(?,?)", (a.id, json.dumps(a.raw)))
            for w in self.cfg.workers.values():
                c.execute("INSERT OR IGNORE INTO workers(id, account, state, idle_since) VALUES(?,?,?,?)",
                          (w.id, w.account, "idle", clock.now()))

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------- project store
class _Slots:
    def __init__(self, s):
        self.s = s

    def take(self, holder) -> Optional[int]:
        with self.s.tx() as c:
            r = c.execute("SELECT slot FROM slots WHERE holder=?", (holder,)).fetchone()
            if r:
                return r["slot"]
            used = {x["slot"] for x in c.execute("SELECT slot FROM slots WHERE holder IS NOT NULL")}
            for slot in range(1, self.s.pcfg.slot_count + 1 if self.s.pcfg else 7):
                if slot not in used:
                    c.execute("INSERT OR REPLACE INTO slots(slot, holder, since) VALUES(?,?,?)", (slot, holder, clock.now()))
                    return slot
        return None

    def release(self, holder) -> None:
        with self.s.tx() as c:
            c.execute("DELETE FROM slots WHERE holder=?", (holder,))

    def of(self, holder) -> Optional[int]:
        r = self.s.conn.execute("SELECT slot FROM slots WHERE holder=?", (holder,)).fetchone()
        return r["slot"] if r else None


class ProjectStore(ProjectStoreAPI):
    def __init__(self, pid: str, cfg: Config, g: Optional[GlobalStore] = None):
        self.pid = pid
        self.cfg = cfg
        self.pcfg = cfg.project(pid)
        self.g = g or GlobalStore(cfg)
        self.state_dir = Path(cfg.state_dir)
        self.conn = connect(self.state_dir / "projects" / pid / "deck.db")
        migrate(self.conn, "project")
        self.tx = Tx(self.conn)
        self.events = Events(self.conn, self.tx, pid, self.state_dir, mirror=lambda ev: self.g.mirror(pid, ev))
        doms = (self.pcfg.domains if self.pcfg else {}) or {}

        def capacity(name):
            if kind_of(name) == "seq":
                return int(doms.get(name, {}).get("capacity", 0))
            return int(doms.get(name, {}).get("capacity", 1))
        self.domains = Domains(self.conn, self.tx, "domains", "domain_waits", capacity=capacity, emit=self.events.emit,
                               extra=("holder_kind", "task"),
                               ttl_default=lambda name: doms.get(name, {}).get("ttlMin", 240),
                               external=self.g.ext_domains)
        self.domains.ext_holder = lambda holder: f"{pid}/{holder}"
        self.g.ext_domains.extra = ("project", pid)
        self.slots = _Slots(self)

    # ---------------- reading
    def _row(self, id):
        r = self.conn.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone()
        if r is None:
            # allow lookup by number ("107") or number prefix
            if re.fullmatch(r"[0-9]+", str(id)):
                r = self.conn.execute("SELECT * FROM tasks WHERE num=?", (int(id),)).fetchone()
        if r is None:
            raise NotFound(f"task {id} not found in project {self.pid}")
        return r

    def _task(self, r) -> dict:
        t = json.loads(r["data"])
        t.update(id=r["id"], state=r["state"], rev=r["rev"], priority=r["priority"], size=r["size"],
                 worker=r["worker"], resumeAt=r["resume_at"], stateSince=r["state_since"],
                 createdAt=r["created_at"], updatedAt=r["updated_at"])
        t["attempts"] = [_schema_attempt(a) for a in self.attempts(r["id"])]
        return t

    def get(self, id: str) -> dict:
        return self._task(self._row(id))

    def resolve(self, id: str) -> str:
        return self._row(id)["id"]

    def list(self, states: Optional[Iterable[str]] = None) -> list:
        q, args = "SELECT * FROM tasks", []
        if states:
            states = list(states)
            q += f" WHERE state IN ({','.join('?' * len(states))})"
            args = states
        return [self._task(r) for r in self.conn.execute(q + " ORDER BY num", args)]

    def attempts(self, task: str) -> list:
        return [attempt_dict(r) for r in self.conn.execute("SELECT * FROM attempts WHERE task=? ORDER BY n", (task,))]

    def current_attempt(self, task: str) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM attempts WHERE task=? ORDER BY n DESC LIMIT 1", (task,)).fetchone()
        return attempt_dict(r) if r else None

    def card(self, task: str) -> Optional[dict]:
        r = self.conn.execute("SELECT data FROM cards WHERE task=?", (task,)).fetchone()
        return json.loads(r["data"]) if r else None

    def card_history(self, task: str, limit: int = 50) -> list:
        rows = self.conn.execute("SELECT * FROM card_history WHERE task=? ORDER BY id DESC LIMIT ?", (task, limit))
        return list(reversed([{**dict(r), "patch": json.loads(r["patch"])} for r in rows]))

    def handoff(self, task: str, n: Optional[int] = None) -> Optional[dict]:
        if n is None:
            r = self.conn.execute("SELECT * FROM handoffs WHERE task=? ORDER BY attempt DESC LIMIT 1", (task,)).fetchone()
        else:
            r = self.conn.execute("SELECT * FROM handoffs WHERE task=? AND attempt=?", (task, n)).fetchone()
        if not r:
            return None
        return {"attempt": r["attempt"], "ts": r["ts"], "valid": bool(r["valid"]), "data": json.loads(r["data"])}

    def merge_queue(self) -> list:
        return [dict(r) for r in self.conn.execute("SELECT * FROM merge_queue ORDER BY enqueued_at, id")]

    def dependants(self, task: str) -> list:
        return [t["id"] for t in self.list() if task in (t.get("deps") or [])]

    # ---------------- writing helpers
    def _save(self, t: dict, now: int):
        data = {k: v for k, v in t.items() if k not in ("attempts", "rev", "state", "worker", "resumeAt", "stateSince",
                                                         "createdAt", "updatedAt")}
        self.conn.execute(
            "UPDATE tasks SET state=?, priority=?, size=?, worker=?, resume_at=?, rev=?, data=?, updated_at=?, state_since=?"
            " WHERE id=?",
            (t["state"], t["priority"], t["size"], t.get("worker"), t.get("resumeAt"), t["rev"], json.dumps(data), now,
             t.get("stateSince"), t["id"]))

    def _check_task(self, t: dict):
        props = schema.load("task")["properties"]

        def nullable(k):
            ty = props.get(k, {}).get("type")
            return isinstance(ty, list) and "null" in ty
        probe = {k: v for k, v in t.items() if k != "command" and (v is not None or nullable(k))}
        probe["attempts"] = t.get("attempts") or []
        errs = schema.validate(probe, "task")
        if errs:
            raise ValidationError("task invalid: " + "; ".join(errs[:4]), errors=errs)

    def _next_num(self) -> int:
        start = self.pcfg.taskNumberStart if self.pcfg else 1
        r = self.conn.execute("SELECT value FROM seq WHERE name='task'").fetchone()
        mx = self.conn.execute("SELECT COALESCE(MAX(num), 0) FROM tasks").fetchone()[0]
        n = max(start, (r["value"] + 1) if r else 0, mx + 1)
        self.conn.execute("INSERT OR REPLACE INTO seq(name, value) VALUES('task', ?)", (n,))
        return n

    # ---------------- create / edit
    def create_task(self, fields: dict, by: str) -> dict:
        now = clock.now()
        f = dict(fields or {})
        unknown = set(f) - EDITABLE - {"id", "slug", "schema", "project", "createdBy", "command"}
        if unknown:
            raise ValidationError(f"unknown task field(s): {', '.join(sorted(unknown))}")
        with self.tx():
            num = self._next_num()
            raw_id = f.pop("id", None) or f.pop("slug", None) or f.get("title", "")
            slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^[0-9]+-?", "", str(raw_id).lower())).strip("-")[:40].strip("-")
            tid = f"{num:03d}" + (f"-{slug}" if slug else "")
            briefs = self.pcfg.briefsDir if self.pcfg else "docs/tasks"
            command = f.pop("command", None)
            t = {"schema": "deck.task/1", "project": self.pid, "id": tid, **{k: v for k, v in DEFAULTS.items()}, **f,
                 "state": "draft", "createdBy": f.get("createdBy") or by}
            t["brief"] = {"path": (f.get("brief") or {}).get("path") or f"{briefs}/{tid}.md"}
            if t.get("kind") == "scale" and t.get("pilotOf") and t["pilotOf"] not in t["deps"]:
                t["deps"] = list(t["deps"]) + [t["pilotOf"]]
            t.pop("slug", None)
            t["rev"] = 1
            t["stateSince"] = now
            self._check_task(t)
            if command is not None:
                if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
                    raise ValidationError("command must be a list of strings")
                t["command"] = command  # shell extension field (design §7.6); stored beside the schema fields
            data = {k: v for k, v in t.items() if k not in ("rev", "state", "stateSince")}
            self.conn.execute(
                "INSERT INTO tasks(id, num, state, priority, size, worker, resume_at, rev, data, created_at, updated_at, state_since)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, num, "draft", t["priority"], t["size"], None, None, 1, json.dumps(data), now, now, now))
            self.events.emit("task.created", "info", f"{tid} created: {t['title']}", task=tid,
                             data={"by": by, "num": num})
        return self.get(tid)

    def edit(self, id: str, fields: dict, *, by: str, rev: Optional[int] = None) -> dict:
        now = clock.now()
        bad = set(fields) - EDITABLE
        if bad:
            raise ValidationError(f"field(s) not editable: {', '.join(sorted(bad))}")
        with self.tx():
            t = self.get(id)
            if rev is not None and rev != t["rev"]:
                raise StaleRev(f"{t['id']} changed (rev {t['rev']}, you sent {rev})")
            if t["state"] in states.TERMINAL:
                raise IllegalTransition(f"{t['id']} is {t['state']}; it cannot be edited")
            if fields.get("pinWorker"):
                self._policy_check_worker(t, fields["pinWorker"], layer="pin", by=by)
            before = {k: t.get(k) for k in fields}
            t.update(fields)
            self._check_task(t)
            t["rev"] += 1
            self._save(t, now)
            if "priority" in fields and before.get("priority") != fields["priority"]:
                self.events.emit("task.priority", "info", f"{t['id']} priority {before.get('priority')} → {fields['priority']}",
                                 task=t["id"], data={"by": by})
        return self.get(t["id"])

    # ---------------- policy helpers
    def _deny(self, reason, *, task=None, worker=None, account=None, layer="lease", by=None):
        self.tx.on_rollback(lambda: self.events.emit("policy.denied", "alert", f"policy denied: {reason}"[:200], task=task, worker=worker,
                         for_=["owner", "W0"] if layer in ("assign", "reassign", "pin") else ["W0"],
                         data={"layer": layer, "account": account, "project": self.pid, "reason": reason, "by": by}))
        raise PolicyDenied(reason, layer=layer, account=account, worker=worker)

    def _policy_check_worker(self, t, worker, *, layer, by=None):
        w = self.cfg.worker(worker)
        if w is None:
            raise NotFound(f"unknown worker {worker}")
        acct = self.cfg.account(w.account)
        ok, why = policy.allow(acct, self.pcfg)
        if not ok:
            self._deny(why, task=t["id"], worker=worker, account=w.account, layer=layer, by=by)
        return w, acct

    # ---------------- attempts / guards
    def _review_rounds(self, task) -> int:
        return sum(1 for e in self.events.since(0, 10000, task=task, types=["review.decided"])
                   if e["data"].get("decision") == "changes")

    def max_attempts(self, t) -> int:
        return int((t.get("retry") or {}).get("maxAttempts", 3)) + self._review_rounds(t["id"])

    def attempts_left(self, t) -> bool:
        return len(self.attempts(t["id"])) < self.max_attempts(t)

    def has_branch(self, t) -> bool:
        return not (t.get("isolation") == "none" or t.get("capability") in ("research", "review")
                    or (t.get("isolation") == "main-checkout"))

    def _guard(self, name, t, eff, actor, now):
        a = self.current_attempt(t["id"])
        if name == "" or name is None:
            return True, ""
        if name == "validated":
            b = eff.get("brief") or {}
            return (bool(b.get("sha256") and b.get("snapshot")), "brief not validated")
        if name == "no_running_attempt":
            return (not (a and a["outcome"] == "running"), "an attempt is running")
        if name == "deps_done":
            for d in t.get("deps") or []:
                try:
                    ds = self.get(d)["state"]
                except NotFound:
                    return False, f"dep {d} missing"
                if ds != "done":
                    return False, f"waiting for {d} ({ds})"
            if t.get("pilotOf"):
                try:
                    if self.get(t["pilotOf"])["state"] != "done":
                        return False, f"pilot {t['pilotOf']} not done"
                except NotFound:
                    return False, f"pilot {t['pilotOf']} missing"
            if t.get("waitingFor"):
                return False, f"waiting for {t['waitingFor']}"
            if self.pcfg is not None and not self.pcfg.enabled:
                return False, "project disabled"
            return True, ""
        if name == "dep_failed":
            for d in t.get("deps") or []:
                try:
                    if self.get(d)["state"] in ("failed", "cancelled"):
                        return True, d
                except NotFound:
                    pass
            return False, "no dependency failed"
        if name == "lease_only":
            return (bool(eff.get("_lease")), "use lease() to start a task")
        if name == "attempt_alive":
            return (bool(a and a["outcome"] == "running"), "no live attempt")
        if name == "attempts_left":
            return (self.attempts_left(t), "attempts exhausted")
        if name in ("valid_handoff", "no_valid_handoff", "blocked_handoff_or_question"):
            h = self.handoff(t["id"], a["n"]) if a else None
            valid = bool(h and h["valid"])
            outcome = h["data"].get("outcome") if h else None
            if name == "valid_handoff":
                return (valid and outcome in ("done", "partial", "failed"), "no valid done/partial handoff")
            if name == "no_valid_handoff":
                return (not valid, "a valid handoff exists")
            card = self.card(t["id"]) or {}
            return ((valid and outcome == "blocked") or bool(card.get("question")), "no blocked handoff or question")
        if name == "quiet_and_idle":
            quiet = int((t.get("timeouts") or {}).get("quietMin", 15)) * 60
            last = (a or {}).get("lastHeartbeat") or (a or {}).get("startedAt") or now
            return (now - last >= 2 * quiet and float(eff.get("cpu_delta", 0)) < 1, "heartbeat recent or CPU busy")
        if name == "heartbeat_recent":
            quiet = int((t.get("timeouts") or {}).get("quietMin", 15)) * 60
            last = (a or {}).get("lastHeartbeat") or 0
            return (now - last < quiet, "no recent heartbeat")
        if name == "restart_killed":
            on = (t.get("retry") or {}).get("onHang", "restart")
            return (on == "restart" and bool(eff.get("killed")) and self.attempts_left(t),
                    "onHang is not restart, process not killed, or attempts exhausted")
        if name == "hang_block":
            on = (t.get("retry") or {}).get("onHang", "restart")
            return (on in ("block", "alert") or not self.attempts_left(t),
                    "onHang=restart with attempts left")
        if name == "exhausted_or_expired":
            hours = int((t.get("timeouts") or {}).get("taskHours", 48))
            atts = self.attempts(t["id"])
            first = atts[0]["startedAt"] if atts else t.get("createdAt") or now
            return (not self.attempts_left(t) or now - first >= hours * 3600, "attempts left and within taskHours")
        if name == "resume_due":
            ra = t.get("resumeAt")
            return (bool(eff.get("failover")) or ra is None or now >= ra, f"resumes at {_hm(ra)}")
        if name == "approve_merge":
            if not self.has_branch(t):
                return False, "task has no branch (use T22)"
            if not self.handoff(t["id"]):
                return False, "no handoff"
            if a and a.get("worker") == actor:
                return False, "reviewer must differ from the worker"
            kind = states.actor_kind(actor)
            if kind in (states.DSP, states.MQ):
                return (bool((t.get("merge") or {}).get("auto")) and t.get("kind") == "chore",
                        "auto-merge only for chores with merge.auto")
            return True, ""
        if name == "approve_no_branch":
            if a and a.get("worker") == actor:
                return False, "reviewer must differ from the worker"
            return (not self.has_branch(t), "task has a branch (use T21)")
        if name == "note_given":
            return (bool(str(eff.get("note") or "").strip()), "a note is required")
        if name == "rework_due":
            ra = t.get("resumeAt")
            return (self.attempts_left(t) and (ra is None or now >= ra), "attempts exhausted or backoff not over")
        if name == "reassign_allowed":
            w = eff.get("worker")
            if not w or not self.cfg.worker(w):
                return False, "target worker unknown"
            ok, why = policy.allow(self.cfg.account_of_worker(w), self.pcfg)
            return ok, why
        raise AssertionError(f"unknown guard {name}")

    def _release_holdings(self, t, keep_slot=False, end_outcome=None, now=None):
        a = self.current_attempt(t["id"])
        self.domains.release_all(t["id"])
        if not keep_slot:
            self.slots.release(t["id"])
        if a and a["outcome"] == "running" and end_outcome:
            self.conn.execute("UPDATE attempts SET outcome=?, ended_at=COALESCE(ended_at, ?) WHERE task=? AND n=?",
                              (end_outcome, now, t["id"], a["n"]))
        if a and a.get("lane") and a.get("worker"):
            tid, w, lane = t["id"], a["worker"], a["lane"]
            row = self.g.conn.execute("SELECT task FROM lanes WHERE worker=? AND lane=?", (w, lane)).fetchone()
            if row is None or row["task"] in (None, tid):
                self.tx.on_commit(lambda: self.g.lanes.release(w, lane))

    # ---------------- the choke point
    def transition(self, id: str, to: str, *, actor: str, reason: str = "", rev: Optional[int] = None, **effects) -> dict:
        if to not in states.STATES:
            raise IllegalTransition(f"unknown state {to}")
        now = clock.now()
        with self.tx():
            t = self.get(id)
            if rev is not None and rev != t["rev"]:
                raise StaleRev(f"{t['id']} changed (rev {t['rev']}, you sent {rev})")
            frm = t["state"]
            cands = states.rows_for(frm, to)
            if effects.get("t"):
                cands = [r for r in cands if r.id == effects["t"]]
            if not cands:
                raise IllegalTransition(f"{t['id']}: {frm} → {to} is not allowed")
            kind = states.actor_kind(actor)
            mine = [r for r in cands if kind in r.actors]
            if not mine:
                raise IllegalTransition(f"{t['id']}: {frm} → {to} is not allowed for {actor}")
            why = ""
            for row in mine:
                ok, why = self._guard(row.guard, t, effects, actor, now)
                if ok:
                    return self._apply(row, t, frm, to, actor, reason, effects, now)
            if mine[0].guard == "reassign_allowed" and effects.get("worker"):
                self._deny(why, task=t["id"], worker=effects["worker"], layer="reassign", by=actor)
            raise IllegalTransition(f"{t['id']}: {frm} → {to} refused ({'/'.join(r.id for r in mine)}): {why}")

    def _apply(self, row, t, frm, to, actor, reason, eff, now) -> dict:
        tid = t["id"]
        a = self.current_attempt(tid)
        ev = []   # (type, severity, text, for_, data)
        t["state"] = to
        t["stateSince"] = now
        t["stateReason"] = (reason or row.note)[:300]
        t["rev"] += 1
        rid = row.id
        if rid == "T1":
            b = dict(t.get("brief") or {})
            b.update({k: v for k, v in (eff.get("brief") or {}).items() if k in ("sha256", "snapshot", "validatedAt")})
            t["brief"] = b
            if "doneWhen" in eff:
                t["doneWhen"] = eff["doneWhen"]
            if "stopPoint" in eff:
                t["stopPoint"] = eff["stopPoint"]
            if eff.get("domains") is not None:
                t["domains"] = eff["domains"]
            ev.append(("task.validated", "info", f"{tid} validated and ready", [], {"warnings": eff.get("warnings", [])}))
        elif rid == "T1x":
            t["stateReason"] = (reason or "validation failed")[:300]
            ev.append(("task.invalid", "warn", f"{tid} invalid: {reason}"[:200], [actor] if actor else [],
                       {"errors": eff.get("errors", [])}))
        elif rid == "T4":
            ev.append(("task.state", "action", f"{tid} blocked: upstream {reason or 'dep'} failed", ["W0"], {}))
        elif rid == "T6":
            if a:
                self.conn.execute("UPDATE attempts SET last_heartbeat=? WHERE task=? AND n=?", (now, tid, a["n"]))
        elif rid == "T7":
            self._release_holdings(t, end_outcome=eff.get("outcome", "crashed"), now=now)
            crashes = sum(1 for x in self.attempts(tid) if x["outcome"] == "crashed")
            t["resumeAt"] = now + 60 * (1, 2, 4)[min(max(crashes - 1, 0), 2)]
            t["worker"] = None
        elif rid in ("T8", "T9"):
            if rid == "T9" and a:
                synth = {"schema": "deck.handoff/1", "task": tid, "attempt": a["n"], "outcome": "unknown",
                         "summary": "The attempt ended without a valid handoff; review the branch and run output.",
                         "doneWhen": [], "tests": [], "risks": ["no valid handoff"], "decisionsNeeded": []}
                prev = self.handoff(tid, a["n"])
                if prev is None:
                    self.conn.execute("INSERT OR REPLACE INTO handoffs(task, attempt, ts, valid, data) VALUES(?,?,?,?,?)",
                                      (tid, a["n"], now, 0, json.dumps(synth)))
                ev.append(("handoff.invalid", "alert", f"{tid} ended without a valid handoff", ["W0"], {"attempt": a["n"]}))
            self._release_holdings(t, end_outcome="handoff", now=now)
            t["review"] = {"requestedAt": now, "decision": None, "by": None, "note": None, "decidedAt": None}
            t["worker"] = None
            unlocks = [d for d in self.dependants(tid)]
            label = "PILOT " if t.get("kind") == "pilot" else ""
            ev.append(("review.requested", "action", f"{label}{tid} ready to review: {t['title']}"[:200], ["W0"],
                       {"attempt": a["n"] if a else None, "unlocks": unlocks}))
        elif rid == "T10":
            card = self.card(tid) or {}
            self._release_holdings(t, end_outcome="blocked", now=now)
            t["worker"] = None
            q = card.get("question")
            if q:
                t["stateReason"] = f"question: {q}"[:300]
                ev.append(("question.asked", "action", f"{tid} asks: {q}"[:200], ["W0"], {"question": q}))
        elif rid == "T11":
            resets = eff.get("resets_at")
            base = resets if resets else now + 3600
            t["resumeAt"] = base + 90
            self._release_holdings(t, keep_slot=True, end_outcome="limit", now=now)
            who = (a or {}).get("worker") or t.get("worker") or "worker"
            t["stateReason"] = (reason or f"{who} hit its {eff.get('kind', '5-hour')} limit · resumes {_hm(t['resumeAt'])}")[:300]
            ev.append(("limit.hit", "warn", t["stateReason"][:200], [],
                       {"resetsAt": resets, "account": (a or {}).get("account"), "resumeAt": t["resumeAt"]}))
        elif rid == "T12":
            ev.append(("attempt.hung", "alert", f"{tid} hung: {reason or 'no heartbeat and no CPU'}"[:200], ["W0"],
                       {"attempt": a["n"] if a else None, **(eff.get("data") or {})}))
        elif rid == "T13":
            ev.append(("attempt.heartbeat", "info", f"{tid} recovered", [], {"recovered": True}))
        elif rid == "T14":
            self._release_holdings(t, end_outcome="hung", now=now)
            t["worker"] = None
            ev.append(("attempt.killed", "warn", f"{tid} killed after hang; continuing in a new attempt", [], {}))
        elif rid == "T15":
            self._release_holdings(t, end_outcome="hung", now=now)
            t["worker"] = None
            ev.append(("task.state", "action", f"{tid} blocked: hung", ["W0"], {}))
        elif rid == "T16":
            self._release_holdings(t, end_outcome=eff.get("outcome", "crashed"), now=now)
            fails = sum(1 for x in self.attempts(tid) if x["outcome"] in ("crashed", "timeout"))
            t["resumeAt"] = now + 60 * (2, 4, 8)[min(max(fails - 1, 0), 2)]
            t["worker"] = None
        elif rid == "T17":
            self._release_holdings(t, end_outcome=eff.get("outcome", "crashed"), now=now)
            t["worker"] = None
            ev.append(("task.state", "alert", f"{tid} failed: {reason or 'attempts exhausted'}"[:200], ["W0"], {}))
        elif rid == "T18":
            t["resumeAt"] = None
            if eff.get("failover"):
                t["pinWorker"] = None
            ev.append(("limit.reset", "info", f"{tid} resumes", [], {"failover": bool(eff.get("failover"))}))
        elif rid == "T19":
            t["resumeAt"] = None
            card = self.card(tid)
            if card and card.get("question"):
                self._card_write(tid, None, "answer", {"text": eff.get("answer")}, now)
            ev.append(("question.answered", "info", f"{tid} unblocked by {actor}", [],
                       {"answer": eff.get("answer"), "decision": eff.get("decision"), "option": eff.get("option")}))
        elif rid == "T20":
            self._release_holdings(t, keep_slot=True, end_outcome="cancelled", now=now)
            t["stateReason"] = f"paused by {actor}"
            t["worker"] = None
        elif rid in ("T21", "T22", "T23"):
            decision = {"T21": "approved", "T22": "approved", "T23": "changes"}[rid]
            t["review"] = {**(t.get("review") or {}), "decision": decision, "by": actor,
                           "note": eff.get("note"), "decidedAt": now}
            ev.append(("review.decided", "info", f"{tid} {decision} by {actor}", [],
                       {"decision": "changes" if rid == "T23" else "approve", "note": eff.get("note")}))
            if rid == "T21":
                self.conn.execute("INSERT OR REPLACE INTO merge_queue(task, enqueued_at, state, started_at, note)"
                                  " VALUES(?,?,?,?,?)", (tid, now, "queued", None, None))
                ev.append(("merge.queued", "info", f"{tid} queued for merge", [], {}))
            if rid == "T22":
                self.slots.release(tid)
            if rid == "T23":
                t["stateReason"] = f"changes: {eff.get('note')}"[:300]
        elif rid == "T24":
            self.conn.execute("UPDATE merge_queue SET state='done' WHERE task=?", (tid,))
            self.slots.release(tid)
            self.domains.release("merge", tid)
            ev.append(("merge.done", "info", f"{tid} merged", [], {}))
        elif rid == "T25":
            self.conn.execute("UPDATE merge_queue SET state='failed', note=? WHERE task=?", (eff.get("note"), tid))
            self.domains.release("merge", tid)
            t["review"] = {**(t.get("review") or {}), "decision": "changes", "by": actor, "note": eff.get("note"),
                           "decidedAt": now}
            if not eff.get("event_emitted"):
                ev.append(("merge.failed", "alert", f"{tid} merge failed: {reason or eff.get('note') or ''}"[:200], ["W0"],
                           {"note": eff.get("note")}))
        elif rid == "T26":
            self.conn.execute("UPDATE merge_queue SET state='waiting', note=? WHERE task=?", ("main checkout busy", tid))
            self.domains.release("merge", tid)
            t["resumeAt"] = now + 120
            ev.append(("merge.failed", "warn", f"{tid} merge waits: main checkout busy", [], {"retryAt": now + 120}))
        elif rid == "T27":
            t["resumeAt"] = None
        elif rid == "T28":
            retry = dict(t.get("retry") or {})
            plus = eff.get("attempts_plus")
            if plus is None and not self.attempts_left(t):
                plus = 1
            if plus:
                retry["maxAttempts"] = int(retry.get("maxAttempts", 3)) + int(plus)
                t["retry"] = retry
            t["resumeAt"] = None
        elif rid == "T29":
            self._release_holdings(t, end_outcome="cancelled", now=now)
            self.domains.clear_waits(tid)
            self.conn.execute("UPDATE merge_queue SET state='cancelled' WHERE task=? AND state!='done'", (tid,))
            t["worker"] = None
            t["resumeAt"] = None
            if t.get("review") and frm == "review":
                t["review"] = {**t["review"], "decision": "rejected", "by": actor, "note": eff.get("note"), "decidedAt": now}
                ev.append(("review.decided", "info", f"{tid} rejected by {actor}", [], {"decision": "reject"}))
        elif rid == "T30":
            target = eff["worker"]
            self._release_holdings(t, end_outcome="cancelled", now=now)
            t["pinWorker"] = target
            t["worker"] = None
            ev.append(("task.reassigned", "info", f"{tid} reassigned to {target} by {actor}", [target],
                       {"from": (a or {}).get("worker"), "to": target}))
        if to not in states.HOLDING and to != "limit_paused" and rid not in ("T1", "T1x"):
            t["worker"] = None if to not in ("starting", "running") else t.get("worker")
        self._save(t, now)
        sev = "info"
        self.events.emit("task.state", "debug" if rid == "T1x" else sev, f"{tid} {frm} → {to}" + (f": {reason}" if reason else ""),
                         task=tid, attempt=(a or {}).get("n"), worker=(a or {}).get("worker"),
                         data={"from": frm, "to": to, "t": rid, "by": actor})
        for typ, sev2, text, for_, data in ev:
            self.events.emit(typ, sev2, text, task=tid, attempt=(a or {}).get("n"), worker=(a or {}).get("worker"),
                             for_=for_, data=data)
        return self.get(tid)

    # ---------------- leases
    def lease(self, id: str, worker: str, lane: int, account: str, *, slot: Optional[int] = None,
              domains: Iterable[str] = (), now: Optional[int] = None, adapter: Optional[str] = None,
              continuation_of: Optional[int] = None) -> dict:
        now = clock.now() if now is None else now
        t = self.get(id)
        tid = t["id"]
        w = self.cfg.worker(worker)
        acct = self.cfg.account(account)
        # policy first, with its alert committed even though the lease is refused
        if w is not None and w.account != account:
            self._deny(f"worker {worker} belongs to account {w.account}, not {account}", task=tid, worker=worker,
                       account=account, layer="lease")
        ok, why = policy.allow(acct, self.pcfg)
        if not ok:
            self._deny(why, task=tid, worker=worker, account=account, layer="lease")
        if w is None:
            raise NotFound(f"unknown worker {worker}")
        with self.tx():
            t = self.get(tid)
            if t["state"] != "queued":
                raise IllegalTransition(f"{tid} is {t['state']}, not queued")
            if w.capabilities and t["capability"] not in w.capabilities:
                raise IllegalTransition(f"{worker} lacks capability {t['capability']}")
            wc = t.get("workerClass") or DEFAULTS["workerClass"]
            if w.adapter not in wc:
                raise IllegalTransition(f"{worker} adapter {w.adapter} not in workerClass {wc}")
            if t.get("pinWorker") not in (None, worker):
                raise IllegalTransition(f"{tid} is pinned to {t['pinWorker']}")
            if not self.attempts_left(t):
                raise IllegalTransition(f"{tid} has no attempts left")
            busy = self.conn.execute("SELECT task FROM attempts WHERE worker=? AND lane=? AND outcome='running'",
                                     (worker, lane)).fetchone()
            grow = self.g.conn.execute("SELECT task, project FROM lanes WHERE worker=? AND lane=? AND state='running'",
                                       (worker, lane)).fetchone()
            if busy or (grow and grow["task"] not in (None, tid)):
                raise IllegalTransition(f"lane {worker}·{lane} is busy")
            names = sorted(set(list(domains or []) + list(t.get("domains") or [])))
            ok, blocker = self.domains.try_acquire(names, tid, now=now)
            if not ok:
                raise DomainBusy(f"waiting: {blocker['reason']}", blocker=blocker)
            if slot is None and t.get("isolation", "worktree") != "none":
                slot = self.slots.take(tid)
            prev = self.current_attempt(tid)
            n = (prev["n"] if prev else 0) + 1
            token = secrets.token_urlsafe(24)
            self.conn.execute(
                "INSERT INTO attempts(task, n, worker, account, adapter, slot, started_at, lease_until, last_heartbeat,"
                " outcome, continuation_of, token_hash, lane) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, n, worker, account, adapter or w.adapter, slot, now, now + LEASE_SEC, now, "running",
                 continuation_of if continuation_of is not None else (prev["n"] if prev else None), _hash(token), lane))
            if slot is not None:
                ws = dict(t.get("workspace") or {})
                ws["slot"] = slot
                t["workspace"] = ws
                self._save({**t}, now)
            self._apply(states.ROW_BY_ID["T5"], self.get(tid), "queued", "starting", "DSP",
                        f"{worker}·{lane} starting", {}, now)
            t2 = self.get(tid)
            t2["worker"] = worker
            self._save(t2, now)
            self.events.emit("attempt.started", "info", f"{tid} attempt {n} on {worker}·{lane}", task=tid, attempt=n,
                             worker=worker, data={"account": account, "lane": lane, "slot": slot})
            self.tx.on_commit(lambda: self.g.lanes.mark(worker, lane, "running", self.pid, tid, n))
        att = attempt_dict(self.conn.execute("SELECT * FROM attempts WHERE task=? AND n=?", (tid, n)).fetchone())
        att["token"] = token
        return att

    def claim(self, id: str, account_home: str, worker: str) -> dict:
        t = self.get(id)
        acct = self.cfg.account_for_home(account_home)
        if acct is None:
            self._deny("session account unknown (home path not configured)", task=t["id"], worker=worker,
                       layer="claim")
        w = self.cfg.worker(worker)
        if w is None or w.account != acct.id:
            self._deny(f"worker {worker} is not on account {acct.id}", task=t["id"], worker=worker, account=acct.id,
                       layer="claim")
        ok, why = policy.allow(acct, self.pcfg)
        if not ok:
            self._deny(why, task=t["id"], worker=worker, account=acct.id, layer="claim")
        if t["state"] == "queued":
            lane = self.g.lanes.take(worker, self.pid, t["id"]) or 1
            att = self.lease(t["id"], worker, lane, acct.id, domains=t.get("domains") or [])
            self.transition(t["id"], "running", actor=worker, reason=f"claimed by {worker}")
            return att
        if t["state"] == "starting":
            a = self.current_attempt(t["id"])
            if not a or a["worker"] != worker:
                raise IllegalTransition(f"{t['id']} is offered to {a and a['worker']}, not {worker}")
            token = secrets.token_urlsafe(24)
            self.update_attempt(t["id"], a["n"], token_hash=_hash(token))
            self.transition(t["id"], "running", actor=worker, reason=f"claimed by {worker}")
            out = self.current_attempt(t["id"])
            out["token"] = token
            return out
        raise IllegalTransition(f"{t['id']} is {t['state']}; only queued or offered tasks can be claimed")

    def update_attempt(self, task: str, n: int, **fields) -> None:
        bad = set(fields) - set(ATTEMPT_COLS)
        if bad:
            raise ValidationError(f"unknown attempt field(s): {', '.join(sorted(bad))}")
        if not fields:
            return
        with self.tx() as c:
            sets = ", ".join(f"{k}=?" for k in fields)
            cur = c.execute(f"UPDATE attempts SET {sets} WHERE task=? AND n=?", (*fields.values(), task, n))
            if cur.rowcount == 0:
                raise NotFound(f"attempt {task}#{n} not found")

    def end_attempt(self, task: str, n: int, outcome: str, exit_code: Optional[int] = None) -> None:
        now = clock.now()
        with self.tx() as c:
            r = c.execute("SELECT * FROM attempts WHERE task=? AND n=?", (task, n)).fetchone()
            if r is None:
                raise NotFound(f"attempt {task}#{n} not found")
            c.execute("UPDATE attempts SET outcome=?, exit_code=?, ended_at=COALESCE(ended_at, ?) WHERE task=? AND n=?",
                      (outcome, exit_code, now, task, n))
            self.events.emit("attempt.ended", "info", f"{task} attempt {n} ended: {outcome}", task=task, attempt=n,
                             worker=r["worker"], data={"outcome": outcome, "exitCode": exit_code})

    def heartbeat(self, task: str, n: Optional[int] = None, **data) -> None:
        a = self.current_attempt(task) if n is None else attempt_dict(
            self.conn.execute("SELECT * FROM attempts WHERE task=? AND n=?", (task, n)).fetchone())
        if not a:
            raise NotFound(f"no attempt for {task}")
        self.update_attempt(task, a["n"], last_heartbeat=clock.now())
        if data:
            self.events.emit("attempt.heartbeat", "debug", f"{task} heartbeat", task=task, attempt=a["n"],
                             worker=a["worker"], data=data)

    # ---------------- cards
    def check_token(self, task: str, attempt: Optional[int], token: Optional[str]) -> Optional[dict]:
        a = self.current_attempt(task) if attempt is None else attempt_dict(
            self.conn.execute("SELECT * FROM attempts WHERE task=? AND n=?", (task, attempt)).fetchone() or {})
        if not a or not a.get("n"):
            return None
        row = self.conn.execute("SELECT token_hash FROM attempts WHERE task=? AND n=?", (task, a["n"])).fetchone()
        if row and row["token_hash"]:
            if not token or not secrets.compare_digest(_hash(token), row["token_hash"]):
                raise CardRejected("attempt token missing or wrong (DECK_TOKEN)")
        return a

    def _card_write(self, task, attempt, op, args, now):
        card = self.card(task) or cards.empty(task)
        new, patch = cards.apply(card, op, args, attempt, now)
        if patch is None:
            return card
        self.conn.execute("INSERT OR REPLACE INTO cards(task, rev, data) VALUES(?,?,?)", (task, new["rev"], json.dumps(new)))
        self.conn.execute("INSERT INTO card_history(task, attempt, ts, op, patch) VALUES(?,?,?,?,?)",
                          (task, attempt, now, op, json.dumps(patch)))
        return new

    def card_op(self, task: str, attempt: Optional[int], op: str, args: dict, token: Optional[str]) -> dict:
        now = clock.now()
        try:
            tid = self.resolve(task)
            a = self.check_token(tid, attempt, token)
            n = a["n"] if a else attempt
            with self.tx():
                new = self._card_write(tid, n, op, args or {}, now)
                if a and a.get("outcome") == "running":
                    self.conn.execute("UPDATE attempts SET last_heartbeat=? WHERE task=? AND n=?", (now, tid, a["n"]))
                if op == "ask":
                    self.events.emit("question.asked", "action", f"{tid} asks: {args.get('question')}"[:200], task=tid,
                                     attempt=n, for_=["W0"], data={"question": args.get("question")})
                elif op != "hb":
                    self.events.emit("card.updated", "debug", f"{tid} card {op}", task=tid, attempt=n, data={"op": op})
            return new
        except CardRejected as e:
            try:
                self.events.emit("card.rejected", "warn", f"{task} card {op} rejected: {e.reason}"[:200], task=task,
                                 attempt=attempt, data={"op": op})
            except Exception:
                pass
            raise

    # ---------------- handoffs and review
    def put_handoff(self, task: str, n: int, data: dict, valid: Optional[bool] = None) -> dict:
        now = clock.now()
        tid = self.resolve(task)
        errs = schema.validate(data, "handoff") if isinstance(data, dict) else ["handoff is not an object"]
        if isinstance(data, dict) and data.get("outcome") == "unknown":
            errs.append("outcome 'unknown' is set by the daemon only")
        if valid is None:
            valid = not errs
        stored = {**(data if isinstance(data, dict) else {"raw": data}), "task": tid, "attempt": n}
        with self.tx():
            self.conn.execute("INSERT OR REPLACE INTO handoffs(task, attempt, ts, valid, data) VALUES(?,?,?,?,?)",
                              (tid, n, now, 1 if valid else 0, json.dumps(stored)))
            if valid:
                self.events.emit("handoff.received", "info", f"{tid} handoff: {stored.get('outcome')}", task=tid,
                                 attempt=n, data={"outcome": stored.get("outcome")})
                for fu in stored.get("followUps") or []:
                    a = self.current_attempt(tid)
                    by = (a or {}).get("worker") or "worker"
                    try:
                        self.create_task({"title": fu["title"], "summary": fu.get("summary") or fu["title"],
                                          "size": fu.get("size", "M"), "createdBy": by}, by=by)
                    except ValidationError:
                        pass
            else:
                self.events.emit("handoff.invalid", "alert", f"{tid} handoff invalid: {'; '.join(errs[:2])}"[:200],
                                 task=tid, attempt=n, for_=["W0"], data={"errors": errs[:10]})
        return {"valid": bool(valid), "errors": errs, "data": stored}

    def review(self, task: str, decision: str, by: str, note: str = "", rev: Optional[int] = None) -> dict:
        t = self.get(task)
        if decision in ("approve", "approved"):
            if self.has_branch(t):
                return self.transition(t["id"], "merging", actor=by, reason=f"approved by {by}", rev=rev, note=note)
            return self.transition(t["id"], "done", actor=by, reason=f"approved by {by}", rev=rev, note=note)
        if decision in ("changes", "changes_requested"):
            return self.transition(t["id"], "changes_requested", actor=by, reason="changes requested", rev=rev, note=note)
        if decision in ("reject", "rejected"):
            return self.transition(t["id"], "cancelled", actor=by, reason=f"rejected by {by}", rev=rev, note=note)
        raise ValidationError(f"unknown review decision {decision}")

    def eligible(self, task: str) -> list:
        """[{worker, account, label, scope, allowed, reason}] for every configured worker (never hidden)."""
        t = self.get(task)
        out = []
        for w in self.cfg.workers.values():
            acct = self.cfg.account(w.account)
            ok, why = policy.allow(acct, self.pcfg)
            if ok and w.capabilities and t["capability"] not in w.capabilities:
                ok, why = False, f"cannot do {t['capability']} work"
            if ok and w.adapter not in (t.get("workerClass") or DEFAULTS["workerClass"]):
                ok, why = False, f"task does not allow {w.adapter}"
            if ok and acct and self.g.limits.active(acct.id):
                lim = self.g.limits.active(acct.id)[0]
                why = f"cooling until {_hm(lim['resets_at'])}"
            elif ok:
                why = None
            out.append({"worker": w.id, "account": w.account, "label": acct.label if acct else w.account,
                        "scope": acct.scope if acct else None, "allowed": ok, "reason": why})
        return out

    def close(self):
        self.conn.close()


_cache: dict = {}


def open_global(cfg: Optional[Config] = None) -> GlobalStore:
    cfg = cfg or Config.load()
    return GlobalStore(cfg)


def open_project(pid: str, cfg: Optional[Config] = None, g: Optional[GlobalStore] = None) -> ProjectStore:
    cfg = cfg or Config.load()
    if pid not in cfg.projects:
        raise NotFound(f"project {pid} is not configured")
    return ProjectStore(pid, cfg, g)
