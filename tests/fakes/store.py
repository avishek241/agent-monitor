"""In-memory FakeStore implementing the frozen store API (deck/store_api.py) for B2 unit tests (design §17).

It follows the §5.2 transition table (pairs only; guards that need B1's validator are skipped), the §2.3 policy
inside lease(), and §6.2.1 domain rules closely enough for planner tests. Kept after B1 lands, for fast tests.
"""
from __future__ import annotations

import copy
import re
import secrets
from typing import Iterable, Optional

import json
from pathlib import Path

from deck import clock, policy
from deck.adapters import schemas as hs
from deck.errors import DomainBusy, IllegalTransition, NotFound, PolicyDenied, StaleRev, ValidationError

HANDOFF_SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "deck" / "schemas" / "handoff.schema.json").read_text())
ATTEMPT_SCHEMA_KEYS = set(json.loads((Path(__file__).resolve().parents[2] / "deck" / "schemas" / "task.schema.json")
                                     .read_text())["$defs"]["attempt"]["properties"])
# B1 store.EDITABLE: the dispatcher may not write workspace/stateReason through edit()
EDITABLE = {"title", "summary", "tags", "priority", "size", "kind", "pilotOf", "deps", "capability", "workerClass",
            "pinWorker", "domains", "estimate", "isolation", "timeouts", "retry", "merge", "waitingFor", "brief"}

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

    def __init__(self, decl=None, events=None, external=None, ext_holder=None):
        self.decl = decl or {}
        self.held = []          # {name, holder, since, expires}
        self.waits = {}         # (name, holder) -> since
        self.events = events
        self.external, self.ext_holder = external, ext_holder

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
        if self.external is not None:
            ext = [n for n in names if n.startswith("ext:")]
            local = [n for n in names if not n.startswith("ext:")]
            for n in local:
                b = self._blocker(n, holder, now)
                if b:
                    for m in local:
                        self.waits.setdefault((m, holder), now)
                    return False, b
            if ext:
                ok, b = self.external.try_acquire(ext, self.ext_holder(holder), ttl, now)
                if not ok:
                    return False, b
            names = local
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
        n = before - len(self.held)
        if self.external is not None:
            n += self.external.release_all(self.ext_holder(holder))
        return n

    def release(self, name, holder=None):
        before = len(self.held)
        self.held = [h for h in self.held if not (h["name"] == name and (holder is None or h["holder"] == holder))]
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
        self.domains = FakeDomains(self.pcfg.domains if self.pcfg else {}, self.events, external=gstore.ext_domains,
                                   ext_holder=lambda h: f"{pid}/{h}")
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

    def _public(self, t):
        """Like B1: task dicts carry only the schema subset of each attempt (no outOffset/slot/cpuSecs)."""
        out = copy.deepcopy(t)
        out["attempts"] = [{k: v for k, v in a.items() if k in ATTEMPT_SCHEMA_KEYS} for a in out["attempts"]]
        return out

    def get(self, id):
        if id not in self.tasks:
            raise NotFound(f"no task {id}")
        return self._public(self.tasks[id])

    def list(self, states=None):
        st = set(states) if states else None
        return [self._public(t) for t in self.tasks.values() if st is None or t["state"] in st]

    def current_attempt(self, task):
        atts = self.tasks[task]["attempts"]
        return dict(copy.deepcopy(atts[-1]), task=task) if atts else None

    def edit(self, id, fields, *, by="W0", rev=None):
        t = self.tasks[id]
        bad = set(fields) - EDITABLE
        if bad:
            raise ValidationError(f"field(s) not editable: {', '.join(sorted(bad))}")
        if rev is not None and rev != t["rev"]:
            raise StaleRev("stale rev")
        if fields.get("pinWorker"):
            w = self.cfg.worker(fields["pinWorker"])
            ok, why = policy.allow(self.cfg.account(w.account) if w else None, self.pcfg)
            if not ok:
                raise PolicyDenied(why)
        t.update(copy.deepcopy(fields))
        t["rev"] += 1
        return self._public(t)

    # ---- B1-equivalent guards / side effects (deck/store.py _guard/_apply), compressed
    def _review_rounds(self, tid):
        return sum(1 for e in self.events.rows if e["task"] == tid and e["type"] == "review.decided"
                   and e["data"].get("decision") == "changes")

    def attempts_left(self, t):
        return len(t["attempts"]) < int((t.get("retry") or {}).get("maxAttempts", 3)) + self._review_rounds(t["id"])

    def has_branch(self, t):
        return not (t.get("isolation") == "none" or t.get("capability") in ("research", "review")
                    or t.get("isolation") == "main-checkout")

    def _release(self, t, keep_slot=False):
        self.domains.release_all(t["id"])
        if not keep_slot:
            self.slots.release(t["id"])
        a = t["attempts"][-1] if t["attempts"] else None
        if a and a.get("lane"):
            self.g.lanes.release(a["worker"], a["lane"])

    def transition(self, id, to, *, actor, reason="", rev=None, **eff):
        t = self.tasks.get(id)
        if t is None:
            raise NotFound(id)
        if rev is not None and rev != t["rev"]:
            raise StaleRev("stale rev")
        frm = t["state"]
        if (frm, to) not in ALLOWED:
            raise IllegalTransition(f"{frm} → {to} not allowed")
        now = clock.now()
        a = t["attempts"][-1] if t["attempts"] else None
        h = self.handoffs.get((id, a["n"])) if a else None
        ev = []
        refuse = None
        pair = (frm, to)
        if pair == ("starting", "queued") or pair == ("running", "queued") and actor not in ("W0", "owner"):
            if not self.attempts_left(t):
                refuse = "attempts exhausted"
        elif pair == ("hung", "queued") and not (eff.get("killed") and self.attempts_left(t)
                                                 and (t.get("retry") or {}).get("onHang", "restart") == "restart"):
            refuse = "restart guard"
        elif pair == ("hung", "blocked") and (t.get("retry") or {}).get("onHang", "restart") == "restart" \
                and self.attempts_left(t):
            refuse = "onHang=restart with attempts left"
        elif to == "failed" and frm in ("running", "hung", "starting") and self.attempts_left(t) \
                and now - ((t["attempts"][0]["startedAt"] if t["attempts"] else now)) < int((t.get("timeouts") or {}).get("taskHours", 48)) * 3600:
            refuse = "attempts left and within taskHours"
        elif pair == ("limit_paused", "queued") and not (eff.get("failover") or not t.get("resumeAt") or now >= t["resumeAt"]):
            refuse = "resume not due"
        elif pair == ("changes_requested", "queued") and not self.attempts_left(t):
            refuse = "attempts exhausted"
        elif pair == ("running", "blocked") and actor not in ("W0", "owner"):
            if not ((h and h["valid"] and h["data"].get("outcome") == "blocked") or (self.cards.get(id) or {}).get("question")):
                refuse = "no blocked handoff or question"
        elif pair == ("review", "merging") and not self.has_branch(t):
            refuse = "no branch"
        elif pair == ("review", "done") and self.has_branch(t):
            refuse = "has a branch"
        if refuse:
            raise IllegalTransition(f"{id}: {frm} → {to} refused: {refuse}")
        t["state"], t["stateReason"], t["stateSince"], t["updatedAt"] = to, reason, now, now
        t["rev"] += 1
        if pair == ("starting", "queued"):
            self._release(t)
            crashes = sum(1 for x in t["attempts"] if x["outcome"] == "crashed")
            t["resumeAt"] = now + 60 * (1, 2, 4)[min(max(crashes - 1, 0), 2)]
        elif pair == ("running", "queued"):
            self._release(t)
            fails = sum(1 for x in t["attempts"] if x["outcome"] in ("crashed", "timeout"))
            t["resumeAt"] = now + 60 * (2, 4, 8)[min(max(fails - 1, 0), 2)]
        elif to == "review" and frm == "running":
            if not (h and h["valid"]):
                synth = {"schema": "deck.handoff/1", "task": id, "attempt": a["n"] if a else None, "outcome": "unknown",
                         "summary": "The attempt ended without a valid handoff; review the branch and run output.",
                         "doneWhen": [], "tests": [], "risks": ["no valid handoff"], "decisionsNeeded": []}
                if h is None and a:
                    self.handoffs[(id, a["n"])] = {"attempt": a["n"], "ts": now, "valid": False, "data": synth}
                ev.append(("handoff.invalid", "alert", f"{id} ended without a valid handoff", ["W0"], {}))
            self._release(t)
            t["review"] = {"requestedAt": now, "decision": None, "by": None, "note": None, "decidedAt": None}
            label = "PILOT " if t.get("kind") == "pilot" else ""
            ev.append(("review.requested", "action", f"{label}{id} ready to review: {t['title']}"[:200], ["W0"], {}))
        elif to == "limit_paused":
            t["resumeAt"] = (eff.get("resets_at") or now + 3600) + 90
            self._release(t, keep_slot=True)
            ev.append(("limit.hit", "warn", (reason or f"{id} hit a limit")[:200], [],
                       {"resetsAt": eff.get("resets_at"), "account": (a or {}).get("account")}))
        elif pair == ("running", "hung"):
            ev.append(("attempt.hung", "alert", f"{id} hung: {reason}"[:200], ["W0"], dict(eff.get("data") or {})))
        elif pair == ("hung", "running"):
            ev.append(("attempt.heartbeat", "info", f"{id} recovered", [], {"recovered": True}))
        elif pair == ("hung", "queued"):
            self._release(t)
            ev.append(("attempt.killed", "warn", f"{id} killed after hang; continuing in a new attempt", [], {}))
        elif pair == ("hung", "blocked"):
            self._release(t)
            ev.append(("task.state", "action", f"{id} blocked: hung", ["W0"], {}))
        elif to == "failed":
            self._release(t)
            ev.append(("task.state", "alert", f"{id} failed: {reason or 'attempts exhausted'}"[:200], ["W0"], {}))
        elif pair == ("limit_paused", "queued"):
            t["resumeAt"] = None
            if eff.get("failover"):
                t["pinWorker"] = None
            ev.append(("limit.reset", "info", f"{id} resumes", [], {"failover": bool(eff.get("failover"))}))
        elif pair in (("ready", "blocked"), ("queued", "blocked")):
            ev.append(("task.state", "action", f"{id} blocked: upstream {reason or 'dep'} failed", ["W0"], {}))
        elif pair == ("running", "blocked"):
            self._release(t)
        elif pair == ("review", "changes_requested"):
            t["review"] = {**(t.get("review") or {}), "decision": "changes", "by": actor, "note": eff.get("note"),
                           "decidedAt": now}
            ev.append(("review.decided", "info", f"{id} changes by {actor}", [], {"decision": "changes"}))
        elif pair in (("review", "merging"), ("review", "done")):
            t["review"] = {**(t.get("review") or {}), "decision": "approved", "by": actor, "decidedAt": now}
            ev.append(("review.decided", "info", f"{id} approved by {actor}", [], {"decision": "approve"}))
        elif pair == ("merging", "done"):
            self.slots.release(id)
            ev.append(("merge.done", "info", f"{id} merged", [], {}))
        elif pair == ("merging", "changes_requested"):
            t["review"] = {**(t.get("review") or {}), "decision": "changes", "by": actor, "note": eff.get("note"),
                           "decidedAt": now}
            if not eff.get("event_emitted"):
                ev.append(("merge.failed", "alert", f"{id} merge failed", ["W0"], {}))
        elif pair == ("changes_requested", "queued"):
            t["resumeAt"] = None
        elif to == "cancelled":
            self._release(t)
        if frm in ("starting", "running", "hung") and actor in ("W0", "owner") and a and a["outcome"] == "running":
            a.update(outcome="cancelled", endedAt=now)      # B1: T20/T29/T30 end the live attempt
        if to not in ("starting", "running", "hung", "limit_paused"):
            t["worker"] = None
        if "worker" in eff:
            t["worker"] = eff["worker"]
        self.events.emit("task.state", "info", f"{id} {frm} → {to}" + (f": {reason}" if reason else ""), task=id,
                         data={"from": frm, "to": to, "by": actor})
        for typ, sev, text, for_, data in ev:
            self.events.emit(typ, sev, text, task=id, attempt=(a or {}).get("n"), worker=(a or {}).get("worker"),
                             for_=for_, data=data)
        return self._public(t)

    def lease(self, id, worker, lane, account, *, slot=None, domains=(), now=None, adapter=None, continuation_of=None):
        now = clock.now() if now is None else now
        t = self.tasks[id]
        acct = self.cfg.account(account)
        w = self.cfg.worker(worker)
        why = None
        if w is not None and w.account != account:
            why = f"worker {worker} belongs to account {w.account}, not {account}"
        else:
            ok, why = policy.allow(acct, self.pcfg)
            why = None if ok else why
        if why:
            self.events.emit("policy.denied", "alert", f"policy denied: {why}"[:200], task=id, worker=worker,
                             for_=["W0"], data={"layer": "lease", "account": account})
            raise PolicyDenied(why)
        if t["state"] != "queued":
            raise IllegalTransition(f"{id} is {t['state']}, not queued")
        if w.capabilities and t["capability"] not in w.capabilities:
            raise IllegalTransition(f"{worker} lacks capability {t['capability']}")
        if w.adapter not in (t.get("workerClass") or ["claude-headless", "codex-exec"]):
            raise IllegalTransition("adapter not in workerClass")
        if t.get("pinWorker") not in (None, worker):
            raise IllegalTransition(f"{id} is pinned to {t['pinWorker']}")
        if not self.attempts_left(t):
            raise IllegalTransition(f"{id} has no attempts left")
        ok, blk = self.domains.try_acquire(sorted(set(list(domains or []) + list(t.get("domains") or []))), id, now=now)
        if not ok:
            raise DomainBusy(f"waiting: {blk['reason']}", blocker=blk)
        if slot is None and t.get("isolation", "worktree") != "none":
            slot = self.slots.take(id)
        prev = t["attempts"][-1]["n"] if t["attempts"] else None
        att = {"n": len(t["attempts"]) + 1, "worker": worker, "adapter": adapter or w.adapter,
               "account": account, "sessionId": None, "pid": None, "pgid": None, "pidStart": None, "runDir": None,
               "startedAt": now, "endedAt": None, "leaseUntil": now + 120, "lastHeartbeat": now, "exitCode": None,
               "outcome": "running", "usageStart": None, "usageEnd": None, "tokens": None,
               "continuationOf": continuation_of if continuation_of is not None else prev, "lane": lane, "slot": slot,
               "outOffset": 0}
        t["attempts"].append(att)
        if slot is not None:
            t["workspace"] = {**(t.get("workspace") or {}), "slot": slot}
        t["state"], t["stateReason"], t["stateSince"], t["worker"] = "starting", f"{worker}·{lane} starting", now, worker
        t["rev"] += 1
        self.events.emit("task.state", "info", f"{id} queued → starting", task=id, data={"from": "queued", "to": "starting"})
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
        errs = hs.validate(data, HANDOFF_SCHEMA) if isinstance(data, dict) else ["handoff is not an object"]
        if isinstance(data, dict) and data.get("outcome") == "unknown":
            errs.append("outcome 'unknown' is set by the daemon only")
        if valid is None:
            valid = not errs
        stored = {**copy.deepcopy(data), "task": task, "attempt": n}
        self.handoffs[(task, n)] = {"attempt": n, "ts": clock.now(), "valid": bool(valid), "data": stored}
        if valid:
            self.events.emit("handoff.received", "info", f"{task} handoff: {stored.get('outcome')}", task=task, attempt=n)
            for fu in stored.get("followUps") or []:
                by = self.tasks[task]["attempts"][-1]["worker"] if self.tasks[task]["attempts"] else "worker"
                self.create_task({"title": fu["title"], "summary": fu.get("summary") or fu["title"],
                                  "size": fu.get("size", "M"), "createdBy": by}, by=by)
        else:
            self.events.emit("handoff.invalid", "alert", f"{task} handoff invalid: {'; '.join(errs[:2])}"[:200],
                             task=task, attempt=n, for_=["W0"], data={"errors": errs[:10]})
        return {"valid": bool(valid), "errors": errs, "data": stored}

    def handoff(self, task, n=None):
        if n is None:
            ns = [k[1] for k in self.handoffs if k[0] == task]
            if not ns:
                return None
            n = max(ns)
        h = self.handoffs.get((task, n))
        return copy.deepcopy(h) if h else None

    def review(self, task, decision, by, note="", rev=None):
        t = self.tasks[task]
        if decision in ("approve", "approved"):
            return self.transition(task, "merging" if self.has_branch(t) else "done", actor=by,
                                   reason=f"approved by {by}", rev=rev, note=note)
        if decision in ("changes", "changes_requested"):
            return self.transition(task, "changes_requested", actor=by, reason="changes requested", rev=rev, note=note)
        return self.transition(task, "cancelled", actor=by, reason=f"rejected by {by}", rev=rev, note=note)


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
