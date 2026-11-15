"""In-memory FakeStore implementing the frozen store API (deck/store_api.py) for B2 unit tests (design §17).

It follows the §5.2 transition table (pairs only; guards that need B1's validator are skipped), the §2.3 policy
inside lease(), and §6.2.1 domain rules closely enough for planner tests. Kept after B1 lands, for fast tests.
"""
from __future__ import annotations

import copy
import re
import secrets
from typing import Iterable, Optional

from deck import clock, policy
from deck.errors import IllegalTransition, NotFound, PolicyDenied, StaleRev

NON_TERMINAL = {"draft", "ready", "queued", "starting", "running", "blocked", "limit_paused", "hung", "review",
                "changes_requested", "merging"}
ALLOWED = {
    ("draft", "ready"), ("draft", "draft"), ("ready", "draft"), ("queued", "draft"),
    ("ready", "queued"), ("ready", "blocked"), ("queued", "blocked"), ("queued", "starting"),
    ("starting", "running"), ("starting", "queued"), ("running", "review"), ("running", "blocked"),
    ("running", "limit_paused"), ("starting", "limit_paused"), ("running", "hung"), ("hung", "running"),
    ("hung", "queued"), ("hung", "blocked"), ("running", "queued"), ("running", "failed"), ("hung", "failed"),
    ("starting", "failed"), ("limit_paused", "queued"), ("blocked", "queued"), ("review", "merging"),
    ("review", "done"), ("review", "changes_requested"), ("merging", "done"), ("merging", "changes_requested"),
    ("merging", "review"), ("changes_requested", "queued"), ("failed", "queued"),
} | {(s, "cancelled") for s in NON_TERMINAL}

SEV_ORDER = ["debug", "info", "action", "warn", "alert"]


def _camel(s):
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def literal_prefix(glob: str) -> str:
    m = re.search(r"[*?\[{]", glob)
    return glob[:m.start()] if m else glob


def overlaps(a: str, b: str) -> bool:
    pa, pb = literal_prefix(a), literal_prefix(b)
    return pa.startswith(pb) or pb.startswith(pa)


class _Counter:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


class FakeEvents:
    def __init__(self, project, counter, mirror):
        self.project, self.counter, self.mirror, self.rows = project, counter, mirror, []

    def emit(self, type, severity, text, task=None, attempt=None, worker=None, for_=(), data=None):
        ev = {"id": self.counter.next(), "ts": clock.now(), "project": self.project, "type": type, "severity": severity,
              "task": task, "attempt": attempt, "worker": worker, "for": list(for_ or []), "text": text[:200],
              "data": data or {}}
        self.rows.append(ev)
        if self.mirror is not None and self.mirror is not self.rows:
            self.mirror.append(ev)
        return ev

    def since(self, since_id=0, limit=500, min_severity=None, task=None):
        lo = SEV_ORDER.index(min_severity) if min_severity else 0
        return [e for e in self.rows if e["id"] > since_id and SEV_ORDER.index(e["severity"]) >= lo
                and (task is None or e["task"] == task)][:limit]

    def of(self, type):
        return [e for e in self.rows if e["type"] == type]


class FakeDomains:
    """area: overlap-exclusive; seq: capacity (0 = unlimited); ext:/res:/merge capacity (default 1)."""

    def __init__(self, decl=None, events=None):
        self.decl = decl or {}
        self.held = []          # {name, holder, since, expires}
        self.waits = {}         # (name, holder) -> since
        self.events = events

    @staticmethod
    def overlaps(glob_a, glob_b):
        return overlaps(glob_a, glob_b)

    def _cap(self, name):
        if name == "merge":
            return 1
        kind = name.split(":", 1)[0]
        default = 0 if kind == "seq" else 1
        return int((self.decl.get(name) or {}).get("capacity", default))

    def _blocker(self, name, holder, now):
        others = [h for h in self.held if h["holder"] != holder and (h["expires"] is None or h["expires"] > now)]
        if name.startswith("area:"):
            g = name[5:]
            for h in others:
                if h["name"].startswith("area:") and overlaps(g, h["name"][5:]):
                    return {"name": name, "holder": h["holder"], "reason": f"overlaps {h['name']}"}
        else:
            cap = self._cap(name)
            same = [h for h in others if h["name"] == name]
            if cap and len(same) >= cap:
                return {"name": name, "holder": same[0]["holder"], "reason": f"capacity {cap}"}
        # anti-starvation: an older waiter (> 30 min) reserves the domain
        for (n, w), since in self.waits.items():
            if w != holder and now - since > 1800 and (n == name or (n.startswith("area:") and name.startswith("area:")
                                                                         and overlaps(n[5:], name[5:]))):
                return {"name": name, "holder": w, "reason": "reserved by long waiter"}
        return None

    def try_acquire(self, names, holder, ttl=None, now=None):
        now = clock.now() if now is None else now
        names = sorted(set(names))
        for n in names:
            b = self._blocker(n, holder, now)
            if b:
                for m in names:
                    self.waits.setdefault((m, holder), now)
                return False, b
        for n in names:
            if not any(h["name"] == n and h["holder"] == holder for h in self.held):
                self.held.append({"name": n, "holder": holder, "since": now, "expires": now + ttl if ttl else None})
            self.waits.pop((n, holder), None)
        return True, None

    def release_all(self, holder):
        before = len(self.held)
        self.held = [h for h in self.held if h["holder"] != holder]
        for k in [k for k in self.waits if k[1] == holder]:
            self.waits.pop(k)
        return before - len(self.held)

    def renew(self, holder, ttl=None, now=None):
        now = clock.now() if now is None else now
        n = 0
        for h in self.held:
            if h["holder"] == holder and ttl:
                h["expires"] = now + ttl
                n += 1
        return n

    def held_by(self, holder):
        return sorted(h["name"] for h in self.held if h["holder"] == holder)


class FakeSlots:
    def __init__(self, count):
        self.count, self.holders = count, {}

    def take(self, holder):
        for s, h in self.holders.items():
            if h == holder:
                return s
        for s in range(1, self.count + 1):
            if s not in self.holders:
                self.holders[s] = holder
                return s
        return None

    def take_other(self, holder, avoid):
        s = None
        for cand in range(1, self.count + 1):
            if cand not in self.holders and cand != avoid:
                s = cand
                break
        if s is not None:
            self.holders[s] = holder
        return s

    def release(self, holder):
        for s in [s for s, h in self.holders.items() if h == holder]:
            self.holders.pop(s)


class FakeProjectStore:
    def __init__(self, cfg, pid, gstore):
        self.cfg, self.pid, self.g = cfg, pid, gstore
        self.pcfg = cfg.project(pid)
        self.tasks = {}
        self.cards = {}
        self.handoffs = {}
        self.num = (self.pcfg.taskNumberStart if self.pcfg else 1) - 1
        self.events = FakeEvents(pid, gstore.counter, gstore.events.rows)
        self.domains = FakeDomains(self.pcfg.domains if self.pcfg else {}, self.events)
        self.slots = FakeSlots(self.pcfg.slot_count if self.pcfg else 6)

    # ---- tasks
    def create_task(self, fields, by="W0"):
        self.num += 1
        tid = fields.get("id") or f"{self.num:03d}-task"
        now = clock.now()
        t = {"schema": "deck.task/1", "project": self.pid, "id": tid, "num": self.num, "title": "", "summary": "",
             "brief": {"path": f"docs/tasks/{tid}.md", "snapshot": f"/runs/{tid}/brief.md"}, "tags": [],
             "priority": "P2", "size": "M", "kind": "normal", "deps": [], "capability": "code",
             "workerClass": ["claude-headless", "codex-exec"], "pinWorker": None, "domains": [], "isolation": "none",
             "timeouts": {"attemptMin": 240, "taskHours": 48, "quietMin": 15},
             "retry": {"maxAttempts": 3, "onLimit": "auto", "onHang": "restart"}, "merge": {"target": "main"},
             "state": "draft", "stateSince": now, "stateReason": "", "resumeAt": None, "worker": None,
             "attempts": [], "workspace": {}, "review": {}, "createdBy": by, "createdAt": now, "updatedAt": now,
             "rev": 1}
        t.update(copy.deepcopy(fields))
        t["id"] = tid
        self.tasks[tid] = t
        self.events.emit("task.created", "info", f"{tid} created", task=tid)
        return copy.deepcopy(t)

    def get(self, id):
        if id not in self.tasks:
            raise NotFound(f"no task {id}")
        return copy.deepcopy(self.tasks[id])

    def list(self, states=None):
        st = set(states) if states else None
        return [copy.deepcopy(t) for t in self.tasks.values() if st is None or t["state"] in st]

    def edit(self, id, fields, *, by="W0", rev=None):
        t = self.tasks[id]
        if rev is not None and rev != t["rev"]:
            raise StaleRev("stale rev")
        if fields.get("pinWorker"):
            w = self.cfg.worker(fields["pinWorker"])
            ok, why = policy.allow(self.cfg.account(w.account) if w else None, self.pcfg)
            if not ok:
                raise PolicyDenied(why)
        t.update(copy.deepcopy(fields))
        t["rev"] += 1
        return copy.deepcopy(t)

    def transition(self, id, to, *, actor, reason="", rev=None, **effects):
        t = self.tasks.get(id)
        if t is None:
            raise NotFound(id)
        if rev is not None and rev != t["rev"]:
            raise StaleRev("stale rev")
        frm = t["state"]
        if (frm, to) not in ALLOWED:
            raise IllegalTransition(f"{frm} → {to} not allowed")
        now = clock.now()
        t["state"], t["stateReason"], t["stateSince"], t["updatedAt"] = to, reason, now, now
        t["rev"] += 1
        if "resume_at" in effects:
            t["resumeAt"] = effects["resume_at"]
        elif to == "limit_paused" and effects.get("resets_at"):
            t["resumeAt"] = effects["resets_at"] + 90
        elif to not in ("queued", "limit_paused"):
            t["resumeAt"] = None
        if "worker" in effects:
            t["worker"] = effects["worker"]
        if "pin_worker" in effects:
            t["pinWorker"] = effects["pin_worker"]
        if effects.get("attempts_plus"):
            t["extraAttempts"] = t.get("extraAttempts", 0) + int(effects["attempts_plus"])
        if effects.get("failover"):
            t["pinWorker"] = None
        if to in ("review",):
            t.setdefault("review", {})
            t["review"] = {"requestedAt": now, "decision": None, "by": None, "note": None, "decidedAt": None}
        if "note" in effects and effects["note"]:
            t.setdefault("review", {})["note"] = effects["note"]
        self.events.emit("task.state", "info", f"{id}: {frm} → {to}" + (f" · {reason}" if reason else ""), task=id,
                         data={"from": frm, "to": to, "actor": actor})
        return copy.deepcopy(t)

    def set_reason(self, id, reason):
        """Planner notes ("waiting: domain … held by …") — B1 exposes this as edit(stateReason)."""
        self.tasks[id]["stateReason"] = reason

    def lease(self, id, worker, lane, account, *, slot, domains, now=None):
        now = clock.now() if now is None else now
        t = self.tasks[id]
        if t["state"] != "queued":
            raise IllegalTransition(f"{id} is {t['state']}, not queued")
        acct = self.cfg.account(account)
        ok, why = policy.allow(acct, self.pcfg)
        if not ok:
            self.events.emit("policy.denied", "alert", f"lease refused: {why}", task=id, worker=worker,
                             for_=["W0"], data={"layer": "lease", "account": account})
            raise PolicyDenied(why)
        if len(t["attempts"]) >= t["retry"]["maxAttempts"] + t.get("extraAttempts", 0):
            raise IllegalTransition("attempts exhausted")
        w = self.cfg.worker(worker)
        prev = t["attempts"][-1]["n"] if t["attempts"] else None
        att = {"n": len(t["attempts"]) + 1, "worker": worker, "adapter": w.adapter if w else "shell",
               "account": account, "sessionId": None, "pid": None, "pgid": None, "pidStart": None, "runDir": None,
               "startedAt": now, "endedAt": None, "leaseUntil": now + 120, "lastHeartbeat": now, "exitCode": None,
               "outcome": "running", "usageStart": None, "usageEnd": None, "tokens": None, "continuationOf": prev,
               "lane": lane, "slot": slot, "outOffset": 0}
        t["attempts"].append(att)
        self.transition(id, "starting", actor="DSP", reason=f"{worker}·{lane}", worker=worker)
        self.events.emit("attempt.started", "info", f"{id} attempt {att['n']} on {worker}·{lane}", task=id,
                         attempt=att["n"], worker=worker)
        out = copy.deepcopy(att)
        out["task"], out["token"] = id, secrets.token_hex(8)
        return out

    def claim(self, id, account_home, worker):
        acct = self.cfg.account_for_home(account_home)
        ok, why = policy.allow(acct, self.pcfg)
        if not ok:
            self.events.emit("policy.denied", "alert", f"claim refused: {why}", task=id, for_=["W0"],
                             data={"layer": "claim"})
            raise PolicyDenied(why)
        t = self.tasks[id]
        if t["state"] == "starting":
            return self.transition(id, "running", actor=worker, reason="claimed")
        raise IllegalTransition("not offered")

    def update_attempt(self, task, n, **fields):
        att = self.tasks[task]["attempts"][n - 1]
        for k, v in fields.items():
            att[_camel(k)] = v

    def end_attempt(self, task, n, outcome, exit_code=None):
        att = self.tasks[task]["attempts"][n - 1]
        att.update(outcome=outcome, exitCode=exit_code, endedAt=clock.now())
        self.events.emit("attempt.ended", "info", f"{task} attempt {n} ended: {outcome}", task=task, attempt=n,
                         worker=att["worker"], data={"outcome": outcome, "exitCode": exit_code})

    def card_op(self, task, attempt, op, args, token=None):
        c = self.cards.setdefault(task, {"task": task, "steps": [], "metrics": []})
        if op == "step.add":
            c["steps"].append({"id": args.get("id") or args["title"].lower().replace(" ", "-")[:32],
                               "title": args["title"], "status": "pending", "etaMin": args["eta"], "attempt": attempt})
        elif op.startswith("step."):
            for s in c["steps"]:
                if s["id"] == args["id"]:
                    s["status"] = {"start": "active", "done": "done", "fail": "failed", "skip": "skipped"}.get(op[5:], s["status"])
        elif op == "note":
            c["note"] = args["text"]
        return copy.deepcopy(c)

    def card(self, task):
        return copy.deepcopy(self.cards.get(task))

    def put_handoff(self, task, n, data, valid=None):
        self.handoffs[(task, n)] = {"data": copy.deepcopy(data), "valid": valid}
        return data

    def handoff(self, task, n=None):
        if n is None:
            ns = [k[1] for k in self.handoffs if k[0] == task]
            if not ns:
                return None
            n = max(ns)
        h = self.handoffs.get((task, n))
        return copy.deepcopy(h["data"]) if h else None

    def review(self, task, decision, by, note="", rev=None):
        t = self.tasks[task]
        if decision == "approve":
            to = "done" if (t.get("isolation") == "none" or not (t.get("workspace") or {}).get("branch")) else "merging"
            r = self.transition(task, to, actor=by, reason=f"approved by {by}", rev=rev)
        elif decision == "changes":
            r = self.transition(task, "changes_requested", actor=by, reason=note, rev=rev, note=note)
        else:
            r = self.transition(task, "cancelled", actor=by, reason="rejected", rev=rev)
        self.tasks[task]["review"].update(decision={"approve": "approved", "changes": "changes"}.get(decision, "rejected"),
                                          by=by, note=note, decidedAt=clock.now())
        return r


# ------------------------------------------------------------------ global
class FakeUsage:
    def __init__(self):
        self.rows = {}

    def put(self, reading):
        r = reading.as_dict() if hasattr(reading, "as_dict") else dict(reading)
        self.rows.setdefault(r["account"], []).append(r)

    def latest(self, account):
        rs = self.rows.get(account)
        return max(rs, key=lambda r: r["read_at"]) if rs else None


class FakeLimits:
    def __init__(self):
        self.rows = []

    def add(self, account, kind, detected_at, resets_at, source, project=None, task=None, attempt=None, raw=None):
        self.rows.append(dict(id=len(self.rows) + 1, account=account, kind=kind, detected_at=detected_at,
                              resets_at=resets_at, source=source, project=project, task=task, attempt=attempt, raw=raw,
                              cleared_at=None))
        return len(self.rows)

    def active(self, account, now=None):
        now = clock.now() if now is None else now
        return [dict(r) for r in self.rows if r["account"] == account and not r["cleared_at"]
                and (r["resets_at"] or 0) > now]

    def clear(self, limit_id, at=None):
        self.rows[limit_id - 1]["cleared_at"] = at or clock.now()


class FakeLanes:
    def __init__(self, cfg):
        self.cfg, self.busy = cfg, {}     # (worker, lane) -> (project, task, attempt)

    def take(self, worker, project=None, task=None, attempt=None):
        w = self.cfg.worker(worker)
        if not w:
            return None
        acct_workers = [x.id for x in self.cfg.workers.values() if x.account == w.account]
        used = sum(1 for (wk, _l) in self.busy if wk in acct_workers)
        if used >= self.cfg.account(w.account).parallel_max:
            return None
        for lane in range(1, w.lanes + 1):
            if (worker, lane) not in self.busy:
                self.busy[(worker, lane)] = (project, task, attempt)
                return lane
        return None

    def release(self, worker, lane):
        self.busy.pop((worker, lane), None)

    def used_by_account(self, account):
        ws = {x.id for x in self.cfg.workers.values() if x.account == account}
        return sum(1 for (wk, _l) in self.busy if wk in ws)


class FakeAccountState:
    def __init__(self):
        self.rows = {}

    def put(self, account, state, capacity):
        self.rows[account] = {"account": account, "state": state, "capacity": copy.deepcopy(capacity)}

    def get(self, account):
        return self.rows.get(account)


class FakeGlobalStore:
    def __init__(self, cfg):
        self.cfg = cfg
        self.counter = _Counter()
        self.events = FakeEvents(None, self.counter, None)
        self.usage = FakeUsage()
        self.limits = FakeLimits()
        self.lanes = FakeLanes(cfg)
        self.account_state = FakeAccountState()
        self.ext_domains = FakeDomains({}, self.events)
        self.usage_cost = []


def make_stores(cfg):
    g = FakeGlobalStore(cfg)
    projects = {pid: FakeProjectStore(cfg, pid, g) for pid in cfg.projects}
    return g, projects
