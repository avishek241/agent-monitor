# B2 verification spikes (2026-11-15)

Versions: Claude Code 2.1.325, codex-cli 0.169.0. Personal accounts only (`CLAUDE_CONFIG_DIR=~/.claude-personal`,
`~/.claude-veggie`; `CODEX_HOME=~/.codex-veggie`). Prompts were tiny ("reply with a handoff object, no tools") in an
empty scratch git repo. No business data was used.

## Spike 1: does Claude's stream-json `result` line carry the handoff JSON? **Yes (with one schema fix).**

```
claude -p --output-format stream-json --verbose --session-id <uuid> \
  --json-schema "<schema>" --permission-mode default --permission-prompts none "<prompt>" </dev/null
```

- **The raw `handoff.schema.json` is rejected**: `Error: --json-schema is not a valid JSON Schema: no schema with key or
  ref "https://json-schema.org/draft/2020-12/schema"` (exit 1, before any API call). Dropping `$schema`/`$id` fixes it.
  `deck/adapters/schemas.py::for_claude()` strips them, and also the daemon-only `readOnly` fields (`task`, `attempt`,
  `git`, `run`), which the worker must not fill in.
- With the cleaned schema, the final `{"type":"result",...}` line has `structured_output` holding the parsed object;
  `result` holds the same JSON as text. Claude gets it through a synthetic `StructuredOutput` tool call
  (`stop_reason: tool_use`, `num_turns: 3`, `terminal_reason: completed`). The adapter reads `structured_output`
  first and falls back to parsing `result` as JSON.
- **Bonus: limits come as data, not text.** Every run emits a `rate_limit_event` line:
  `{"type":"rate_limit_event","rate_limit_info":{"status":"allowed|rejected","resetsAt":<epoch>,"rateLimitType":"five_hour",
  "unifiedWindows":{"five_hour":{"utilization":0.25,"resetsAt":<epoch>},"seven_day":{"utilization":0.19,"resetsAt":<epoch>}}}}`.
  When the account is over its limit, `status:"rejected"` arrives first, then a synthetic assistant message and a
  `result` with `is_error:true, api_error_status:429, api_error:"usage_limit_reached"` and the text
  "You've hit your 5-hour limit · resets 1:50am (Asia/Calcutta)". So the Claude adapter takes the **absolute**
  `resetsAt` from `rate_limit_event` (M4 can't recur); the text regex is only a fallback. `allowed` events also give a
  free, fresh usage reading (`utilization` × 100) for the quota model.
- `system/init` carries `session_id` (equal to the `--session-id` we passed).

## Spike 2: which flags does `codex exec resume` accept? **`--json`, `--output-schema`, `-o`, `-c`, `-m`; NOT `-s`, `-C`.**

- `codex exec resume --help` and real runs: accepted `--json`, `--output-schema FILE`, `-o/--output-last-message FILE`,
  `-c key=value`, `-m`, `--skip-git-repo-check`, `--dangerously-bypass-approvals-and-sandbox`, `--ephemeral`, `--last`.
- Rejected: `-s/--sandbox` and `-C/--cd` (`error: unexpected argument '-s' found`). So the resume launch sets the
  sandbox with `-c sandbox_mode="workspace-write"` (+ `-c sandbox_workspace_write.network_access=true`) and sets the
  working directory through the process `cwd` (LaunchSpec.cwd), not `-C`.
- Resume kept the same `thread_id` (`thread.started` repeats it) and returned the structured last message in `-o`.
- **`--output-schema` needs OpenAI strict mode.** The raw schema fails at the API:
  `Invalid schema for response_format 'codex_output_schema': 'schema.required' is required to include every key in
  properties. Missing 'schema'.` (`turn.failed`, exit 1). `schemas.for_codex()` makes every property required, turns
  optional ones nullable and sets `additionalProperties:false`; Codex then returns `null` for unused optional fields,
  which `schemas.strip_nulls()` removes before validation.
- Rollout files carry `payload.rate_limits.primary/secondary.{used_percent,window_minutes,resets_at}` (absolute epochs),
  as v1 assumed. The `--json` stream itself carries no rate-limit data; `turn.completed.usage` has token counts.
- Noise: Codex logs `ERROR ... failed to load skill` lines on stderr for skills it scans; harmless, ignored by the parser.

## Spike 3: can a Claude session be resumed across config folders? **Yes, by transcript path; not by id.**

- `CLAUDE_CONFIG_DIR=~/.claude-veggie claude -p --resume <session id from ~/.claude-personal>` →
  `No conversation found with session ID: …` (exit 1).
- `--resume /abs/path/<session>.jsonl` (the transcript inside the other config dir) **works**: same `session_id`, and
  the model quoted text from the earlier turn.
- **Catch**: the continuation is appended to the transcript **next to the given path**, here inside the other
  account's folder (`~/.claude-personal/projects/…`), not in the resuming account's folder. So the adapter must copy the
  transcript into the run dir first (`runs/…/<n>/<session>.jsonl`) and resume from the copy, exactly as the design says.
  Verified: resuming a copy wrote `<run>/<session_id>.jsonl` beside it and left both config folders untouched.
- Later resumes on the new account must keep using the path (the new account's own folder doesn't know the id), so
  the adapter stores `resume_path` on the attempt.

## What changed in B2 because of the spikes
1. `claude_headless` passes `schemas.for_claude(handoff)`; `codex_exec` writes `schemas.for_codex(handoff)` to
   `<run>/handoff.schema.json` and `strip_nulls()` the result.
2. Claude limit detection: `rate_limit_event.status == "rejected"` → `Limit(resets_at=resetsAt)` (absolute), plus
   `UsageReading` from `allowed` events. The regex on the `result` text stays as a fallback only.
3. Codex resume argv: `codex exec resume <thread> --json --output-schema F -o F -c sandbox_mode=… [-m M] PROMPT`, cwd =
   workspace.
4. Cross-account Claude failover: copy the transcript into the run dir and `--resume <copy path>`; the continuation
   prompt is still sent as the new user turn.
