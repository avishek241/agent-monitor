# W0 integration: B1 + B2 on `v2-int`

Branch `v2-int` (worktree `agent-monitor-int`), started from `v2-b1` (`b6e4f79`). Not pushed. B3 (`v2-b3`, the UI) is still being built and is **not** merged here; what it needs is listed at the end.

## What was merged
1. **B2, rebased without its TEMP snapshot.** `v2-b2` was copied to `v2-b2-rebased` (`v2-b2` itself is untouched). The spikes commit `dd8f752` was cherry-picked onto `v2-b1` first, then `git rebase --onto <that commit> fc6c50c v2-b2-rebased`. This is the handoff's rebase plus cherry-pick, done spikes-first because B2's first runtime commit edits `deck/adapters/schemas.py`, which the spikes commit creates. The resulting tree is the same.
   - Conflict: B2's first commit adds `tests/helpers.py`, which B1 owns. It was resolved by putting B2's version at `tests/b2_helpers.py`, as B2's later rename commit does. On replay, that rename commit deleted B1's `tests/helpers.py`, so commit `969c50d` restores it.
   - Check: every B1 file is byte-identical to `v2-b1`. The only B2 files that differ from `v2-b2` are the TEMP stand-ins that were dropped (`policy.py` is now B1's real one).
   - The merge commit is `5c899b4` (`--no-ff`), and 237 tests passed at that point.
2. **`deck/cli.py`** imports `cli_runtime` right after `EXTRA_COMMANDS = {}`, so `deck capacity`, `plan`, `hooks`, `import-v1`, `daemon` and `run` are live.
3. **W0 decisions** (commit `0458fc9`):

| Decision | Change |
|---|---|
| Owner retry | T28 `failed → queued` now has actors `(W0, OWN)`. The panel and phone `retry` action no longer returns 409. |
| Status reason | New `ProjectStore.set_reason(task, text) -> bool` (also in `store_api`). It writes `stateReason` without a transition: `rev`, `stateSince` and the state stay the same, and no event is written, so it can run every tick. It does nothing on terminal tasks, and the next transition overwrites it. The dispatcher already calls it for every candidate it could not place, so the board shows text like `waiting: domain area:src/api/routes/** held by 001-api-totals-fix`. |
| Workspace path | Project schema 2 adds `attempts.workspace` and `attempts.branch`. The dispatcher writes both at launch. `workspace_of()` reads the task's own fields first, then the latest attempt, then the derived worktree path. (The task schema is frozen, so these show up in `current_attempt()`/`attempts()`, not in `task.attempts[]`.) |
| Main checkout busy | T26 is now `merging → merging` (MQ). It sets `resumeAt = now + 120`, keeps `stateSince` (the task keeps its queue position) and keeps the approval, sets the `merge_queue` row to `waiting`, and gives a reason like "merge waits: … · retry HH:MM". It never falls back to review and never needs a second approval. `mergeq` calls it on a busy checkout or a dirty overlap, and the queue honours the stored `resumeAt`, so a restart does not lose the timer. T24 and T25 clear `resumeAt`. `merging → review` is now illegal. |
| Learned job cost | Global schema 2 adds `usage_cost.account`. `GlobalStore.usage_cost.put(rec)` / `.recent(account=, size=, tool=)` keep 50 rows per (account, size). The dispatcher records each finished attempt it can measure: no active limit, and usage readings at both start and end. `quota.estimate(..., account=)` uses the account's own medians once it has 3 or more rows, then the tool's medians, then the S/M/L/XL defaults. All planner and CLI call sites pass the account. |

4. **A fix found by the smoke test** (`deck/run.py`): `deck-run` carries the `DECK_RUN_ID` marker itself, so the `ps eww` child it starts also carried the marker and listed itself. As a result, every attempt logged "killed 1 leftover process(es)". The fix excludes the `ps` child's pid. A regression test (`test_marked_caller_does_not_find_its_own_ps`) fails without the fix and passes with it.
5. **`tests/smoke_deckd.py`**: the end-to-end smoke below, runnable again with `python3 tests/smoke_deckd.py`. It is not part of unittest discovery.

## Tests: 246, all passing
`python3 -m unittest discover -s tests -t .` passes on Python 3.14 and on the system Python 3.9.6.

| Suite | Tests | Suite | Tests |
|---|---|---|---|
| test_states | 42 (T26 rewritten + new T26 test, T28 owner) | test_dispatcher | 24 |
| test_brief | 22 | test_adapters | 21 |
| test_runtime | 21 (+1 marker regression) | test_capacity | 17 |
| test_policy | 17 | test_api | 16 |
| test_cards | 14 | test_domains | 13 |
| test_mergeq | 8 (+1 busy → stays merging → merges after timer, survives restart) | test_cli | 8 |
| test_integration_store | 7 | test_mistakes | 6 |
| test_w0 | 6 (new: set_reason, usage_cost, per-account estimate, real-store dispatcher reason, workspace on attempt + cost learned) | test_contract | 4 (+ UsageCostAPI) |

B1 135 + B2 102 = 237 at the merge, plus 9 W0 tests = 246.

## Smoke test (deckd end to end)
Setup:
- A temporary state dir, a temporary machine config and a temporary git repo, all under `$TMPDIR/deck-smoke-*`.
- Two personal accounts. Each has one `shell` worker (W1, W2) with 1 lane and `parallel.max` 1.
- deckd runs with macOS notifications off. `notifyCommand` writes the action and alert events to a file.
- The API runs on a spare loopback port.

Three tasks, created with `command` and given briefs that pass `deck validate` and `deck ready`. Each shell job sleeps 6 s, writes a file and commits it in its worktree.
- A, `area:src/api/**`
- B, `area:src/api/routes/**`, which conflicts with A
- C, `area:docs/guide/**`

| Check | Result |
|---|---|
| Parallel where allowed | ✅ A on W1 and C on W2 started together |
| Conflicting task waits with a reason | ✅ B stayed `queued` with `stateReason` = "waiting: domain area:src/api/routes/** held by 001-api-totals-fix", and `deck board` showed it. B ran on W1 after A finished. |
| Handoffs land in review | ✅ all 3 reached `review` with valid handoffs (shell synthetic `partial`) |
| Workspace on attempt | ✅ every attempt row has its worktree path |
| Approve → merge queue | ✅ `deck approve` moved each to `merging`. The merge queue then merged all 3 into `main`, one at a time, and each ended `done`. |
| Notifications | ✅ 3 × `review.requested` and 1 × `backlog.low` went through `notifyCommand` |
| API | ✅ `GET /api/overview` answered |
| Clean shutdown | ✅ SIGTERM → exit 0, `deckd.pid` removed, no processes left from the run |
| Leftover-process noise | ✅ after the `run.py` fix there is no false "killed 1 leftover" warning. The first run showed one per attempt. |

The smoke ran 3 times, and the last 2 runs were after the fix. Every run passed.

## Still open from B2's list (not decided here)
- The `merge_queue.state` column now gets `waiting` (T26), `done` (T24) and `failed` (T25), but B2 never sets `active`. The queue is driven by the `merging` state.
- `deck hb --waiting` is only logged, so interactive sessions get no hang detection.
- Not built yet: `import-v1 --watch`, event retention / `deck gc`, the v1-format card for S1, and a Claude-adapter smoke through deckd.
- `DECK_ATTEMPT` is the attempt number, and the orphan marker is `DECK_RUN_ID`. The design should be updated to say so.

## What remains for merging B3 (`v2-b3`)
B3 touches no file that `v2-int` changes (it adds `web/`, `tests/web/*.mjs`, `PanelV2.swift`, `start.sh` and mockups), so a plain merge is expected to apply cleanly. Its fixtures were checked against this tree's `views_types.check_vm`, and `overview`, `project`, `project.client-portal`, `task` and `review` all pass. Before or while merging:

**API shapes B3 expects** (all already served by B1's `deck/server.py`):

| Route | Shape |
|---|---|
| `GET /api/overview` | `OverviewVM`: `time, inbox, projects[ProjectTile], accounts[AccountVM], machine, backlog, alerts` |
| `GET /api/projects/{p}` | `ProjectVM`: board columns `backlog, upNext, running, review, needsYou, doneToday` of `TaskVM`; `inbox[ReviewVM]`; `lanes[LaneVM]` |
| `GET /api/projects/{p}/tasks/{id}` | `TaskDetailVM`: `{task, card, attempts, handoff, history, events, view}`. B3 also accepts `task: null` |
| `GET /api/projects/{p}/tasks/{id}/eligible-workers` | `[EligibleVM]` |
| `GET /api/accounts` | `[AccountVM]`. `capacity` comes from the planner's `account_state` (`lanesUsed, lanesMax, spare, why`, …) |
| `GET /api/events?since=` | With `Accept: text/event-stream`: `event: deck`, `id: <global id>`, resumes with `Last-Event-ID`. B3 reads it with `fetch`, because `EventSource` cannot send `Authorization` |
| `POST /api/projects/{p}/tasks/{id}/actions` | `{action, rev?, note?, worker?, priority?, decision?, option?}` → `{ok, task}` or `{ok:false, code, reason}`. `code` is one of `policy_denied` (403), `illegal`/`stale` (409), `invalid` (400) or `not_found`. Actions: `approve, changes, reject, pause, resume, cancel, retry, answer, priority, reassign` (same list on both sides) |

**Pairing endpoint**:
- `deck pair` prints a 6-digit code that is valid for 5 minutes.
- The device POSTs `/api/pair {code, name}`. No token is needed, and the request can come from any address.
- A good code returns `{ok:true, token}` once. A wrong or expired code returns 403 `{ok:false, code:"forbidden", reason}`.
- B3 stores the token in `deck.token` and sends it as `Authorization: Bearer`.
- Remote GETs need that token as well. Local POSTs need either it or the local token in `<stateDir>/token`, so the local browser deck must pair (or paste the local token) before any action works.

**Items to fix or confirm when merging B3**:
1. **Priority range**: B3's `actionBody` allows `P0..P3`, but the task schema's enum is `P1|P2|P3`, so a P0 would come back 400 `invalid`. Limit the UI to P1–P3, or extend the schema (schemas are frozen, so that would be a design change).
2. **Retry is now allowed for the owner** (T28). If B3 greys out `retry` for `failed` tasks, enable it.
3. **Merging tasks can wait.** A task in `merging` may now carry `resumeAt` and a `stateReason` like "merge waits: … · retry HH:MM". Show that reason in the review/merge column instead of a plain "merging".
4. **Waiting reasons**: `queued` tasks now carry "waiting: …" reasons from `set_reason`. These change without a `task.state` event, so the UI picks them up on the next project fetch, not from SSE. If live updates matter, either re-fetch on `plan.round` events or have deckd emit a debug event when a reason changes.
5. `GET /` serves `web/` from the repo root. After the merge, check that `./start.sh --v2` and `PanelV2.swift` point at deckd's port (default 7788) and that the panel uses the local token for POSTs.
6. Run B3's node tests (`tests/web/*.mjs`) together with the Python suite after the merge.
