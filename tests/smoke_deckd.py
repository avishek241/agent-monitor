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
    # 4. approve → merge queue (merging), then the merge queue merges them
    for tid in ids:
        r = deck("approve", tid)
        say(f"deck approve {tid}:", r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.returncode)
    snap = snapshot()
    results["approve_to_merging"] = all(snap[t][0] in ("merging", "done") for t in ids)
    snap = wait_for(lambda s: all(v[0] == "done" for v in s.values()), 60)
    say("after the merge queue:", {k: v[0] for k, v in snap.items()})
    results["merged_done"] = all(v[0] == "done" for v in snap.values())
    files = sh("git", "ls-files", cwd=REPO).stdout.split()
    results["main_has_work"] = all(f in files for _t, _s, _d, f in specs)
    # 5. API answers
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/overview", timeout=5) as r:
            ov = json.loads(r.read())
        results["api_overview"] = isinstance(ov, dict)
    except Exception as e:  # noqa: BLE001
        say("API error:", e)
        results["api_overview"] = False
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
