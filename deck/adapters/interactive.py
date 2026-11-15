"""Interactive sessions (design §7.5): no launch. The dispatcher OFFERS a task; the session runs `deck claim`.

Hooks feed heartbeats into the store through the `deck hb` CLI, so nothing here scrapes a terminal (A1).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .base import Adapter, register

OFFER_SEC = 15 * 60
HOOK_MARK = "deck hb"


def hook_commands(deck_cmd: str = "deck") -> dict:
    return {
        "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": f"{deck_cmd} hb"}]}],
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": f"{deck_cmd} hb --turn-end"}]}],
        "Notification": [{"matcher": "permission_prompt|idle_prompt",
                          "hooks": [{"type": "command", "command": f"{deck_cmd} hb --waiting-from-stdin"}]}],
        "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": f"{deck_cmd} hb --session-end"}]}],
    }


def install_claude_hooks(home: str, deck_cmd: str = "deck") -> Path:
    """Merge deck hooks into <CLAUDE_CONFIG_DIR>/settings.json. Idempotent; other hooks are kept."""
    path = Path(os.path.expanduser(home)) / "settings.json"
    try:
        settings = json.loads(path.read_text())
    except (OSError, ValueError):
        settings = {}
    hooks = settings.setdefault("hooks", {})
    for event, entries in hook_commands(deck_cmd).items():
        cur = [e for e in hooks.get(event, []) if not any(HOOK_MARK in (h.get("command") or "") for h in e.get("hooks", []))]
        hooks[event] = cur + entries
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.deck-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, path)
    return path


def install_codex_notify(home: str, deck_cmd: str = "deck") -> Path:
    """Set `notify = ["deck", "hb", "--codex-notify"]` in <CODEX_HOME>/config.toml (top-level key)."""
    path = Path(os.path.expanduser(home)) / "config.toml"
    text = path.read_text() if path.exists() else ""
    line = f'notify = ["{deck_cmd}", "hb", "--codex-notify"]'
    if re.search(r"(?m)^notify\s*=", text):
        text = re.sub(r"(?m)^notify\s*=.*$", line, text, count=1)
    else:
        # top-level keys must come before the first [table]
        m = re.search(r"(?m)^\[", text)
        text = (text[:m.start()] + line + "\n" + text[m.start():]) if m else (text.rstrip("\n") + ("\n" if text else "") + line + "\n")
    path.write_text(text)
    return path


def install_hooks(account, deck_cmd: str = "deck") -> Path:
    if account.tool == "codex":
        return install_codex_notify(account.home, deck_cmd)
    return install_claude_hooks(account.home, deck_cmd)


@register
class Interactive(Adapter):
    name = "interactive"
    binary = None

    def launch_spec(self, ctx):
        return None     # offer only (T5 → starting with offer_expires; `deck claim` = T6)
