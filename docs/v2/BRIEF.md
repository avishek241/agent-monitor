# agent-monitor v2 — "Agent Deck": orchestrator + task manager + live deck

Owner request (2026-11-15): make the best orchestrator/task manager for running several AI coding agents
(Claude Code headless, Codex CLI, interactive sessions) on one Mac. Study other solutions first, then design a
completely new mechanism so today's mistakes stop happening, tasks are created properly, handoffs are clean, and
the floating window + a phone deck always show tasks, subtasks and what matters. Project-agnostic (config-driven);
Bhumi is the first user. Public MIT repo — no private/project data in git.

## Mistakes observed in one real day (design must make each impossible or loud)
1. Worker finished but orchestrator wasn't told (launched with `&`/detached) → workers idle 30 min.
2. A finished pilot (051) was never reviewed for 6 h — no review queue.
3. Cards with wrong keys (`name` vs `title`, metrics as dict) → panel showed nothing; stale cards; cards restarted from 0.
4. Limit misread: "resets 8:50pm" re-read after resume → worker shown 100% for 24 h; Codex readings stale.
5. Wrong brief path passed to a runner; brief missing Done-when; workers asking for rules mid-task.
6. Parallel workers generated the same migration number; merge conflicts; a runner's `merge --abort` aborting the orchestrator's merge.
7. Tests hard-coded to one worker's DB/port → release gate blocked 4 times.
8. Hung jobs (stdin wait, stuck external request) noticed only by luck; orphan servers left running.
9. A worker hit its 5-hour limit mid-task; resume depended on the orchestrator remembering.
10. Orchestrator time spent on bookkeeping (board rows, titles.json, runner scripts) instead of review.

## Phases (stop for owner/W0 review after each)
**P0 Research** — study and compare (features, task model, isolation, handoff, UI): Claude Squad, Vibe Kanban, Crystal,
CCManager, Conductor, Sculptor, Claude Code subagents/agent teams & background tasks, OpenAI Codex cloud tasks,
GitHub Copilot coding agent, Devin, OpenHands, Task Master AI, Backlog.md, Linear/Jira agent integrations, plus
workflow engines' ideas (DAGs, retries, leases: Temporal/Airflow/GitHub Actions). Public docs/repos only.
Output `docs/v2/research.md`: comparison table, what to copy, what to avoid, mapped to mistakes 1–10.

**P1 Design** — `docs/v2/design.md`:
- **Task store** (one SQLite file or JSONL, local): task = id, title, plain summary, brief path, tags, priority, size,
  deps, required capability (code/data/ui/infra/browser), worker class, state machine
  `draft → ready → queued → running → (blocked|limit-paused|hung) → review → done|changes-requested`, subtasks/steps,
  metrics, attempts, artifacts, handoff. Brief validator (must have goal, rules, done-when, stop point) before `ready`.
- **Dispatcher daemon**: leases ready tasks to idle workers by capability + remaining quota (reads real usage),
  launches via adapters (claude headless, codex exec, interactive notify), always attached & watched; heartbeat/hang
  detection; auto-resume after limit reset; one-at-a-time locks for shared resources (DB migrations numbering,
  merge lock, external systems like a Tally/PC); kills orphan servers it started.
- **Handoff contract**: structured `handoff.json` + md (what changed, tests, risks, decisions needed); review inbox
  for the orchestrator with approve / request changes / merge; merge queue serialised.
- **Card protocol v2** with schema validation and a tiny SDK (`deck card ...`), append-only history (no resets).
- **Events/notifications**: event log; push to the orchestrator session (stdout/notify file) and to the phone.
- **UI**: (a) floating macOS panel redesigned; (b) PWA "deck" for phone/tablet over Tailscale, in the style of the
  owner's `~/Documents/Personal/phonedeck` (installable, big tappable tiles). Views: Workers (quota bars, current task,
  step progress, ETA), Board (ready/running/review/blocked with deps), Review inbox, Timeline, Alerts (idle worker,
  hung, stale, limit), Repo health. Actions: approve review, pause/resume, reassign, bump priority.
**P2 Build** core (store, dispatcher, adapters, validator, CLI) with tests. **P3 Build** UI (panel + PWA).
**P4 Pilot** on Bhumi alongside the current scripts, then switch over.

Rules: keep v1 working until v2 is proven; no network calls except to the local daemon (and optional Tailscale);
no tokens spent by the monitor itself; never store secrets; tests for the dispatcher state machine.

## Owner additions (2026-11-15 22:20)
- **Multi-project:** an **Overview** page across ALL projects the owner works on (each project = its own config/task store), plus a **dedicated page per project** (workers, board, review inbox, alerts, repo/health for that project). Add/remove projects by config; the phone deck and the floating panel both get the overview + per-project navigation.
- **All accounts, including the professional (office) Claude account**, appear in the accounts view with real usage/limits.
- **Account policy — hard rule enforced by the dispatcher, not just shown:** each account is tagged `office` or `personal`; each project is tagged `office` or `personal`.
  - Personal accounts MAY run office projects (if the owner allows per project).
  - The **office account must NEVER run personal-project tasks** — the dispatcher refuses the lease, the CLI refuses manual assignment, and the UI greys it out with the reason. Violations are impossible by construction, and any attempt is logged as an alert.
  - Account/project tags live in the user's private config (never in git).
