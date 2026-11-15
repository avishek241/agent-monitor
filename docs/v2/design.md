# Agent Deck v2: P1 design

Status: draft for W0/owner review (P1). Builds on `research.md`; the ideas it adopts are cited as C1–C17 and the anti-patterns as A1–A10. The ten observed mistakes are cited as **M1–M10**.

Normative files:
- `schemas/task.schema.json`
- `schemas/card.schema.json`
- `schemas/handoff.schema.json`
- `schemas/event.schema.json`
- `schemas/config.schema.json`

Mockups are in `mockups/panel.html` and `mockups/deck.html`.

> **Reading guide for builders.** §3 (data), §5 (state machine), §11 (CLI), §12 (HTTP + view models) and §17 (interfaces) are contracts: change them only with W0 sign-off. Everything else describes behaviour.

---

## 0. Goals and non-goals

**Goals**
1. Every mistake M1–M10 becomes impossible, or at least loud (see the table in §18).
2. Tasks are created properly: from a template, validated, numbered by the tool.
3. Handoffs are structured, and they always reach a review inbox.
4. **Every account runs as many jobs in parallel as its quota, time to reset and job sizes allow**, preferring quota that is about to reset. Conflict domains keep parallel jobs safe, the planner re-plans on every completion, and spare capacity or a thin backlog raises an alert. Nobody has to remember to start anything.
5. One **Overview** covers all projects, with a **page per project**. The same view models drive the floating panel and the phone deck.
6. **Account policy is enforced.** An office account never runs personal-project work.

**Non-goals**
- No cloud service, push service or telemetry. The only network use is localhost plus optional Tailscale (A9).
- The deck spends no tokens itself and makes no LLM calls (A8).
- No event-sourced replay. State lives in rows and events are an append-only log (A8).
- No auto-approval of reviews, except `merge.auto` on chores whose gates pass.
- No pull requests or remote pushes. Merges are local and serial.

## 1. Architecture

```
            ┌──────────────────────── private config (never in git) ───────────────────────┐
            │ ~/.config/agent-deck/deck.json  (machine: accounts+scope, workers, projects)    │
            │ ~/.config/agent-deck/projects/<id>.json (project: repo, rules, slots, gates)    │
            └──────────────────────────────────────────────────────────────────────────────────┘
                                   │ read                                  
 ┌──────────────┐  CLI (lib)  ┌────▼─────────────────────────────┐  spawn via deck-run  ┌───────────────┐
 │ deck <cmd>   │────────────▶│ deck.store  (SQLite, WAL,        │◀─────────────────────│ dispatcher    │
 │ (W0, workers,│             │  BEGIN IMMEDIATE, single module  │                      │ loop in deckd │
 │  hooks)      │             │  owns ALL transitions + policy)  │                      └──────┬────────┘
 └──────────────┘             └────┬───────────────┬─────────────┘                             │
                                   │               │                                     ┌─────▼──────┐
                       global.db (accounts, usage, │ projects/<id>/deck.db (tasks,       │ adapters   │
                       workers, ext domains, events)│ attempts, cards, history, domains,    │ claude /   │
                                                   │ events, merge queue)                │ codex /    │
                                   ┌───────────────┴─────────┐                           │ interactive│
                                   │ deckd HTTP (127.0.0.1    │──SSE──▶ Panel.swift        │ / shell    │
                                   │  + optional Tailscale)   │──SSE──▶ PWA deck (phone)   └─────┬──────┘
                                   └──────────────────────────┘                                 │
                     runs/<project>/<task>/<attempt>/{prompt.md, out.jsonl, err.log,            ▼
                       pid.json, exit.json, handoff.json, handoff.md}              claude -p / codex exec
```

- **Language**: Python 3.9+ using only the standard library (`sqlite3`, `http.server`, `subprocess`, `json`), the same as v1. Optional dev dependency: `jsonschema` for tests only. The runtime ships a ~150-line validator for the subset of JSON Schema we use (type, enum, const, required, additionalProperties, pattern, min/max, items).
- **Processes**:
  - `deckd` is one daemon per machine: dispatcher, supervisor, merge queue, HTTP server, SSE, and the v1 importer.
  - `deck` is the CLI. It uses the store library directly, so it works when `deckd` is down. If `deckd` is down, the CLI warns that nothing will dispatch.
  - `deck-run` wraps every spawned agent process (§6.6).
- **Single choke point**: every state change goes through `deck.store.transition()`, and every lease through `deck.store.lease()`. Both run in `BEGIN IMMEDIATE` transactions. The account policy check lives inside `lease()` and inside `claim()` (§2.3), so no code path can skip it.
- **Packaging**: `deck/` Python package, `bin/deck`, `bin/deckd`, `web/` (PWA static files served by `deckd`), and `PanelV2.swift`. v1 files stay untouched until the migration finishes (§15).

## 2. Projects, accounts and the account policy

### 2.1 Projects
- The private machine config lists projects: `{id, config, scope, allowPersonalAccounts, weight, enabled, color, icon}` (config.schema.json `machine.projects`).
- Each project has its **own project config** and its **own store** at `<stateDir>/projects/<id>/deck.db`. Projects never share task rows.
- **Adding a project** means adding an entry and running `deck project add <id> --config <path> --scope personal|office`. Removing one sets `enabled:false`, or deletes the entry (its store stays on disk; nothing is deleted).
- **Machine-wide state** lives in `<stateDir>/global.db`: accounts, usage readings, limits, workers and lanes, `ext:` domains (machine-wide), and the global event mirror. Accounts and quota are shared across projects, which is why dispatching is machine-wide.
- **Overview** shows every project. A **project page** shows that project's workers, board, review inbox, alerts, timeline, repo and health (§13).

### 2.2 Accounts and workers
- An **account** is one credential home: a Claude config dir or a `CODEX_HOME`. It has `tool`, `scope` (`office|personal`), usage sources, ceilings, and `dispatch` (false means the deck shows usage but never leases to it, e.g. the owner's office account used by hand).
- A **worker** is a named identity on an account (`W0…Wn`) with an adapter, role, capabilities and up to `lanes` parallel jobs (`W1·1`, `W1·2`, …). The account's `parallel.max` caps the total, and the capacity planner (§6.2) decides how many jobs actually run from quota, time to reset and job size.
- **All accounts appear in the Accounts view with real usage**, the office Claude account included. Usage is read from the statusline file in that config dir, exactly as for the others.
- An account's **identity at runtime** comes from its *home path*, never from a free-text label:
  - The CLI maps `CLAUDE_CONFIG_DIR` or `CODEX_HOME` (realpath) to the account whose `home` matches.
  - With no match, an interactive session is `unknown` and may claim nothing.
  - `DECK_ACCOUNT` is only accepted when it agrees with the home mapping.

### 2.3 Policy (hard rule)

`policy.allow(account, project) -> (bool, reason)` is a pure function in `deck/policy.py`, 30 lines, with no configuration that can override the hard rows:

| account.scope \ project.scope | personal | office |
|---|---|---|
| **personal** | allow | allow **iff** `project.allowPersonalAccounts == true`, else deny ("office project does not allow personal accounts") |
| **office** | **DENY — hard-coded** ("office account may never run personal projects") | allow |
| missing / unknown | deny ("account has no scope tag") | deny |
| (any) and project scope missing | deny ("project has no scope tag") | — |

**Where it is enforced.** The checks are defence in depth; each one is tested (§16 T-POL).
1. `store.lease()` runs the check inside the lease transaction. On deny it writes no row, emits a `policy.denied` event (`severity: alert`), and the dispatcher simply never considers that pair again in this tick. Normally the dispatcher filters candidates first (§6.2), so a deny inside `lease()` means a bug. It is alerted as one.
2. `store.claim()` (interactive sessions) runs the same check against the session's home-derived account.
3. `deck assign/reassign/pin` (manual) runs the check before writing. On deny it prints the reason and exits with code 3. It logs `policy.denied` (alert, `for: ['owner']`).
4. `adapter.launch()` re-checks with the account it is about to put in the environment, just before `exec`. A mismatch aborts the launch and logs `policy.denied` with `data.layer = "launch"`.
5. Config load: when an account and a project are both configured but `pinWorker` or `workerClass` makes a task unrunnable under the policy, `deck validate` reports it. Project `scope` cannot be changed while tasks are `running` (reload refuses).
6. UI: `GET …/eligible-workers` returns `{worker, allowed, reason}`. The reassign sheet greys out disallowed workers and shows the reason; it never hides them (mockup `deck.html` → "Reassign").

**Privacy.** Scope tags exist only in the private config. The repo ships `examples/` with neutral names. `deck doctor` fails if any config file is tracked by git or sits inside a configured repo.

## 3. Data model

### 3.1 Files and stores
```
~/.config/agent-deck/deck.json                    machine config (private)
~/.config/agent-deck/projects/<id>.json           project config (private)
<stateDir>/global.db                              accounts, usage, limits, workers/lanes, ext domains, events(mirror)
<stateDir>/projects/<id>/deck.db                  tasks, attempts, cards, card_history, domains, events, merge_queue, seq
<stateDir>/runs/<id>/<task>/<n>/…                 per-attempt files (see §6.6)
<stateDir>/token                                  local write token (0600)
<stateDir>/devices.json                           paired phone tokens (hashed)
```
- `stateDir` defaults to `~/.local/share/agent-deck`.
- SQLite runs with `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`.
- Each DB has a `meta(schema_version)` row; migrations are ordered SQL files in `deck/migrations/{global,project}/NNN.sql`.

### 3.2 Project DB tables (B1 owns)
```sql
tasks(id TEXT PRIMARY KEY, num INTEGER UNIQUE, state TEXT NOT NULL, priority TEXT, size TEXT,
      worker TEXT, resume_at INTEGER, rev INTEGER NOT NULL DEFAULT 1,
      data TEXT NOT NULL,                -- full task JSON (task.schema.json)
      created_at INTEGER, updated_at INTEGER, state_since INTEGER);
attempts(task TEXT, n INTEGER, worker TEXT, account TEXT, adapter TEXT, session_id TEXT,
      pid INTEGER, pgid INTEGER, pid_start TEXT, run_dir TEXT, slot INTEGER,
      started_at INTEGER, ended_at INTEGER, lease_until INTEGER, last_heartbeat INTEGER,
      cpu_secs REAL, out_offset INTEGER DEFAULT 0, exit_code INTEGER, outcome TEXT,
      usage_start REAL, usage_end REAL, tokens INTEGER, continuation_of INTEGER, token_hash TEXT, lane INTEGER,
      PRIMARY KEY(task, n));
cards(task TEXT PRIMARY KEY, rev INTEGER, data TEXT);            -- card.schema.json
card_history(id INTEGER PRIMARY KEY, task TEXT, attempt INTEGER, ts INTEGER, op TEXT, patch TEXT);  -- append-only (trigger forbids UPDATE/DELETE)
handoffs(task TEXT, attempt INTEGER, ts INTEGER, valid INTEGER, data TEXT, PRIMARY KEY(task, attempt));
domains(name TEXT, holder TEXT, holder_kind TEXT, acquired_at INTEGER, expires_at INTEGER, PRIMARY KEY(name, holder));  -- area:/seq:/res:/merge
domain_waits(name TEXT, task TEXT, since INTEGER, reserved INTEGER DEFAULT 0, PRIMARY KEY(name, task));   -- anti-starvation
slots(slot INTEGER PRIMARY KEY, holder TEXT, since INTEGER);
merge_queue(id INTEGER PRIMARY KEY, task TEXT UNIQUE, enqueued_at INTEGER, state TEXT, started_at INTEGER, note TEXT);
events(id INTEGER PRIMARY KEY, ts INTEGER, type TEXT, severity TEXT, task TEXT, attempt INTEGER, worker TEXT,
       for_ TEXT, text TEXT, data TEXT);                          -- append-only (trigger)
seq(name TEXT PRIMARY KEY, value INTEGER);                        -- task numbers ('task'), never reused
```

### 3.3 Global DB tables
```sql
accounts(id TEXT PRIMARY KEY, data TEXT);                        -- snapshot of config entry (for UI history)
usage(account TEXT, read_at INTEGER, source TEXT, pct5 REAL, pct7 REAL, resets5 INTEGER, resets7 INTEGER,
      confidence TEXT, PRIMARY KEY(account, read_at));            -- absolute epochs only
limits(id INTEGER PRIMARY KEY, account TEXT, kind TEXT, detected_at INTEGER, resets_at INTEGER,
      source TEXT, project TEXT, task TEXT, attempt INTEGER, raw TEXT, cleared_at INTEGER);
workers(id TEXT PRIMARY KEY, account TEXT, state TEXT, idle_since INTEGER);
lanes(worker TEXT, lane INTEGER, state TEXT, project TEXT, task TEXT, attempt INTEGER, since INTEGER, PRIMARY KEY(worker, lane));
account_state(account TEXT PRIMARY KEY, state TEXT, capacity_json TEXT, spare_since INTEGER, updated_at INTEGER);  -- planner output for UIs
ext_domains(name TEXT, holder TEXT, project TEXT, acquired_at INTEGER, expires_at INTEGER, PRIMARY KEY(name, holder));
events(...same as project, plus project TEXT...);                -- mirror of every event (Overview + SSE)
usage_cost(account_tool TEXT, size TEXT, delta5 REAL, delta7 REAL, minutes REAL, ts INTEGER);  -- learned cost + duration per attempt, by size
```

### 3.4 Task, card and handoff
- **Task**: see `task.schema.json`. Important fields: `project`, `brief{path, sha256, snapshot}`, `deps`, `capability`, `workerClass`, `pinWorker`, `domains`, `estimate`, `timeouts{attemptMin, taskHours, quietMin}`, `retry{maxAttempts, onLimit, onHang}`, `merge{preMerge, gates, auto}`, `kind` / `pilotOf`, `state` / `stateReason` / `waitingFor` / `resumeAt`, `attempts[]`, `workspace{path, branch, slot, env}`, `review{…}`, `rev`.
- **Card** (`card.schema.json`):
  - Steps have a stable `id`, `title`, `status`, `etaMin` (required) and `startedAt` (daemon-set).
  - Metrics are a **list** of `{label, done, total, kind}`.
  - The card is patched through ops (§8) and never replaced. Every accepted op goes to `card_history`.
- **Handoff** (`handoff.schema.json`): the final structured message of the attempt, plus `git` and `run` facts the daemon collects.

Example task (abbreviated):
```json
{"schema":"deck.task/1","project":"shop-ops","id":"107-supplier-import","title":"Import the supplier list",
 "summary":"Load 53 suppliers from the accounts export so purchase bills can link to them",
 "brief":{"path":"docs/tasks/107-supplier-import.md","sha256":"9f…","snapshot":"…/runs/shop-ops/107-supplier-import/brief.md"},
 "tags":["data","owner-visible"],"priority":"P1","size":"M","kind":"pilot","deps":["104-vendor-model"],
 "capability":"data","workerClass":["codex-exec","claude-headless"],"domains":["ext:pc-ssh","area:db/suppliers/**"],
 "state":"running","stateReason":"W2 · step 3/5","worker":"W2","rev":14}
```

## 4. Briefs and the validator (M5)

`deck new` creates both the task row and the brief from `templates/brief.md`. Task fields (tags, priority, size, capability, deps, domains, worker class) live in the **store**. The brief holds only prose, plus a generated header comment that `deck` rewrites. Nobody types a brief path into a runner again: adapters only pass the **snapshot** path.

Brief template (sections in this order; a heading match is case-insensitive):
```markdown
<!-- deck: generated header — 107-supplier-import · P1 · M · data · deps 104 · domains ext:pc-ssh, area:db/suppliers/** -->
# Import the supplier list
## Goal            (one paragraph: the outcome the owner sees)
## Context         (links, files, prior tasks; optional)
## Rules           (task-specific rules; the project rulesFiles are injected automatically)
## Steps           (optional; numbered)
## Done when       (checklist, ≥1 "- [ ]" item, each verifiable: a command, a count, a file)
## Stop point      (where to STOP and hand off, e.g. "after the 20-record pilot, before any live write")
## Not allowed     (optional: files/actions forbidden)
## Needs from owner (optional)
```

Validator `deck validate <task>` runs automatically on `deck ready` and whenever the brief file changes while the task is `ready` or `queued`. Rule ids appear in its error messages:

| Id | Rule | Severity |
|---|---|---|
| V1 | Brief file exists inside `project.repo` (or is absolute for `isolation:none`) | error |
| V2 | Sections `Goal`, `Rules`, `Done when` and `Stop point` are present and non-empty | error |
| V3 | `Done when` has ≥1 checklist item. Each item is ≤200 chars and is not just "works" or "done" (a deny-list of vague items) | error |
| V4 | Title is 4–80 chars, summary is 8–160 chars, plain words (no path or backticks in the title) | error |
| V5 | Every `deps` id exists in the same project, and the dependency graph has no cycle | error |
| V6 | `seq:`/`ext:`/`res:` domains are declared in the project config; `merge` is reserved; `area:` globs are relative to the repo | error |
| V7 | At least one configured worker could run it: capability, `workerClass`, **policy allow**, `dispatch:true` | error |
| V8 | `kind:scale` has a `pilotOf` that exists. The pilot must be `done` before the scale task can be queued | error |
| V9 | No secrets: regexes for keys, tokens, passwords and long base64 strings | error |
| V10 | Paths mentioned in backticks exist (relative to the repo) | warn |
| V11 | Brief is under 25 KB, and the `Rules` section does not restate rulesFiles (it links instead) | warn |
| V12 | Size `XL` → suggest a split into a pilot plus phases | warn |
| V13 | Code/ui/infra task with no `area:` → derived from brief paths and shown; none derivable → `area:**` with the warning "declare areas to allow parallel work" | warn |

On success: `brief.sha256` and `snapshot` are written (the brief is copied to `runs/<p>/<task>/brief.md`), the doneWhen and stopPoint are parsed into the task, and the task goes `draft → ready`. If the brief file later changes (the hash differs), the task returns to `draft` with the reason "brief edited after validation" (T2), but only if no attempt is running. With an attempt running, it emits a warning instead.

## 5. State machine

### 5.1 States
| State | Meaning | Holds lease / domains / slot? | Board column |
|---|---|---|---|
| `draft` | being written; not validated | – | Backlog |
| `ready` | validated; waiting for deps/gates | – | Backlog |
| `queued` | runnable; waiting for capacity, a lane or a domain | – | Up next |
| `starting` | lease granted, process launching | yes | Running |
| `running` | attempt alive with heartbeats | yes | Running |
| `blocked` | needs a person (question, dep failed, paused, hang) | no | Needs you |
| `limit_paused` | account hit its limit; durable `resumeAt` | no (slot/worktree kept) | Running (paused) |
| `hung` | no heartbeat and no CPU for 2×quietMin | yes until killed | Running (red) |
| `review` | handoff received; waiting for approve/changes | no (worktree kept) | Review |
| `changes_requested` | review note or merge-gate failure; will re-queue | no | Up next |
| `merging` | in merge queue (gates, merge) | `merge` domain while active | Review (merging) |
| `done` | merged (or approved with no branch) | – | Done |
| `failed` | attempts exhausted / unrecoverable | – | Needs you |
| `cancelled` | stopped by W0/owner | – | (hidden) |

### 5.2 Transitions
Actors: **W0** is the orchestrator session, **OWN** the owner (panel or phone), **DSP** the dispatcher, **SUP** the supervisor (heartbeat and process watch inside `deckd`), **ADP** an adapter signal, **WRK** the worker via the CLI, **MQ** the merge queue, **TMR** a durable timer.

| # | From → To | Trigger | Actor | Guard | Side effects / events |
|---|---|---|---|---|---|
| T1 | draft → ready | `deck ready` | W0/OWN | validator passes (§4) | sha + snapshot; `task.validated` |
| T1x | draft → draft | `deck ready` fails | W0 | — | `task.invalid` (warn) with rule ids |
| T2 | ready/queued → draft | brief edited (hash change), `deck unready` | SUP/W0 | no running attempt | `task.state` |
| T3 | ready → queued | all deps `done`, `waitingFor` empty, pilot done (V8), project enabled | DSP | — | `task.state` |
| T4 | ready/queued → blocked | a dep became `failed`/`cancelled` (upstream failed) | DSP | — | reason "dep 104 failed"; `for: W0` |
| T5 | queued → starting | lease granted (§6.2) | DSP | free lane, **policy allow**, capability, **capacity.admit** (§6.2), domains + slot acquired, attempts < max | attempt row n+1; `attempt.started`; worker busy |
| T6 | starting → running | first output line / session id within 120 s | ADP | process alive | `task.state` |
| T7 | starting → queued | spawn failed, setup failed, no output in 120 s | SUP | attempts < max | attempt outcome `crashed`; release domains/slot; backoff 1/2/4 min; immediate re-plan |
| T8 | running → review | process ended **with valid handoff** outcome `done`/`partial` | ADP/SUP | — | `handoff.received`, `review.requested` (action, `for: W0`) |
| T9 | running → review | process ended, **no valid handoff** | SUP | — | synthetic handoff `outcome: unknown`; `handoff.invalid` (alert); `review.requested` |
| T10 | running → blocked | handoff outcome `blocked`, or `deck ask` + end of turn | ADP/WRK | — | `question.asked` (action); domains/slot released, worktree kept |
| T11 | running/starting → limit_paused | adapter `Limit` signal or exit with limit text | ADP | — | `limit.hit` (absolute `resetsAt`); worker → cooling; `resumeAt = resetsAt + 90 s` |
| T12 | running → hung | no heartbeat ≥ 2×quietMin **and** CPU delta < 1 s over the last quietMin | SUP | — | `attempt.hung` (alert) with the deepest child command and its age |
| T13 | hung → running | heartbeat resumes before kill | SUP | — | `attempt.heartbeat` "recovered" |
| T14 | hung → queued | `onHang=restart`, kill done, attempts < max | SUP | — | `attempt.killed`; continuation attempt next |
| T15 | hung → blocked | `onHang=block`/`alert`, or attempts exhausted | SUP | — | `for: W0` |
| T16 | running → queued | crash (non-zero exit with no handoff, no limit) or `attemptMin` timeout | SUP | attempts < max | retry with backoff 2/4/8 min (`resumeAt`) |
| T17 | running/hung/starting → failed | attempts exhausted, or `taskHours` exceeded | SUP | — | alert, `for: W0` |
| T18 | limit_paused → queued | `now ≥ resumeAt` **or** a failover decision (§6.4) | TMR/DSP | — | continuation hint: same worker + session |
| T19 | blocked → queued | `deck answer`, `deck unblock`, `deck resume` | W0/OWN | — | `question.answered` |
| T20 | running → blocked | `deck pause` (SIGINT → wait 20 s → SIGTERM) | W0/OWN | — | reason "paused by …"; resume continues the session |
| T21 | review → merging | `deck approve` (or `merge.auto` + kind chore) | W0/OWN | handoff present; reviewer ≠ worker id | `review.decided`; `merge.queued` |
| T22 | review → done | approve on a task with no branch (`isolation:none`, research) | W0/OWN | — | `review.decided` |
| T23 | review → changes_requested | `deck changes "<note>"` | W0/OWN | note non-empty | `review.decided` |
| T24 | merging → done | merged + gates passed | MQ | — | `merge.done`; worktree + slot released; dependants re-evaluated (T3) |
| T25 | merging → changes_requested | conflict with target, preMerge or gate failure | MQ | — | `merge.conflict`/`merge.failed` with file list / output tail as the note |
| T26 | merging → review | main checkout busy (MERGE_HEAD present, dirty overlap) | MQ | — | `merge.failed` "main checkout busy"; retry in 2 min without reviewer action |
| T27 | changes_requested → queued | immediately (or after `resumeAt` backoff) | DSP | attempts < max (+1 allowed per review round) | continuation: resume the same session with the note |
| T28 | failed → queued | `deck retry [--attempts +N]` | W0 | — | — |
| T29 | any non-terminal → cancelled | `deck cancel` | W0/OWN | — | SIGINT/SIGTERM if running; domains/slot released; worktree kept (never deleted by cancel) |
| T30 | running → queued (reassign) | `deck reassign <task> <W>` | W0/OWN | **policy allow** for the target | SIGINT current; pin the new worker; continuation prompt (no session carry across tools) |

Rules:
- An illegal transition raises `IllegalTransition` and writes nothing.
- Every transition increments `rev`. Callers pass the `rev` they read (optimistic lock), except DSP/SUP inside their own `BEGIN IMMEDIATE` transaction.
- `done`, `cancelled` and `failed` are terminal, except T28.

### 5.3 Worker, lane and account states (derived, global DB)
- **Worker identity** (`W1`…) owns up to `lanes` parallel jobs, shown as `W1·1`, `W1·2`, … An **account** caps the sum over its workers with `parallel.max`. Accounts and workers are not 1:1 runners any more: one account runs **several jobs at once** when quota allows (owner addition, 22:35).
- **Lane states**: `free`, `running`, `cooling` (the account hit its limit; `until = resetsAt`). Worker state: `active` (≥1 running lane), `idle`, `offline` (adapter `check()` failed), `disabled`.
- **Account state**: `ok`, `tight` (capacity planner admits only S jobs), `full` (admits nothing until reset), `cooling`, `unknown`.
- **`account.spare`** (action, `for: W0`) fires when an account has **0 running jobs**, the planner says it could admit at least one job, and eligible queued/ready work exists, for longer than `scheduler.spareAlertMin` (default 5 min). The text names the obstacle: "Codex·b has 45 % to spend before 23:10 and 0 jobs — 3 tasks wait on domain ext:pc-ssh". When no eligible work exists, `backlog.low` fires instead (§6.10).

## 6. Dispatcher (`deckd`)

### 6.1 Loop: tick plus immediate re-plan
The planner runs:
- **immediately** (debounced by `replanDebounceMs`, default 500 ms) on any of: attempt ended, task entered `queued`, limit cleared, a new usage reading that changes an account's capacity by ≥ 5 points, domain released, config reload;
- every 5 s as a safety tick;
- at the next durable timer.

A CLI write touches `<stateDir>/wake`, which `deckd` polls by mtime every 250 ms. **Re-plan on every completion** is therefore built in: `attempt.ended` → plan within one second.

```
on_wake / tick():
  reload config if changed (refuse a project scope change while it has running tasks)
  for each attempt in starting/running/hung: supervise(attempt)        # §6.5–6.6
  fire timers: limit_paused/changes_requested/backoff with resume_at <= now → queued (T18/T27)
  promote ready → queued (T3); propagate upstream failures (T4)
  refresh usage readings (cached 20 s; §6.3)
  review SLA check (§6.9); merge queue step (§6.8)
  plan()                                                                # §6.2 — repeats until a round admits nothing
  spare-capacity and backlog checks (§5.3, §6.10)
```

### 6.2 Planner: parallel, quota-aware assignment

**Step 1: capacity per account.** This lives in `deck/capacity.py`, a pure function of readings, running jobs, estimates and the clock.

For account `a` at time `now`:
```
R5   = ceiling5(a) - reservePct(a) - eff5(a, now)          # spendable 5-h % right now (§6.3)
R7   = weeklyCeiling(a) - pct7(a)                           # spendable 7-day %
T5   = max(1, minutes until resets5(a))                     # window end; unknown → 300
running jobs j on a: est_j (% of 5-h window), D_j (expected minutes), elapsed_j
burn_j        = est_j / D_j                                 # %/min, learned per (tool,size)
committed5    = Σ_j burn_j × min(T5, max(0, D_j - elapsed_j))   # only spend that lands BEFORE the reset
committed7    = Σ_j est_j × k7(a)                            # k7 = learned ratio Δ7d / Δ5h (default 0.12)
admit(a, task) ⇔
     lanes_used(a) < parallel.max(a)
 and machine: running_total < maxParallelTotal and free RAM ≥ minFreeRamGb
 and committed5 + burn_task × min(T5, D_task) ≤ R5          # quota that lands before the reset
 and committed7 + est_task × k7(a) ≤ R7                       (P1 may exceed R7 by 5 points)
 and a not cooling
```
- Jobs that run past the reset only spend the part before the reset in this window. When `T5` is short, a nearly full account can still admit a job, because most of the job's spend lands in the next window. That is how the planner **prefers quota that is about to reset**.
- `est` and `D` are the medians of the last 20 completed attempts for (tool, size), with defaults S 8 %/30 min, M 20 %/90 min, L 40 %/180 min, XL 70 %/360 min. `task.estimate` overrides them.
- `unknown` readings assume `eff5 = 50` and allow at most 1 job.

**Step 2: use-it-or-lose-it urgency.** This orders accounts within a round:
```
waste(a)   = max(0, R5 - committed5)                         # % that will expire unused at the reset if nothing more starts
urgency(a) = waste(a) / max(T5, 15)                         # % per minute at risk; higher = fill first
```

**Step 3: candidate tasks** (queued, all enabled projects), ordered by:
1. resumes first (`limit_paused` → queued, `changes_requested` → queued: they hold context);
2. priority P1 < P2 < P3;
3. project fair share `running(project) / weight` (lower first);
4. unblocks most (number of dependants, descending);
5. age, then id.

**Step 4: rounds.** One job per account per round, so lanes fill evenly. Repeat until a round admits nothing.
```
plan():
  loop:
    admitted = 0
    for a in accounts sorted by urgency(a) desc where a.dispatch and not cooling:
      for task in cands (Step 3 order):
        w = best worker identity on a with a free lane such that
              adapter(w) ∈ task.workerClass and task.capability ∈ w.capabilities
              and task.pinWorker ∈ (None, w.id)
        if not w: continue
        ok, why = policy.allow(a, project(task))              # §2.3; filtered here, re-checked in lease()
        if not ok: continue
        if not capacity.admit(a, task): note(task, "waiting: no account with quota for size L"); continue
        if not domains.try_acquire(task.domains + slot, holder=task):  # §6.2.1, all-or-nothing
              note(task, "waiting: domain area:apps/api/** held by 106"); continue
        store.lease(task, w, lane)   # BEGIN IMMEDIATE: recheck queued, lane free, policy, capacity; insert attempt; T5
        adapter.launch(...)          # outside the transaction; failure → T7 and immediate re-plan
        cands.remove(task); admitted += 1; break              # next account (round-robin by urgency)
    if admitted == 0: break
```
- **Affinity tie-breaks inside an account**: the same worker that holds the task's last session (resume), then the task's `workerClass` order, then a worker that ran this project in the last 2 h.
- **Across accounts**, `urgency` decides who receives the next task, not the task's preference. The exception is resumes: a task in `limit_paused` → `queued` first tries its own account, if that account can admit it.

#### 6.2.1 Conflict domains (replace one-job-per-worker safety)
Parallel jobs are safe only when they cannot collide. Each task carries `domains` (task.schema.json). They are acquired all-or-nothing, in sorted order (no deadlock), for the whole attempt, and released at attempt end (worktree kept).

| Domain | Meaning | Conflict rule | Default capacity |
|---|---|---|---|
| `area:<glob>` | repo files/areas the task will change | two tasks conflict when their globs **overlap** (one's literal prefix is a prefix of the other's, e.g. `apps/api/**` vs `apps/api/src/invoices/**`) | exclusive per overlap |
| `seq:<name>` | shared numbering, e.g. `seq:db-migrations` | parallel allowed; renumbered at merge under the merge lock (§6.8). Strict mode `capacity: 1` serialises the whole attempt | unlimited (0) |
| `ext:<name>` | single-stream external resource (remote PC, accounting system, a shared device), **machine-wide across projects** | exclusive | 1 |
| `res:<name>` | other declared project resource (e.g. a demo DB) | counted | from config |
| `merge` | reserved: held only by the merge queue | exclusive | 1 |
| `slot` | ports/DB names (§6.6) | one slot per attempt | `slots.count` |

- **Area derivation**: when a code/ui/infra task declares no `area:`, the validator derives areas from the paths in the brief (the top two segments, e.g. `apps/web/**`) and shows them on `deck ready` for confirmation.
- A task with **no** areas and capability `code` gets `area:**` (it conflicts with every code task) and V13 warns: "declare areas to allow parallel work".
- **Anti-starvation**: a task that has waited more than 30 min for a domain *reserves* it. New overlapping holders are refused until it runs. This emits `domain.waiting` (warn).
- **Visibility**: the project page lists held domains, holders and waiters ("DOMAINS & MERGE"). Every waiting task's `stateReason` names the blocking domain and holder.

### 6.3 Quota model (M4, M9)
- **Readings** go into the `usage` table. Every reading stores **absolute** epochs (`resets5`, `resets7`) and `read_at`. Sources:
  - Claude: the statusline file `rate_limits.five_hour.{used_percentage,resets_at}` and `seven_day` (the account's own statusline writes it, office account included).
  - Codex: rollout `payload.rate_limits.primary/secondary.{used_percent,resets_at}` (newest timestamp across files, as in v1).
  - Adapters: `Limit` signals → the `limits` table.
- **Effective usage** `eff5(account, now)`:
  1. If an uncleared limit with `resets_at > now` exists → 100 (`cooling`).
  2. Else take the latest reading. If `resets5 <= now` → 0 with confidence `low` ("window rolled over").
  3. Else `pct5`, with confidence `high` if `read_at` is under 10 min old, `stale` otherwise.
  4. No reading → `unknown` (treated as 50 for scoring; never shown as 0 %).
- **Limits are never re-derived from text after the event.** A relative "resets 8:50pm" is converted **once**, at detection, using the time the line was read (`detected_at`): today if that is after `detected_at`, else +24 h. The adapter parsers are incremental by byte offset (`attempts.out_offset`), so a line is parsed once, ever. A limit is cleared (`cleared_at`) by time passing, or by a newer reading with `pct5 < 100` and `read_at > detected_at`.
- **Cost estimate** `est(task, w)` = median of the last 20 `usage_cost.delta` for (tool, size). Defaults: S 8, M 20, L 40, XL 70 (% of the 5-hour window). After every attempt, `usage_end - usage_start` is recorded when both readings are fresh.
- **Admission** uses these numbers through the capacity planner (§6.2, Step 1). The hard stops are: `eff5 ≥ ceilingPct` (default 92) admits nothing, and the weekly limit is hard for all tasks except P1 (5-point grace).

### 6.4 Limit pause and auto-resume (M9)
When an adapter emits `Limit(resetsAt?)`, or the process exits with limit text:
1. Attempt outcome `limit`. Domains are released, and the **slot and worktree are kept**. Every other running job on the same account is left alone: each one hits the limit on its own, and they pause together. The account goes `cooling`, so the planner admits nothing more on it.
2. The `limits` row is inserted. The worker goes to `cooling until resetsAt`; if `resetsAt` is unknown, it is `now + 60 min`, re-checked against readings.
3. Task `limit_paused`, `resumeAt = resetsAt + 90 s`, with the reason "W3 hit its 5-hour limit · resumes 20:51".
4. Failover check, according to `retry.onLimit`:
   - `wait`: nothing more.
   - `failover`: immediately T18 to queued, with `pinWorker` cleared and the score's same-session bonus removed.
   - `auto` (default): fail over only if `resetsAt - now > 45 min` **and** the capacity planner admits the task on another eligible account (policy included). Otherwise wait.
5. On resume, the continuation uses the **same session** when the same worker resumes:
   - `claude -p --resume <session_id>`
   - `codex exec resume <thread_id>`

   On a different worker, it uses a continuation prompt (§7.2). For Claude-to-Claude failover across accounts, the adapter may pass `--resume <absolute transcript path>`. Claude Code accepts a `.jsonl` path; the transcript is copied into the run dir first. B2 verifies this in a spike, and the continuation prompt is the fallback.
6. Nobody has to remember anything. `limit_paused` shows a countdown everywhere, and `limit.reset` fires when the worker cools down.

### 6.5 Heartbeats, leases and hang detection (M1, M8)
- **Lease**: `lease_until = now + 120 s`. The supervisor renews it each tick while the process is alive. If `deckd` was down, leases expire and reconciliation (§6.7) re-adopts or ends them.
- **Heartbeat sources**, any of which updates `last_heartbeat`:
  - `out.jsonl` grew;
  - a card op or `deck hb`;
  - interactive hooks (PostToolUse, Stop);
  - CPU time of the process group rose by ≥ 2 s since the last tick.
- **Hang states**:
  - `suspect` after `quietMin` (default 15) with no output, card or hook. This emits `attempt.suspect` (warn) with "deepest child: `curl https://…` for 14 min". The process tree comes from `ps -o pid,ppid,pgid,etime,time,command -g <pgid>`.
  - `hung` after 2×quietMin with CPU delta < 1 s over the last quietMin → T12 (alert). Long builds burn CPU and don't trip this. A stdin wait or a stuck network call does.
- **Actions** (`retry.onHang`):
  - `restart` (default): SIGINT the group, wait 20 s, SIGTERM, wait 10 s, SIGKILL → T14, continuation attempt.
  - `block`: kill and T15.
  - `alert`: keep alive, T15 only after `attemptMin`.
- **Prevention**:
  - Every process starts with `stdin=/dev/null`.
  - Env: `CI=1 GIT_TERMINAL_PROMPT=0 GIT_EDITOR=true PAGER=cat npm_config_yes=true`.
  - Claude runs with `--permission-prompts none` (deny, don't wait).
  - Codex runs with a non-interactive sandbox preset.

### 6.6 Process supervision and orphans: `deck-run` (M1, M8)
`deckd` never runs an agent directly. It starts the agent through `deck-run`:

```
python3 -m deck.run --run-dir R --attempt-id P:T:N -- <argv…>
```

`deck-run` does the following:
1. Calls `os.setsid()`, so the agent gets its own process group and session.
2. Sets `stdin=/dev/null`, stdout → `R/out.jsonl`, stderr → `R/err.log` (line-buffered).
3. Puts `DECK_ATTEMPT=P:T:N` into the env. Every descendant inherits it, which marks it as the deck's.
4. Writes `R/pid.json` `{pid, pgid, start}`.
5. Waits for the agent, then writes `R/exit.json` `{code, signal, endedAt}` atomically (tmp + rename). This file is the **durable completion signal**: `deckd` sees the attempt finished even if it was down at the time (M1).
6. **Cleans up orphans**:
   - After a 5 s grace period it kills the remaining members of the pgid.
   - It finds escapees (processes that `setsid`/`nohup` themselves, such as dev servers) by scanning `ps eww -o pid,command` for `DECK_ATTEMPT=P:T:N`, and by the listening ports in the attempt's slot range (`lsof -nP -iTCP -sTCP:LISTEN`).
   - It kills them (SIGTERM → SIGKILL) and records `orphansKilled` and `orphan.killed` events.
   - Processes listed in the handoff's `servicesStarted` with `keep:true` are not supported in v2. Long-lived servers belong to `shell` tasks with their own lifecycle.

The supervisor reads `out.jsonl` incrementally from `out_offset` and feeds lines to `adapter.parse()`.

### 6.7 Restart reconciliation (M1, M9, C17)
On `deckd` start, for every attempt with outcome `running`:
- `exit.json` exists → process the end normally (handoff, T8–T11/T16).
- `pid` alive **and** `ps -o lstart= -p pid` equals `pid_start` → re-adopt it: renew the lease and continue tailing from `out_offset`.
- Otherwise the process is dead with no exit file (it crashed or the machine rebooted) → outcome `crashed`, then T16. The continuation resumes from `session_id` when the adapter supports it.

Afterwards it emits `daemon.reconciled` with the counts.

### 6.8 Merge queue (M6, M7)
- **One consumer per repo**, FIFO by `enqueued_at`. P1 tasks jump ahead of P2/P3, but never pre-empt a merge that has started.
- It holds the project `merge` domain for the whole step. W0's manual merges must use `deck merge <task>` or `deck domain run merge -- git merge …`.

```
merge_step(task):
  pre: main checkout has no MERGE_HEAD / REBASE_HEAD / CHERRY_PICK_HEAD, no index.lock
       else T26 "main checkout busy" (retry in 2 min) — never touch another party's merge
  1. in task worktree: git merge --no-edit <target>
       conflict → git merge --abort  (IN THE WORKTREE ONLY — our own merge) → T25 with conflicting files
  2. if handoff.migrations or project.migrationRule.paths match changed files:
       under merge (seq:db-migrations is implied) → run project.migrationRule.regenerate in the worktree → commit "regenerate migration after main"
  3. task.merge.preMerge commands; commit if changed
  4. gates (project.gates + task.merge.gates) in the worktree with a FRESH slot (≠ the attempt's slot) and its env
       → catches hard-coded ports / DB names (M7); failure → T25 with the last 60 lines
  5. in main checkout: record had_merge_head=false; git merge --ff-only task/<id> || git merge --no-edit task/<id>
       failure → only if WE created MERGE_HEAD: git merge --abort; T25
  6. success → T24; remove the worktree at the recorded path and delete the branch (only `task/` prefix, only if merged)
```
- The canonical-port check: the gate step also greps the diff of test files for `slots.canonicalPorts`. A hit is a warning in the handoff, shown to the reviewer.

### 6.9 Review SLA (M2)
- `review.requested` (action, `for: [W0]`) fires on entry.
- `review.overdue` fires at 30 min and then every 60 min (alert after 2 h).
- The inbox count is on every UI header, and the panel's collapsed bar shows "2 to review".
- Pilot tasks are labelled **PILOT**: approving one unblocks the linked scale task (T3), and the inbox shows "approving starts 108".

### 6.10 Ready backlog (always something to run)
- **Target**: `backlogTarget` (auto = total spare lanes across dispatchable accounts + 2) eligible tasks in `ready`/`queued`.
- **`backlog.low`** (action, `for: W0`) fires when eligible work drops below the target. It is deduplicated per 30 min. The text says how much capacity is unused and until when: "Spare: Codex·a 2 lanes (≈40 % until 23:10), Claude·main 1 lane — only 1 ready task. Brief 3 more (S/M sizes fit)."
- **Draft follow-ups** from handoffs (`followUps`) and pilot→scale pairs are listed as one-tap "make ready" suggestions in the W0 inbox. They never become `ready` without W0, because the brief must be validated.
- The **size-fit hint** uses the capacity model: for each account it states the largest size that fits before the reset, so W0 briefs work that can actually use the quota that is about to expire.

## 7. Adapters

### 7.1 Interface (`deck/adapters/base.py`, B2 owns; signatures frozen)
```python
@dataclass
class LaunchContext:
    project: ProjectCfg; task: dict; attempt: int; worker: WorkerCfg; account: AccountCfg
    workspace: Path; slot_env: dict[str, str]; run_dir: Path
    prompt: str                    # full prompt (§7.2) — also written to run_dir/prompt.md
    handoff_schema: Path           # schemas/handoff.schema.json
    resume_session: str | None     # session/thread id to resume, if continuing on the same account
    deck_env: dict[str, str]       # DECK_PROJECT, DECK_TASK, DECK_ATTEMPT, DECK_TOKEN, DECK_URL, DECK_STATE

@dataclass
class LaunchSpec:
    argv: list[str]; env: dict[str, str]; cwd: Path

class Signal: ...                   # union of the dataclasses below
@dataclass class SessionId(Signal): id: str
@dataclass class Activity(Signal):  kind: str; text: str          # tool call / message → heartbeat + "recent"
@dataclass class Tokens(Signal):    total: int
@dataclass class Limit(Signal):     resets_at: int | None; kind: str; raw: str   # absolute epoch or None
@dataclass class Waiting(Signal):   reason: str                    # permission/idle prompt (interactive)
@dataclass class Final(Signal):     handoff: dict | None; text: str; is_error: bool
@dataclass class ApiError(Signal):  kind: str; text: str           # e.g. overloaded, auth

class Adapter(Protocol):
    name: str                                            # 'claude-headless' | 'codex-exec' | 'interactive' | 'shell'
    def check(self, account: AccountCfg) -> tuple[bool, str]          # binary present, logged in, version
    def launch_spec(self, ctx: LaunchContext) -> LaunchSpec | None    # None for interactive (offer only)
    def parse(self, lines: list[str], now: int, state: dict) -> list[Signal]   # incremental, pure
    def usage(self, account: AccountCfg, now: int) -> UsageReading | None
```

### 7.2 Prompt (built by B2 `deck/prompt.py`, the same for all adapters)
```
You are {worker.id} working on project "{project.title}", task {task.id}: {task.title}.
1. Read and follow {rulesFiles…} (binding project rules).
2. Your brief is {brief.snapshot}. Use ONLY this path. Its "Done when" and "Stop point" are binding.
3. Workspace: {workspace} on branch task/{id}. Commit there in small commits. Never switch branches, merge, rebase main, or push.
4. Use the env vars {DECK_PORT_*}, {TEST_DB…} for any server/test DB; never hard-code ports or DB names; stop every server you start.
5. Progress: run `deck card step add|start|done`, `deck card metric`, `deck card note` (already configured; no paths needed). Steps need etaMin.
6. If you need a decision you cannot make reversibly: `deck ask "<one question>"`, then finish with outcome "blocked".
7. Stop at the Stop point. Your FINAL message must be the handoff JSON (schema enforced).
{continuation block — only for attempt > 1:}
CONTINUATION of attempt {n-1} ({reason: limit | hang | crash | review changes | reassigned}).
Done steps so far: {card steps with status done}. Last note: {note}. Next step from previous handoff: {nextStep}.
Reviewer note: {review.note}. Commits so far: {git log --oneline target..branch | head -20}.
Do not redo finished steps; keep the card (append steps, never reset).
```

### 7.3 `claude-headless`
- **Launch**:

  ```
  claude -p --output-format stream-json --verbose --session-id <uuid4> \
    --json-schema "$(cat handoff.schema.json)" --permission-mode <preset> \
    --permission-prompts none [--model M] "<prompt>"
  ```

  Env: `CLAUDE_CONFIG_DIR=<account.home>`, plus `deck_env` and `slot_env`. `--bare` is not used, because it skips the subscription login. `cwd` is the workspace.
- **Resume**: `claude -p --resume <session_id> …same flags… "<continuation prompt>"`. `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1` is set when the previous attempt was interrupted (pause, hang).
- **Parse**: the `system/init` event gives `session_id` (a SessionId check). `assistant` messages with `tool_use` → Activity. `system/api_retry` with `error:"rate_limit"` → keep counting, and after `max_retries` → Limit(None). The final `result` → Final(`structured_output` if present, `result` text, `is_error`).
- **Limit text**: a regex on `result` and stderr: `/(hit your|reached your) (5-hour|weekly|usage) limit.*?resets? (at )?(\d{1,2})(:(\d{2}))?\s*([ap]m)?/i` → Limit(resets_at computed once from `now`).
- **Stop**: SIGINT (the turn ends cleanly), then SIGTERM (exit 143).
- **Usage**: from the account's statusline file (`bin/claude-statusline.sh`, unchanged from v1).
- **Spike for B2**: confirm `structured_output` appears on the stream-json `result` line. If it does not, the fallback is the CLI `deck handoff submit` (the prompt mentions it), then the synthetic handoff (T9).

### 7.4 `codex-exec`
- **Launch**:

  ```
  codex exec --json --output-schema <run>/handoff.schema.json -o <run>/last-message.json \
    -C <workspace> (-s workspace-write -c sandbox_workspace_write.network_access=true | --dangerously-bypass-approvals-and-sandbox) \
    [-m M] "<prompt>"
  ```

  Env: `CODEX_HOME=<account.home>`.
- **Resume**: `codex exec resume <thread_id> --json … "<continuation prompt>"`. B2 checks in a spike which flags `resume` accepts. The fallback is a new `exec` with the continuation prompt.
- **Parse**: `thread.started` → SessionId(`thread_id`). `item.started` / `item.completed` (command_execution, file_change, …) → Activity. `turn.completed.usage` → Tokens. `turn.failed` / `error` whose message matches `/usage limit|rate limit|try again (at|in)/i` → Limit. `resets_at` is taken from the newest rollout `rate_limits` of that account when available, and is otherwise parsed once from the message. At exit, `-o` file JSON → Final(handoff).
- **Usage**: rollout files (v1 `usage()` logic, moved to `deck/quota.py`).

### 7.5 `interactive` (W0, or a human-driven Claude/Codex TUI)
- **There is no launch.** The dispatcher **offers** the task: T5 goes to `starting` with an `offer_expires` 15 min out, and an event (action, `for: [worker]`) says "107 is offered to W0 — run `deck claim 107`".
  - If the session does not claim it in time, T7 returns it to `queued` and the score penalises that worker for the task for 30 min.
- **`deck claim <task>`**: the account is derived from the session's home (§2.2) and the **policy is checked** (§2.3). This is T6.
- **Hooks**: `deck hooks install --account <id>` writes the following into that account's `settings.json`:
  - `PostToolUse` → `deck hb`
  - `Stop` → `deck hb --turn-end`
  - `Notification` (`permission_prompt`, `idle_prompt`) → `deck hb --waiting "<message>"`, which sends a Waiting signal: the UI shows "waiting for you" and SUP does not count it as hung.
  - `SessionEnd` → `deck hb --session-end`
- **Codex interactive**: `notify = ["deck", "hb", "--codex-notify"]` in `config.toml` (turn complete).
- **Ending**: `deck handoff submit <file|->` → T8. If the session ends with no handoff → T9 (synthetic, alert).
- **No terminal scraping** (A1).

### 7.6 `shell` (data jobs, scripts, SSH jobs to another machine)
- `argv` comes from the task (`task.command`, an extension field validated by B1 when `workerClass == ['shell']`).
- Heartbeats are output lines. Final = exit code + `deck handoff submit` if the script calls it, else a synthetic handoff from the exit code (0 → `partial`, which the reviewer confirms).

## 8. Card protocol v2 and SDK (M3)

`deck card` reads `DECK_PROJECT`, `DECK_TASK`, `DECK_ATTEMPT` and `DECK_TOKEN` from the env (set by the adapter), so workers never pass paths.

```
deck card step add "Read sources" --eta 15 [--id read]      # appends; id from slug if omitted; duplicate title → no-op
deck card step start <id|title>                             # sets status active + startedAt (daemon clock)
deck card step done  <id|title> | step fail <id> | step skip <id>
deck card step eta   <id> <min>                             # re-estimate
deck card metric "Files processed" 120 [--total 500] [--kind bad|pass]
deck card note "Profiling batch 3/8"
deck card show [--json]
deck ask "Should rows without GST number be imported as inactive?"
deck hb [--turn-end|--waiting MSG|--session-end]            # heartbeat (hooks)
deck handoff submit <file|->                               # validated against handoff.schema.json
```
- Every op is validated. Unknown flags, a missing `--eta`, a negative count, a title over 70 chars → **exit 2** with a one-line reason, and `card.rejected` (warn) appears on the UI.
- **Merge semantics**: ops patch the stored card in a transaction. Steps are never removed. A continuation attempt's `step add` appends after the existing steps (each step records its `attempt`), so a card **cannot be reset**.
- Each accepted op goes to `card_history` (append-only, trigger-enforced). The UI shows "step 3 of 7 (2 added by attempt 2)".
- **v1 compatibility** (observe mode, §15): the importer reads `progressDir/*.json` and normalises it:
  - `name`/`label` → `title`;
  - dict metrics → a list;
  - missing `etaMin` → `null` with a warning.

  Each normalisation is recorded in `card.warnings`, so the panel shows *what was wrong* instead of nothing.
- **Python SDK**: `from deck.sdk import card; card.step("Read sources", eta=15).start()`. It is a thin wrapper over the same store calls, for Python data scripts.

## 9. Handoff contract (M2, M5)

- **Source of truth**: `<runDir>/handoff.json` (schema-validated) and a row in `handoffs`.
- **Rendered**: `handoff.md` is written into the worktree at `{briefsDir}/{id}-handoff.md` and committed by the daemon as "docs: {id} handoff (attempt n)", so it merges with the work.
- **The daemon adds** `git` (commits, files changed, uncommitted count, conflicts with the target via `git merge-tree --write-tree`) and `run` (minutes, tokens, usage delta, exit code, orphans killed).
- **Review inbox item** = handoff summary, the Done-when table with met/evidence, tests, risks, **decisions needed** (each one a one-tap answer on the phone when it has `options`), and a diff stat.
- **Reviewer actions**:
  - `approve` → T21/T22.
  - `changes "<note>"` → T23.
  - `answer <decision#> <option>` is recorded and passed to the continuation.
  - `reject` → T29.
- `followUps` become `draft` tasks (`createdBy: W2`). They never become `ready` without W0.
- **Uncommitted changes** (`git.uncommitted > 0`) mark the review item amber: "N uncommitted changes will not be merged".

## 10. Events and notifications (M1, M10)

- **Format**: `event.schema.json`. Every event is a one-line `text` for humans plus typed `data`. Severity:
  - `action`: someone must act (review requested, question, account with spare capacity and eligible work (`account.spare`), backlog low (`backlog.low`)).
  - `alert`: something is wrong now (hung, policy denied, handoff invalid, merge failed, limit hit with no failover).
- **Storage**: the project `events` table plus a global mirror. Retention: 30 days of `debug`/`info`, forever for `action`/`alert`. The tables are append-only.
- **Delivery**:
  1. **Orchestrator stream**: `deck watch --for W0 [--min action] [--project p]`. It blocks, prints one line per event (`HH:MM [project] text`), and exits only on Ctrl-C. W0 runs it as a background monitor in its session, so completion is pushed, not polled (M1).
  2. **Inbox file**: `<stateDir>/inbox/<W>.jsonl` (append) for sessions that can only tail files.
  3. **SSE**: `GET /api/events?since=<id>` (`Last-Event-ID` resume) feeds the panel and the PWA. The PWA calls the browser Notification API while it is open.
  4. **macOS notification** (`osascript display notification`) for `action`/`alert`, rate-limited to one per task per 5 min.
  5. **Optional `notifyCommand`** (private config) receives the event JSON on stdin, for the owner's own channel.
- **Coalescing**: a repeat of the same `(type, task)` within 60 s updates the count instead of adding a row in the UIs.

## 11. CLI (B1 owns `deck/cli.py`; B2 adds the `run`/adapter subcommands)

Project resolution order: `--project`, then `$DECK_PROJECT`, then the project whose `repo` contains the cwd, then an error.

Identity comes from `$DECK_WORKER`, or the account from the home path. Writes from a worker attempt need `$DECK_TOKEN`.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 2 | validation error |
| 3 | policy denied |
| 4 | illegal transition / stale `rev` |
| 5 | not found |
| 6 | domain busy (with `--no-wait`) |

| Command | Purpose |
|---|---|
| `deck init` | create the machine config skeleton, state dir and token; print next steps |
| `deck project add <id> --config P --scope office\|personal [--allow-personal-accounts]` / `project list` / `project disable <id>` | manage projects (writes the private config) |
| `deck account list` / `deck usage [--json]` | accounts with scope, 5-hour/7-day %, reset times, reading age, cooling |
| `deck workers` | worker states, current task, eligible-for summary |
| `deck new "<title>" --summary … --size M --prio P1 --cap data [--kind pilot] [--deps 104] [--domains ext:pc-ssh,area:apps/api/**] [--est-pct 25] [--workers codex-exec,claude-headless]` | allocate number, create task + brief from template, open `$EDITOR` |
| `deck validate <task>` / `deck ready <task>` / `deck unready <task>` | validator / T1 / T2 |
| `deck list [--state …] [--all-projects]` / `deck board` / `deck show <task> [--json]` | views (`board` = ASCII columns) |
| `deck edit <task> --prio P1 --deps +105 --domains -ext:x --pin W2` | field edits (`rev` checked; pin checks policy) |
| `deck assign <task> <W>` / `deck reassign <task> <W>` | manual dispatch / T30, **policy enforced** (exit 3 + alert) |
| `deck pause <task>` / `deck resume <task>` / `deck cancel <task>` / `deck retry <task>` | T20 / T19 / T29 / T28 |
| `deck claim <task>` / `deck hb …` / `deck card …` / `deck ask …` / `deck handoff submit …` | worker side (§7.5, §8) |
| `deck answer <task> "<text>" \| <decision#> <option>` | T19, answer passed to the continuation |
| `deck review [list]` / `deck review show <task>` / `deck approve <task>` / `deck changes <task> "<note>"` / `deck reject <task>` | inbox |
| `deck merge <task>` / `deck merge status` | enqueue / show merge queue |
| `deck domain list` / `deck domain run <name> [--ttl 30] -- <cmd…>` / `deck domain release <name> --force` | conflict domains (held, waiting, reserved) |
| `deck capacity [--json]` | per account: lanes used/max, R5, committed, T5, urgency, largest size that fits, why nothing is admitted |
| `deck plan --dry-run` | what the planner would start right now, and why each other queued task waits |
| `deck watch [--for W0] [--min action] [--project p]` / `deck events [--since id] [--json]` | event stream (M1) |
| `deck snapshot [--overview \| --project p] --json` | the exact view models of §12 (UI fixtures, debugging) |
| `deck export board --md > docs/tasks/BOARD.md` / `deck export titles > titles.json` | generated bookkeeping (M10) |
| `deck hooks install --account <id>` | interactive hooks into a Claude config dir / Codex config |
| `deck import-v1 [--watch]` | v1 cards/runner logs → read-only "legacy" tasks (§15) |
| `deck doctor` | config checks (tags present, config not in git, CLIs logged in, ports free, worktrees vs branches, stray `DECK_ATTEMPT` processes, DB integrity) |
| `deckd [--foreground]` / `deck daemon start\|stop\|status` | daemon (`launchd` plist provided) |
| `deck pair` | show a 6-digit pairing code for the phone deck (Tailscale) |

## 12. HTTP API and view models (B1 serves; B3 consumes)

Binding: `127.0.0.1:7788`, plus the Tailscale IP when `listen.tailscale`. **GET is open on localhost.**

Over Tailscale, every request needs `Authorization: Bearer <device token>`. Tokens come from `deck pair` and are stored hashed. POSTs always need a token: the local token from the `token` file for the panel, a device token for the phone, or an attempt token for card ops.

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/overview` | `OverviewVM` |
| GET | `/api/projects/{p}` | `ProjectVM` |
| GET | `/api/projects/{p}/tasks/{id}` | `{task, card, attempts, handoff, history[-50:], events[-50:]}` |
| GET | `/api/projects/{p}/tasks/{id}/eligible-workers` | `[{worker, account, label, scope, allowed, reason}]` (policy + capability + quota) |
| GET | `/api/accounts` | `[AccountVM]` |
| GET | `/api/events?since=&project=&min=` | JSON list, or **SSE** when `Accept: text/event-stream` (`event: deck`, `id: <event id>`) |
| POST | `/api/projects/{p}/tasks/{id}/actions` | `{action: approve\|changes\|reject\|pause\|resume\|cancel\|retry\|answer\|priority\|reassign, note?, worker?, priority?, decision?, option?, rev}` → `{ok, task}` or `{ok:false, code:'policy_denied'\|'illegal'\|'stale', reason}` |
| POST | `/api/projects/{p}/tasks/{id}/card` | `{op, …}` (same ops as the CLI) |
| GET | `/` | PWA (`web/index.html`, `manifest.json`, `sw.js`) |

**OverviewVM** (abbreviated example; B3 commits a full fixture to `web/fixtures/overview.json`):
```json
{"time": 1794760000, "inbox": {"review": 2, "questions": 1, "alerts": 1},
 "projects": [
   {"id":"shop-ops","title":"Shop operations","scope":"personal","color":"#ff5a1f","icon":"🛠","enabled":true,
    "counts":{"running":2,"queued":3,"review":2,"blocked":1,"limit_paused":1,"hung":0,"doneToday":4},
    "headline":"107 pilot ready to review · W3 resumes 20:51","alerts":1},
   {"id":"client-portal","title":"Client portal","scope":"office","color":"#4f7cff","icon":"🏢","enabled":true,
    "counts":{"running":1,"queued":0,"review":0,"blocked":0,"limit_paused":0,"hung":0,"doneToday":1},"headline":"W4 on 12 · step 2/4","alerts":0}],
 "accounts": [
   {"id":"codex-a","label":"Codex · a","tool":"codex","scope":"personal","pct5":52,"pct7":40,"resets5":1794763600,
    "readAgeSec":35,"confidence":"high","state":"ok","dispatch":true,
    "capacity":{"lanesUsed":2,"lanesMax":3,"spendable5":36,"committed5":18,"minutesToReset":60,"urgency":0.30,
                "fitsSize":"M","spare":true,"why":null},
    "lanes":[{"worker":"W1","lane":1,"state":"running","project":"shop-ops","task":"106-match-receipts","title":"Match receipts to bills",
              "step":"Map columns","stepIndex":3,"stepCount":5,"etaMin":22,"lastActiveSec":20},
             {"worker":"W1","lane":2,"state":"running","project":"client-portal","task":"12-login-copy","title":"Login page wording",
              "step":"Tests","stepIndex":2,"stepCount":3,"etaMin":9,"lastActiveSec":4},
             {"worker":"W1","lane":3,"state":"free"}]},
   {"id":"claude-office","label":"Claude · office","tool":"claude","scope":"office","pct5":18,"pct7":30,"resets5":1794772000,
    "readAgeSec":240,"confidence":"high","state":"ok","dispatch":true,
    "capacity":{"lanesUsed":1,"lanesMax":2,"spendable5":64,"committed5":12,"minutesToReset":200,"urgency":0.26,"fitsSize":"L","spare":true,
                "why":"no eligible task: 3 queued tasks are personal projects (account policy)"},
    "lanes":[{"worker":"W4","lane":1,"state":"running","project":"client-portal","task":"13-export-csv"},{"worker":"W4","lane":2,"state":"free"}]},
   {"id":"claude-worker","label":"Claude · worker","tool":"claude","scope":"personal","pct5":100,"state":"cooling","resets5":1794771060,
    "confidence":"high","capacity":{"lanesUsed":0,"lanesMax":3,"spare":false,"why":"limit — resumes 104 at 20:51"},
    "lanes":[{"worker":"W3","lane":1,"state":"cooling","task":"104-vendor-model","resumeAt":1794771150}]}],
 "machine": {"running":4,"maxParallelTotal":6,"freeRamGb":5.1},
 "backlog": {"eligible":2,"target":5,"low":true,"hint":"Spare: Codex·a 1 lane (M fits before 22:42), Claude·office 1 lane (office projects only)"},
 "alerts": [{"id":991,"ts":1794759900,"type":"attempt.suspect","severity":"warn","project":"shop-ops","task":"109-stock-costs",
            "text":"109 quiet 16 min — deepest child: ssh pc 'python profile.py' for 15 min"}]}
```
**ProjectVM**: `{project:{id,title,scope,allowPersonalAccounts,color,repo:{commit,changes[]},health[]}, workers:[WorkerVM (those busy on, or eligible for, this project, each with allowed/reason)], board:{backlog:[TaskVM],upNext:[TaskVM],running:[TaskVM],review:[TaskVM],needsYou:[TaskVM],doneToday:[TaskVM]}, inbox:[ReviewVM], alerts:[Event], timeline:[Event (last 100 info+)], domains:[{name,kind,holder,since,waiting:[task],reserved:bool}], merge:{active,queue[]}, lanes:[LaneVM running for this project]}`

**AccountVM / LaneVM**: as in the Overview example. `capacity` is the planner's own output (`account_state.capacity_json`), so the UIs show exactly why an account is, or is not, getting more work.

**TaskVM**: `{id,num,title,summary,tags,priority,size,state,stateReason,worker,progress:0..1|null,step:{title,index,count,etaMin}|null,etaFinish:int|null,metrics:[{label,done,total,kind,rate,left}],lastActiveSec,warnings[],deps:[{id,state}],resumeAt,kind}`

**ReviewVM**: `{task:TaskVM, attempt, summary, outcome, doneWhen[], tests[], risks[], decisionsNeeded[], git:{commits,filesChanged,uncommitted,conflictsWithMain}, waitingSec, pilot:bool, unlocks:[id]}`

ETA, rate and time-left math is ported from v1 `with_eta` and `metrics_of` into `deck/views.py` (B1), so the panel and the PWA show identical numbers.

## 13. UI

Both UIs consume **only** §12. Neither reads files or the DB. A navigation model is shared by the panel and the deck:

```
Overview ──► Project page ──► Task detail ──► (Review sheet | Reassign sheet | Question sheet)
   ▲  tabs on project page: Workers · Board · Review · Alerts · Timeline · Repo
   └── project switcher (chips on panel, tiles/swipe on deck); "All" = Overview
```

### 13.1 Floating panel (macOS, `PanelV2.swift`; full mockup `mockups/panel.html`)
```
┌──────────────────────────────── 520 px ─┐
│ ⚙ Agent Deck            21:42  ⌄        │  header: inbox badges ▸ [2 review] [1 ?] [1 !]
│ [All] [● Shop ops 🛠 P] [● Client 🏢 O] │  project chips (scope badge P/O), All = Overview
├─────────────────────────────────────────┤
│ NEEDS YOU · 4                            │  review + questions + alerts, newest first
│  ▣ 107 PILOT Import supplier list  W2 ✓ │  [Approve] [Changes…]  (approve unlocks 108)
│  ? 110 Q: import rows without GST?       │  [Answer…]
│  ! 109 quiet 16 min — ssh pc profile.py  │  [Open] [Kill & retry]
├─────────────────────────────────────────┤
│ ACCOUNTS · PARALLEL LANES     4/6 jobs   │  machine cap
│  Codex·a  PERSONAL ▇▇▇▇▇░ 52% ↻60m 2/3   │  quota bar + reset + lanes used/max
│   W1·1 ▶ Shop · 106 Match receipts 3/5 22m│
│   W1·2 ▶ Client · 12 Login wording 2/3 9m │
│   W1·3 ○ free · M fits before 22:42  ⚠   │  spare-capacity warning (account.spare)
│  Claude·office OFFICE ▇▇░░ 18% ↻3h20 1/2 │  office account visible with real usage
│   W4·1 ▶ Client · 13 Export CSV 1/4 40m   │
│   W4·2 ○ free · office projects only      │  why it is idle (policy), not an alarm
│  Claude·worker PERSONAL ⏸ LIMIT → 20:51   │  countdown, never "100% for 24 h"
│   W3·1 ⏸ Shop · 104 resumes 20:51         │
│  Claude·main (W0) PERSONAL 41% · orchestr.│
├─────────────────────────────────────────┤
│ BACKLOG  2 ready / target 5  ▲ brief 3   │  backlog.low hint with sizes that fit
├─────────────────────────────────────────┤
│ PROJECTS                                 │  Overview only: one row per project
│  Shop ops  P  ▶2 ⏳3 ▣2 ✋1   "107 ready"  │
│  Client    O  ▶1                          │
└─────────────────────────────────────────┘
```
- The project page swaps PROJECTS for **BOARD** (Running, Up next, Review, Needs you, Done today), **DOMAINS & MERGE** and **REPO & HEALTH**.
- Task cards keep the v1 look: orange number chip, steps, metrics, ETA, and raw activity collapsed.
- The collapsed bar: `▶4 running · ▣2 review · ! 1 · Claude·w LIMIT 20:51`.
- Actions work in place: Approve, Changes (text field), Answer, Pause/Resume, Reassign (sheet with greyed policy rows), Priority.
- The panel uses the local token, read from `<stateDir>/token`.

### 13.2 Phone deck (PWA served by `deckd`; mockup `mockups/deck.html`)
- **Look**: phonedeck's **Studio** language (owner's PhoneDeck):
  - warm grey hardware body with corner screws, a dark **LCD** status block (Doto digits, scanlines, glow);
  - chunky keys with a 2 px ink border and a 4 px drop that press down on tap;
  - JetBrains Mono labels in spaced caps, Space Grotesk titles, orange `#ff5a1f` accent.
- **Install**: installable (`manifest.json`, `display: standalone`, portrait-first, landscape grid on tablets). Fonts are self-hosted in `web/fonts/`, with no CDN at runtime.
- **Screens**:
  1. **Overview**:
     - LCD: clock, number running, inbox count, the soonest reset.
     - **Project tiles**, big coloured keys with icon, scope badge, counts and headline. Tap → project page.
     - **Accounts** strip with LCD bars, office included, scope badges.
     - "Needs you" list.
  2. **Project page**:
     - LCD with the project name and its counts.
     - Segmented keys: WORKERS · BOARD · REVIEW · ALERTS · TIMELINE · REPO.
     - Swipe left/right to the next project.
  3. **Task detail**: steps with ETA, metrics bars, attempts, last events, and action keys: PAUSE, RESUME, REASSIGN, PRIORITY ▲▼, CANCEL (hold to confirm).
  4. **Review sheet**: summary, Done-when ✓/✗ with evidence, tests, risks, decision chips (one tap), and **APPROVE** / **CHANGES** keys.
  5. **Reassign sheet**: every worker as a key. Disallowed workers are shown **greyed with a lock icon and the reason**, e.g. "Office account — never runs personal projects" or "Office project doesn't allow personal accounts"; they are never hidden.
  6. **Accounts & lanes**:
     - LCD: jobs running / total lanes, the machine cap, free RAM.
     - One block per account: scope badge, a 5-hour bar with reset countdown, lanes used/max, and each lane as a row (running task + ETA, ⏸ cooling with resume time, or a free lane with the planner's reason: "M fits before 22:42", "only office projects", "waits on ext:pc-ssh").
     - The backlog bar and spare-capacity cards with one-tap "make ready" for drafts.
- **Updates**: SSE with `Last-Event-ID`. When disconnected, the deck shows an "OFFLINE · last update 21:40" LCD badge.
- **Actions**: POST with the device token. On a policy error it shows the server's reason verbatim.

### 13.3 Accessibility and size
- Tap targets are at least 56 px, text at least 14 px, and colour is never the only cue: states have icons and words.
- PWA budget: under 60 KB of JS+CSS without fonts, and no framework. Vanilla JS modules: `api.js`, `store.js`, `views/*.js`.

## 14. Security and privacy
- **Network**: none except localhost and optional Tailscale. CORS is off, and `Host` is checked against the allowed hosts.
- **Secrets**: never stored. The only token files are `token` (0600) and `devices.json` (hashed).
- **Logs**: run dirs hold agent output. They stay in `stateDir`, never in a repo. Retention defaults to 14 days, then they are compressed. Nothing is ever deleted without `deck gc --yes`.
- **Repo contents**: no project data in this repo. Examples use neutral names (`shop-ops`, `client-portal`), and tests use temp dirs.

## 15. Migration from v1

| Stage | What runs | Exit criterion |
|---|---|---|
| S0 (P2 done) | v1 panel unchanged. `deckd` in **observe mode**: `deck import-v1 --watch` mirrors v1 cards and runner logs as `legacy` tasks (read-only, state from card status). The PWA and PanelV2 can be tried side by side. | v2 views show the same running/finished items as v1 for 1 day |
| S1 | New tasks are created with `deck new` + `deck ready`. W0 dispatches by hand with `deck assign` (adapters replace the per-project runner script for new tasks). Old runs finish under the old runner. `deck card` also writes a v1-format card file (`legacy.progressDir`) so the v1 panel still shows v2 tasks. | 5 tasks through review and merge with no manual bookkeeping |
| S2 | Auto-dispatch is on for **one** account with `lanes: 1` (`accounts[].dispatch` toggle in the machine config), then all accounts, then lanes raised to 2–3 once T-CAP numbers match reality (compare predicted vs actual `usage_cost`). W0 runs `deck watch --for W0`. | 1 day with no `account.spare` > 10 min and no missed review > 30 min |
| S3 | BOARD.md and `titles.json` are generated by `deck export` (committed by W0 when wanted). The runner script is retired for repo tasks; it may stay for non-repo/PC jobs as `shell` tasks. | — |
| S4 | The v1 `status.py` panel is retired. `start.sh` starts `deckd` + PanelV2. v1 files move to `legacy/` (kept). | owner OK |

Rollback at any stage: stop `deckd` and the v1 scripts and panel keep working, since v1 files are never modified before S4.

## 16. Test plan

The tooling is `python3 -m unittest`, with no network. Temp dirs serve as stateDir and repos. A **fake clock** (`deck.clock.now`, injectable) and **fake CLIs** (`tests/fakes/fake-claude`, `fake-codex`) replay recorded JSONL fixtures. CI: GitHub Actions on macOS for Swift build plus Python tests (this repo is public; no secrets needed).

| Id | Area | Cases |
|---|---|---|
| T-SM | State machine (B1) | table-driven: **every** row of §5.2 succeeds with its guard satisfied, fails with it violated; every *unlisted* (from,to) pair raises `IllegalTransition`; rev mismatch → exit 4; terminal states |
| T-VAL | Brief validator (B1) | fixtures per rule V1–V12 (pass + fail); snapshot/sha; edit-after-ready → T2 |
| T-CARD | Card protocol (B1) | ops; schema rejects (exit 2); merge never removes steps; continuation appends; history append-only (UPDATE/DELETE raise); v1 normaliser (`name`, `label`, dict metrics) with warnings |
| T-POL | **Account policy** (B1 + B2) | full matrix of §2.3 (office×personal **deny**, office×office allow, personal×office with/without `allowPersonalAccounts`, untagged account, untagged project); `lease()` denies + `policy.denied` alert even when the dispatcher filter is bypassed (direct call); `deck assign`/`reassign`/`edit --pin` exit 3 + alert; `claim()` from an office home into a personal project denied; adapter launch re-check (account swapped in config between lease and launch → aborted); limit **failover never picks an office worker for a personal task**; eligible-workers endpoint returns `allowed:false` + reason; property test: random configs × random tasks → no attempt row ever has (office account, personal project) |
| T-DSP | Planner (B2) | candidate ordering keys; fair share across 2 projects; capability/workerClass; pin; one job per account per round, rounds repeat until nothing is admitted; resume prefers own account; **re-plan on completion**: `attempt.ended` → next job leased within 1 s (fake clock) without waiting for the 5 s tick |
| T-CAP | **Capacity / parallel** (B2) | (a) account at 20 % with 3 lanes and 3 queued S tasks → 3 jobs start in parallel; (b) the same account at 80 % → only as many as `committed5 + burn × min(T5, D) ≤ R5` allows; (c) **about-to-reset preference**: account A at 80 % resetting in 10 min (R5 = 7, L burn ≈ 0.22 %/min × 10 = 2.2 ≤ 7 → admitted) vs account B at 40 % resetting in 4 h (urgency 47/240 ≈ 0.20 < A's 7/15 ≈ 0.47) → the next L task goes to A; a 4th L task on A at 90 % with 60 min to reset (R5 = −3) is refused; (d) weekly ceiling blocks non-P1, P1 gets 5-point grace; (e) `parallel.max`, `maxParallelTotal` and `minFreeRamGb` caps; (f) unknown reading → max 1 job; (g) learned est/D from `usage_cost` medians, defaults when empty; (h) a limit on one lane → account `cooling`, no new admits, other lanes untouched; (i) property test: random readings/tasks → never more running jobs than `parallel.max`, never a projected `committed5 > R5` at admission |
| T-DOM | **Conflict domains** (B1 + B2) | glob overlap table (`apps/api/**` vs `apps/api/src/x/**` overlap; `apps/api/**` vs `apps/web/**` don't; `**` overlaps all); all-or-nothing acquisition in sorted order (no deadlock with 2 tasks × 2 domains in opposite order); `ext:` exclusive **across projects**; `seq:db-migrations` parallel by default, two tasks both adding "0030" → merge queue renumbers the second; strict `capacity:1` serialises; `merge` reserved (validator V6); anti-starvation reservation after 30 min; area derivation from brief paths (V13) |
| T-SPARE | **Spare capacity + backlog** (B2) | account with capacity, 0 running jobs, eligible queued task blocked by nothing → job starts (no alert); eligible task exists but planner cannot place it for 5 min (fake clock) → `account.spare` with the blocking reason (domain/policy/capability); office account idle because only personal tasks are queued → reason "account policy", severity info (not action); eligible backlog < target → `backlog.low` once per 30 min with size-fit hint; alert clears when a job starts |
| T-QUO | Quota (B2) | Claude statusline & Codex rollout fixtures; window rollover → 0 (low confidence); stale; **regression M4**: a log containing "resets 8:50pm" read again after resume/next day never yields a limit (offset parsing + `cleared_at`); limit computed once with fake clock across midnight |
| T-LIM | Limit pause/resume (B2) | Limit signal → limit_paused + cooling; timer fires at resetsAt+90 s → queued → same worker resumes with `--resume`; `auto` failover when reset > 45 min and headroom exists; `wait` policy |
| T-HANG | Heartbeats (B2) | fake agent that goes silent with no CPU → suspect → hung → restart; silent but CPU-busy (build) → not hung; stdin read gets EOF immediately; interactive `Waiting` not hung |
| T-ORPH | Orphans (B2) | fake agent spawns `python -m http.server` with `setsid` and `nohup` → killed after exit via `DECK_ATTEMPT` env + slot port; count recorded |
| T-REC | Reconcile (B2) | kill `deckd` mid-attempt; agent finishes while daemon down → `exit.json` processed on restart; alive agent re-adopted (pid_start match); PID reuse (start mismatch) → crashed |
| T-MQ | Merge queue (B2) | temp git repo: clean merge; conflict in worktree → aborted in worktree only, T25; **main checkout with someone else's MERGE_HEAD → untouched**, T26; two tasks generating migration "0030" → second regenerated to "0031" under `db-migrations`; gate runs in a different slot (a test hard-coding the attempt's port fails) |
| T-ADP | Adapters (B2) | parse fixtures for claude stream-json (init, tool_use, api_retry rate_limit, result with structured_output, limit text) and codex `--json` (thread.started, item.*, turn.completed, turn.failed limit); argv builders (golden files); interactive claim + hooks |
| T-API | HTTP (B1) | every endpoint vs JSON fixtures; SSE resume by Last-Event-ID; token rules (localhost GET open; POST needs token; Tailscale needs device token); policy error shape |
| T-UI | UI (B3) | PWA renders `web/fixtures/*.json` (overview, project, review, reassign with greyed office row) in headless Chromium via a tiny script if Playwright is present (optional); Swift: `swiftc -typecheck PanelV2.swift`; a decode test of fixtures in a Swift test target |
| T-MIS | **Mistake scenarios** (B2, end-to-end with fakes) | one test per M1–M10 reproducing the original failure and asserting the new outcome (§18 column "Becomes") |

## 17. Work split: three parallel build tasks

All three start from this design. **Hour 0 contract freeze**: B1 commits `deck/store_api.py` (signatures below as stubs that raise `NotImplementedError`), `deck/views_types.py` (TypedDicts for §12) and the schemas as `deck/schemas/*.json`, copied from `docs/v2/schemas`. B2 and B3 code against those. B3 starts from fixtures and does not need B1's stubs.

### B1: Core store, protocols, CLI and API (P2a)
- **Owns**: `deck/store.py`, `deck/states.py`, `deck/policy.py`, `deck/brief.py`, `deck/cards.py`, `deck/domains.py`, `deck/events.py`, `deck/views.py`, `deck/server.py`, `deck/cli.py` (all but `run` and adapter commands), `deck/migrations/*`, `deck/schemas/*`, `templates/brief.md`, and tests T-SM, T-VAL, T-CARD, T-POL (store/CLI/API parts), T-DOM (overlap + acquisition), T-API.
- **Store API it must provide** (frozen):

  ```python
  open_project(pid) -> ProjectStore ; open_global() -> GlobalStore
  ProjectStore.create_task(fields, by) -> task ; get(id) ; list(states=None)
  ProjectStore.transition(id, to, *, actor, reason, rev=None, **effects) -> task       # raises IllegalTransition, StaleRev
  ProjectStore.lease(id, worker, lane, account, *, slot, domains, now) -> Attempt              # policy + guards inside BEGIN IMMEDIATE; raises PolicyDenied
  ProjectStore.claim(id, account_home, worker) -> Attempt                              # interactive; policy
  ProjectStore.update_attempt(task, n, **fields) ; end_attempt(task, n, outcome, exit_code)
  ProjectStore.card_op(task, attempt, op, args, token) -> card                         # raises CardRejected
  ProjectStore.put_handoff(task, n, data, valid) ; review(task, decision, by, note)
  ProjectStore.domains: try_acquire(names, holder, ttl) -> (bool, blocker|None) ; release_all(holder) ; renew(holder) ; overlaps(glob_a, glob_b) -> bool
  ProjectStore.slots: take(holder) -> int|None ; release(holder)
  ProjectStore.events.emit(type, severity, text, task=None, attempt=None, worker=None, for_=(), data=None)
  GlobalStore.usage.put(reading) ; latest(account) ; limits.add(...) ; limits.active(account, now)
  GlobalStore.lanes.take(worker) -> lane|None ; release(worker, lane) ; GlobalStore.account_state.put(account, state, capacity) ; ext_domains (same API as domains)
  policy.allow(account_cfg, project_cfg) -> (bool, reason)
  views.overview(now) -> OverviewVM ; views.project(pid, now) -> ProjectVM ; views.task(pid, id) ; views.eligible(pid, id)
  ```
- **Done when**: every test listed above passes; `deck new → ready → assign → card → handoff submit → approve` works with a fake worker; `deck snapshot --overview --json` validates against `views_types`.

### B2: Runtime: dispatcher, adapters, supervision and merge queue (P2b)
- **Owns**: `deck/dispatcher.py`, `deck/supervise.py`, `deck/run.py` (`deck-run`), `deck/capacity.py` (pure planner maths of §6.2), `deck/quota.py` (ported from v1 `usage()`/`claude_limit_from_logs` with the absolute-time fix), `deck/adapters/{base,claude_headless,codex_exec,interactive,shell}.py`, `deck/prompt.py`, `deck/mergeq.py`, `deck/import_v1.py`, `deckd` main loop, `deck hooks install`, `launchd/agent-deck.plist`, `tests/fakes/*`, and tests T-DSP, T-CAP, T-DOM (planner side), T-SPARE, T-QUO, T-LIM, T-HANG, T-ORPH, T-REC, T-MQ, T-ADP, T-POL (dispatcher/launch/failover parts), T-MIS.
- **Spikes first, max 1 h, results written into the PR description**:
  1. Does `claude -p` stream-json `result` include `structured_output` with `--json-schema`?
  2. Which flags does `codex exec resume` accept (`--json`, `--output-schema`, `-o`)?
  3. Does `claude --resume <path.jsonl>` work across `CLAUDE_CONFIG_DIR`s?
- **Depends on** B1's store API stubs. Until they land, it uses an in-memory `FakeStore` implementing the same API (in `tests/fakes/store.py`, kept afterwards for unit tests).
- **Done when**: the T-* tests above pass with fake CLIs; one real smoke run per adapter in a scratch repo (documented commands; not in CI).

### B3: UI: PWA deck and floating panel v2 (P3)
- **Owns**: `web/` (`index.html`, `manifest.json`, `sw.js`, `app.js`, `api.js`, `views/*.js`, `styles.css`, `fonts/`, `icons/`), `web/fixtures/{overview,project,task,review,eligible}.json`, `PanelV2.swift`, the `start.sh` v2 flag (`./start.sh --v2`), and T-UI.
- **Contract**: only §12 endpoints and shapes. It develops with `python3 -m http.server` serving `web/` and fixtures (`?fixtures=1` makes `api.js` read `fixtures/*.json`).
- **Must show**: Overview and project pages with navigation; **per-account parallel lanes** (W1·1…W1·n, free lanes with the planner's `why` / size that fits), machine jobs x/max, backlog bar; accounts with scope badges and the office account; review sheet; reassign sheet with greyed, reasoned policy rows; countdowns for `limit_paused`; alerts; offline state.
- **Done when**: the fixtures render in both UIs, matching `mockups/*.html` in structure and Studio style; actions POST the right bodies (verified against a stub server); PanelV2 typechecks and decodes the fixtures.

### Integration (W0, after B1–B3)
Point the PWA and PanelV2 at the real `deckd` and run the T-MIS scenarios together, then S0 → S1 from §15. Merge order: B1 → B2 → B3, each rebasing on the previous merge. File ownership is disjoint by construction.

## 18. Mistakes 1–10: design trace

| M | Mechanism (sections) | Becomes | Test |
|---|---|---|---|
| 1 Finished but nobody told | `deck-run` exit file + supervisor + `review.requested`/`account.spare` events, immediate re-plan on completion + `deck watch --for W0` + panel/phone badges (§6.6, §6.7, §10) | impossible + loud | T-REC, T-MIS-1 |
| 2 Pilot unreviewed 6 h | `review` state, SLA alerts, pilot gate, inbox on all UIs (§6.9, §9) | loud (alert at 30 min) | T-MIS-2 |
| 3 Wrong card keys / stale / reset | card schema + CLI only + merge-only + history; v1 normaliser warnings; stale = heartbeat-based states (§8) | impossible | T-CARD |
| 4 Limit misread | absolute `resetsAt` once, offset parsing, `cleared_at`, effective usage rules (§6.3) | impossible | T-QUO regression |
| 5 Wrong brief path / no Done-when / rules mid-task | validator, snapshot path passed by the daemon, rules injected, `deck ask` (§4, §7.2) | impossible | T-VAL, T-MIS-5 |
| 6 Migration number clash / merge-abort clash | merge queue, `merge` domain, `area:` overlap rules, `db-migrations` regenerate, never abort a foreign merge (§6.8) | impossible | T-MQ |
| 7 Tests hard-coded to a DB/port | slots + env templates, gates in a *different* slot, canonical-port grep (§6.8) | loud at review | T-MQ |
| 8 Hung jobs / orphans | stdin `/dev/null`, non-interactive env, heartbeat + CPU hang detection, pgid + env-marker + port cleanup (§6.5, §6.6) | impossible / loud | T-HANG, T-ORPH |
| 9 Limit mid-task, manual resume | `limit_paused` durable timer, same-session resume, auto failover (§6.4) | impossible | T-LIM |
| 10 Bookkeeping instead of review | `deck new` numbering + template, dispatcher picks workers, generated board/titles, events pushed (§4, §6.2, §11) | removed | T-MIS-10 |

**Owner additions**: multi-project Overview plus project pages (§2.1, §12, §13); all accounts, including the office one, with usage (§2.2, §6.3); office/personal policy enforced in lease, claim, CLI, launch and UI, with alerts (§2.3, T-POL); **parallel jobs per account** from a quota/time-to-reset/size capacity model that prefers about-to-reset quota, conflict domains, immediate re-plan on completion, `account.spare` and `backlog.low` alerts, ready backlog target (§5.3, §6.1, §6.2, §6.2.1, §6.10, T-CAP, T-DOM, T-SPARE).

## 19. Decisions flagged for W0/owner (reversible defaults chosen)
1. Python stdlib daemon rather than Node or Swift. The default is Python, for continuity with v1 and zero install.
2. Per-project SQLite plus a global DB, rather than one DB. The default is per project, following the owner's "each project = own store".
3. Review is required for every task except chores with `merge.auto`. Default on.
4. Hang `quietMin` is 15 min and the review SLA 30 min. Both are configurable per project.
5. Phone notifications only while the PWA is open (no push service). `notifyCommand` covers the rest.
6. The office account defaults to `dispatch: false` (show usage only) until the owner enables it for office projects.
