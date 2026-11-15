"""Task workspaces and slot environments (design §6.6, §6.8; M7).

A slot is a small integer; ports and DB names are derived from it through the project's `slots.env` templates, so
no test can rely on one worker's port or database.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def port_base(project, slot: int) -> int:
    tpl = str((getattr(project, "slots", None) or {}).get("portBase", "4{slot}1"))
    return int(tpl.format(slot=slot))


def port_range(project, slot: int) -> range:
    b = port_base(project, slot) * 10
    return range(b, b + 10)


def slot_env(project, slot, task_id: str) -> dict:
    if slot is None:
        return {}
    slots = getattr(project, "slots", None) or {}
    pb = port_base(project, slot)
    env = {"DECK_SLOT": str(slot), "DECK_PORT_BASE": f"{pb}0"}
    for i in range(10):
        env[f"DECK_PORT_{i}"] = f"{pb}{i}"
    for k, tpl in (slots.get("env") or {}).items():
        env[k] = str(tpl).format(slot=slot, portBase=pb, task=task_id.replace("-", "_"))
    return env


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check,
                          stdin=subprocess.DEVNULL, timeout=120)


def ensure(project, task: dict) -> dict:
    """Create (or reuse) the task worktree on branch task/<id>. Returns task.workspace fields.

    isolation 'none' / 'main-checkout' run in the repo itself."""
    iso = task.get("isolation") or "worktree"
    repo = Path(os.path.expanduser(project.repo))
    if iso != "worktree":
        return {"path": str(repo), "branch": project.mainBranch, "base": project.mainBranch}
    ws = task.get("workspace") or {}
    if ws.get("path") and Path(ws["path"]).is_dir():
        return {k: ws[k] for k in ("path", "branch", "base") if k in ws}
    wdir = Path(os.path.expanduser(project.worktreesDir or f"{repo}-worktrees"))
    wdir.mkdir(parents=True, exist_ok=True)
    path = wdir / task["id"]
    branch = f"task/{task['id']}"
    base = (task.get("merge") or {}).get("target") or project.mainBranch
    if path.is_dir():
        return {"path": str(path), "branch": branch, "base": base}
    exists = _git(repo, "rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0
    if exists:
        _git(repo, "worktree", "add", str(path), branch)
    else:
        _git(repo, "worktree", "add", "-b", branch, str(path), base)
    for inc in getattr(project, "worktreeInclude", None) or []:
        src, dst = repo / inc, path / inc
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(src, dst)
    for cmd in getattr(project, "setup", None) or []:
        subprocess.run(cmd, shell=True, cwd=str(path), stdin=subprocess.DEVNULL, capture_output=True, timeout=1800)
    return {"path": str(path), "branch": branch, "base": base}
