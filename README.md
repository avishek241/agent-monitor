# agent-monitor

A floating, always-on-top macOS panel that shows what your AI agents and background jobs are doing — across any project. It reads local files only (task cards, runner logs, Codex session logs, Claude Code status lines) plus optional localhost health checks: **no network calls, no AI, no tokens**.

It was built while orchestrating one Claude Code orchestrator (W0) and several Codex/Claude workers (W1–W3) on a real project, and is project-agnostic: everything project-specific lives in one JSON config.

## What it shows
- **Accounts** — 5-hour / 7-day usage per Claude Code and Codex account, reset countdown, "LIMIT" when a headless worker reports it, the age of each reading (never a fake 0%).
- **In progress** — one card per task: plain-English title + one-line summary, worker badge (W0, W1…), tags, steps with per-step and whole-task ETA, **progress numbers** (e.g. "Documents profiled 620 / 3,657 · ≈42/min · ~1.2 h left"), last activity, stale warning after 30 min without an update; raw shell activity is collapsed.
- **Up next** — queued tasks (waiting on a condition) and blocked ones (waiting on a person).
- **Recently finished** — from runner logs and done cards, by any agent.
- **Health** — your app/API URLs (green/red), grouped.
- **Repository** — last commit and uncommitted changes (uses `git --no-optional-locks`, so it never blocks your agents' commits).

**Themes**: three layouts, each in light and dark, taken from the v2 mockups. *Studio* is PhoneDeck's hardware look, with a warm-grey body, dark LCD blocks and chunky ink-bordered keys. *Mission Control* is dense and data-first: monospace numerals, LEDs, thin rules, a KPI grid, sparklines, a live ticker and key hints. *Paper* is editorial: serif headlines, a one-paragraph summary, soft cards, pastel pills and quota rings. Pick the layout and Light, Dark or Auto (Auto follows macOS) in the panel header. **⌘T** cycles layouts and **⌘⇧T** flips light and dark while the panel is focused. Both choices are remembered. Colours, fonts and spacing are tokens in the `Theme` struct in `Panel.swift`, and every theme meets WCAG AA contrast. Fonts are bundled in `fonts/` (SIL OFL) and registered for the panel process only.

## Install / run
```bash
git clone <this repo> ~/agent-monitor            # or copy the folder
mkdir -p ~/.config/agent-monitor
cp examples/minimal.config.json ~/.config/agent-monitor/config.json   # then edit it
./start.sh                                       # or ./start.sh path/to/other-config.json
```
Requirements: macOS 13+, Xcode command-line tools (`swiftc`), Python 3.9+. `start.sh` compiles `Panel.swift` to `~/.local/bin/agent-monitor-panel` when it changed and starts it; close the window to stop. The panel keeps **one** long-lived `status.py --serve` process (≈15 ms CPU per 2-s tick) and restarts it if it dies.

## Config reference (`examples/full.config.json` shows every key)
| Key | Meaning |
|---|---|
| `title` | Window title |
| `progressDir` | Folder of task cards (`<task-id>.json`), see below |
| `titlesFile` | Optional `{ "037": {"title": "…", "summary": "…"} }` plain names by task number (cards' own `title`/`summary` win) |
| `repo`, `worktreesDir` | Git repo for "Repository" + auto-metrics (commits on `task/<name>`, files changed, handoff written) |
| `runnerLogDirGlobs` | Folders with runner logs `<task>.log` ("finished with exit N", "hit its limit") |
| `claudeTaskOutputGlobs` | Claude Code background-agent transcripts (`*.output`) to show as Claude agents |
| `codexAccounts[]` | `label`, `home` (CODEX_HOME), `runnerAlias` (name used in runner logs), `short` (badge) |
| `codexTaskPattern` | Regex (group 1 = brief path) to find the task in a running `codex exec` command line |
| `claudeAccounts[]` | `label`, `statusFile` (written by `bin/claude-statusline.sh`), optional `limitLogGlobs` (headless logs with "You've hit your 5-hour limit · resets 3:50pm") |
| `headlessTaskPattern`, `headlessClaudeLabel` | Detect `claude -p "… <task> …"` workers that haven't written a card yet |
| `workers` | `[["Claude-A","W0"], …]` prefixes shown on badges and account rows |
| `health[]` | `label`, `url`, optional `group` (one row per group) |
| `usageCacheFile` | Where usage parsing is cached (20 s) |
| `theme` | Optional default look, e.g. `"studio"`, `"mission-dark"`, `"paper-auto"` (your last choice in the panel wins) |

Relative paths are resolved against the config file's folder; `~` is expanded.

## Task card format (`<progressDir>/<task-id>.json`)
Any agent, script or human can write these (atomically: write `x.tmp`, then rename). `bin/card.py` does it for you.
```json
{
  "source": "interactive", "active": true, "status": "in-progress",
  "account": "W1 Codex-Rahul", "startedAt": 1794700000,
  "title": "Plain-English name", "summary": "One line: what and why", "tags": ["data", "P1"],
  "steps": [{"title": "Read sources", "status": "done|active|pending", "etaMin": 15, "startedAt": 1794700000}],
  "metrics": [{"label": "Files processed", "done": 620, "total": 3657}, {"label": "Errors", "done": 3, "kind": "bad"}],
  "note": "current activity"
}
```
- `status: "queued"` (with `waitingFor`) or `"blocked"` and `active: false` → shown under **Up next**.
- `status: "done"`, `active: false` → shown under **Recently finished** for 12 h.
- Task ids starting with a number (`037-data-understanding`) show that number as the card badge.
- **Continuation runs must keep existing done steps and metrics** — append, never restart a card from zero.

Prompt snippet for agents: *"Keep `<progressDir>/<task>.json` updated: title, summary, tags, steps with etaMin and startedAt, metrics with real done/total counts updated every few minutes; on a continuation keep existing steps and append yours."*

## Claude Code usage on the panel
Add to each account's `settings.json`:
```json
"statusLine": {"type": "command", "command": "~/agent-monitor/bin/claude-statusline.sh /tmp/claude-status-<account>.json"}
```
and use the same path as that account's `statusFile`.

## Files
`Panel.swift` (SwiftUI panel) · `fonts/` · `status.py` (data, `--serve` streaming mode, `--config`) · `start.sh` · `bin/card.py` · `bin/claude-statusline.sh` · `examples/*.config.json`.

## Licence
MIT — see [LICENSE](LICENSE).
