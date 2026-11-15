"""Merge queue (design §6.8; M6, M7). One consumer per repo, serial, under the project `merge` domain.

Rules that make M6 impossible:
- Never touch a main checkout that has someone else's MERGE_HEAD / REBASE_HEAD / CHERRY_PICK_HEAD / index.lock.
- `git merge --abort` only (a) inside the task worktree for our own merge of the target, or (b) in the main checkout
  when WE created the MERGE_HEAD in this step.
- Migrations are renumbered on top of the target under the merge lock (`seq:db-migrations` implied).
Deviation from §5.2 T26: a busy main checkout keeps the task in `merging` and retries in 2 min (no review→merging
round trip, which would need a reviewer actor); the `merge.failed` event still says "main checkout busy".
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path

from . import clock
from . import workspace as wsmod

PRIO = {"P1": 0, "P2": 1, "P3": 2}
BUSY_RETRY = 120
GATE_TIMEOUT = 1800


def _run(args, cwd, env=None, timeout=600, shell=False):
    r = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL, shell=shell)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def git(cwd, *args, timeout=120):
    return _run(["git", *args], cwd, timeout=timeout)


def git_dir(repo) -> Path:
    rc, out = git(repo, "rev-parse", "--git-dir")
    p = Path(out.strip())
    return p if p.is_absolute() else Path(repo) / p


def busy_reason(repo, target):
    g = git_dir(repo)
    for f in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "index.lock"):
        if (g / f).exists():
            return f"main checkout busy ({f} present)"
    for d in ("rebase-merge", "rebase-apply"):
        if (g / d).exists():
            return f"main checkout busy ({d} in progress)"
    rc, head = git(repo, "symbolic-ref", "--short", "HEAD")
    if rc == 0 and head.strip() != target:
        return f"main checkout is on {head.strip()}, not {target}"
    return None


def changed_files(ws, target, branch):
    rc, out = git(ws, "diff", "--name-only", f"{target}...{branch}")
    return [ln for ln in out.splitlines() if ln.strip()]


def commit_all(ws, msg):
    git(ws, "add", "-A")
    rc, out = git(ws, "diff", "--cached", "--quiet")
    if rc != 0:
        git(ws, "commit", "-q", "-m", msg)
        return True
    return False


class MergeQueue:
    def __init__(self, dispatcher):
        self.d = dispatcher
        self.retry_at = {}          # (pid, tid) -> ts

    def queue(self, pid, store, now):
        items = [t for t in store.list(["merging"]) if self.retry_at.get((pid, t["id"]), 0) <= now]
        return sorted(items, key=lambda t: (PRIO.get(t.get("priority"), 1), t.get("stateSince") or 0, t["id"]))

    def step(self, pid, store, now=None):
        """Merge at most one task of this project. Returns (task id, result) or None."""
        now = clock.now() if now is None else now
        q = self.queue(pid, store, now)
        if not q:
            return None
        task = q[0]
        ok, blk = store.domains.try_acquire(["merge"], "merge-queue", now=now)
        if not ok:
            return None
        try:
            self.d.emit(pid, "merge.started", "info", f"merging {task['id']}", task=task["id"])
            res = self.merge(pid, store, task, now)
        finally:
            store.domains.release_all("merge-queue")
        return task["id"], res

    # ------------------------------------------------------------------
    def merge(self, pid, store, task, now):
        pcfg = self.d.cfg.project(pid)
        tid = task["id"]
        wsd = self.d.workspace_of(task)
        ws = wsd.get("path")
        branch = wsd.get("branch") or f"task/{tid}"
        target = (task.get("merge") or {}).get("target") or pcfg.mainBranch
        repo = Path(os.path.expanduser(pcfg.repo))
        if not ws or not Path(ws).is_dir():
            return self._changes(pid, store, tid, "worktree missing — nothing to merge")
        why = busy_reason(repo, target)
        if why:
            self.retry_at[(pid, tid)] = now + BUSY_RETRY
            self.d.emit(pid, "merge.failed", "warn", f"{tid}: {why} — retry in 2 min, not touching it", task=tid)
            return "busy"
        # dirty overlap: uncommitted files in main that the branch also changes
        rc, st = git(repo, "status", "--porcelain")
        dirty = {ln[3:].strip() for ln in st.splitlines() if ln.strip()}
        overlap = dirty & set(changed_files(ws, target, branch))
        if overlap:
            self.retry_at[(pid, tid)] = now + BUSY_RETRY
            self.d.emit(pid, "merge.failed", "warn", f"{tid}: main checkout has uncommitted changes to "
                        f"{', '.join(sorted(overlap))[:80]} — retry in 2 min", task=tid)
            return "busy"
        # 1. bring the target into the worktree (our own merge; abort here only)
        rc, out = git(ws, "merge", "--no-edit", target)
        if rc != 0:
            _rc, conf = git(ws, "diff", "--name-only", "--diff-filter=U")
            git(ws, "merge", "--abort")
            files = [f for f in conf.splitlines() if f.strip()]
            self.d.emit(pid, "merge.conflict", "alert", f"{tid} conflicts with {target}: {', '.join(files)[:120]}",
                        task=tid, for_=["W0"], data={"files": files})
            return self._changes(pid, store, tid, f"Conflicts with {target} in: {', '.join(files)}. Merge {target} "
                                                  "into your branch, resolve, commit.")
        # 2. migrations renumbered on top of the target (under the merge lock)
        rule = pcfg.migrationRule or {}
        changed = changed_files(ws, target, branch)
        h = ((store.handoff(tid) or {}).get("data") if hasattr(store, "handoff") else None)
        mig = [f for f in changed if any(fnmatch.fnmatch(f, p) for p in rule.get("paths") or [])]
        if (mig or (h or {}).get("migrations")) and rule.get("regenerate"):
            ok = store.domains.try_acquire(["seq:db-migrations"], "merge-queue", now=now)[0]
            rc, out = _run(rule["regenerate"], ws, env=self._env(pcfg, None, tid), shell=True)
            if rc != 0:
                return self._changes(pid, store, tid, "Migration regenerate failed:\n" + out[-2000:])
            if commit_all(ws, "regenerate migration after main"):
                self.d.emit(pid, "merge.gate", "info", f"{tid}: migration renumbered on top of {target}", task=tid)
        # 3. preMerge commands
        for cmd in (task.get("merge") or {}).get("preMerge") or []:
            rc, out = _run(cmd, ws, env=self._env(pcfg, None, tid), shell=True, timeout=GATE_TIMEOUT)
            if rc != 0:
                return self._changes(pid, store, tid, f"preMerge `{cmd}` failed:\n" + "\n".join(out.splitlines()[-60:]))
        commit_all(ws, f"pre-merge: {tid}")
        # 4. gates in a FRESH slot (≠ the attempt's slot) — catches hard-coded ports / DB names (M7)
        self._canonical_port_check(pid, pcfg, ws, target, branch, tid)
        gates = list(pcfg.gates or []) + list((task.get("merge") or {}).get("gates") or [])
        if gates:
            avoid = (task.get("workspace") or {}).get("slot")
            if avoid is None and task.get("attempts"):
                avoid = task["attempts"][-1].get("slot")
            holder, slot = self._fresh_slot(store, tid, avoid)
            try:
                env = self._env(pcfg, slot, tid)
                for cmd in gates:
                    rc, out = _run(cmd, ws, env=env, shell=True, timeout=GATE_TIMEOUT)
                    self.d.emit(pid, "merge.gate", "info" if rc == 0 else "warn",
                                f"{tid} gate `{cmd[:60]}` {'passed' if rc == 0 else 'FAILED'} (slot {slot})", task=tid)
                    if rc != 0:
                        return self._changes(pid, store, tid, f"Gate `{cmd}` failed in slot {slot}:\n"
                                             + "\n".join(out.splitlines()[-60:]))
            finally:
                if holder:
                    store.slots.release(holder)
        # 5. into the main checkout; abort only a merge WE started
        had = (git_dir(repo) / "MERGE_HEAD").exists()
        rc, out = git(repo, "merge", "--ff-only", branch)
        if rc != 0:
            rc, out = git(repo, "merge", "--no-edit", branch)
            if rc != 0:
                if not had and (git_dir(repo) / "MERGE_HEAD").exists():
                    git(repo, "merge", "--abort")
                return self._changes(pid, store, tid, "Merge into the main checkout failed:\n" + out[-1500:])
        # 6. done: remove the worktree we created and the merged task/ branch
        store.transition(tid, "done", actor="MQ", reason=f"merged into {target}")       # T24: store emits merge.done
        if branch.startswith("task/"):
            git(repo, "worktree", "remove", ws)
            git(repo, "branch", "-d", branch)
        store.slots.release(tid)
        self.d.replan = True
        return "done"

    def _env(self, pcfg, slot, tid):
        env = dict(os.environ)
        env.update({"CI": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "PAGER": "cat"})
        if slot is not None:
            env.update(wsmod.slot_env(pcfg, slot, tid))
        return env

    def _fresh_slot(self, store, tid, avoid):
        h1 = f"merge-gate:{tid}"
        s1 = store.slots.take(h1)
        if s1 is None or s1 != avoid:
            return h1, s1
        h2 = f"merge-gate2:{tid}"
        s2 = store.slots.take(h2)
        store.slots.release(h1)
        return (h2, s2) if s2 is not None else (None, s1)

    def _canonical_port_check(self, pid, pcfg, ws, target, branch, tid):
        ports = [str(p) for p in (pcfg.slots or {}).get("canonicalPorts") or []]
        if not ports:
            return
        tests = [f for f in changed_files(ws, target, branch) if re.search(r"(^|/)(tests?|__tests__|e2e)(/|$)|\.(test|spec)\.", f)]
        if not tests:
            return
        rc, diff = git(ws, "diff", f"{target}...{branch}", "--", *tests)
        hits = sorted({p for p in ports for ln in diff.splitlines() if ln.startswith("+") and re.search(rf"\b{p}\b", ln)})
        if hits:
            self.d.emit(pid, "merge.gate", "warn", f"{tid}: tests hard-code port(s) {', '.join(hits)} — use the slot env",
                        task=tid, for_=["W0"], data={"ports": hits})

    def _changes(self, pid, store, tid, note):
        store.transition(tid, "changes_requested", actor="MQ", reason="merge gate failed", note=note[:4000],
                         event_emitted=True)
        self.d.emit(pid, "merge.failed", "alert", f"{tid}: merge stopped — {note.splitlines()[0][:120]}", task=tid,
                    for_=["W0"])
        return "changes"
