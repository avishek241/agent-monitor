# Agent Deck v2: P0 research

Date: 2026-11-15. Sources: public docs, READMEs and issue trackers only (links inline and in the [Sources](#sources) list). "n/d" means the public docs do not document the point. It does not mean the feature is missing.

The context is one Mac running one orchestrator (W0) and three workers (Claude Code headless, two Codex CLI accounts) on a real project. In one day we saw ten failure modes (BRIEF.md, mistakes 1–10). This document asks what existing tools do about each of them.

## 1. Headline findings

1. **None of the local session managers knows about quota.** Claude Squad, Vibe Kanban, Crystal, CCManager, Conductor and Sculptor say nothing about 5-hour or weekly limits. Devin has per-session ACU limits and Codex cloud shares the plan allowance, but neither schedules around them. Mistakes 4 and 9 are ours to solve.
2. **Scraping the terminal is the weakest status signal around.** Claude Squad hashes the tmux pane and matches hard-coded prompt strings. CCManager's own docs call its regex detection "especially buggy" for Codex and Gemini. A Claude Squad issue (#266) reports that prompts get lost because they are sent before the agent is ready. Structured signals are better: `claude -p --output-format stream-json`, `codex exec --json`, exit codes, and Claude Code hooks (`Stop`, `Notification` with `idle_prompt`/`permission_prompt`, `SessionEnd`).
3. **Every serious tool isolates a task in a worktree or container.** Most also give each workspace its own port (Conductor's `CONDUCTOR_PORT`, OpenHands allocates file-locked port ranges) and copy ignored files into new worktrees (CCManager's `.worktreeinclude`). Our mistakes 6, 7 and 8 come from shared ports, DB names and merge state.
4. **Board columns are derived from events, never dragged.** Vibe Kanban moves a task from To do to In Progress to In Review to Done on real events (attempt started, attempt finished, PR merged), and a manual drag does nothing. Linear derives agent-session state (`pending/active/awaitingInput/error/complete/stale`) from the agent's last activity.
5. **The review loop belongs to the same session.** Conductor, Vibe Kanban, Claude Code on the web and Copilot (`@copilot` on the PR) all send review comments back to the agent that did the work. None of them lets the agent approve or merge its own work.
6. **Workflow engines already solved leases, hangs and retries.** Temporal has heartbeat timeouts, start-to-close and schedule-to-close timeouts, and retry with backoff. Airflow has zombie detection, pools and a `deferred` state that frees a slot. SQS visibility timeouts are leases. GitHub Actions has concurrency groups and `needs`. A single SQLite file with `BEGIN IMMEDIATE` can implement every one of these for four workers.
7. **Briefs and handoffs need structure.** Devin playbooks have Outcome, Procedure, Specifications, Advice, Forbidden actions and Required from user. Backlog.md has Acceptance Criteria plus a Definition of Done with the rule "never Done without every item checked". `claude -p --json-schema` and `codex exec --output-schema` can force the final message to match a handoff schema, so the handoff no longer depends on the worker remembering to write one.

## 2. Comparison table

| Tool | Task model / states | Isolation | Knows done / waiting via | Handoff / review | UI | Notify | Quota |
|---|---|---|---|---|---|---|---|
| **Claude Squad** | session = task; only "paused" named | tmux + worktree per session | tmux pane hash + prompt-string match | diff tab; commit + push | TUI | n/d | n/d |
| **Vibe Kanban** (sunsetting) | task → attempts; To do / In Progress / In Review / Done, automatic | own branch + dev server per workspace | "attempt completes" (mechanism n/d); PR state polled every 60 s | diff + inline comments to the agent; PR | local web | n/d | n/d |
| **Crystal** (deprecated Feb 2026) | resumable sessions | worktrees | n/d | diff, rebase from/to main with git preview | desktop | desktop alert when input needed | n/d |
| **CCManager** | session per worktree: busy / waiting / idle | worktree, `.worktreeinclude`, optional devcontainer | per-agent detection strategy (regex on terminal) | create / merge / delete worktree | TUI | **status-change hooks run a command** | n/d |
| **Conductor** | workspace = delegation, branch/PR = integration; archive/restore | worktree from fresh origin/main, `CONDUCTOR_PORT`, setup scripts, `.context/` notes | n/d (closed source) | diff with comments → agent; Checks tab; agent resolves conflicts | Mac app | n/d | n/d |
| **Sculptor** | workspace + agents; sessions reopenable | Docker container per agent | n/d | "pairing mode" syncs into local checkout; keep/drop per agent | desktop | n/d | n/d |
| **Claude Code subagents / background** | subagent with `isolation: worktree`, `background`, `maxTurns` | worktree, auto-removed if unchanged | completion notification to parent; hooks `Stop`/`SubagentStop` | result message; resumable by id | in-session | hooks | shared limits |
| **Claude Code agent teams** (experimental) | shared task list: pending / in progress / completed, deps → blocked | separate sessions (no worktree by default) | idle teammate notifies lead; `TeammateIdle`, `TaskCompleted` hooks (exit 2 blocks) | lead reviews; plan approval | in-session | mailbox JSON files | shared limits |
| **Codex cloud** | chat per task, best-of-N attempts | container per task; setup script with network, agent offline by default | turn complete; CLI `notify` on `agent-turn-complete` | answer + diff → PR or follow-up | web / CLI | notify hook | plan allowance |
| **Copilot coding (cloud) agent** | issue → draft PR → review request | Actions runner; may push only `copilot/*` | PR review request | `@copilot` comments → more commits; human merges | GitHub | review request | monthly allowance |
| **Devin** | queued / working / blocked / paused / finished… ; sleeps when idle, woken by message, PR comment or CI | VM | status polling; coordinator woken by child | PR + messages; playbooks | web / API | n/d | ACU per session |
| **OpenHands** | event stream of Actions → Observations | Docker sandbox, versioned runtime images, file-locked port ranges | event stream; headless `--json` | resolver: label → draft PR or branch + issue comment | web / CLI | n/d | n/d |
| **Task Master AI** | `tasks.json`: id, title, description, status, dependencies, priority, details, testStrategy, subtasks; `next` = deps satisfied, then priority, dep count, id | — | — | status `review` | CLI / MCP | — | — |
| **Backlog.md** | one `.md` per task; AC + Definition of Done checklists; Final Summary | — | — | "never Done without every item checked" | CLI / web / MCP | — | — |
| **Linear agents** | AgentSession: pending / active / awaitingInput / error / complete / stale, derived from activities | — | activities: thought / action / elicitation / response / error | human in the issue | Linear | webhooks (5 s ack, 10 s first activity) | — |
| **Jira / Rovo agents** | assign, @mention, transition rule, or board column triggers the agent | — | — | private draft → human turns it into a comment | Jira | — | credits |
| **Temporal** | activity attempts, retry policy | — | **heartbeat timeout** + heartbeat details for resume; start-to-close, schedule-to-close | — | — | — | — |
| **Airflow** | queued / running / up_for_retry / deferred / awaiting_input… | — | **zombie detection** by heartbeat timeout; SIGTERM → SIGKILL | — | — | — | **pools** (named slots) |
| **GitHub Actions** | jobs with `needs` DAG | runner | timeout-minutes | environments with required reviewers; **merge queue** | — | — | **concurrency groups** |
| **SQS** | message lease (visibility timeout), extend = heartbeat, DLQ | — | — | — | — | — | — |

## 3. What to copy (each mapped to mistakes 1–10)

| # | Idea (source) | How Agent Deck uses it | Fixes |
|---|---|---|---|
| C1 | **Structured completion signals instead of scraping** (Claude `stream-json` final `result`, Codex `--json` `turn.completed/turn.failed`, process exit, Claude hooks `Stop`/`Notification`) | Every launch is supervised by a small wrapper (`deck-run`) that records the exit code to a file even if the daemon is down. The daemon emits `attempt.ended` and `review.requested`, and the orchestrator runs `deck watch --for W0` (a blocking stream). Nobody relies on `&` again. | 1, 10 |
| C2 | **Columns derived from events** (Vibe Kanban, Linear) | Task state changes only through the state machine. The board, `titles.json` and BOARD.md are *generated* from the store, not hand-written. | 3, 10 |
| C3 | **Review inbox with SLA, human gate, agent never self-merges** (Copilot, Jira drafts, GH environments) | `review` is a first-class state with `requestedAt`. `review.overdue` fires after 30 min and repeats. Approve sends the task to a serial merge queue. | 2 |
| C4 | **Review comments go back to the same session** (Conductor, Copilot `@copilot`, Claude Code web) | "Request changes" resumes the same agent session (`claude --resume <id>` / `codex exec resume <id>`) with the note as its prompt. | 2, 5 |
| C5 | **Schema-validated protocol, reject loudly** (Backlog.md uses CLI/MCP, not hand-edits; agent teams report "sent" only after the write lands) | Workers update cards only through `deck card …`. The daemon validates against `card.schema.json`, merges patches and keeps history. Invalid input exits with code 2 and a reason. v1 cards are normalised with visible warnings. | 3 |
| C6 | **Structured final output** (`claude -p --json-schema`, `codex exec --output-schema`) | The final message *is* the handoff (`handoff.schema.json`). A missing or invalid handoff is flagged in red. | 5, 2 |
| C7 | **Brief template with Outcome / Procedure / Forbidden / Required-from-user** (Devin playbooks) **+ AC/DoD** (Backlog.md) **+ setup script** (Codex cloud) | Brief validator: Goal, Rules, Done-when (≥1 checklist item), Stop point, Tags. The project rules file is always injected. The daemon snapshots the brief and passes the path itself. | 5 |
| C8 | **Leases + heartbeat timeout + zombie reaper** (SQS visibility timeout, Temporal heartbeats, Airflow zombies) | Each attempt has `leaseUntil`. Heartbeats come from output growth, card patches and CPU time. Detection runs suspect → hung, then SIGINT → SIGTERM → SIGKILL on the process group, then retry with the heartbeat details as continuation context. | 8, 9 |
| C9 | **Durable timers + retry policy** (Temporal retry, Airflow up_for_retry) | `limit_paused` with an absolute `resumeAt` stored once in the DB. The dispatcher resumes on its own, or fails over to a worker with headroom. | 4, 9 |
| C10 | **Pools / concurrency groups** (Airflow pools, GH concurrency) | Named locks: `merge` (owned by the merge queue), `db-migrations`, `ext:<system>`. All-or-nothing acquisition in sorted order, with TTL. | 6 |
| C11 | **Merge queue** (GH merge queue) | Serial: merge main into the branch, run `preMerge` (e.g. regenerate migration), run gates in the task's own slot, then merge into the target. It never aborts a merge it did not start. | 6, 7 |
| C12 | **Per-workspace port and env + setup script** (Conductor `CONDUCTOR_PORT`, CCManager `.worktreeinclude`, OpenHands port ranges) | Each attempt gets a numbered slot. Ports, DB name and env come from config templates (`{slot}`), are verified free before launch, and are injected as env. Tests read env, never constants. | 7, 8 |
| C13 | **`next` rule: deps satisfied → priority → fewer deps → id** (Task Master) | Base ordering for the dispatcher, then quota fit and age. | 10 |
| C14 | **Status-change hooks** (CCManager) and **agent-teams hooks** (`TeammateIdle`, `TaskCompleted` with exit 2) | Interactive adapter: `deck hooks install` adds `Stop`/`Notification`/`PostToolUse` hooks to a Claude config dir and a `notify` program to Codex. Idle or waiting interactive sessions become events. | 1, 8 |
| C15 | **Explicit blocked-with-reason + wake on message** (Devin sleep/wake, Linear `elicitation`) | `deck ask "…"` → `blocked` with the question on the card; `deck answer` resumes the same session. This replaces workers asking for rules mid-task in free text. | 5 |
| C16 | **Pilot first, best-of-N attempts** (Codex `--attempts`, Vibe Kanban attempts, Devin "run the playbook twice") | `kind: pilot` tasks gate their `scale` task. Attempts are numbered and kept, never overwritten. | 2, 3 |
| C17 | **Crash-safe session list** (CCManager), but resume the conversation, not just the command | On restart the daemon reconciles every running attempt (pid + start time), re-attaches to its output file or marks it crashed and resumes it from its session id. | 1, 9 |

## 4. What to avoid

| # | Anti-pattern (seen where) | Our rule | Related |
|---|---|---|---|
| A1 | Terminal scraping as the primary signal (Claude Squad, CCManager) | Scraping is allowed only as a labelled fallback for interactive sessions with no hooks. | 1, 8 |
| A2 | Sending input before the agent is ready (Claude Squad #266) | Headless only: the prompt goes in argv or stdin at launch. Interactive tasks are *offered*, never typed into a TUI. | 5 |
| A3 | Deleting workspaces in a way that can touch the main repo (Vibe Kanban #3406) | The deck removes only worktrees it created (recorded path, branch prefix `task/`), only after merge, and never with `rm -rf` on a computed path. | — |
| A4 | Restore that reruns the command but loses the conversation (CCManager) | Resume by session id. If impossible, start a continuation prompt built from card history, handoff `nextStep` and the git diff. | 9 |
| A5 | Auto-approval on by default (CCManager, Claude Squad `--autoyes`) | Permission mode is set per worker in config. Unattended runs use `--permission-prompts none` semantics: deny, don't hang. | 8 |
| A6 | Trusting self-reported completion (agent teams: "status can lag, leads stop early") | `review` requires a handoff *and* daemon-collected git facts. Done-when items are checked by the reviewer, and gates run in the merge queue. | 2, 7 |
| A7 | Two agents in one checkout (agent teams overwrites) | Default isolation is a worktree. `main-checkout` tasks must hold the `main-checkout` lock. | 6 |
| A8 | Over-engineering: event-sourced replay (Temporal), sticky queues, LLM complexity scoring (Task Master), a state zoo (Airflow) | Plain state rows plus an append-only event log, 14 states, no LLM calls from the deck (no tokens spent by the monitor). | 10 |
| A9 | Cloud push services for phone alerts | No external network. The PWA uses SSE while open. An optional `notifyCommand` hook lets the owner wire their own channel. | rules |
| A10 | Mixing hand-edited config with daemon state (agent teams overwrites team config) | Config is JSON the owner edits. State lives only in `deck.db`, written by the daemon. | 3 |

## 5. Mistakes 1–10: how each gets fixed

| Mistake | What we saw | Root cause | Fix (ideas) | Becomes |
|---|---|---|---|---|
| 1 Worker finished, orchestrator not told | Detached `&` runner; W0 idle 30 min | No supervisor; completion only in a log file | C1, C14, C17 | **Impossible**: every launch is supervised. **Loud**: `review.requested` + `worker.idle` go to `deck watch`, the panel and the phone |
| 2 Finished pilot unreviewed for 6 h | No review queue | Review was a memory item | C3, C4, C6, C16 | **Loud**: inbox count on every UI, overdue alert after 30 min, pilot gate blocks the scale task |
| 3 Wrong card keys, stale cards, reset to 0 | Free-form JSON files | No schema, whole-file rewrite | C2, C5 | **Impossible**: CLI + schema + merge-only + history. Legacy cards are normalised with a visible warning |
| 4 Limit misread (24 h at 100 %) | "resets 8:50pm" re-parsed after resume | Relative time parsed at read time, not event time | C9 | **Impossible**: `resetsAt` is an absolute epoch computed once at the event, stored, and expired by the clock |
| 5 Wrong brief path, no Done-when, rules asked mid-task | Runner took a hand-typed path | Brief unvalidated, rules not injected | C6, C7, C15 | **Impossible**: validator gate before `ready`; daemon passes its own snapshot path; rules always injected; `deck ask` for real questions |
| 6 Same migration number, merge conflicts, `merge --abort` hit W0 | Parallel generation + unsynchronised merges | No resource locks; runner owned the merge | C10, C11 | **Impossible**: `db-migrations` lock + preMerge regeneration; only the merge queue merges; it refuses if `MERGE_HEAD` exists |
| 7 Tests hard-coded to one worker's DB/port | Constants in tests | No slot env | C11, C12 | **Loud**: gate runs in a different slot than the worker used, so hard-coding fails at review. Doctor greps for configured canonical ports in changed test files |
| 8 Hung jobs, orphan servers | stdin wait, stuck request, `&` servers | No heartbeat, no process-group ownership | C8, C12, A5 | **Impossible** (stdin `/dev/null`, `CI=1`, process group killed at end) / **loud** (suspect → hung alerts) |
| 9 Limit mid-task, resume needed memory | No durable timer | Resume was a human action | C9, C17, A4 | **Impossible**: `limit_paused.resumeAt` timer; auto resume or failover |
| 10 Orchestrator doing bookkeeping | Board rows, titles.json, runner scripts by hand | No single source of truth | C2, C13, C1 | Generated board/titles, `deck new` from template, dispatcher picks workers; W0 only writes briefs and reviews |

## 6. Top five ideas adopted

1. **A supervised launch with structured signals and a push stream to the orchestrator** (C1, C14, C17). This fixes 1 and 10.
2. **A state machine plus generated views and a review inbox with SLA, pilot gates and a merge queue** (C2, C3, C11, C16). This fixes 2, 6 and 10.
3. **Schema-enforced protocols at every boundary**: brief validator before `ready`, card patches through the CLI, handoff as forced structured output (C5, C6, C7). This fixes 3 and 5.
4. **Leases, heartbeats and durable timers borrowed from Temporal, Airflow and SQS**, with quota readings stored as absolute times (C8, C9). This fixes 4, 8 and 9.
5. **Isolation slots and conflict domains, which make parallel lanes per account safe** (ports, DB names and env per slot; `area:`, `seq:`, `ext:`, `merge` domains; a quota-budget capacity planner that runs several jobs per account and spends quota that is about to reset first) (C10, C12, §8). This fixes 6, 7, 8 and keeps every account busy (1, 10).

## 7. Owner additions: multi-project and office/personal accounts

**Prior art for multi-project:**
- CCManager has a multi-project mode that keeps sessions alive while you switch repos.
- Conductor scopes workspaces per repo inside one app.
- Linear and Jira scope agent sessions per issue and team.

None of these offers an overview across projects with shared accounts and quota. Agent Deck therefore keeps **one store per project** (so tasks never mix) and **one machine-wide account/quota layer** (because limits are per account, not per project). The dispatcher works machine-wide and shares capacity between projects with Airflow-style weights.

**Prior art for account separation:** none of the tools studied enforces which credential may work on which project. GitHub environments' deployment-branch rules and required reviewers come closest: a gate on *where* a job may run, enforced by the platform rather than by convention. We copy that principle.
- The policy is a pure function, `allow(account.scope, project.scope)`.
- The office × personal row is hard-coded to deny, so no config can override it.
- The check runs at every entry point: lease, interactive claim, manual assign/reassign/pin, and adapter launch.
- The UI shows refused options greyed out with the reason, and every refusal is logged as an alert.
- Scope tags live only in the private machine config, never in git.

| Addition | Mechanism | Related mistakes |
|---|---|---|
| Overview + per-project pages | `OverviewVM` / `ProjectVM`; project chips on the panel and tiles on the deck | 10 (one place to look) |
| All accounts incl. office, with real usage | Office account reads its own statusline file; `dispatch:false` option for show-only | 4, 9 |
| Office never runs personal tasks | `policy.allow` in `lease()`, `claim()`, CLI and launch; `policy.denied` alert; greyed UI | — (new hard rule) |

## 8. Owner addition (22:35): parallel jobs per account

**Prior art:**
- Airflow **pools** give named slot counts with priority weights.
- Temporal **task queues** have workers pull work only when they have spare capacity.
- GitHub Actions **concurrency groups** and the merge queue allow one in-flight job per key, with configurable build concurrency.
- SQS gives at-least-once delivery with leases.

None of these is *quota-aware*. They count slots, not a budget that resets on a clock. The local agent managers (Claude Squad, CCManager, Conductor…) let you open many sessions on one account, but they leave quota to the human.

**What Agent Deck adopts:**
- **Slots become lanes, sized by a budget.** An account's concurrent jobs are capped by `parallel.max` and admitted by a capacity check. The check counts only the spend that lands **before** the 5-hour reset (`burn × min(time to reset, job duration)`) against the remaining 5-hour and 7-day quota. The duration and spend estimates are learned medians per tool and size.
- **Use it or lose it.** Accounts are filled in order of `urgency = quota that would expire unused / minutes to reset`. Quota that is about to reset gets spent first, and a nearly full account can still take a job that mostly runs after its reset.
- **Conflict domains instead of one job per worker** (Airflow pools + GH concurrency keys):
  - `area:` globs (overlap means conflict);
  - `seq:` numbering (parallel allowed, renumbered at merge);
  - `ext:` single-stream external systems (machine-wide, exclusive);
  - `res:` counted resources;
  - the reserved `merge` domain.
- **Event-driven re-plan** (Temporal "pull when free"): the planner runs within a second of any completion, limit reset or new queued task, with the 5-second tick only as a safety net.
- **Spare-capacity and backlog alerts**: `account.spare` fires when an account has quota and no job while eligible work exists, and names the obstacle. `backlog.low` fires when ready work drops below the spare lanes + 2, and says which sizes fit before the reset.

| Addition | Mechanism | Related mistakes |
|---|---|---|
| Several jobs per account | lanes + capacity admission + urgency ordering | 1, 9, 10 |
| Safe overlap | conflict domains, merge-time renumbering | 6, 7 |
| Re-plan on completion | wake on `attempt.ended` | 1 |
| Spare capacity / backlog alerts | `account.spare`, `backlog.low` | 1, 10 |

## Sources

Local managers
- Claude Squad: https://github.com/smtg-ai/claude-squad · tmux detection: https://raw.githubusercontent.com/smtg-ai/claude-squad/main/session/tmux/tmux.go · issues: https://github.com/smtg-ai/claude-squad/issues
- Vibe Kanban: https://github.com/BloopAI/vibe-kanban · https://vibekanban.com/docs/core-features/creating-tasks.md · issues: https://github.com/BloopAI/vibe-kanban/issues
- Crystal: https://github.com/stravu/crystal
- CCManager: https://github.com/kbwo/ccmanager · https://cdn.jsdelivr.net/npm/ccmanager@4.4.3/README.md
- Conductor: https://www.conductor.build/docs/ · https://www.conductor.build/docs/concepts/workspaces-and-branches · https://www.conductor.build/docs/concepts/workflow
- Sculptor: https://github.com/imbue-ai/sculptor · https://imbue.com/sculptor/

Hosted agents
- Claude Code headless: https://code.claude.com/docs/en/headless · hooks: https://code.claude.com/docs/en/hooks · subagents: https://code.claude.com/docs/en/sub-agents · agent teams: https://code.claude.com/docs/en/agent-teams · web: https://code.claude.com/docs/en/claude-code-on-the-web
- Codex non-interactive: https://learn.chatgpt.com/docs/non-interactive-mode · cloud: https://learn.chatgpt.com/docs/cloud · environments: https://learn.chatgpt.com/docs/environments/cloud-environment.md
- GitHub Copilot cloud agent: https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github · https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent
- Devin: https://docs.devin.ai/sdk/reference/variables/SessionStatus.md · https://docs.devin.ai/product-guides/creating-playbooks · https://docs.devin.ai/api-reference/common-flows.md
- OpenHands runtime: https://docs.openhands.dev/usage/architecture/runtime

Task tools and engines
- Task Master task structure: https://github.com/eyaltoledano/claude-task-master/blob/main/docs/task-structure.md
- Backlog.md: https://github.com/MrLesk/Backlog.md · agent guidelines: https://raw.githubusercontent.com/MrLesk/Backlog.md/main/src/guidelines/agent-guidelines.md
- Linear agent interaction: https://linear.app/developers/agent-interaction
- Jira agents: https://support.atlassian.com/jira-software-cloud/docs/collaborate-on-work-items-with-ai-agents/
- Temporal: https://docs.temporal.io/encyclopedia/detecting-activity-failures · https://docs.temporal.io/encyclopedia/retry-policies · https://docs.temporal.io/task-queue
- Airflow: https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html · https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/pools.html
- GitHub Actions: https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs · https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions · https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment · https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- SQS visibility timeout: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html

Note: CLI flags for the adapters (`claude -p --output-format stream-json --json-schema --session-id --resume`, `codex exec --json --output-schema -o`, `codex exec resume <id>`) were also checked against the locally installed CLIs (Claude Code 2.1.x, codex-cli 0.169).
