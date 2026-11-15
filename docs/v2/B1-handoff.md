# B1 handoff: core store, protocols, CLI and API

Branch `v2-b1` (worktree `agent-monitor-b1`). Python 3.9+ standard library only. Reassigned from a worker that hit its limit before committing; everything here was written from scratch against `design.md`.

## Commits
1. `f723f3f` **Contract freeze**: `deck/store_api.py`, `deck/views_types.py`, `deck/config.py`, `deck/errors.py`, `deck/clock.py`, and `deck/schemas/*.json` (byte-identical copies of `docs/v2/schemas`). B2 and B3 code against this commit.
2. The store, the state machine table, the account policy, conflict domains, events and the card protocol.
3. The brief validator, view models, the HTTP API (with SSE and tokens) and the `deck` CLI.
4. to 7. Tests, plus small fixes the tests found.

## What was built
| File | What it does |
|---|---|
| `deck/store.py` | `ProjectStore` / `GlobalStore` on SQLite (WAL, nested `BEGIN IMMEDIATE`, after-commit and after-rollback hooks). `transition()` is the only place state changes. `lease()` and `claim()` check the policy inside the store. Also: task numbering (`seq`, never reused), attempts with sha256 attempt tokens, cards with append-only history, handoffs (followUps become `draft` tasks), review, merge-queue rows, usage, limits, lanes (capped by `worker.lanes` and `account.parallel.max`), account_state, slots. |
| `deck/states.py` | The §5.2 table as data: rows T1–T30 plus T1x, with actors and guard names. `BOARD_COLUMN`, `HOLDING`, `TERMINAL`. |
| `deck/policy.py` | `allow(account, project) -> (bool, reason)`. It is pure and fails closed. **office × personal is hard-coded to DENY**; no flag overrides it. Accepts dicts or config objects. |
| `deck/domains.py` | `area:` overlap (directory-aligned literal prefix; when unsure it counts as overlap). `seq:`/`res:` capacities from the project config (`seq` defaults to 0 = unlimited). `merge` has capacity 1. `ext:` lives in `global.db`, so it is machine-wide, and its holder is `<project>/<task>`. Acquisition is all-or-nothing in sorted order. TTL and renew. A task that has waited 30 minutes reserves the domain and emits `domain.waiting`. |
| `deck/events.py` | Append-only project log (a trigger blocks UPDATE and DELETE). The global mirror is written after commit, then `<stateDir>/inbox/<W>.jsonl` for every `for` entry, then `<stateDir>/wake` is touched. |
| `deck/brief.py` | Template (`templates/brief.md`), section parser, validator rules V1–V13, `ready()` (snapshot to `runs/<p>/<task>/brief.md`, sha256, doneWhen and stopPoint parsed, areas derived), `check_changed()` (T2, or only a warning while running). |
| `deck/cards.py` | Pure `apply(card, op, args, attempt, now)` with strict validation. Steps are never removed. Continuation steps carry their own `attempt`. `normalize_v1()` records each fix in `warnings`. |
| `deck/views.py` | Overview, Project, Task, Review, Account and Lane view models. The ETA maths is ported from v1 `with_eta`. Metric rate and time left come from `card_history`, so they survive a restart. |
| `deck/server.py` | HTTP API per §12, an SSE stream with `Last-Event-ID`, and the token and Host rules of §14. Adds `POST /api/pair` (see below). |
| `deck/cli.py`, `bin/deck` | Every §11 command B1 owns (list below). Exit codes 0/2/3/4/5/6. |
| `deck/schema.py` | A ~120-line JSON Schema subset validator. |
| `deck/migrations/{project,global}/001.sql` | The tables from §3.2 and §3.3, the append-only triggers, and an `ext_domain_waits` table (needed for anti-starvation across projects). |

CLI commands implemented: `init`, `project add/list/disable`, `account list`, `usage`, `workers`, `new`, `validate`, `ready`, `unready`, `list`, `board`, `show`, `edit`, `assign`, `reassign`, `pause`, `resume`, `cancel`, `retry --attempts +N`, `claim`, `hb`, `card step add/start/done/fail/skip/eta`, `card metric/note/show`, `ask`, `handoff submit`, `answer`, `review [list|show]`, `approve`, `changes`, `reject`, `merge <task>|status`, `domain list/run/release`, `events`, `watch`, `snapshot`, `export board|titles`, `doctor`, `pair`, and `serve` (the HTTP API only).

## Tests: 135, all passing (`python3 -m unittest discover -s tests -t .`), on Python 3.14 and 3.9.6
| Suite | Tests | Covers |
|---|---|---|
| `test_states` (T-SM) | 41 | **Every row T1–T30 + T1x**, each with its guard satisfied and violated, and a meta-test that fails if a row has no test. Every unlisted (from, to) pair × 4 actors raises `IllegalTransition`, and a refused transition writes nothing. Also: stale rev (exit 4), terminal states, holdings released (domains, ext, slot, lane), and the full happy path. |
| `test_policy` (T-POL) | 17 | **The full §2.3 matrix** (15 rows, as dicts and as objects), including office × personal with `allowPersonalAccounts:true`, which is still denied. `lease()` called directly with an office account on a personal project raises `PolicyDenied`, writes no attempt row, and records a `policy.denied` alert (kept although the transaction rolls back). Also: worker/account mismatch, untagged account, untagged project, pin, reassign, claim from an office home, the eligible list, and the CLI `assign`/`edit --pin`/`reassign`/`claim` paths (exit 3 plus an alert). A **property test** runs 160 random lease and claim attempts, some lying about the account, and asserts that no attempt row ever has an office account on a non-office project. |
| `test_brief` (T-VAL) | 22 | V1–V13 pass and fail, snapshot and sha, T1x recording, edit after ready → draft, edit while running only warns, template, CLI new/validate/ready/unready, numbers never reused. |
| `test_cards` (T-CARD) | 14 | Ops, 12 kinds of reject (exit 2 plus `card.rejected`), token required, limits, a card op counts as a heartbeat, continuation appends (and the old token stops working), append-only triggers, ask/answer, the v1 normaliser, and the CLI fake-worker flow `new → ready → assign → card → handoff submit → approve`. |
| `test_domains` (T-DOM) | 13 | Overlap table (12 pairs, both directions), all-or-nothing, opposite-order no-deadlock, `ext:` across projects, `seq` parallel vs strict, `merge` cap, TTL and renew, the 30-minute reservation, lease → `DomainBusy` (exit 6), slots, `deck domain run/list/release`. |
| `test_api` (T-API) | 16 | Every endpoint checked with `views_types.check_vm`. SSE resumes by `Last-Event-ID`. A POST with no token or a wrong token gets 401. Host check returns 403. A remote request needs a device token; the pairing code works once, and the token is stored hashed. Error shapes: `policy_denied` 403, `illegal`/`stale` 409, `invalid` 400. Card POST needs the attempt token. |
| `test_cli` | 8 | `snapshot` validates against `views_types`; list, board, show, export, edit, events, `watch --for W0`, the inbox file, doctor, retry, workers, init (0600 files). |
| `test_contract` | 4 | Schemas byte-identical to `docs/v2/schemas`; the store implements every `store_api` method with matching leading parameters. |

## Notes for B2 (runtime)
- **Store entry points**: `store.open_project(pid, cfg, g=None)` and `store.open_global(cfg)`. Share one `GlobalStore` across project stores (`ProjectStore(pid, cfg, g)`). Connections are not thread-safe; use one lock per process (as `server.DeckApp` does).
- **Actors**: pass `actor="DSP"|"SUP"|"ADP"|"MQ"|"TMR"`, `"W0"`, `"owner"` or a worker id (a worker id counts as WRK). The table enforces the actor, so call `transition(..., actor="SUP")` for supervisor moves.
- **Effects that guards read**:
  - T7, T16: `outcome='crashed'|'timeout'`.
  - T11: `resets_at` (absolute epoch or None, which means now + 60 min) and `kind`. This sets `resumeAt = resets + 90`.
  - T12: `cpu_delta` (seconds over the last quietMin; default 0). The guard also checks `lastHeartbeat` age.
  - T14: `killed=True`.
  - T18: `failover=True` (this also clears `pinWorker`).
  - T25: `note` (the conflict files or gate tail) and `event_emitted=True` if you already emitted `merge.conflict`.
  - T26: sets `resumeAt = now + 120` for the retry.
  - T30: `worker`.
  - Use `t="T16"` and similar to pick a row when two rows share a (from, to) pair.
- **`lease(id, worker, lane, account, *, slot=None, domains=(), now=None, adapter=None, continuation_of=None)`**:
  - It **acquires the task's domains itself**, all-or-nothing; anything already held by the task is fine. It raises `DomainBusy` (with `.data['blocker']`) if they are busy, and it takes a slot if `slot is None`.
  - It marks the global lane `running` after commit.
  - It does **not** run `capacity.admit`. That stays in the planner, because the frozen signature has no capacity input.
  - It returns the attempt dict with a one-time plain `token`. Put it in `DECK_TOKEN`; the store keeps only a sha256.
  - It does not check `account.dispatch`; the planner must filter on it.
- **Lanes**: `g.lanes.take(worker, project, task, attempt)` returns the lowest free lane, honouring `worker.lanes` and `account.parallel_max`. `lease()` re-marks the lane, and leaving a holding state releases it automatically. `limit_paused` keeps the **slot** but releases the domains and the lane.
- **Leaving a holding state is automatic**. Every move out of `starting`, `running` or `hung` releases the project and ext domains, the slot (except into `limit_paused` or `blocked` by pause) and the lane. It also ends a `running` attempt with the matching outcome. You only need `end_attempt()` to record `exit_code` and emit `attempt.ended`.
- **Handoffs**: `put_handoff(task, n, data)` validates against the schema and returns `{valid, errors, data}`. Call it before `transition(..., "review")`: T8 needs a valid handoff with outcome done, partial or failed; otherwise T9 inserts the synthetic `unknown` handoff and alerts. `deck handoff submit` only moves the task itself for `interactive`/`shell` attempts; for headless attempts the adapter's `Final` drives the move.
- **Heartbeats**: `store.heartbeat(task, n=None, **data)`. Card ops also bump `last_heartbeat`. `deck hb --waiting MSG` only logs `data.waiting` (there is no column); B2 can read it from `attempt.heartbeat` events if needed.
- **Other hooks**:
  - `deck assign` pins the task and queues it (T3 as DSP). It never leases, so the dispatcher must honour `pinWorker`.
  - `deck merge <task>` approves the task (T21) and inserts a `merge_queue` row with state `queued`. MQ sets `active`/`done`/`failed`/`waiting`.
  - CLI writes touch `<stateDir>/wake`. `cli.daemon_running()` reads `<stateDir>/deckd.pid`; deckd should write that file.
- **Extending the CLI**: register B2 subcommands through `deck.cli.EXTRA_COMMANDS[name] = (setup(subparser), handler(ctx, args))`. `deck serve` runs only the HTTP API; deckd should call `server.make_server(cfg)` in a thread.
- **Not done in B1**, as listed for B2 in design §17: macOS notifications and `notifyCommand` delivery, review SLA events, the `account.spare` and `backlog.low` events, and event retention.

## Notes for B3 (UI)
- The contract is `deck/views_types.py` (TypedDicts, `REQUIRED` keys, `check_vm()`, `ROUTES`, `ACTIONS`). `deck snapshot --overview --json` and `deck snapshot --project <p> --json` print real view models that pass `check_vm`.
- **Additions beyond §12**, all optional:
  - TaskVM: `note`, `question`, `project`.
  - ReviewVM: `valid`.
  - TaskDetailVM: `view` (the TaskVM of the task).
  - Project: `icon`, `enabled`.
  - WorkerVM: `adapter`, `state`.
  - DomainVM: `holders` and `expires` on the internal list.
  - `merge.queue[]` items are `{task, state, enqueuedAt, note}`.
  - Health entries have `ok: null` (B1 makes no network calls).
- **Units**: `metrics[].rate` is per minute and `left` is in minutes (numbers, not v1 strings). `etaFinish` is an absolute epoch.
- **Capacity**: `AccountVM.capacity` is whatever the planner wrote to `account_state`. Until B2 runs, it is `{lanesUsed, lanesMax, spare:false, why}`. `why` is "dispatch off (usage shown only)" for `dispatch:false` accounts, or "limit — resumes HH:MM" while cooling.
- **Actions** (`POST …/actions`):
  - The actor is always `owner`.
  - `retry` follows the table (T28 is W0-only), so from the panel or phone it returns 409 `illegal`. Grey it out, or flag it for W0 if the owner should be allowed (a one-line table change).
  - `resume` moves `blocked → queued` (T19).
  - `reassign` sends T30 for a running task and a pin edit otherwise. Both are policy-checked: 403 `{ok:false, code:'policy_denied', reason}`.
  - `rev` is optional, and a mismatch returns 409 `stale`.
- **Pairing**: `deck pair` prints a 6-digit code. The phone POSTs `/api/pair {code, name}` (allowed without a token, from any address) and gets `{ok, token}` back once. Use it as `Authorization: Bearer`.
- **SSE**: `GET /api/events` with `Accept: text/event-stream` sends `event: deck`, `id: <global id>`, `retry: 3000`, and a `: ping` every 15 s. `?timeout=` (seconds) exists for tests. The ids are global ids, not project-local ones.
- **Static files**: `GET /` serves `web/` from the repo root (B3's files). Without `web/` it returns a placeholder page.

## Decisions taken (reversible, flagged for W0)
1. `edit` with a list prefix needs the `=` form for removals: `--domains=-area:x/**` (argparse otherwise reads `-area…` as an option).
2. A worker with an empty `capabilities` list is treated as able to do any capability. Configs should list capabilities.
3. `has_branch` is false for `isolation: none` or `main-checkout`, and for capability `research`/`review`; those take T22 on approve.
4. Each review round with "changes" grants one extra attempt. It is counted from `review.decided` events, not stored as a column.
5. `deck assign` pins and queues; it never leases (see the B2 notes).

STOP: B1 is complete, and B2 and B3 integrate against it.
