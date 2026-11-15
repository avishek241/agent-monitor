"""Dispatcher (design §6.1–6.4, §6.9–6.10): quota-aware PARALLEL planner, timers, failover, spare/backlog alerts.

Every lease goes through `store.lease()` (policy re-checked inside its transaction) and every state change through
`store.transition()`. This module decides; the store enforces.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import capacity, clock, policy, prompt, quota
from . import run as deckrun
from . import workspace as wsmod
from .adapters import base as ab
from .adapters.claude_headless import ClaudeHeadless
from .errors import DeckError, IllegalTransition, PolicyDenied
from .supervise import ACTIVE, Probe, Supervisor, backoff

PRIO = {"P1": 0, "P2": 1, "P3": 2}
USAGE_TTL = 20
FAILOVER_MIN_SEC = 45 * 60
BACKLOG_DEDUP = 30 * 60


def free_ram_gb() -> Optional[float]:
    """Free + inactive + speculative pages (macOS vm_stat); None when unknown."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    page = 4096
    first = out.splitlines()[0] if out else ""
    if "page size of" in first:
        try:
            page = int(first.split("page size of")[1].split()[0])
        except (IndexError, ValueError):
            pass
    tot = 0
    for ln in out.splitlines():
        if ln.startswith(("Pages free", "Pages inactive", "Pages speculative")):
            try:
                tot += int(ln.split(":")[1].strip().rstrip("."))
            except (IndexError, ValueError):
                pass
    return tot * page / 1e9 if tot else None


class Dispatcher:
    def __init__(self, cfg, gstore, stores: dict, *, state_dir=None, launcher=None, probe=None, ram=None,
                 read_usage=None, mergeq=None, deck_url="http://127.0.0.1:7788"):
        self.cfg, self.gstore, self.stores = cfg, gstore, stores
        self.state_dir = Path(state_dir or cfg.state_dir)
        self.launcher = launcher or deckrun.spawn
        self.probe = probe or Probe()
        self.ram = ram or free_ram_gb
        self.read_usage = read_usage or quota.read_account
        self.mergeq = mergeq
        self.deck_url = deck_url
        self.sup = Supervisor(self)
        self.replan = False
        self.offer_penalty = {}
        self._usage_at = {}
        self._cooling = set()
        self._spare = {}            # account -> {"since", "alerted"}
        self._backlog_alert_at = None
        self._review_alert = {}     # (pid, tid) -> next alert ts
        self.notes = {}             # (pid, tid) -> waiting reason (last plan)
        self._note_strength = {}
        self.last_plan = []         # [(pid, tid, worker, lane)] started by the last plan()

    # ------------------------------------------------------------------ events
    def emit(self, pid, type, severity, text, **kw):
        if pid and pid in self.stores:
            return self.stores[pid].events.emit(type, severity, text, **kw)
        ev = getattr(self.gstore, "events", None)
        if ev is not None:
            return ev.emit(type, severity, text, **kw)
        for st in self.stores.values():
            return st.events.emit(type, severity, text, **kw)
        return None

    # ------------------------------------------------------------------ tick (§6.1)
    def tick(self, now=None):
        now = clock.now() if now is None else now
        deckrun.reap()
        for pid, st in self.stores.items():
            for task in st.list(ACTIVE):
                try:
                    self.sup.supervise(pid, st, task, now)
                except DeckError as e:
                    self.emit(pid, "daemon.error", "warn", f"supervise {task['id']}: {e}", task=task["id"])
        self.fire_timers(now)
        self.promote(now)
        self.refresh_usage(now)
        self.review_sla(now)
        if self.mergeq is not None:
            for pid, st in self.stores.items():
                self.mergeq.step(pid, st, now)
        started = self.plan(now)
        self.spare_checks(now)
        self.replan = False
        return started

    def on_wake(self, now=None):
        """Immediate re-plan (attempt ended, task queued, CLI write): same work as a tick."""
        return self.tick(now)

    def reconcile(self, now=None):
        now = clock.now() if now is None else now
        tot = {"finished": 0, "adopted": 0, "crashed": 0}
        for pid, st in self.stores.items():
            for k, v in self.sup.reconcile(pid, st, now).items():
                tot[k] += v
        self.emit(None, "daemon.reconciled", "info",
                  f"reconciled: {tot['finished']} finished while down, {tot['adopted']} re-adopted, {tot['crashed']} crashed",
                  data=tot)
        return tot

    # ------------------------------------------------------------------ timers + promotion
    def fire_timers(self, now):
        for pid, st in self.stores.items():
            for t in st.list(["limit_paused"]):
                if t.get("resumeAt") and now >= t["resumeAt"]:
                    st.transition(t["id"], "queued", actor="TMR", reason="limit reset — resuming")
                    self.replan = True
            for t in st.list(["changes_requested"]):
                if not t.get("resumeAt") or now >= t["resumeAt"]:
                    st.transition(t["id"], "queued", actor="DSP", reason="changes requested — continuing", attempts_plus=1)
                    self.replan = True

    def _project_enabled(self, pid):
        p = self.cfg.project(pid)
        return bool(p and p.enabled)

    def promote(self, now):
        for pid, st in self.stores.items():
            if not self._project_enabled(pid):
                continue
            states = {t["id"]: t["state"] for t in st.list()}
            for t in st.list(["ready", "queued"]):
                deps = t.get("deps") or []
                bad = [d for d in deps if states.get(d) in ("failed", "cancelled")]
                if bad:
                    st.transition(t["id"], "blocked", actor="DSP", reason=f"dep {bad[0]} {states[bad[0]]}")
                    self.emit(pid, "task.state", "action", f"{t['id']} blocked: dep {bad[0]} {states[bad[0]]}",
                              task=t["id"], for_=["W0"])
                    continue
                if t["state"] != "ready":
                    continue
                if any(states.get(d) != "done" for d in deps) or t.get("waitingFor"):
                    continue
                if t.get("kind") == "scale" and states.get(t.get("pilotOf")) != "done":
                    continue
                st.transition(t["id"], "queued", actor="DSP", reason="ready to run")
                self.replan = True

    # ------------------------------------------------------------------ usage + limits (§6.3)
    def refresh_usage(self, now):
        for a in self.cfg.accounts.values():
            if now - self._usage_at.get(a.id, 0) < USAGE_TTL:
                continue
            self._usage_at[a.id] = now
            try:
                r = self.read_usage(a, now)
            except Exception:  # noqa: BLE001 — a broken reading never stops dispatch
                r = None
            if r is not None:
                latest = self.gstore.usage.latest(a.id)
                if not latest or r.read_at > (latest.get("read_at") or 0):
                    self.gstore.usage.put(r)
            latest = self.gstore.usage.latest(a.id)
            for lim in self.gstore.limits.active(a.id, now):
                if quota.limit_cleared_by(lim, latest) and (latest or {}).get("source") != "stream":
                    self.gstore.limits.clear(lim["id"], now)
        for a in self.cfg.accounts.values():
            cooling = bool(self.gstore.limits.active(a.id, now))
            if a.id in self._cooling and not cooling:
                self.emit(None, "limit.reset", "info", f"{a.label} limit reset — back to work", data={"account": a.id})
                self.replan = True
            (self._cooling.add if cooling else self._cooling.discard)(a.id)

    def eff(self, aid, now):
        return quota.effective(self.gstore.usage.latest(aid), self.gstore.limits.active(aid, now), now)

    def eff5(self, aid, now):
        return self.eff(aid, now).get("eff5")

    def record_cost(self, task, att, usage_end, now):
        rows = getattr(self.gstore, "usage_cost", None)
        if rows is None or att.get("usageStart") is None or usage_end is None:
            return
        a = self.cfg.account(att["account"])
        rec = {"account_tool": a.tool if a else "?", "size": task.get("size"), "delta5": usage_end - att["usageStart"],
               "delta7": None, "minutes": (now - (att.get("startedAt") or now)) / 60, "ts": now}
        if rec["delta5"] >= 0 and not self.gstore.limits.active(att["account"], now):
            (rows.append if isinstance(rows, list) else rows.put)(rec)

    def _history(self):
        rows = getattr(self.gstore, "usage_cost", None)
        if isinstance(rows, list):
            return rows
        if rows is not None and hasattr(rows, "recent"):
            return rows.recent()
        return []

    # ------------------------------------------------------------------ capacity inputs
    def _running(self):
        """[(pid, task, attempt)] for every live attempt across projects."""
        out = []
        for pid, st in self.stores.items():
            for t in st.list(ACTIVE):
                a = Supervisor.current_attempt(t)
                if a:
                    out.append((pid, t, a))
        return out

    def account_input(self, aid, now, running=None) -> capacity.AccountInput:
        a = self.cfg.account(aid)
        e = self.eff(aid, now)
        jobs = []
        for _pid, t, att in (running if running is not None else self._running()):
            if att.get("account") == aid:
                est, mins = quota.estimate(t.get("size"), a.tool, self._history(), t.get("estimate"))
                jobs.append(capacity.Job(est, mins, (now - (att.get("startedAt") or now)) / 60))
        return capacity.AccountInput(
            id=aid, eff5=e["eff5"], pct7=e.get("pct7"), resets5=e.get("resets5"), confidence=e["confidence"],
            cooling=e["cooling"], cooling_until=e.get("coolingUntil"), parallel_max=a.parallel_max,
            reserve_pct=a.reserve_pct, ceiling_pct=a.ceiling_pct, weekly_ceiling_pct=a.weekly_ceiling_pct,
            jobs=jobs, dispatch=a.dispatch)

    def machine(self, running_total):
        s = self.cfg.scheduler
        return capacity.Machine(running_total, int(s.get("maxParallelTotal", 6)), self.ram(),
                                float(s.get("minFreeRamGb", 2)))

    # ------------------------------------------------------------------ candidates (§6.2 step 3)
    def candidates(self, now, per_project=None):
        """Queued tasks in §6.2 step-3 order: resumes, priority, project fair share, unblocks most, age, id."""
        if per_project is None:
            per_project = {}
            for pid, _t, _a in self._running():
                per_project[pid] = per_project.get(pid, 0) + 1
        cands = []
        for pid, st in self.stores.items():
            if not self._project_enabled(pid):
                continue
            all_tasks = st.list()
            for t in all_tasks:
                if t["state"] != "queued" or (t.get("resumeAt") and t["resumeAt"] > now):
                    continue
                t["_unblocks"] = sum(1 for o in all_tasks if t["id"] in (o.get("deps") or []))
                cands.append((pid, t))
        return self.order(cands, per_project)

    def order(self, cands, per_project):
        def key(c):
            pid, t = c
            weight = float(self.cfg.project(pid).weight or 1)
            return (0 if t.get("attempts") else 1, PRIO.get(t.get("priority"), 1), per_project.get(pid, 0) / weight,
                    -t.get("_unblocks", 0), t.get("createdAt") or 0, t["id"])
        return sorted(cands, key=key)

    def workers_for(self, aid, pid, task, now):
        """Eligible worker identities on account `aid`, in affinity order (§6.2 tie-breaks)."""
        last = (task.get("attempts") or [{}])[-1] if task.get("attempts") else {}
        wc = task.get("workerClass") or ["claude-headless", "codex-exec"]
        out = []
        for w in self.cfg.workers.values():
            if w.account != aid or w.adapter not in wc:
                continue
            if w.capabilities and task.get("capability") and task["capability"] not in w.capabilities:
                continue
            if task.get("pinWorker") and task["pinWorker"] != w.id:
                continue
            if w.role == "orchestrator" and task.get("pinWorker") != w.id:
                continue
            if self.offer_penalty.get((pid, task["id"], w.id), 0) > now:
                continue
            out.append(w)
        out.sort(key=lambda w: (0 if w.id == last.get("worker") else 1, wc.index(w.adapter), w.id))
        return out

    # ------------------------------------------------------------------ plan (§6.2 step 4)
    def plan(self, now=None):
        now = clock.now() if now is None else now
        running = self._running()
        accts = [a for a in self.cfg.accounts.values() if a.dispatch]
        inputs = {a.id: self.account_input(a.id, now, running) for a in accts}
        caps = {aid: capacity.account_capacity(ai, now) for aid, ai in inputs.items()}
        mach = self.machine(len(running))
        per_project = {}
        for pid, _t, _a in running:
            per_project[pid] = per_project.get(pid, 0) + 1
        cands = self.candidates(now, per_project)
        self.notes, self._note_strength = {}, {}
        started = []
        while True:
            admitted = 0
            order = sorted((a for a in accts if not caps[a.id].hard_stop), key=lambda a: (-caps[a.id].urgency, a.id))
            for a in order:
                for pid, task in list(cands):
                    res = self._try_place(a, pid, task, inputs[a.id], caps[a.id], mach, now)
                    if res:
                        cands.remove((pid, task))
                        per_project[pid] = per_project.get(pid, 0) + 1
                        cands = self.order(cands, per_project)
                        started.append(res)
                        admitted += 1
                        mach.running_total += 1
                        est, mins = quota.estimate(task.get("size"), a.tool, self._history(), task.get("estimate"))
                        inputs[a.id].jobs.append(capacity.Job(est, mins, 0))
                        caps[a.id] = capacity.account_capacity(inputs[a.id], now)
                        break
            if admitted == 0:
                break
        for pid, task in cands:
            why = self.notes.get((pid, task["id"]))
            if not why:
                why = self._no_account_reason(pid, task, caps, now)
                self.notes[(pid, task["id"])] = why
            st = self.stores[pid]
            if hasattr(st, "set_reason"):
                st.set_reason(task["id"], f"waiting: {why}")
        self._publish_account_state(caps, inputs, now)
        if started:
            self.emit(None, "plan.round", "debug", f"started {len(started)} job(s)",
                      data={"started": [f"{p}/{t}@{w}·{l}" for p, t, w, l in started]})
        self.last_plan = started
        return started

    def _note(self, pid, task, why, strength):
        k = (pid, task["id"])
        cur = self.notes.get(k)
        if cur is None or strength >= self._note_strength.get(k, 0):
            self.notes[k] = why
            self._note_strength[k] = strength

    def _no_account_reason(self, pid, task, caps, now):
        accts = [a for a in self.cfg.accounts.values()]
        if not any(self.workers_for(a.id, pid, task, now) for a in accts):
            return "no configured worker matches capability/workerClass/pin"
        if not any(a.dispatch and self.workers_for(a.id, pid, task, now) for a in accts):
            return "only accounts with dispatch off can run it"
        allowed = [a for a in accts if a.dispatch and self.workers_for(a.id, pid, task, now)
                   and policy.allow(a, self.cfg.project(pid))[0]]
        if not allowed:
            return "account policy: no allowed account"
        stops = [caps[a.id].hard_stop for a in allowed if a.id in caps and caps[a.id].hard_stop]
        if stops and len(stops) == len(allowed):
            return stops[0]
        return f"no account with quota for size {task.get('size', 'M')}"

    def _try_place(self, acct, pid, task, ai, cap, mach, now):
        tid = task["id"]
        pcfg = self.cfg.project(pid)
        st = self.stores[pid]
        workers = self.workers_for(acct.id, pid, task, now)
        if not workers:
            return None
        ok, why = policy.allow(acct, pcfg)
        if not ok:
            self._note(pid, task, f"account policy ({acct.label}: {why})", 1)
            return None
        est, mins = quota.estimate(task.get("size"), acct.tool, self._history(), task.get("estimate"))
        ok, why = capacity.admit(cap, ai, est, mins, task.get("priority", "P2"), mach)
        if not ok:
            self._note(pid, task, f"{acct.label}: {why}", 2)
            return None
        names = list(task.get("domains") or [])
        holder = tid
        # one all-or-nothing call: the project store delegates ext: names to the machine-wide table (holder pid/tid)
        ok, blk = st.domains.try_acquire(names, holder, now=now)
        if not ok:
            self._note(pid, task, f"domain {blk['name']} held by {blk['holder']}", 3)
            return None
        slot = st.slots.take(holder)
        if slot is None:
            st.domains.release_all(holder)
            self._note(pid, task, "no free slot (ports/DB)", 3)
            return None
        for w in workers:
            lane = self.gstore.lanes.take(w.id, pid, tid)
            if lane is None:
                continue
            try:
                att = st.lease(tid, w.id, lane, acct.id, slot=slot, domains=names, now=now)
            except PolicyDenied as e:
                self.gstore.lanes.release(w.id, lane)
                self._release_holds(pid, st, tid, keep_slot=False)
                self._note(pid, task, f"policy denied in lease: {e.reason}", 4)
                return None
            except DeckError as e:          # IllegalTransition, DomainBusy, NotFound …
                self.gstore.lanes.release(w.id, lane)
                self._release_holds(pid, st, tid, keep_slot=False)
                self._note(pid, task, f"lease refused: {e.reason}", 4)
                return None
            if not self._launch(pid, st, st.get(tid), att, w, acct, now):
                self._note(pid, task, "launch failed — retrying after backoff", 4)
                return None
            return (pid, tid, w.id, lane)
        self._release_holds(pid, st, tid, keep_slot=False)
        self._note(pid, task, f"{acct.label}: no free lane on its workers", 2)
        return None

    def _release_holds(self, pid, st, tid, keep_slot=False):
        st.domains.release_all(tid)          # includes this task's ext: holds (pid/tid) in the global table
        if not keep_slot:
            st.slots.release(tid)

    def release(self, pid, st, task, att, keep_slot=False):
        """End-of-attempt release: domains, lane, and the slot unless kept (limit_paused keeps it)."""
        self._release_holds(pid, st, task["id"], keep_slot=keep_slot)
        if att.get("lane") is not None:
            self.gstore.lanes.release(att["worker"], att["lane"])

    # ------------------------------------------------------------------ launch
    def _launch(self, pid, st, task, att, worker, acct, now):
        tid, n = task["id"], att["n"]
        pcfg = self.cfg.project(pid)
        try:
            # policy layer 4: re-check with the account we are about to put in the environment
            live = self.cfg.account_of_worker(worker.id)
            ok, why = policy.allow(live, pcfg)
            if live is None or live.id != att["account"] or not ok:
                raise PolicyDenied(why or "worker's account changed between lease and launch")
            ws = wsmod.ensure(pcfg, task) if pcfg.repo else {"path": os.getcwd()}
            task["workspace"] = {**(task.get("workspace") or {}), **ws, "slot": att.get("slot")}
            senv = wsmod.slot_env(pcfg, att.get("slot"), tid)
            run_dir = self.state_dir / "runs" / pid / tid / str(n)
            run_dir.mkdir(parents=True, exist_ok=True)
            resume, interrupted = self._resume_target(task, att, worker, acct, run_dir)
            card = st.card(tid) if hasattr(st, "card") else None
            last_h = self._last_handoff(st, task)
            text = prompt.build(pcfg, task, worker, ws["path"], senv, n, card=card, last_handoff=last_h)
            (run_dir / "prompt.md").write_text(text)
            run_id = f"{pid}:{tid}:{n}"
            deck_env = {"DECK_PROJECT": pid, "DECK_TASK": tid, "DECK_ATTEMPT": str(n), "DECK_TOKEN": att.get("token", ""),
                        "DECK_URL": self.deck_url, "DECK_STATE": str(self.state_dir), "DECK_RUN_ID": run_id}
            adapter = ab.get(worker.adapter)
            schema = Path(__file__).parent / "schemas" / "handoff.schema.json"
            ctx = ab.LaunchContext(project=pcfg, task=task, attempt=n, worker=worker, account=acct,
                                   workspace=Path(ws["path"]), slot_env=senv, run_dir=run_dir, prompt=text,
                                   handoff_schema=schema, resume_session=resume, deck_env=deck_env,
                                   interrupted=interrupted)
            spec = adapter.launch_spec(ctx)
            st.update_attempt(tid, n, usage_start=self.eff5(acct.id, now))
            if spec is None:      # interactive: offer only
                st.update_attempt(tid, n, lease_until=now + 900)
                self.emit(pid, "attempt.started", "action", f"{tid} is offered to {worker.id} — run `deck claim {tid}`",
                          task=tid, attempt=n, worker=worker.id, for_=[worker.id])
                return True
            (run_dir / "launch.json").write_text(json.dumps({"argv": spec.argv[:-1] + ["<prompt.md>"],
                                                             "cwd": str(spec.cwd)}, indent=1))
            ports = list(wsmod.port_range(pcfg, att["slot"])) if att.get("slot") is not None else None
            self.launcher(spec, run_dir, run_id, ports)
            st.update_attempt(tid, n, run_dir=str(run_dir), lease_until=now + 120)
            return True
        except PolicyDenied as e:
            self.emit(pid, "policy.denied", "alert", f"launch of {tid} on {worker.id} refused: {e.reason}"[:200],
                      task=tid, attempt=n, worker=worker.id, for_=["W0"], data={"layer": "launch"})
            self._launch_failed(pid, st, tid, att, now, f"policy: {e.reason}")
            return False
        except Exception as e:  # noqa: BLE001 — any setup failure is T7, never a stuck 'starting'
            self.emit(pid, "daemon.error", "warn", f"launch of {tid} failed: {e}"[:200], task=tid, attempt=n)
            self._launch_failed(pid, st, tid, att, now, str(e)[:120])
        return False

    def _launch_failed(self, pid, st, tid, att, now, why):
        st.end_attempt(tid, att["n"], "crashed")
        self.release(pid, st, st.get(tid), att)
        self.sup._first_ok(st, tid, [("queued", dict(reason=f"launch failed: {why}", resume_at=now + backoff(att["n"], 1)))],
                           [("failed", dict(reason=f"launch failed: {why}; attempts exhausted"))])
        self.replan = True

    def workspace_of(self, task):
        """Task workspace; falls back to the deterministic worktree path/branch when the store has no path."""
        ws = dict(task.get("workspace") or {})
        pcfg = self.cfg.project(task.get("project") or "")
        if not ws.get("path") and pcfg is not None:
            ws.update(wsmod.expected(pcfg, task))
        return ws

    def _last_handoff(self, st, task):
        if hasattr(st, "handoff"):
            try:
                h = st.handoff(task["id"])
                return (h or {}).get("data")
            except Exception:  # noqa: BLE001
                return None
        for a in reversed(task.get("attempts") or []):
            p = Path(a.get("runDir") or "/nonexistent") / "handoff.json"
            if p.exists():
                return json.loads(p.read_text())
        return None

    def _resume_target(self, task, att, worker, acct, run_dir):
        """Same account+adapter → resume the session id; Claude→Claude across accounts → copied transcript path
        (spike 3); anything else → continuation prompt only."""
        prev = [a for a in (task.get("attempts") or [])[:-1] if a.get("sessionId")]
        if not prev:
            return None, False
        last = prev[-1]
        interrupted = last.get("outcome") in ("hung", "cancelled", "timeout")
        if last.get("adapter") != worker.adapter:
            return None, False
        if last.get("account") == acct.id:
            copied = Path(last.get("runDir") or "/nonexistent") / f"{last['sessionId']}.jsonl"
            if worker.adapter == "claude-headless" and copied.exists():
                return str(copied), interrupted          # this session already lives in a run dir copy
            return last["sessionId"], interrupted
        if worker.adapter == "claude-headless":
            src_acct = self.cfg.account(last.get("account"))
            copied = Path(last.get("runDir") or "/nonexistent") / f"{last['sessionId']}.jsonl"
            if copied.exists():
                import shutil
                dst = Path(run_dir) / copied.name
                shutil.copy2(copied, dst)
                return str(dst), interrupted
            if src_acct:
                return ClaudeHeadless.prepare_cross_account_resume(src_acct.home, last["sessionId"], run_dir), interrupted
        return None, False

    # ------------------------------------------------------------------ limits + failover (§6.4)
    def after_limit(self, pid, st, tid, att, resets_at, now):
        task = st.get(tid)
        mode = (task.get("retry") or {}).get("onLimit", "auto")
        if mode == "wait":
            return
        if mode == "auto":
            if resets_at - now <= FAILOVER_MIN_SEC:
                return
            if not self._other_account_can_take(pid, task, att["account"], now):
                return
        st.transition(tid, "queued", actor="DSP", reason=f"failover from {att['account']} (limit)", failover=True,
                      pin_worker=None, resume_at=None)
        self.emit(pid, "task.reassigned", "info", f"{tid} fails over from {att['account']} after its limit", task=tid)
        self.replan = True

    def _other_account_can_take(self, pid, task, exclude, now):
        running = self._running()
        pcfg = self.cfg.project(pid)
        free_pin = dict(task, pinWorker=None)
        for a in self.cfg.accounts.values():
            if a.id == exclude or not a.dispatch or not policy.allow(a, pcfg)[0]:
                continue
            if not self.workers_for(a.id, pid, free_pin, now):
                continue
            ai = self.account_input(a.id, now, running)
            cap = capacity.account_capacity(ai, now)
            est, mins = quota.estimate(task.get("size"), a.tool, self._history(), task.get("estimate"))
            if capacity.admit(cap, ai, est, mins, task.get("priority", "P2"), self.machine(len(running)))[0]:
                return True
        return False

    # ------------------------------------------------------------------ review SLA (§6.9)
    def review_sla(self, now):
        for pid, st in self.stores.items():
            live = set()
            for t in st.list(["review"]):
                k = (pid, t["id"])
                live.add(k)
                req = (t.get("review") or {}).get("requestedAt") or t.get("stateSince") or now
                nxt = self._review_alert.get(k, req + 30 * 60)
                if now >= nxt:
                    waited = (now - req) // 60
                    sev = "alert" if now - req >= 2 * 3600 else "action"
                    pilot = " PILOT" if t.get("kind") == "pilot" else ""
                    self.emit(pid, "review.overdue", sev, f"{t['id']}{pilot} waiting for review {waited} min",
                              task=t["id"], for_=["W0"])
                    self._review_alert[k] = now + 60 * 60
            for k in [k for k in self._review_alert if k[0] == pid and k not in live]:
                self._review_alert.pop(k)

    # ------------------------------------------------------------------ spare capacity + backlog (§5.3, §6.10)
    def _eligible(self, a, pid, task, now):
        return bool(self.workers_for(a.id, pid, task, now)) and policy.allow(a, self.cfg.project(pid))[0]

    def spare_checks(self, now):
        running = self._running()
        mach = self.machine(len(running))
        pending = [(pid, t) for pid, st in self.stores.items() if self._project_enabled(pid)
                   for t in st.list(["queued", "ready"])]
        spare_lanes, hints = 0, []
        for a in self.cfg.accounts.values():
            if not a.dispatch:
                continue
            ai = self.account_input(a.id, now, running)
            cap = capacity.account_capacity(ai, now)
            could = capacity.admit(cap, ai, *quota.estimate("S", a.tool, self._history()), machine=None,
                                   ignore_lanes=True)[0]
            free = max(0, cap.lanes_max - cap.lanes_used) if could else 0
            spare_lanes += free
            if free:
                fits = capacity.fits_size(cap, ai, a.tool, self._history())
                hints.append(f"{a.label} {free} lane(s) ({fits or '-'} fits)")
            sp = self._spare.setdefault(a.id, {"since": None, "alerted": False})
            if cap.lanes_used or not could:
                sp.update(since=None, alerted=False)
                continue
            elig = [(pid, t) for pid, t in pending if self._eligible(a, pid, t, now)]
            blocked_by_policy = [(pid, t) for pid, t in pending
                                 if self.workers_for(a.id, pid, t, now) and not policy.allow(a, self.cfg.project(pid))[0]]
            if not elig and not blocked_by_policy:
                sp.update(since=None, alerted=False)
                continue
            sp["since"] = sp["since"] or now
            if sp["alerted"] or now - sp["since"] < int(self.cfg.scheduler.get("spareAlertMin", 5)) * 60:
                continue
            sp["alerted"] = True
            left = f"{max(0.0, cap.R5):.0f} %"
            until = time.strftime("%H:%M", time.localtime(ai.resets5)) if ai.resets5 else "reset"
            if elig:
                reasons = {}
                for pid, t in elig:
                    r = self.notes.get((pid, t["id"])) or ("waiting for deps" if t["state"] == "ready" else "not placed")
                    reasons[r] = reasons.get(r, 0) + 1
                top = max(reasons, key=reasons.get)
                self.emit(None, "account.spare", "action",
                          f"{a.label} has {left} to spend before {until} and 0 jobs — {reasons[top]} task(s) wait: {top}"[:200],
                          for_=["W0"], data={"account": a.id, "reason": top})
            else:
                self.emit(None, "account.spare", "info",
                          f"{a.label} idle: queued tasks are not allowed on it (account policy)"[:200],
                          data={"account": a.id, "reason": "account policy"})
        target = int(self.cfg.scheduler.get("backlogTarget", 0)) or (spare_lanes + 2)
        eligible = [(pid, t) for pid, t in pending
                    if any(self._eligible(a, pid, t, now) for a in self.cfg.accounts.values() if a.dispatch)]
        if len(eligible) < target and spare_lanes:
            if self._backlog_alert_at is None or now - self._backlog_alert_at >= BACKLOG_DEDUP:
                self._backlog_alert_at = now
                self.emit(None, "backlog.low", "action",
                          (f"Spare: {', '.join(hints)} — only {len(eligible)} ready task(s). "
                           f"Brief {target - len(eligible)} more.")[:200],
                          for_=["W0"], data={"eligible": len(eligible), "target": target, "hints": hints})

    # ------------------------------------------------------------------ UI feed
    def _publish_account_state(self, caps, inputs, now):
        for aid, cap in caps.items():
            a = self.cfg.account(aid)
            ai = inputs[aid]
            fits = capacity.fits_size(cap, ai, a.tool, self._history())
            spare = cap.lanes_used < cap.lanes_max and not cap.hard_stop and fits is not None
            why = cap.hard_stop
            if not why and fits is None:
                why = f"{max(0.0, cap.R5 - cap.committed5):.0f}% left before the reset — no job size fits"
            if not why and spare:
                mine = [r for (pid, _t), r in self.notes.items() if a.label in r or "policy" in r]
                why = mine[0] if mine else None
            state = ("cooling" if cap.cooling else "unknown" if cap.unknown else "full" if cap.hard_stop or fits is None
                     else "tight" if fits == "S" else "ok")
            try:
                self.gstore.account_state.put(aid, state, cap.as_vm(fits, spare, why))
            except Exception:  # noqa: BLE001
                pass
