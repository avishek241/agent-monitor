# B3 handoff: UI (phone deck PWA and PanelV2)

Branch `v2-b3` (worktree `agent-monitor-b3`). Reassigned from a worker that stopped before committing; everything here was written from scratch against `design.md` §12–13, the mockups and B1's frozen `deck/views_types.py`. Nothing is pushed.

## What was built
| Part | Files |
|---|---|
| Phone deck PWA: vanilla ES modules, no framework, no build step, installable (manifest + service worker), self-hosted OFL fonts | `web/index.html`, `manifest.json`, `sw.js`, `app.js` (DOM glue), `api.js` (the only network code), `store.js` (routes), `fmt.js`, `theme.js`, `workers.js`, `themes.css`, `styles.css`, `views/*.js` (Studio screens + settings), `layouts/{index,mission,paper}.{js,css}`, `icons/`, `fonts/` |
| Fixtures (neutral data; all pass B1 `check_vm`) | `web/fixtures/{overview,project,project.client-portal,task,review,eligible}.json` |
| Floating panel v2 | `PanelV2.swift` (one file; v1 `Panel.swift` untouched) |
| Launcher | `./start.sh --v2 [--url … \| --fixtures web/fixtures]`; the v1 path is unchanged |
| Tests | `tests/web/*.test.mjs` (node --test), plus `PanelV2 --check-fixtures` |

### Screens (deck)
- **Overview**: all projects, accounts with parallel lanes, needs-you list, backlog, Workers section.
- **Project page**: tabs Workers · Board · Review · Alerts · Log · Repo; swipe between projects.
- **Task detail**: steps with ETA, metrics, attempts, events and actions.
- **Sheets**: review (done-when evidence, tests, risks, one-tap decisions, Approve / Changes / hold-to-Reject), reassign (disallowed workers greyed with a lock and the server's reason, never hidden, not tappable), and answer.
- **Accounts & lanes**: W1·1…W1·n, free lanes explained only by the planner's `capacity.why` / `fitsSize`, cooling countdowns (never "100 % for 24 h"), the office account with usage, and spare-capacity cards.
- **Settings**: layout and mode pickers, plus pairing (6-digit code → `POST /api/pair`, or paste the local token).
- **Live updates**: SSE through `fetch` (because `EventSource` can't send `Authorization`) with `Last-Event-ID`; an OFFLINE · last update HH:MM badge; the board view re-fetches every 10 s; notifications while the deck is open.

### Appearance (owner requests)
- **Three layouts × light/dark, plus Auto.** Layout and mode are chosen separately and both are persisted (deck: `localStorage deck.appearance`; panel: `UserDefaults deck.layout` / `deck.mode`). Older saved values are migrated.
  - Studio is `mockups/deck.html` / `panel.html`.
  - Mission Control is `alt-a`.
  - Paper is `alt-b`.
  - Auto follows `prefers-color-scheme` on the phone and the macOS appearance on the panel.
  - Quick keys: ▦ cycles the layout and ◐ cycles the mode. Settings (⚙) has both pickers.
- **Tokens and structure.** Each layout×mode is a token set in `web/themes.css` (6 sets, hex only). Mission and Paper also have their own structure (`layouts/*.js` renderers ported from the mockups' render code) over the same view models. They reuse the shared action attributes, so `app.js` handles every action unchanged.
- **Readability.**
  - Phone: body text ≥ 15 px, line-height 1.45, a 4/8 px spacing scale, tap targets ≥ 44 px, and text that wraps instead of truncating (truncated chips carry `title`).
  - Panel: body text ≥ 13 pt (badges 11 pt) and `.help()` tooltips on truncated text. Panel line spacing is about 1.3, slightly under 1.45 (flagged below).
  - WCAG AA is checked for every text/background pair in all six sets, on both the phone and the panel.
- **Worker colours.**
  - One colour per account, using Okabe–Ito (colour-blind safe): blue, orange, green, purple, vermillion, sky, yellow. Vermillion comes before sky so five accounts never get two blues.
  - Each colour is tuned per token set to ≥ 3:1 on cards. `AccountVM.color` (config) overrides it.
  - Used on task cards (stripe + badge), lanes, account rows, timeline rows and graph series, with a legend.
- **Workers section in every layout.**
  - It expands on click and the state survives refreshes and reloads.
  - Collapsed, each worker shows a dot, name, 5-hour % and lanes used/max.
  - Expanded, each worker in its colour shows:
    - a 5-hour (solid) / 7-day (dashed) usage graph with reset markers, drawn as an LCD dot matrix in Studio;
    - burn %/h and time to the limit;
    - running lanes with task ids;
    - jobs done today;
    - the spare-capacity hint;
    - its queued pipeline.
  - Missing data hides a row; nothing is invented.

## Tests
- **`node --test tests/web/*.test.mjs` → 99 / 99 pass.** The suites:
  - fixtures shape;
  - fmt/store (routes round-trip);
  - views (every Studio screen, escaping/XSS);
  - api (action bodies, auth, policy reason verbatim, pairing and SSE resume, all against a stub HTTP server);
  - contract (fixtures pass B1 `check_vm`, and the action list equals B1 `ACTIONS`);
  - PWA shell (manifest, icons, the service worker caches the shell and never `/api`; no CDN);
  - themes (AA contrast for 6 sets × 34 pairs, tile text, readability tokens, preference logic, settings picker, and a **headless-Chrome screenshot per layout × mode**, skipped when Chrome is absent);
  - layout-mission (13) and layout-paper (10);
  - workers (colours, expand, graph/dot matrix, ≥ 3:1 palette in all sets).
- **`swiftc -typecheck PanelV2.swift`**: clean, with no errors or warnings.
- **`PanelV2 --check-fixtures web/fixtures` → 57 ok, 0 FAIL, exit 0.** It checks:
  - decoding of every fixture;
  - the action-body rules;
  - layout/mode resolution, including Auto;
  - migration of the old setting;
  - AA contrast for all six sets (33–87 pairs each);
  - worker-colour contrast;
  - the Workers section state;
  - merging text, P0 and Retry.
- **Snapshots without a screen**: `PanelV2 --fixtures web/fixtures --layout L --mode M [--project p --tab t] [--expand-workers] --snapshot out.png`. Deck: `index.html?fixtures=1&layout=L&mode=M&workers=open#/…`.

## For the integrator (merging `v2-b3` into `v2-int`)
B3 adds only `web/`, `tests/web/`, `PanelV2.swift`, `start.sh` and docs, so the merge should apply cleanly. **Expected API checks** (all served by B1 `deck/server.py`):

| Check | Expect |
|---|---|
| `GET /` | serves `web/index.html`; `manifest.json`, `sw.js`, `fonts/*.woff2` and `layouts/*` return 200 with the right content types (`.js` must be `text/javascript`, otherwise module scripts are refused) |
| `GET /api/overview` | OverviewVM; the deck renders it. Optional extras B3 uses when present: `needsYou[]`, and per account `color`, `usage{points[{ts,pct5,pct7}], resets[], burn5PerHour, minutesToLimit}`, `doneToday`, `queued[{project,task,title}]` (see below) |
| `GET /api/projects/{p}` | ProjectVM; `workers[]` without usage is joined with `overview.accounts` by id; `health[].ok` may be null ("not checked") |
| `GET /api/projects/{p}/tasks/{id}` | TaskDetailVM incl. `view`; `task: null` is handled |
| `GET …/eligible-workers` | `[EligibleVM]`; `allowed:false` rows show `reason` verbatim |
| `GET /api/events` with `Accept: text/event-stream` and `Authorization` | `event: deck`, `id:` global id; the deck resumes with `Last-Event-ID` and `?since=` |
| `POST …/actions` | the body is exactly `{action, rev?, note?, worker?, priority?, decision?, option?}`; a 403 `policy_denied` reason is shown verbatim; a 409 `stale` re-fetches; a 401/403 opens pairing |
| `POST /api/pair {code, name}` | `{ok, token}` is stored as `deck.token` and sent as `Authorization: Bearer`; a 403 reason is shown |
| Merging tasks | `resumeAt` + `stateReason` "merge waits: … · retry HH:MM" render as "merging · retry in 1m · main checkout busy" |
| Priority | the UI sends P0–P3 (W0 decision: P0 allowed; integration extends the schema) |
| Retry | always offered to the owner, including on failed tasks (T28 now allows OWN) |

Smoke test after the merge:
1. `deck serve`, then open `http://127.0.0.1:7788/`.
2. Pair it: run `deck pair` and type the code, or paste `~/.local/share/agent-deck/token`.
3. Approve a review and check that its card moves.
4. Run `./start.sh --v2` (panel against :7788, using the local token from `<stateDir>/token`).
5. Run `node --test tests/web/*.test.mjs` together with the Python suite.

## Decisions and flags for W0 / owner (reversible)
1. **Contract additions B1/B2 should serve:**
   - `OverviewVM.needsYou[]` is optional. Without it, the Studio overview's Needs-you list shows alerts only; Mission and Paper derive it from the project inboxes.
   - `TaskVM.rev` would let list actions send `rev`. It is optional, so actions work without it.
   - For the Workers section, `AccountVM.usage` (history from the `usage` table plus reset times, and burn and minutes-to-limit from the B2 planner), `doneToday` and `queued[]`. Until B1/B2 serve them, the Workers section shows only the summary, lanes and spare hint.
2. **Size.** The design asks for < 60 KB of JS+CSS. Three full layouts are 179 KB raw but **54 KB gzipped**. The test enforces gzip < 60 KB and raw < 200 KB. Have deckd send gzip, or accept the larger raw size.
3. **Fonts.** Only the Studio fonts are bundled. Mission (IBM Plex Mono, Barlow) and Paper (Fraunces, Source Sans 3) fall back to system faces because there is no CDN. The OFL files can be added to `web/fonts/` later.
4. **Paper CSS uses native nesting,** which needs Safari ≥ 16.5. Older browsers would show Paper unstyled.
5. **Mission differences from the mockup:**
   - A 20-cell quota bar replaces the sparkline when there is no history, and a static ticker line on the panel.
   - "QUIET" instead of "HUNG" unless the server says hung.
   - At ALL scope, OPS stacks Alerts, Timeline and Repo. Real sub-tabs would need `#/go/ops/<sub>` routes.
6. **Paper differences:**
   - Buttons the API has no actions for (Use it, Dispatch, Snooze) are left out.
   - The panel's Today has no day-so-far feed, because there is no global timeline in the overview.
7. **Panel differences:**
   - Board, review and timeline need a project selected, since the API is per project.
   - No keyboard-shortcut hints.
   - Line spacing is about 1.3.
8. **Swipe and hold.** Swipe between projects and hold-to-cancel/reject are phone only. The panel uses a two-step "Confirm cancel".
9. **First run.** Without a token, the deck opens Settings once (pairing), because actions need a token even on localhost.

STOP: B3 is complete. W0 merges `v2-b3` into `v2-int` and runs the smoke test above.
