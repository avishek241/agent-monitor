"""`shell` adapter (design §7.6): data jobs, scripts, SSH jobs. argv comes from `task.command`."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from .base import Activity, Adapter, Final, LaunchSpec, register


@register
class Shell(Adapter):
    name = "shell"
    binary = None

    def launch_spec(self, ctx):
        cmd = ctx.task.get("command")
        if not cmd:
            raise ValueError(f"task {ctx.task['id']} has workerClass shell but no command")
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        env = self.base_env(ctx)
        env["DECK_PROMPT_FILE"] = str(Path(ctx.run_dir) / "prompt.md")
        return LaunchSpec(argv=argv, env=env, cwd=Path(ctx.workspace))

    def parse(self, lines, now, state):
        return [Activity("output", ln.strip()[:160]) for ln in lines if ln.strip()]

    def final(self, run_dir, state):
        """Synthetic handoff from the exit code unless the script ran `deck handoff submit`."""
        try:
            ex = json.loads((Path(run_dir) / "exit.json").read_text())
        except (OSError, ValueError):
            return None
        code = ex.get("code")
        if code == 0:
            h = {"outcome": "partial", "summary": "Shell job exited 0; reviewer confirms the result.",
                 "doneWhen": [], "tests": [], "risks": [], "decisionsNeeded": []}
            return Final(h, "exit 0", False)
        return Final(None, f"exit {code}", True)
