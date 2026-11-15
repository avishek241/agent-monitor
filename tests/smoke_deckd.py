"""End-to-end smoke of deckd (B1 + B2 + W0) with a temp state dir, temp config, one fake project, two shell workers.

Usage: python3 tests/smoke_deckd.py [repo-root]     (not part of unittest discovery; takes ~40 s)
Checks: two jobs run in parallel on two workers; the job conflicting on an area domain waits with a reason on the
task and the board; handoffs land in review; approve moves them to merging and the merge queue merges them;
the API answers; deckd shuts down cleanly with no leftover processes. Exit 0 when every check passes.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
TMP = Path(tempfile.mkdtemp(prefix="deck-smoke-"))
STATE, REPO, HOMES = TMP / "state", TMP / "repo", TMP / "homes"
PORT = 17788 + os.getpid() % 1000
LOG = []


def say(*a):
    line = " ".join(str(x) for x in a)
    LOG.append(line)
    print(line, flush=True)


def sh(*args, cwd=None, check=True, env=None):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode:
        raise SystemExit(f"FAILED {args}: {r.stdout}{r.stderr}")
    return r


GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "smoke", "GIT_AUTHOR_EMAIL": "smoke@example.invalid",
           "GIT_COMMITTER_NAME": "smoke", "GIT_COMMITTER_EMAIL": "smoke@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

# ---- fake project repo
REPO.mkdir(parents=True)
for d in ("src/api/routes", "docs/guide", "docs/tasks"):
    (REPO / d).mkdir(parents=True, exist_ok=True)
(REPO / "src/api/app.txt").write_text("api\n")
(REPO / "src/api/routes/r.txt").write_text("routes\n")
(REPO / "docs/guide/g.txt").write_text("guide\n")
(REPO / "AGENTS.md").write_text("Smoke project rules: commit in your worktree only.\n")
(REPO / ".gitignore").write_text("docs/tasks/\n")
sh("git", "init", "-q", "-b", "main", cwd=REPO, env=GIT_ENV)
sh("git", "add", "-A", cwd=REPO, env=GIT_ENV)
sh("git", "commit", "-q", "-m", "init", cwd=REPO, env=GIT_ENV)

# ---- temp config: two accounts (personal), one shell worker each
for h in ("a", "b"):
    (HOMES / h).mkdir(parents=True)
pfile = TMP / "smoke.project.json"
pfile.write_text(json.dumps({"kind": "project", "title": "Smoke", "repo": str(REPO), "mainBranch": "main",
                             "worktreesDir": str(TMP / "worktrees"), "briefsDir": "docs/tasks",
                             "slots": {"count": 3}, "gates": []}))
cfg_path = TMP / "deck.json"
cfg_path.write_text(json.dumps({
    "kind": "machine", "stateDir": str(STATE), "listen": {"host": "127.0.0.1", "port": PORT},
    "notifyCommand": f"cat >> {TMP / 'notify.jsonl'}",
    "scheduler": {"minFreeRamGb": 0, "replanDebounceMs": 100},
    "accounts": [
        {"id": "acct-a", "label": "Shell A", "tool": "codex", "scope": "personal", "home": str(HOMES / "a"),
         "parallel": {"max": 1}},
        {"id": "acct-b", "label": "Shell B", "tool": "codex", "scope": "personal", "home": str(HOMES / "b"),
         "parallel": {"max": 1}},
    ],
    "workers": [
        {"id": "W1", "account": "acct-a", "adapter": "shell", "lanes": 1, "capabilities": ["code"]},
        {"id": "W2", "account": "acct-b", "adapter": "shell", "lanes": 1, "capabilities": ["code"]},
    ],
    "projects": [{"id": "smoke", "config": str(pfile), "scope": "personal"}],
}))
ENV = {**GIT_ENV, "DECK_CONFIG": str(cfg_path), "PYTHONPATH": str(ROOT), "DECK_ACTOR": "W0"}
for k in ("DECK_PROJECT", "DECK_TASK", "DECK_ATTEMPT", "DECK_TOKEN", "DECK_WORKER"):
    ENV.pop(k, None)


def deck(*args, check=True):
    r = subprocess.run([sys.executable, "-m", "deck.cli", "-p", "smoke", *args], capture_output=True, text=True,
                       env=ENV, cwd=str(TMP))
    if check and r.returncode:
        raise SystemExit(f"deck {' '.join(args)} → {r.returncode}: {r.stdout}{r.stderr}")
    return r


# ---- deckd, with macOS notifications off (notifyCommand still captures action/alert events)
launcher = (
    "import signal,sys\n"
    "from deck.config import Config\nfrom deck.deckd import Daemon\nfrom deck.notify import Notifier\n"
    "cfg=Config.load()\nd=Daemon(cfg, notifier=Notifier(cfg, macos=False))\n"
    "def stop(*_): d.stop=True\n"
    "signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)\nd.run()\n")
dlog = open(TMP / "deckd.log", "w")
daemon = subprocess.Popen([sys.executable, "-c", launcher], env=ENV, cwd=str(TMP), stdout=dlog, stderr=subprocess.STDOUT)
for _ in range(40):
    if (STATE / "deckd.pid").exists():
        break
    time.sleep(0.25)
assert (STATE / "deckd.pid").exists(), "deckd did not write its pid file"
say(f"deckd up (pid {daemon.pid}), API on 127.0.0.1:{PORT}, state {STATE}")

# ---- three tasks with valid briefs; A and B conflict on area:src/api/**
sys.path.insert(0, str(ROOT))
from deck import store as dstore  # noqa: E402
from deck.config import Config  # noqa: E402

cfg = Config.load(str(cfg_path))
JOB = ("sleep 6; echo \"$DECK_TASK by $DECK_WORKER_ID\" > \"{f}\"; git add -A; git commit -qm \"$DECK_TASK work\"; "
       "echo done")
specs = [("API totals fix", "Fix the totals in the API layer", ["area:src/api/**"], "src/api/totals.txt"),
         ("API routes tidy", "Tidy the API routes and their names", ["area:src/api/routes/**"], "src/api/routes/tidy.txt"),
         ("Guide wording pass", "Reword the user guide introduction", ["area:docs/guide/**"], "docs/guide/intro.txt")]
ids = []
ps = dstore.open_project("smoke", cfg)
for title, summary, doms, f in specs:
    t = ps.create_task({"title": title, "summary": summary, "size": "S", "capability": "code", "domains": doms,
                        "workerClass": ["shell"], "command": ["sh", "-c", JOB.format(f=f)]}, by="W0")
    ids.append(t["id"])
    (REPO / t["brief"]["path"]).write_text(
        f"# {title}\n\n## Goal\n{summary}.\n\n## Rules\nWork only in your worktree; commit there.\n\n"
        f"## Steps\n1. Write `{f}`.\n2. Commit it.\n\n## Done when\n- [ ] `{f}` exists on the task branch\n"
        f"- [ ] the job exits 0\n\n## Stop point\nAfter the single commit; do not merge.\n")
ps.close()
ps.g.close()
for tid in ids:
    r = deck("validate", tid)
    assert "brief OK" in r.stdout, r.stdout
    deck("ready", tid)
say("created + ready:", ", ".join(ids), "(A/B share area:src/api/**)")


from deck import views_types as vt  # noqa: E402
import gzip as _gz  # noqa: E402
import re as _re  # noqa: E402
import posixpath as _pp  # noqa: E402

WEB = ROOT / "web"


def http(path, method="GET", body=None, headers=None, gz=True):
    """(status, headers, body bytes, already gunzipped)."""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if gz:
        r.add_header("Accept-Encoding", "gzip")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            st, h, raw = resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        with e:
            st, h, raw = e.code, dict(e.headers), e.read()
    if h.get("Content-Encoding") == "gzip":
        raw = _gz.decompress(raw)
    return st, h, raw


def jget(path):
    st, h, raw = http(path)
    return st, (json.loads(raw) if "json" in h.get("Content-Type", "") else raw)


def check_api(tids):
    """Every view-model endpoint the deck uses: 200 and a valid shape. Returns (ok, problems, overview)."""
    probs = []
    st, ov = jget("/api/overview")
    probs += [f"overview {st}"] if st != 200 else [f"overview: {e}" for e in vt.check_vm("OverviewVM", ov)]
    st, accts = jget("/api/accounts")
    probs += [f"accounts {st}"] if st != 200 else [f"accounts: {e}" for a in accts for e in vt.check_vm("AccountVM", a)]
    st, pv = jget("/api/projects/smoke")
    probs += [f"project {st}"] if st != 200 else [f"project: {e}" for e in vt.check_vm("ProjectVM", pv)]
    for tid in tids:
        st, d = jget(f"/api/projects/smoke/tasks/{tid}")
        probs += [f"task {tid} {st}"] if st != 200 else [f"task {tid}: {e}" for e in vt.check_vm("TaskDetailVM", d)]
        st, el = jget(f"/api/projects/smoke/tasks/{tid}/eligible-workers")
        probs += [f"eligible {tid} {st}"] if st != 200 else [f"eligible {tid}: {e}" for r in el
                                                           for e in vt.check_vm("EligibleVM", r)]
    st, evs = jget("/api/events?since=0")
    if st != 200 or not isinstance(evs, list) or not evs:
        probs.append(f"events json {st}")
    st, h, raw = http("/api/events?since=0&timeout=1", headers={"Accept": "text/event-stream"}, gz=False)
    if st != 200 or "event: deck" not in raw.decode(errors="replace") or "text/event-stream" not in h.get("Content-Type", ""):
        probs.append(f"events sse {st}")
    return not probs, probs, ov


def check_static():
    """The deck pages over HTTP: index, every module app.js imports (and the lazy layouts), stylesheets, fonts,
    manifest, service worker and icons, with the right types and gzip where it helps."""
    probs, sizes = [], {}
    st, h, raw = http("/")
    if st != 200 or not h.get("Content-Type", "").startswith("text/html") or b'src="app.js"' not in raw:
        probs.append(f"/ {st} {h.get('Content-Type')}")
    html = raw.decode()
    files = set(_re.findall(r'(?:href|src)="([^"#:]+)"', html))
    todo = ["app.js", "layouts/mission.js", "layouts/paper.js"]
    while todo:
        f = todo.pop()
        if f in files and f.endswith(".js") and f in sizes:
            continue
        files.add(f)
        st, h, raw = http("/" + f)
        sizes[f] = int(h.get("Content-Length") or 0)
        if st != 200 or not h.get("Content-Type", "").startswith("text/javascript") or h.get("Content-Encoding") != "gzip":
            probs.append(f"{f} {st} {h.get('Content-Type')} {h.get('Content-Encoding')}")
            continue
        if raw != (WEB / f).read_bytes():
            probs.append(f"{f} body differs")
        for m in _re.findall(r'(?:from|import\()\s*"(\.[^"]+)"', raw.decode()):
            n = _pp.normpath(_pp.join(_pp.dirname(f), m))
            if n not in sizes:
                todo.append(n)
    css = (WEB / "styles.css").read_text()
    files |= set(_re.findall(r"url\((fonts/[^)]+)\)", css))
    files |= {"manifest.json", "sw.js", "icons/icon-192.png", "icons/icon-512.png"}
    want = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".json": "application/manifest+json",
            ".png": "image/png", ".svg": "image/svg+xml"}
    for f in sorted(files - set(sizes)):
        st, h, raw = http("/" + f)
        ext = "." + f.rsplit(".", 1)[-1]
        if st != 200 or not h.get("Content-Type", "").startswith(want.get(ext, "")):
            probs.append(f"{f} {st} {h.get('Content-Type')}")
        elif ext == ".css" and h.get("Content-Encoding") != "gzip":
            probs.append(f"{f} not gzipped")
        if ext in (".js", ".css"):
            sizes[f] = int(h.get("Content-Length") or 0)
    return not probs, probs, sizes, len(files | set(sizes))


def snapshot():
    out = {}
    s = dstore.open_project("smoke", cfg)
    try:
        for t in s.list():
            out[t["id"]] = (t["state"], t.get("stateReason"), t.get("worker"))
    finally:
        s.close()
        s.g.close()
    return out


def wait_for(pred, timeout=60, every=0.3):
    end = time.time() + timeout
    while time.time() < end:
        snap = snapshot()
        if pred(snap):
            return snap
        time.sleep(every)
    return snapshot()


results = {}
try:
    # 1. parallel where allowed
    snap = wait_for(lambda s: sum(v[0] in ("starting", "running") for v in s.values()) >= 2, 30)
    running = [k for k, v in snap.items() if v[0] in ("starting", "running")]
    waiting = [k for k, v in snap.items() if v[0] == "queued"]
    say("running in parallel:", running, "workers:", [snap[k][2] for k in running])
    results["parallel"] = len(running) == 2 and len({snap[k][2] for k in running}) == 2
    # 2. the conflicting one waits, with a reason
    say("waiting:", [(k, snap[k][1]) for k in waiting])
    a, b, c = ids
    results["conflict_waits"] = (len(waiting) == 1 and waiting[0] in (a, b)
                                 and (snap[waiting[0]][1] or "").startswith("waiting:")
                                 and "area:src/api" in (snap[waiting[0]][1] or ""))
    results["guide_runs"] = c in running
    # board shows the reason
    board = deck("board").stdout
    results["board_reason"] = "waiting:" in board
    say("board:\n" + board.rstrip())
    ok, probs, ov = check_api(ids)
    results["api_shapes_while_running"] = ok
    acc = {x["id"]: x for x in ov["accounts"]}
    qd = [q["task"] for x in acc.values() for q in x.get("queued") or []]
    results["queued_shows_waiter"] = waiting[0] in qd
    say("api while running:", "ok" if ok else probs[:5], "| queued per account:",
        {k: [q["task"] for q in v.get("queued") or []] for k, v in acc.items()})
    ok, probs, sizes, n = check_static()
    results["deck_pages_served"] = ok
    js_css = {k: v for k, v in sizes.items()}
    say(f"deck files fetched: {n}; problems: {probs[:5]}")
    # 3. handoffs land in review; the waiter then runs
    snap = wait_for(lambda s: all(v[0] in ("review",) for v in s.values()), 90)
    say("after the jobs:", {k: v[0] for k, v in snap.items()})
    results["all_review"] = all(v[0] == "review" for v in snap.values())
    s = dstore.open_project("smoke", cfg)
    try:
        hs = {tid: s.handoff(tid) for tid in ids}
        atts = {tid: s.current_attempt(tid) for tid in ids}
    finally:
        s.close()
        s.g.close()
    results["handoffs_valid"] = all(h and h["valid"] for h in hs.values())
    results["workspace_on_attempt"] = all(a_ and a_.get("workspace") for a_ in atts.values())
    say("handoffs:", {t: (h["data"]["outcome"], h["valid"]) for t, h in hs.items()})
    say("attempt workspaces:", {t: a_.get("workspace") for t, a_ in atts.items()})
    ok, probs, ov = check_api(ids)
    results["api_shapes_in_review"] = ok
    acc = {x["id"]: x for x in ov["accounts"]}
    results["done_today_counts"] = sum(x.get("doneToday") or 0 for x in acc.values()) == 3
    results["needs_you_lists_reviews"] = sorted(n["task"] for n in ov.get("needsYou", []) if n["kind"] == "review") == sorted(ids)
    say("api in review:", "ok" if ok else probs[:5], "| doneToday:", {k: v.get("doneToday") for k, v in acc.items()},
        "| needsYou:", [(n["kind"], n["task"]) for n in ov.get("needsYou", [])])
    # 4. approve → merge queue (merging), then the merge queue merges them. The first goes through the phone
    #    path: `deck pair` → POST /api/pair → device token → POST …/actions approve with rev.
    code = _re.search(r"\b(\d{6})\b", deck("pair").stdout).group(1)
    st, h, raw = http("/api/pair", "POST", {"code": code, "name": "smoke-phone"})
    tok = json.loads(raw).get("token") if st == 200 else None
    st2, h2, raw2 = http("/api/pair", "POST", {"code": code, "name": "again"})
    results["pairing_once"] = bool(tok) and st2 == 403
    rev = jget(f"/api/projects/smoke/tasks/{ids[0]}")[1]["task"]["rev"]
    st, h, raw = http(f"/api/projects/smoke/tasks/{ids[0]}/actions", "POST", {"action": "approve", "rev": rev},
                      headers={"Authorization": f"Bearer {tok}"})
    res = json.loads(raw)
    results["approve_over_http"] = st == 200 and res.get("ok") and res["task"]["state"] in ("merging", "done")
    say(f"POST approve {ids[0]} with the paired token:", st, res.get("task", {}).get("state"), res.get("reason"))
    for tid in ids[1:]:
        r = deck("approve", tid)
        say(f"deck approve {tid}:", r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.returncode)
    snap = snapshot()
    results["approve_to_merging"] = all(snap[t][0] in ("merging", "done") for t in ids)
    snap = wait_for(lambda s: all(v[0] == "done" for v in s.values()), 60)
    say("after the merge queue:", {k: v[0] for k, v in snap.items()})
    results["merged_done"] = all(v[0] == "done" for v in snap.values())
    files = sh("git", "ls-files", cwd=REPO).stdout.split()
    results["main_has_work"] = all(f in files for _t, _s, _d, f in specs)
    # 5. API shapes after the merges
    ok, probs, ov = check_api(ids)
    results["api_shapes_after_merge"] = ok
    say("api after merge:", "ok" if ok else probs[:5])
finally:
    daemon.send_signal(signal.SIGTERM)
    try:
        rc = daemon.wait(timeout=15)
    except subprocess.TimeoutExpired:
        daemon.kill()
        rc = "killed"
    results["clean_shutdown"] = rc == 0 and not (STATE / "deckd.pid").exists()
    say("deckd exit:", rc, "pid file removed:", not (STATE / "deckd.pid").exists())
    left = sh("pgrep", "-f", str(TMP), check=False).stdout.split()
    results["no_leftover_processes"] = not left
    say("leftover processes:", left)

say("RESULTS", json.dumps(results, indent=1))
ev = sh(sys.executable, "-m", "deck.cli", "-p", "smoke", "events", check=False, env=ENV).stdout
(TMP / "events.txt").write_text(ev)
say("tmp:", TMP)
sys.exit(0 if all(results.values()) else 1)
