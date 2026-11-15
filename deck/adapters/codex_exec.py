"""`codex exec` adapter (design §7.4, spikes in docs/v2/B2-spikes.md)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import quota
from . import schemas
from .base import Activity, Adapter, ApiError, Final, LaunchContext, LaunchSpec, Limit, SessionId, Tokens, register

LIMIT_MSG = re.compile(r"usage limit|rate limit|try again (at|in)", re.I)
TRY_AGAIN_AT = re.compile(r"try again at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", re.I)
TRY_AGAIN_IN = re.compile(r"try again in\s+(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?", re.I)


def _limit_from_message(msg: str, now: int):
    m = TRY_AGAIN_AT.search(msg)
    if m:
        return quota.clock_time_to_epoch(int(m.group(1)), int(m.group(2) or 0), m.group(3), now)
    m = TRY_AGAIN_IN.search(msg)
    if m and (m.group(1) or m.group(2)):
        return now + int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60
    return None


@register
class CodexExec(Adapter):
    name = "codex-exec"
    binary = "codex"

    def launch_spec(self, ctx: LaunchContext) -> LaunchSpec:
        run = Path(ctx.run_dir)
        handoff = json.loads(Path(ctx.handoff_schema).read_text())
        (run / "handoff.schema.json").write_text(schemas.dump(schemas.for_codex(handoff)))
        common = ["--json", "--output-schema", str(run / "handoff.schema.json"), "-o", str(run / "last-message.json")]
        bypass = ctx.worker.permission == "bypass"
        if ctx.resume_session:
            # spike 2: `resume` rejects -s and -C → sandbox via -c, cwd via the process
            argv = [self.binary, "exec", "resume", ctx.resume_session] + common
            if bypass:
                argv.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                argv += ["-c", 'sandbox_mode="workspace-write"', "-c", "sandbox_workspace_write.network_access=true"]
        else:
            argv = [self.binary, "exec"] + common + ["-C", str(ctx.workspace)]
            if bypass:
                argv.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                argv += ["-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true"]
        argv.append("--skip-git-repo-check")
        if ctx.worker.model:
            argv += ["-m", ctx.worker.model]
        argv.append(ctx.prompt)
        env = self.base_env(ctx)
        env["CODEX_HOME"] = ctx.account.home
        return LaunchSpec(argv=argv, env=env, cwd=Path(ctx.workspace))

    def parse(self, lines, now, state):
        out = []
        for line in lines:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            if t == "thread.started" and d.get("thread_id"):
                out.append(SessionId(d["thread_id"]))
            elif t in ("item.started", "item.completed"):
                it = d.get("item") or {}
                kind = it.get("type") or it.get("item_type") or "item"
                txt = it.get("command") or it.get("text") or ",".join(c.get("path", "") for c in it.get("changes") or [])
                out.append(Activity(kind, str(txt)[:160]))
            elif t == "turn.completed":
                u = d.get("usage") or {}
                state["tokens"] = state.get("tokens", 0) + int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
                out.append(Tokens(state["tokens"]))
            elif t in ("turn.failed", "error"):
                msg = d.get("message") or (d.get("error") or {}).get("message") or ""
                if LIMIT_MSG.search(msg) and not state.get("limit"):
                    state["limit"] = True
                    # resets_at: the dispatcher prefers the newest rollout rate_limits of the account (absolute);
                    # this message is parsed ONCE as a fallback.
                    out.append(Limit(_limit_from_message(msg, now), "usage", msg[:200]))
                elif t == "turn.failed":
                    out.append(ApiError("turn_failed", msg[:200]))
        return out

    def final(self, run_dir, state):
        p = Path(run_dir) / "last-message.json"
        if not p.exists():
            return None
        text = p.read_text()
        try:
            h = json.loads(text)
            h = schemas.strip_nulls(h) if isinstance(h, dict) else None
        except ValueError:
            h = None
        return Final(h, text[:2000], False)

    def usage(self, account, now):
        return quota.read_account(account, now)
