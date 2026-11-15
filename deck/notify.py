"""Event delivery that B2 owns (design §10 items 4–5): macOS notification + optional notifyCommand.

Only `action`/`alert` events, rate-limited to one per (type, task) per 5 min. No network, no tokens.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from . import clock

RATE_SEC = 300


class Notifier:
    def __init__(self, cfg, runner=None, macos=True):
        self.cfg = cfg
        self.runner = runner or self._run
        self.macos = macos and shutil.which("osascript") is not None
        self.last = {}
        self.sent = []

    @staticmethod
    def _run(argv, stdin=None):
        try:
            subprocess.run(argv, input=stdin, text=True, capture_output=True, timeout=10, shell=isinstance(argv, str))
        except (OSError, subprocess.SubprocessError):
            pass

    def deliver(self, ev: dict) -> bool:
        if ev.get("severity") not in ("action", "alert"):
            return False
        key = (ev.get("type"), ev.get("project"), ev.get("task"))
        now = clock.now()
        if now - self.last.get(key, -10 ** 9) < RATE_SEC:
            return False
        self.last[key] = now
        title = f"Agent Deck · {ev.get('project') or 'machine'}"
        text = str(ev.get("text", ""))[:200]
        if self.macos:
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            self.runner(["osascript", "-e", f'display notification "{esc(text)}" with title "{esc(title)}"'])
        cmd = getattr(self.cfg, "notify_command", None)
        if cmd:
            self.runner(cmd, json.dumps(ev))
        self.sent.append(ev)
        return True
