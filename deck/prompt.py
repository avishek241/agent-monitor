"""The worker prompt (design §7.2): identical for every adapter. Only the brief SNAPSHOT path is ever passed (M5)."""
from __future__ import annotations

import subprocess
from typing import Optional

REASONS = {"limit": "limit", "hung": "hang", "crashed": "crash", "timeout": "crash", "handoff": "review changes",
           "reassigned": "reassigned", "cancelled": "paused", "blocked": "question answered"}


def _git_log(workspace, target, branch, limit=20):
    try:
        out = subprocess.run(["git", "-C", str(workspace), "log", "--oneline", f"{target}..{branch}"],
                             capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        return [ln for ln in out.stdout.splitlines() if ln.strip()][:limit]
    except (OSError, subprocess.SubprocessError):
        return []


def continuation_reason(task: dict, attempt: Optional[int] = None) -> Optional[str]:
    atts = [a for a in task.get("attempts") or [] if attempt is None or (a.get("n") or 0) < attempt]
    if not atts:
        return None
    last = atts[-1]
    if task.get("reassignedFrom"):
        return "reassigned"
    if (task.get("review") or {}).get("decision") == "changes":
        return "review changes"
    return REASONS.get(last.get("outcome") or "", last.get("outcome") or "retry")


def build(project, task: dict, worker, workspace, slot_env: dict, attempt: int, card: Optional[dict] = None,
          last_handoff: Optional[dict] = None, commits: Optional[list] = None, answers: Optional[list] = None) -> str:
    rules = ", ".join(getattr(project, "rulesFiles", None) or ["AGENTS.md"])
    brief = (task.get("brief") or {}).get("snapshot") or (task.get("brief") or {}).get("path") or "(missing brief)"
    branch = (task.get("workspace") or {}).get("branch") or f"task/{task['id']}"
    names = [f"${k}" for k in sorted(slot_env or {}) if not k.startswith("DECK_PORT_")]
    if any(k.startswith("DECK_PORT_") for k in slot_env or {}):
        names.insert(0, "$DECK_PORT_0…$DECK_PORT_9")
    env_names = ", ".join(names) or "(none configured)"
    lines = [
        f'You are {worker.id} working on project "{getattr(project, "title", "") or project.id}", task {task["id"]}: {task.get("title", "")}.',
        f"1. Read and follow {rules} (binding project rules).",
        f'2. Your brief is {brief}. Use ONLY this path. Its "Done when" and "Stop point" are binding.',
        f"3. Workspace: {workspace} on branch {branch}. Commit there in small commits. Never switch branches, merge, rebase main, or push.",
        f"4. Use the env vars {env_names} for any server/test DB; never hard-code ports or DB names; stop every server you start.",
        "5. Progress: run `deck card step add|start|done`, `deck card metric`, `deck card note` (already configured; no paths needed). Steps need etaMin (--eta).",
        '6. If you need a decision you cannot make reversibly: `deck ask "<one question>"`, then finish with outcome "blocked".',
        "7. Stop at the Stop point. Your FINAL message must be the handoff JSON (schema enforced). "
        "If structured output is unavailable, run `deck handoff submit -` with it instead.",
    ]
    if attempt > 1:
        reason = continuation_reason(task, attempt) or "retry"
        steps = [s.get("title", s.get("id")) for s in (card or {}).get("steps", []) if s.get("status") == "done"]
        note = (card or {}).get("note") or "-"
        nxt = (last_handoff or {}).get("nextStep") or "-"
        rnote = (task.get("review") or {}).get("note") or "-"
        if commits is None:
            target = (task.get("merge") or {}).get("target") or getattr(project, "mainBranch", "main")
            commits = _git_log(workspace, target, branch)
        lines += [
            f"CONTINUATION of attempt {attempt - 1} ({reason}).",
            f"Done steps so far: {', '.join(steps) or 'none'}. Last note: {note}. Next step from previous handoff: {nxt}.",
            f"Reviewer note: {rnote}. Commits so far: {'; '.join(commits) if commits else 'none'}.",
        ]
        if answers:
            lines.append("Answers to your questions: " + " | ".join(str(a) for a in answers))
        lines.append("Do not redo finished steps; keep the card (append steps, never reset).")
    return "\n".join(lines) + "\n"
