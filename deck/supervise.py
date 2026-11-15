"""Supervisor (design §6.5–6.7): tails every attempt, heartbeats, hang detection, end of attempt, reconciliation.

Runs inside deckd's tick. It never blocks on a kill: kills run in a background thread and the wrapper's exit.json
reports the end on a later tick.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Optional

from . import clock, quota
from . import run as deckrun
from .adapters import base as ab
from .adapters import schemas as hs

ACTIVE = ("starting", "running", "hung")
START_TIMEOUT = 120
LIMIT_EXIT_GRACE = 60
CPU_HEARTBEAT = 2.0
CPU_HUNG = 1.0
REVIEW_SLA = 30 * 60


class Probe:
    """Process facts and actions. Tests replace it to drive hangs with a fake clock."""

    def __init__(self, kill_waits=(20, 10)):
        self.kill_waits = kill_waits

    alive = staticmethod(deckrun.alive)
    process_start = staticmethod(deckrun.process_start)
    group_cpu_secs = staticmethod(deckrun.group_cpu_secs)
    deepest_child = staticmethod(deckrun.deepest_child)
    group_pids = staticmethod(deckrun.group_pids)

    def kill(self, pgid, block=False):
        if block:
            return deckrun.kill_group(pgid, *self.kill_waits)
        threading.Thread(target=deckrun.kill_group, args=(pgid, *self.kill_waits), daemon=True).start()
        return "started"


def backoff(n: int, base_min: int) -> int:
    """1/2/4 or 2/4/8 minutes for attempt n (1-based)."""
    return base_min * 60 * (2 ** max(0, n - 1))


def git_facts(workspace: Optional[str], base: Optional[str], branch: Optional[str]) -> dict:
    if not workspace or not Path(workspace).is_dir():
        return {}

    def g(*a):
        r = subprocess.run(["git", "-C", workspace, *a], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=30)
        return r.stdout if r.returncode == 0 else ""
    out = {"branch": branch, "base": base}
    if base and branch:
        log = g("log", "--format=%h%x09%s", f"{base}..{branch}")
        out["commits"] = [{"sha": ln.split("\t", 1)[0], "subject": ln.split("\t", 1)[-1]} for ln in log.splitlines()][:50]
        stat = g("diff", "--shortstat", f"{base}...{branch}")
        nums = [int(x) for x in __import__("re").findall(r"(\d+) (?:file|insertion|deletion)", stat)]
        out["filesChanged"] = nums[0] if nums else 0
        out["insertions"] = nums[1] if len(nums) > 1 else 0
        out["deletions"] = nums[2] if len(nums) > 2 else 0
        mt = subprocess.run(["git", "-C", workspace, "merge-tree", "--write-tree", base, branch], capture_output=True,
                            text=True, stdin=subprocess.DEVNULL, timeout=30)
        out["conflictsWithMain"] = mt.returncode == 1
    out["uncommitted"] = len([ln for ln in g("status", "--porcelain").splitlines() if ln.strip()])
    return {k: v for k, v in out.items() if v is not None}


def render_handoff_md(task: dict, h: dict) -> str:
    lines = [f"# {task['id']} handoff (attempt {h.get('attempt')})", "", f"**Outcome:** {h.get('outcome')}", "",
             h.get("summary", ""), "", "## Done when"]
    for d in h.get("doneWhen") or []:
        lines.append(f"- [{'x' if d.get('met') else ' '}] {d.get('item')} — {d.get('evidence')}")
    lines += ["", "## Tests"] + [f"- `{t.get('command')}`: {t.get('result')}" for t in h.get("tests") or []]
    lines += ["", "## Risks"] + [f"- {r}" for r in h.get("risks") or []]
    lines += ["", "## Decisions needed"] + [f"- {d.get('question')}" + (f" ({' / '.join(d['options'])})" if d.get("options") else "")
                                           for d in h.get("decisionsNeeded") or []]
    if h.get("nextStep"):
        lines += ["", f"**Next step:** {h['nextStep']}"]
    return "\n".join(lines) + "\n"


class Supervisor:
    def __init__(self, rt):
        self.rt = rt                # the Dispatcher (cfg, gstore, stores, probe, emit, replan flag)
        self.mem = {}               # (pid, task, n) -> volatile per-attempt state
        schema_path = Path(__file__).parent / "schemas" / "handoff.schema.json"
        self.handoff_schema = json.loads(schema_path.read_text())

    # ------------------------------------------------------------------ helpers
    def _m(self, pid, tid, n):
        return self.mem.setdefault((pid, tid, n), {"parse": {}, "samples": [], "suspect": False, "killed": None,
                                                   "final": None, "limit": None, "lastCpu": None, "lines": 0})

    @staticmethod
    def current_attempt(task):
        atts = task.get("attempts") or []
        return atts[-1] if atts and atts[-1].get("outcome") == "running" else None

    def _read_new_lines(self, store, task, att):
        rd = att.get("runDir")
        if not rd:
            return [], False
        p = Path(rd) / "out.jsonl"
        if not p.exists():
            return [], False
        off = int(att.get("outOffset") or 0)
        size = p.stat().st_size
        if size <= off:
            return [], False
        with open(p, "rb") as fh:
            fh.seek(off)
            data = fh.read()
        cut = data.rfind(b"\n")
        if cut < 0:
            return [], True          # partial line: output grew (heartbeat) but nothing to parse yet
        chunk = data[:cut + 1]
        store.update_attempt(task["id"], att["n"], out_offset=off + len(chunk))
        att["outOffset"] = off + len(chunk)
        return chunk.decode("utf-8", "replace").splitlines(), True

    # ------------------------------------------------------------------ per tick
    def supervise(self, pid, store, task, now):
        att = self.current_attempt(task)
        if not att:
            return
        tid, n = task["id"], att["n"]
        m = self._m(pid, tid, n)
        adapter = ab.get(att.get("adapter") or "shell")
        tmo = task.get("timeouts") or {}
        quiet_sec = int(tmo.get("quietMin", 15)) * 60

        if att.get("adapter") == "interactive":
            return self._supervise_interactive(pid, store, task, att, now, quiet_sec)
        rd = att.get("runDir")
        if not rd:
            if now - (att.get("startedAt") or now) > START_TIMEOUT:
                self._requeue_start_failure(pid, store, task, att, "launch never happened")
            return
        rd = Path(rd)
        if att.get("pid") is None and (rd / "pid.json").exists():
            try:
                pj = json.loads((rd / "pid.json").read_text())
                store.update_attempt(tid, n, pid=pj["pid"], pgid=pj["pgid"], pid_start=pj.get("start"))
                att.update(pid=pj["pid"], pgid=pj["pgid"], pidStart=pj.get("start"))
                m["wrapper"] = pj.get("wrapper")
            except (OSError, ValueError, KeyError):
                pass

        lines, grew = self._read_new_lines(store, task, att)
        hb = grew
        if lines:
            m["lines"] += len(lines)
            for sig in adapter.parse(lines, now, m["parse"]):
                hb = self._signal(pid, store, task, att, sig, now) or hb
        if task["state"] == "starting" and (m["lines"] or att.get("sessionId")):
            task = store.transition(tid, "running", actor="ADP", reason=f"{att['worker']}·{att.get('lane', 1)}")
        if (rd / "exit.json").exists():
            return self.finish(pid, store, store.get(tid), now)

        pgid = att.get("pgid")
        if pgid:
            cpu = self.rt.probe.group_cpu_secs(pgid)
            if m["lastCpu"] is None:
                m["lastCpu"] = cpu
            elif cpu - m["lastCpu"] >= CPU_HEARTBEAT:
                hb, m["lastCpu"] = True, cpu
            m["samples"].append((now, cpu))
            m["samples"] = [s for s in m["samples"] if now - s[0] <= 2 * quiet_sec + 60]
            store.update_attempt(tid, n, cpu_secs=cpu)
            if not self.rt.probe.alive(att["pid"]) and not (m.get("wrapper") and self.rt.probe.alive(m["wrapper"])):
                if not (rd / "exit.json").exists():
                    m["noExitSince"] = m.get("noExitSince") or now
                    if now - m["noExitSince"] > 30:
                        return self._crashed_no_exit(pid, store, task, att, now)
        if hb:
            store.update_attempt(tid, n, last_heartbeat=now)
            att["lastHeartbeat"] = now
            m["suspect"] = False
            if task["state"] == "hung":
                store.transition(tid, "running", actor="SUP", reason="heartbeat resumed")
                self.rt.emit(pid, "attempt.heartbeat", "info", f"{tid} recovered", task=tid, attempt=n)
                task = store.get(tid)
        store.update_attempt(tid, n, lease_until=now + 120)

        if task["state"] == "starting" and now - (att.get("startedAt") or now) > START_TIMEOUT and not m["lines"]:
            if pgid:
                self.rt.probe.kill(pgid)
            m["killed"] = "start-timeout"
            return
        if m["limit"] and now - m["limit"]["at"] > LIMIT_EXIT_GRACE and pgid and not m["killed"]:
            m["killed"] = "limit"
            self.rt.probe.kill(pgid)
            return
        if now - (att.get("startedAt") or now) > int(tmo.get("attemptMin", 240)) * 60 and not m["killed"] and pgid:
            m["killed"] = "timeout"
            self.rt.emit(pid, "attempt.killed", "alert", f"{tid} attempt {n} passed attemptMin — stopping", task=tid,
                         attempt=n, for_=["W0"])
            self.rt.probe.kill(pgid)
            return
        if task["state"] in ("running", "hung") and pgid and not m["killed"]:
            self._hang_check(pid, store, task, att, now, quiet_sec, m)

    def _hang_check(self, pid, store, task, att, now, quiet_sec, m):
        tid, n = task["id"], att["n"]
        quiet = now - (att.get("lastHeartbeat") or att.get("startedAt") or now)
        if task["state"] == "running" and quiet >= quiet_sec and not m["suspect"]:
            m["suspect"] = True
            dc = self.rt.probe.deepest_child(att["pgid"])
            child = f" — deepest child: {dc[0][:60]} for {dc[1] // 60} min" if dc else ""
            self.rt.emit(pid, "attempt.suspect", "warn", f"{tid} quiet {quiet // 60} min{child}", task=tid, attempt=n,
                         worker=att["worker"], data={"deepestChild": dc[0] if dc else None})
        if task["state"] == "running" and quiet >= 2 * quiet_sec:
            old = [c for (t, c) in m["samples"] if now - t >= quiet_sec]
            cur = m["samples"][-1][1] if m["samples"] else 0.0
            delta = (cur - old[-1]) if old else 0.0
            if delta < CPU_HUNG:
                dc = self.rt.probe.deepest_child(att["pgid"])
                store.transition(tid, "hung", actor="SUP", reason=f"no output for {quiet // 60} min, CPU idle",
                                 cpu_delta=delta)
                self.rt.emit(pid, "attempt.hung", "alert",
                             f"{tid} hung: quiet {quiet // 60} min, CPU idle" + (f" — {dc[0][:60]}" if dc else ""),
                             task=tid, attempt=n, worker=att["worker"], for_=["W0"],
                             data={"deepestChild": dc[0] if dc else None, "ageSec": dc[1] if dc else None})
                on_hang = (task.get("retry") or {}).get("onHang", "restart")
                if on_hang in ("restart", "block"):
                    m["killed"] = "hung"
                    self.rt.probe.kill(att["pgid"])

    def _supervise_interactive(self, pid, store, task, att, now, quiet_sec):
        tid = task["id"]
        if task["state"] == "starting" and now > int(att.get("leaseUntil") or 0):
            store.end_attempt(tid, att["n"], "crashed")
            self.rt.release(pid, store, task, att, keep_slot=False)
            store.transition(tid, "queued", actor="SUP", reason=f"offer to {att['worker']} expired")
            self.rt.offer_penalty[(pid, tid, att["worker"])] = now + 1800
            self.rt.replan = True

    def _signal(self, pid, store, task, att, sig, now):
        tid, n = task["id"], att["n"]
        m = self._m(pid, tid, n)
        if isinstance(sig, ab.SessionId):
            if att.get("sessionId") != sig.id:
                store.update_attempt(tid, n, session_id=sig.id)
                att["sessionId"] = sig.id
            return True
        if isinstance(sig, ab.Activity):
            m["recent"] = sig.text
            return True
        if isinstance(sig, ab.Tokens):
            store.update_attempt(tid, n, tokens=sig.total)
            return True
        if isinstance(sig, ab.Usage):
            self.rt.gstore.usage.put(ab.UsageReading(att["account"], now, "stream", sig.pct5, sig.pct7, sig.resets5,
                                                     sig.resets7))
            return False
        if isinstance(sig, ab.Limit):
            self._record_limit(pid, task, att, sig, now)
            return True
        if isinstance(sig, ab.Final):
            m["final"] = sig
            return True
        if isinstance(sig, ab.ApiError):
            m["apiError"] = sig
            return True
        return False

    def _record_limit(self, pid, task, att, sig, now):
        m = self._m(pid, task["id"], att["n"])
        if m["limit"]:
            return
        resets = sig.resets_at
        if resets is None and att.get("adapter") == "codex-exec":
            r = quota.read_account(self.rt.cfg.account(att["account"]), now)
            if r and r.resets5 and r.resets5 > now:
                resets = r.resets5
        if resets is None:
            resets = now + 3600         # unknown: +60 min, re-checked against readings
        lid = self.rt.gstore.limits.add(att["account"], sig.kind, now, resets, "adapter", project=pid, task=task["id"],
                                        attempt=att["n"], raw=sig.raw)
        m["limit"] = {"at": now, "resets": resets, "kind": sig.kind, "id": lid}
        self.rt.replan = True

    # ------------------------------------------------------------------ end of attempt
    def finish(self, pid, store, task, now):
        att = self.current_attempt(task)
        if not att:
            return
        tid, n = task["id"], att["n"]
        m = self._m(pid, tid, n)
        rd = Path(att["runDir"])
        adapter = ab.get(att.get("adapter") or "shell")
        lines, _ = self._read_new_lines(store, task, att)
        for sig in adapter.parse(lines, now, m["parse"]):
            self._signal(pid, store, task, att, sig, now)
        try:
            ex = json.loads((rd / "exit.json").read_text())
        except (OSError, ValueError):
            ex = {"code": None}
        code = ex.get("code")
        if ex.get("orphansKilled"):
            try:
                orphans = json.loads((rd / "orphans.json").read_text())
            except (OSError, ValueError):
                orphans = []
            self.rt.emit(pid, "orphan.killed", "warn", f"{tid}: killed {ex['orphansKilled']} leftover process(es)",
                         task=tid, attempt=n, data={"orphans": orphans})
        final = m["final"] or adapter.final(rd, m["parse"])
        # limit text on stderr / final text (fallback when no structured signal came)
        if not m["limit"]:
            err_tail = ""
            try:
                err_tail = (rd / "err.log").read_text(errors="replace")[-4000:]
            except OSError:
                pass
            hit = quota.parse_limit_text(((final.text if final else "") or "") + "\n" + err_tail, int(ex.get("endedAt") or now))
            if hit and (code not in (0, None) or (final and final.is_error)):
                self._record_limit(pid, task, att, ab.Limit(hit[1], hit[0], hit[2]), now)
        handoff, errors = None, []
        if final and final.handoff is not None:
            handoff = dict(final.handoff)
            handoff.setdefault("schema", "deck.handoff/1")
            errors = hs.validate(handoff, self.handoff_schema)
        cur = store.get(tid)
        if cur["state"] not in ACTIVE:
            # paused / cancelled / reassigned while running: the actor already moved the task
            store.end_attempt(tid, n, "cancelled", code)
            self.rt.release(pid, store, cur, att, keep_slot=cur["state"] == "blocked")
            self._done(pid, tid, n)
            return
        retry = cur.get("retry") or {}
        max_att = int(retry.get("maxAttempts", 3)) + int(cur.get("extraAttempts", 0))
        more = n < max_att
        usage_end = self.rt.eff5(att["account"], now)
        store.update_attempt(tid, n, usage_end=usage_end)
        self.rt.record_cost(cur, att, usage_end, now)

        if m["limit"]:
            lim = m["limit"]
            store.end_attempt(tid, n, "limit", code)
            self.rt.release(pid, store, cur, att, keep_slot=True)
            resume_at = lim["resets"] + 90
            hhmm = __import__("time").strftime("%H:%M", __import__("time").localtime(resume_at))
            store.transition(tid, "limit_paused", actor="ADP", reason=f"{att['worker']} hit its limit · resumes {hhmm}",
                             resets_at=lim["resets"], resume_at=resume_at)
            self.rt.emit(pid, "limit.hit", "warn", f"{att['account']} hit its {lim['kind']} limit · {tid} resumes {hhmm}",
                         task=tid, attempt=n, worker=att["worker"],
                         data={"account": att["account"], "resetsAt": lim["resets"]})
            self.rt.after_limit(pid, store, tid, att, lim["resets"], now)
        elif m["killed"] == "hung":
            store.end_attempt(tid, n, "hung", code)
            self.rt.release(pid, store, cur, att)
            on_hang = retry.get("onHang", "restart")
            if on_hang == "restart" and more:
                store.transition(tid, "queued", actor="SUP", reason="restarted after hang", killed=True)
                self.rt.emit(pid, "attempt.killed", "warn", f"{tid} attempt {n} killed after hang; continuing", task=tid,
                             attempt=n)
            elif on_hang == "restart":
                store.transition(tid, "failed", actor="SUP", reason="hung; attempts exhausted")
                self.rt.emit(pid, "task.state", "alert", f"{tid} failed: hung and no attempts left", task=tid, for_=["W0"])
            else:
                store.transition(tid, "blocked", actor="SUP", reason="hung — needs a look")
        elif handoff is not None and not errors:
            self._accept_handoff(pid, store, cur, att, handoff, code, now)
        elif m["killed"] in ("timeout", "start-timeout") or (code not in (0, None)) or (handoff is None and code is None):
            outcome = "timeout" if m["killed"] == "timeout" else "crashed"
            store.end_attempt(tid, n, outcome, code)
            self.rt.release(pid, store, cur, att)
            if cur["state"] == "starting" and more:
                store.transition(tid, "queued", actor="SUP", reason="did not start", resume_at=now + backoff(n, 1))
            elif more:
                store.transition(tid, "queued", actor="SUP", reason=f"attempt {n} {outcome} (exit {code}) — retry",
                                 resume_at=now + backoff(n, 2))
            else:
                store.transition(tid, "failed", actor="SUP", reason=f"attempt {n} {outcome}; attempts exhausted")
                self.rt.emit(pid, "task.state", "alert", f"{tid} failed after {n} attempts", task=tid, for_=["W0"])
        else:
            # exited cleanly with no valid handoff → synthetic handoff, still reaches review (T9)
            synth = {"schema": "deck.handoff/1", "outcome": "unknown",
                     "summary": "No valid handoff was returned; check the run log and the branch before deciding.",
                     "doneWhen": [], "tests": [], "risks": ["no valid handoff"] + errors[:3], "decisionsNeeded": []}
            self.rt.emit(pid, "handoff.invalid", "alert", f"{tid} ended without a valid handoff", task=tid, attempt=n,
                         for_=["W0"], data={"errors": errors[:5]})
            self._accept_handoff(pid, store, cur, att, synth, code, now, valid=False)
        self._done(pid, tid, n)

    def _accept_handoff(self, pid, store, task, att, h, code, now, valid=True):
        tid, n = task["id"], att["n"]
        ws = task.get("workspace") or {}
        h["task"], h["attempt"] = tid, n
        h["git"] = git_facts(ws.get("path"), ws.get("base"), ws.get("branch")) if ws.get("branch") else {}
        h["run"] = {"worker": att["worker"], "adapter": att.get("adapter"),
                    "minutes": round((now - (att.get("startedAt") or now)) / 60, 1), "tokens": att.get("tokens"),
                    "exitCode": code, "orphansKilled": 0}
        rd = Path(att["runDir"])
        try:
            ex = json.loads((rd / "exit.json").read_text())
            h["run"]["orphansKilled"] = int(ex.get("orphansKilled") or 0)
        except (OSError, ValueError):
            pass
        (rd / "handoff.json").write_text(json.dumps(h, indent=1))
        (rd / "handoff.md").write_text(render_handoff_md(task, h))
        store.put_handoff(tid, n, h, valid)
        self._commit_handoff_md(task, h)
        outcome = h.get("outcome")
        if outcome == "blocked":
            store.end_attempt(tid, n, "blocked", code)
            self.rt.release(pid, store, task, att)
            q = (h.get("decisionsNeeded") or [{}])[0].get("question") or h.get("nextStep") or "worker is blocked"
            store.transition(tid, "blocked", actor="ADP", reason=q[:160])
            self.rt.emit(pid, "question.asked", "action", f"{tid} asks: {q}"[:200], task=tid, attempt=n, for_=["W0"])
        else:
            store.end_attempt(tid, n, "handoff", code)
            self.rt.release(pid, store, task, att)
            store.transition(tid, "review", actor="ADP", reason=f"handoff: {outcome}", handoff=h)
            self.rt.emit(pid, "handoff.received", "info", f"{tid} handed off ({outcome})", task=tid, attempt=n)
            pilot = " PILOT" if task.get("kind") == "pilot" else ""
            self.rt.emit(pid, "review.requested", "action", f"{tid}{pilot} ready to review: {task.get('title', '')}"[:200],
                         task=tid, attempt=n, for_=["W0"])
        for fu in h.get("followUps") or []:
            try:
                store.create_task({"title": fu["title"], "summary": fu.get("summary", ""), "size": fu.get("size", "M"),
                                   "followUpOf": tid}, by=att["worker"])
            except Exception:  # noqa: BLE001 — a bad follow-up never loses the handoff
                pass

    def _commit_handoff_md(self, task, h):
        ws = task.get("workspace") or {}
        if (task.get("isolation") or "worktree") != "worktree" or not ws.get("path") or not Path(ws["path"]).is_dir():
            return
        pcfg = self.rt.cfg.project(task.get("project") or "")
        rel = Path((pcfg.briefsDir if pcfg else "docs/tasks")) / f"{task['id']}-handoff.md"
        dst = Path(ws["path"]) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render_handoff_md(task, h))
        for a in (["add", str(rel)], ["commit", "-q", "-m", f"docs: {task['id']} handoff (attempt {h.get('attempt')})",
                                      "--", str(rel)]):
            subprocess.run(["git", "-C", ws["path"], *a], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        h.setdefault("git", {})

    def _requeue_start_failure(self, pid, store, task, att, why):
        tid, n = task["id"], att["n"]
        store.end_attempt(tid, n, "crashed")
        self.rt.release(pid, store, task, att)
        store.transition(tid, "queued", actor="SUP", reason=f"start failed: {why}", resume_at=clock.now() + backoff(n, 1))
        self.rt.replan = True

    def _crashed_no_exit(self, pid, store, task, att, now):
        tid, n = task["id"], att["n"]
        store.end_attempt(tid, n, "crashed")
        self.rt.release(pid, store, task, att)
        more = n < int((task.get("retry") or {}).get("maxAttempts", 3)) + int(task.get("extraAttempts", 0))
        if more:
            store.transition(tid, "queued", actor="SUP", reason="process died without an exit record — retry",
                             resume_at=now + backoff(n, 2))
        else:
            store.transition(tid, "failed", actor="SUP", reason="process died; attempts exhausted")
        self._done(pid, tid, n)

    def _done(self, pid, tid, n):
        self.mem.pop((pid, tid, n), None)
        self.rt.replan = True

    # ------------------------------------------------------------------ restart reconciliation (§6.7)
    def reconcile(self, pid, store, now):
        counts = {"finished": 0, "adopted": 0, "crashed": 0}
        for task in store.list(ACTIVE):
            att = self.current_attempt(task)
            if not att or att.get("adapter") == "interactive":
                continue
            rd = Path(att["runDir"]) if att.get("runDir") else None
            if rd and (rd / "exit.json").exists():
                self.finish(pid, store, task, now)
                counts["finished"] += 1
                continue
            if rd and att.get("pid") is None and (rd / "pid.json").exists():
                try:
                    pj = json.loads((rd / "pid.json").read_text())
                    att.update(pid=pj["pid"], pgid=pj["pgid"], pidStart=pj.get("start"))
                    store.update_attempt(task["id"], att["n"], pid=pj["pid"], pgid=pj["pgid"], pid_start=pj.get("start"))
                except (OSError, ValueError, KeyError):
                    pass
            pid_ = att.get("pid")
            if pid_ and self.rt.probe.alive(pid_) and self.rt.probe.process_start(pid_) == att.get("pidStart"):
                store.update_attempt(task["id"], att["n"], lease_until=now + 120)
                counts["adopted"] += 1
                continue
            self._crashed_no_exit(pid, store, task, att, now)
            counts["crashed"] += 1
        return counts


def stop_attempt(attempt: dict, probe: Optional[Probe] = None, block=False):
    """Pause/cancel/reassign helper for the CLI (T20/T29/T30): SIGINT → 20 s → SIGTERM → 10 s → SIGKILL."""
    pg = attempt.get("pgid")
    if pg:
        return (probe or Probe()).kill(pg, block=block)
    return "no-process"
