"""`claude -p` headless adapter (design §7.3, spikes in docs/v2/B2-spikes.md)."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from .. import quota
from . import schemas
from .base import (Activity, Adapter, ApiError, Final, LaunchContext, LaunchSpec, Limit, SessionId, Tokens, Usage,
                   register)

PERMISSION_MODES = {"bypass": "bypassPermissions", "auto": "acceptEdits", "accept-edits": "acceptEdits",
                    "default": "default", "plan": "plan", None: "acceptEdits"}


def find_transcript(home: str, session_id: str):
    """Transcript of a session inside a Claude config dir (projects/<cwd-slug>/<id>.jsonl)."""
    base = Path(os.path.expanduser(home)) / "projects"
    for p in base.glob(f"*/{session_id}.jsonl"):
        return p
    return None


@register
class ClaudeHeadless(Adapter):
    name = "claude-headless"
    binary = "claude"

    def launch_spec(self, ctx: LaunchContext) -> LaunchSpec:
        handoff = json.loads(Path(ctx.handoff_schema).read_text())
        perm = PERMISSION_MODES.get(ctx.worker.permission, ctx.worker.permission or "acceptEdits")
        argv = [self.binary, "-p", "--output-format", "stream-json", "--verbose"]
        if ctx.resume_session:
            argv += ["--resume", ctx.resume_session]
        else:
            argv += ["--session-id", str(uuid.uuid4())]
        argv += ["--json-schema", schemas.dump(schemas.for_claude(handoff)),
                 "--permission-mode", perm, "--permission-prompts", "none"]
        if ctx.worker.model:
            argv += ["--model", ctx.worker.model]
        argv.append(ctx.prompt)
        env = self.base_env(ctx)
        env["CLAUDE_CONFIG_DIR"] = ctx.account.home
        if ctx.resume_session and ctx.interrupted:
            env["CLAUDE_CODE_RESUME_INTERRUPTED_TURN"] = "1"
        return LaunchSpec(argv=argv, env=env, cwd=Path(ctx.workspace))

    @staticmethod
    def prepare_cross_account_resume(from_home: str, session_id: str, run_dir: Path):
        """Spike 3: copy the other account's transcript into the run dir and resume from the copy's PATH.
        Resuming the original path would append to the other account's folder. Returns the path or None."""
        src = find_transcript(from_home, session_id)
        if not src:
            return None
        dst = Path(run_dir) / f"{session_id}.jsonl"
        shutil.copy2(src, dst)
        return str(dst)

    def parse(self, lines, now, state):
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                hit = quota.parse_limit_text(line, now)
                if hit and not state.get("limit"):
                    state["limit"] = True
                    out.append(Limit(hit[1], hit[0], hit[2]))
                continue
            t = d.get("type")
            if t == "system" and d.get("subtype") == "init" and d.get("session_id"):
                out.append(SessionId(d["session_id"]))
            elif t == "system" and d.get("subtype") == "api_retry":
                if "rate" in str(d.get("error", "")).lower():
                    state["retries"] = state.get("retries", 0) + 1
                    mx = d.get("max_retries")
                    if mx and d.get("attempt", 0) >= mx and not state.get("limit"):
                        state["limit"] = True
                        out.append(Limit(None, "rate", "api_retry rate_limit exhausted"))
                out.append(Activity("retry", f"API retry {d.get('attempt', '?')}: {d.get('error', '')}"[:120]))
            elif t == "rate_limit_event":
                info = d.get("rate_limit_info") or {}
                win = info.get("unifiedWindows") or {}
                f5, s7 = win.get("five_hour") or {}, win.get("seven_day") or {}
                if f5 or s7:
                    out.append(Usage(pct5=round(float(f5["utilization"]) * 100, 1) if "utilization" in f5 else None,
                                     pct7=round(float(s7["utilization"]) * 100, 1) if "utilization" in s7 else None,
                                     resets5=f5.get("resetsAt"), resets7=s7.get("resetsAt")))
                if info.get("status") == "rejected" and not state.get("limit"):
                    state["limit"] = True
                    kind = {"seven_day": "weekly"}.get(info.get("rateLimitType"), info.get("rateLimitType") or "five_hour")
                    out.append(Limit(int(info["resetsAt"]) if info.get("resetsAt") else None, kind,
                                     json.dumps(info)[:200]))
            elif t == "assistant":
                for c in (d.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use":
                        inp = c.get("input") or {}
                        txt = inp.get("command") or inp.get("file_path") or inp.get("description") or ""
                        out.append(Activity("tool", f"{c.get('name')}: {txt}"[:160]))
                    elif c.get("type") == "text" and c.get("text"):
                        out.append(Activity("message", c["text"][:160]))
                usage = (d.get("message") or {}).get("usage") or {}
                if usage:
                    state["tokens"] = state.get("tokens", 0) + int(usage.get("output_tokens") or 0)
            elif t == "result":
                text = d.get("result") if isinstance(d.get("result"), str) else ""
                is_err = bool(d.get("is_error"))
                if (d.get("api_error") == "usage_limit_reached" or d.get("api_error_status") == 429) and not state.get("limit"):
                    hit = quota.parse_limit_text(text, now)
                    state["limit"] = True
                    out.append(Limit(hit[1] if hit else None, hit[0] if hit else "usage", (text or "429")[:200]))
                elif is_err and not state.get("limit"):
                    hit = quota.parse_limit_text(text, now)
                    if hit:
                        state["limit"] = True
                        out.append(Limit(hit[1], hit[0], hit[2]))
                    elif d.get("api_error_type") or d.get("api_error"):
                        out.append(ApiError(str(d.get("api_error_type") or d.get("api_error")), (text or "")[:200]))
                handoff = d.get("structured_output")
                if handoff is None and text and not is_err:
                    try:
                        maybe = json.loads(text)
                        handoff = maybe if isinstance(maybe, dict) and "outcome" in maybe else None
                    except ValueError:
                        handoff = None
                u = d.get("usage") or {}
                total = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens"))
                if total:
                    out.append(Tokens(total))
                out.append(Final(handoff, text or "", is_err))
        return out

    def usage(self, account, now):
        return quota.read_account(account, now)
