# W0 integration: B1 + B2 + B3 on `v2-int`

Branch `v2-int` (worktree `agent-monitor-int`), started from `v2-b1` (`b6e4f79`). Not pushed; W0 publishes. Part 1 below is the B1 + B2 integration; part 2 ("B3 merged") is the UI merge and what it needed from the server.

## Part 1: what was merged (B1 + B2)
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

## Tests after part 1: 246, all passing
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

## Smoke test after part 1 (deckd end to end)
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

## Part 2: B3 merged (`v2-b3` at `6c64eae`)
1. **Merge.** `git merge --no-ff v2-b3` applied with no conflicts. B3 adds `web/`, `tests/web/*.mjs`, `PanelV2.swift`, `start.sh --v2` and docs, and touches no file part 1 changed. Its handoff is `docs/v2/B3-handoff.md`.
2. **P0 priority.** `task.schema.json` (both the `deck/schemas` and `docs/v2/schemas` copies, kept byte-identical) now has `P0 | P1 | P2 | P3`.
   - The CLI `--prio` accepts P0, and the API `priority` action accepts P0..P3, anything else → 400 `invalid`.
   - The dispatcher and the merge queue order P0 before P1.
   - P0 gets P1's 5-point weekly-quota grace.
   - Tests: candidate order, merge-queue order, the capacity grace, the API (P0 accepted, P4 → 400) and `deck new --prio P0`.
3. **deckd serves `web/` with gzip** (`deck/server.py`):
   - **Content types:** set explicitly; `.js` is `text/javascript`, or module scripts are refused.
   - **gzip:** level 9, for text types of 512 B or more, when the client sends `Accept-Encoding: gzip`. Results are cached by path, mtime and size.
   - **Caching headers:** `Vary: Accept-Encoding`, an `ETag` that answers `If-None-Match` with 304, and `Cache-Control: no-cache`.
   - **Not gzipped:** fonts and PNGs, which are already compressed.
   - **60 KB budget, measured per response as deckd sends it.** Each file is compressed separately, so a first load of every module came to 65.4 KB, over the limit. B3's own budget test measures one concatenated gzip (58.6 KB), which is not what a browser downloads.
   - **The fix: lazy layouts.** `web/app.js` now loads the Mission and Paper renderers with `import()` only when that layout is chosen; `layouts/index.js` stays as the eager registry for B3's tests. Switching layout from any screen now re-renders.
   - **First-load JS + CSS, gzipped as served:**

     | Layout | Size |
     |---|---|
     | Studio | 45.0 KB |
     | Mission | 53.7 KB |
     | Paper | 55.8 KB |

     `tests/test_web.py` enforces < 60 KB for each layout.
   - **Service worker:** still precaches every shell file in the background for offline use.
4. **Optional view-model fields for the Workers section and needs-you.** All are added to `deck/views_types.py`, checked by `check_vm` when present, and in the fixtures.

   | Field | Source |
   |---|---|
   | `AccountVM.usage.points[{ts,pct5,pct7}]` | `usage` table over 24 h (new `GlobalStore.usage.since`), one point per 15 min |
   | `AccountVM.usage.resets[]` | 5-hour reset times seen in those readings and in `limits`, from 24 h ago up to the next reset |
   | `AccountVM.usage.burn5PerHour` | slope of pct5 over the last hour of the current window (≥ 5 min of readings; null after a rollover or at the limit) |
   | `AccountVM.usage.minutesToLimit` | (100 − pct5) / burn; null when burn is unknown or 0, or while cooling |
   | `AccountVM.doneToday` | attempts that ended with a handoff since local midnight, all projects (new `ProjectStore.attempts_ended_since`) |
   | `AccountVM.queued[{project,task,num,title,worker,why}]` | the **planner** (`Dispatcher.account_queues`): each waiting candidate goes under the account of its last worker (resume), else the eligible account with the highest urgency; limit_paused tasks are listed under the account they resume on. Published through `account_state.capacity.queued`; the view moves it to `AccountVM.queued`. |
   | `AccountVM.color` | `color` from the account config, when set |
   | `OverviewVM.needsYou[{kind,project,task,num,title,text,rev,options?,eventId?}]` | the store, in this order: questions, blocked/failed tasks, reviews (longest waiting first), then alert events on live tasks from the last 24 h |
   | `TaskVM.rev` | the task row, so list actions can send `rev` |

   With no usage readings, `usage` is null and the UI hides the graph rather than inventing one. The fixture `queued` items gained `num`, `worker` and `why`.
5. **Fonts.** These are copied from `agent-monitor/fonts/` (Latin subsets, SIL OFL 1.1) into `web/fonts/`, with `@font-face` rules in `styles.css` and the list in `web/fonts/README.md`. A browser downloads a font file only when the active layout uses it.
   - Mission Control: IBM Plex Mono 400/600/700 and Barlow Semi Condensed 500/600/700
   - Paper: Fraunces and Source Sans 3
   - PanelV2 does not register bundled fonts yet. It falls back to system faces, as B3 flagged; the v1 `Panel.swift` `FontKit` code could be ported.

### Test counts after part 2
- **Python:** `python3 -m unittest discover -s tests -t .` → **254 tests, all pass** on Python 3.14 and 3.9.6.
  - Part 1's 246.
  - New `tests/test_web.py` (8): types, gzip and ETag, path traversal, the per-layout budget, fonts served, usage-series maths, overview extras and the cooling account.
  - The P0 checks were added to existing tests, plus one planner-queue check in `test_w0`.
- **Node:** `node --test tests/web/*.test.mjs` → **99 / 99 pass**, including the headless-Chrome screenshot per layout × mode, which still renders and differs with lazy layouts.
- **PanelV2:**
  - `swiftc -typecheck PanelV2.swift` is clean.
  - `swiftc -O PanelV2.swift` compiles.
  - `PanelV2 --check-fixtures web/fixtures` → **57 ok, 0 FAIL** with the updated fixtures.

### Smoke test after part 2 (`python3 tests/smoke_deckd.py`)
The smoke test from part 1 now also does the following:
- **Deck pages over HTTP:** fetches `/`, follows `app.js`'s module graph (plus the two lazy layouts), the stylesheets, every font in `styles.css`, the manifest, the service worker and the icons. That is 33 files, each checked for 200, the right content type, gzip for JS and CSS, and a body identical to the file.
- **View-model endpoints:** checks every endpoint the UI uses in three phases (while running, in review, after the merges), each response 200 and passing `check_vm`:
  - `/api/overview` and `/api/accounts`
  - `/api/projects/smoke`
  - `/api/projects/smoke/tasks/{id}` and `…/eligible-workers` for each task
  - `/api/events` as JSON and as SSE
- **The phone's approve path:** `deck pair`, then `POST /api/pair` returns a token once (a second use → 403), then `POST …/actions {approve, rev}` with the device token. The other two tasks are approved with `deck approve`.

| Check | Result |
|---|---|
| Parallel where allowed / conflict waits with a reason on task and board / waiter runs after | ✅ |
| Planner queue: the waiting task appears in `AccountVM.queued` of the account it will run on | ✅ (`acct-a: [002-api-routes-tidy]`) |
| Deck pages: 33 files, types, gzip | ✅ |
| View-model endpoints: 200 + valid shape while running, in review, after merge | ✅ |
| `doneToday` | ✅ (acct-a 2, acct-b 1) |
| `needsYou` lists the three reviews | ✅ |
| Handoffs valid, workspace on attempt | ✅ |
| Pairing works once; approve over HTTP with the device token → merging | ✅ |
| Merge queue merges all three; main has the work | ✅ |
| Clean shutdown, no leftover processes | ✅ |

The usage graph and burn fields are not exercised by the smoke test, because the temporary account homes produce no usage readings. They are covered by `tests/test_web.py`.

## Still open from B2's list (not decided here)
- The `merge_queue.state` column now gets `waiting` (T26), `done` (T24) and `failed` (T25), but B2 never sets `active`. The queue is driven by the `merging` state.
- `deck hb --waiting` is only logged, so interactive sessions get no hang detection.
- Not built yet: `import-v1 --watch`, event retention / `deck gc`, the v1-format card for S1, and a Claude-adapter smoke through deckd.
- `DECK_ATTEMPT` is the attempt number, and the orphan marker is `DECK_RUN_ID`. The design should be updated to say so.

## Left for the owner / W0 (from B3's flags)
- Paper's CSS uses native nesting, which needs Safari 16.5 or later.
- PanelV2 has a line height of about 1.3, against the 1.45 target.
- In Mission, OPS sub-tabs would need `#/go/ops/<sub>` routes.
- These are phone-only: swipe between projects and hold-to-reject.
- Actions need a token even on localhost, so the first run opens pairing. The local token is in `<stateDir>/token`.
- Waiting reasons and the per-account `queued` lists change without a `task.state` event. The deck re-fetches the board every 10 s, which is enough. If they must update instantly, have deckd emit a debug event when a reason changes.
- PanelV2 font registration, as noted above.
