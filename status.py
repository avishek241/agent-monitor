#!/usr/bin/env python3
"""agent-monitor: live status of AI-agent / background work as JSON for the floating panel.
Local files only (plus optional localhost health checks): no network, no AI, no tokens.
All project-specific settings come from a JSON config (see README.md and examples/):
  --config <path>  or  $AGENT_MONITOR_CONFIG  or  ~/.config/agent-monitor/config.json"""
import glob, json, os, re, subprocess, time, urllib.request

# Every Claude session has its own scratchpad; runner logs and background-agent transcripts can be in any of them.
import sys as _sys


def _load_config():
    path = None
    if "--config" in _sys.argv:
        path = _sys.argv[_sys.argv.index("--config") + 1]
    path = path or os.environ.get("AGENT_MONITOR_CONFIG") or os.path.expanduser("~/.config/agent-monitor/config.json")
    try:
        with open(os.path.expanduser(path)) as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    cfg["_dir"] = os.path.dirname(os.path.abspath(os.path.expanduser(path)))
    return cfg


CFG = _load_config()
X = lambda p: os.path.expanduser(os.path.join(CFG["_dir"], p)) if p and not os.path.isabs(os.path.expanduser(p)) else os.path.expanduser(p or "")
LOG_DIR_GLOBS = [X(g) for g in CFG.get("runnerLogDirGlobs", [])]          # folders holding "<task>.log" runner logs
CLAUDE_TASKS_GLOBS = [X(g) for g in CFG.get("claudeTaskOutputGlobs", [])]  # Claude Code background agent transcripts


def log_files():
    return [f for g in LOG_DIR_GLOBS for f in glob.glob(os.path.join(g, "*.log"))]


def find_log(name):
    hits = [f for f in log_files() if os.path.basename(f) == name + ".log"]
    return max(hits, key=os.path.getmtime) if hits else ""
REPO = X(CFG.get("repo", ""))                                   # git repo whose changes / task branches are shown ("" = none)
WORKTREES = X(CFG.get("worktreesDir", os.path.join(os.path.dirname(REPO), os.path.basename(REPO) + "-worktrees") if REPO else ""))
PROGRESS = X(CFG.get("progressDir", "/tmp/agent-progress"))   # one JSON card per task (README: card format)
# Codex accounts: {"label": "Codex-A (worker)", "home": "~/.codex", "runnerAlias": "rahul", "short": "Codex-A"}
CODEX = CFG.get("codexAccounts", [])
HOMES = {a["label"]: X(a["home"]) for a in CODEX}
NOW = time.time()
PHASES = ["Explore", "Build", "Verify", "Handoff"]


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def tail(path, n=4000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _titles():
    try:
        with open(X(CFG.get("titlesFile", "titles.json"))) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


TITLES = _titles()


def pretty(name):
    """Task number and plain-English title: the configured titles file first, then the file slug."""
    m = re.match(r"(?:TASK-)?(\d+)-(.*)", name)
    if not m:
        return ("", name)
    t = TITLES.get(m.group(1), {}).get("title")
    return (m.group(1), t or m.group(2).replace("-", " ").capitalize())


def summary_for(num):
    return TITLES.get(num, {}).get("summary", "")


def ts(s):
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return None


def phase_of(cmd):
    c = cmd.lower()
    if re.search(r"handoff|result-0\d\d|result\.md", c) and re.search(r"cat >|write|tee|>>|edit |out-file|set-content", c):
        return 3
    if re.search(r"jest|vitest|tsc\b|next build|playwright|smoke-crawl|check\.js|npm test|pnpm test|verify|audit|sha256|get-filehash", c):
        return 2
    if re.search(r"^edit |apply patch|\bcat >|cat <<|> [\w./-]+\.(ts|tsx|js|mjs|md|json|ps1|py|css)|set-content|out-file|copy-item|move-item|mkdir", c):
        return 1
    return 0


def clean_cmd(raw):
    if "write_stdin" in raw:
        return None  # polling a running process, not a new command
    m = re.search(r'cmd:"((?:[^"\\]|\\.)*)"', raw)
    if m:
        c = m.group(1).encode().decode("unicode_escape", "ignore")
    elif "apply_patch" in raw or "*** Begin Patch" in raw:
        files = re.findall(r"\*\*\* (?:Add|Update|Delete) File: ([^\\\n]+)", raw)
        c = "edit " + ", ".join(os.path.basename(f) for f in files[:3]) if files else "apply patch"
    else:
        c = raw
    c = re.sub(r"\s+", " ", c).strip()
    c = re.sub(r"^cd \S+ (&&|;) ", "", c)
    return c[:140]


_HEAD = {}  # (path, task name) -> bool; the brief name appears in the first prompt, so one check per file is enough


def session_for(name):
    best = None
    for home in HOMES.values():
        for f in glob.glob(f"{home}/sessions/*/*/*/*.jsonl"):
            try:
                mt = os.path.getmtime(f)
                if NOW - mt > 6 * 3600:
                    continue
                key = (f, name)
                if key not in _HEAD:
                    with open(f, "rb") as fh:
                        _HEAD[key] = name.encode() in fh.read(400_000)
                if _HEAD[key] and (best is None or mt > os.path.getmtime(best)):
                    best = f
            except OSError:
                pass
    return best


_SESS = {}  # path -> {"off": bytes read, "calls": [...], "outputs": {...}, "tokens": int, "last": str}


def session_details(f):
    d = {"commands": 0, "tokens": 0, "last": "", "recent": [], "phase": 0, "edits": 0, "lastActive": 0}
    if not f:
        return d
    st = _SESS.setdefault(f, {"off": 0, "calls": [], "outputs": {}, "tokens": 0, "last": ""})
    calls, outputs = st["calls"], st["outputs"]
    d["tokens"], d["last"] = st["tokens"], st["last"]
    try:
        with open(f, "rb") as fb:
            fb.seek(st["off"])
            chunk = fb.read()
        end = chunk.rfind(b"\n") + 1  # only complete lines; the rest is read next tick
        st["off"] += end
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            if True:
                if '"payload":{"type":"custom_tool_call"' in line or '"payload":{"type":"function_call"' in line:
                    try:
                        j = json.loads(line)
                    except Exception:
                        continue
                    p = j.get("payload", {})
                    c = clean_cmd(p.get("input") or p.get("arguments") or "")
                    if c is None:
                        continue
                    calls.append({"id": p.get("call_id"), "cmd": c, "t": ts(j.get("timestamp", ""))})
                    st["edits"] = st.get("edits", 0) + (c.startswith("edit ") or c == "apply patch")
                    st["phase"] = max(st.get("phase", 0), phase_of(c))
                elif '"payload":{"type":"custom_tool_call_output"' in line or '"payload":{"type":"function_call_output"' in line:
                    cid = re.search(r'"call_id":"([^"]+)"', line)
                    wall = re.search(r"Wall time ([\d.]+) seconds", line)
                    code = re.search(r"(?:exited with code|Exit code:?) (-?\d+)", line)
                    if cid:
                        outputs[cid.group(1)] = (float(wall.group(1)) if wall else None, int(code.group(1)) if code else None)
                elif '"total_token_usage"' in line:
                    m = re.search(r'"total_token_usage":\{[^}]*"total_tokens":(\d+)', line)
                    if m:
                        d["tokens"] = int(m.group(1))
                if '"payload":{"type":"agent_message"' in line or ('"type":"message"' in line and '"role":"assistant"' in line):
                    m = re.search(r'"(?:message|text)":"((?:[^"\\]|\\.){10,240})', line)
                    if m:
                        d["last"] = m.group(1).encode().decode("unicode_escape", "ignore")
        st["tokens"], st["last"] = d["tokens"], d["last"]
        if len(calls) > 400:  # keep memory bounded; counts stay right via the offset
            st.setdefault("dropped", 0)
            st["dropped"] += len(calls) - 400
            del calls[:-400]
            keep = {c["id"] for c in calls}
            for k in [k for k in outputs if k not in keep]:
                del outputs[k]
    except OSError:
        return d
    d["commands"] = len(calls) + st.get("dropped", 0)
    d["edits"], d["phase"] = st.get("edits", 0), st.get("phase", 0)
    stamps = [c["t"] for c in calls if c["t"]]
    d["lastActive"] = int(NOW - max(stamps)) if stamps else 0
    for c in calls[-7:][::-1]:
        wall, code = outputs.get(c["id"], (None, None))
        state = "running" if c["id"] not in outputs else ("fail" if code not in (None, 0) else "ok")
        dur = wall if wall is not None else (NOW - c["t"] if state == "running" and c["t"] else None)
        d["recent"].append({"cmd": c["cmd"], "state": state, "secs": round(dur, 1) if dur is not None else -1})
    return d


_RATE = {}  # (task, metric label) -> [(t, done)] samples, for speed and time-left


def metrics_of(task, raw):
    """Normalise card metrics and add speed/time left from successive readings (kept in memory in --serve mode)."""
    out = []
    for m in (raw or [])[:6]:
        try:
            label, done = str(m.get("label", ""))[:40], float(m.get("done"))
        except (TypeError, ValueError, AttributeError):
            continue
        total = m.get("total")
        total = float(total) if isinstance(total, (int, float)) and total > 0 else None
        item = {"label": label, "done": done, "total": total, "kind": str(m.get("kind", ""))[:10], "rate": "", "left": ""}
        hist = _RATE.setdefault((task, label), [])
        if not hist or hist[-1][1] != done:
            hist.append((NOW, done))
        del hist[:-30]
        old = [h for h in hist if NOW - h[0] <= 1800]
        if len(old) >= 2 and old[-1][1] > old[0][1] and old[-1][0] > old[0][0]:
            per_min = (old[-1][1] - old[0][1]) / ((old[-1][0] - old[0][0]) / 60)
            item["rate"] = f"≈{per_min:.0f}/min" if per_min >= 1 else f"≈{per_min * 60:.0f}/h"
            if total and done < total and item["kind"] != "bad":
                mins = (total - done) / per_min
                item["left"] = f"~{mins:.0f} min left" if mins < 120 else f"~{mins / 60:.1f} h left"
        out.append(item)
    return out


_AUTO = {}  # task -> (time, metrics) cache for git-derived metrics


def auto_metrics(name):
    """Cheap facts for cards that report no metrics: commits on the task branch, files changed, handoff written."""
    hit = _AUTO.get(name)
    if hit and NOW - hit[0] < 30:
        return hit[1]
    m = []
    branch = f"task/{name}"
    n = sh(f"git --no-optional-locks -C '{REPO}' rev-list --count main..{branch} 2>/dev/null").strip() if REPO else ""
    if n.isdigit():
        m.append({"label": "Commits on task branch", "done": float(n), "total": None, "kind": "", "rate": "", "left": ""})
        files = sh(f"git --no-optional-locks -C '{REPO}' diff --name-only main...{branch} 2>/dev/null").split()
        m.append({"label": "Files changed", "done": float(len(files)), "total": None, "kind": "", "rate": "", "left": ""})
    wt = os.path.join(WORKTREES, name) if WORKTREES else ""
    if wt and os.path.isdir(wt):
        num = name.split("-")[0]
        has = bool(glob.glob(os.path.join(wt, "docs", "tasks", f"{num}*handoff*.md")))
        m.append({"label": "Handoff written", "done": 1.0 if has else 0.0, "total": 1.0, "kind": "", "rate": "", "left": ""})
    _AUTO[name] = (NOW, m)
    return m


def with_eta(steps, note):
    """Adds per-step and whole-task ETAs from each step's etaMin (estimated minutes) and
    startedAt (unix seconds when the step became active). Steps without etaMin show 'ETA ?'."""
    out, left, known = [], 0.0, True
    for s in steps[:10]:
        title, status = str(s.get("title", ""))[:70], s.get("status", "pending")
        eta = s.get("etaMin")
        if status != "done":
            if isinstance(eta, (int, float)):
                rem = float(eta)
                if status == "active" and s.get("startedAt"):
                    rem = eta - (NOW - float(s["startedAt"])) / 60
                if rem < 0:
                    title += f" · overrun {-rem:.0f}m"
                    rem = 1
                else:
                    title += f" · ~{max(1, round(rem))}m" + (" left" if status == "active" else "")
                left += max(1, rem)
            else:
                known = False
                title += " · ETA ?"
        out.append({"title": title, "status": status})
    if out and any(s["status"] != "done" for s in out):
        finish = time.strftime("%H:%M", time.localtime(NOW + left * 60))
        prefix = f"ETA ~{max(1, round(left))} min (≈{finish})" if known else (f"ETA ≥{round(left)} min" if left else "ETA ?")
        note = f"{prefix} · {note}"
    return out, note


def codex_short(alias):
    """Display name for a Codex runner alias (from the runner log or CODEX_HOME)."""
    for a in CODEX:
        if alias and alias.lower() in (str(a.get("runnerAlias", "")).lower(), a["label"].lower()):
            return a.get("short", a["label"])
    return CODEX[0].get("short", CODEX[0]["label"]) if CODEX else "Codex"


def running():
    out = []
    for line in sh("pgrep -f 'codex exec' | xargs -I{} ps -o pid=,etime=,command= -p {}").splitlines():
        pid = line.split()[0] if line.split() else ""
        m = re.search(CFG.get("codexTaskPattern", r"complete (\S+\.md)"), line)
        if not m:
            continue
        name = os.path.basename(m.group(1))[:-3]
        if any(t["key"] == name for t in out):
            continue
        log = find_log(name)
        txt = tail(log, 200000)
        try:
            with open(log, encoding="utf-8", errors="replace") as fh:
                accts = re.findall(r"codex account '(\w+)'", fh.read())  # whole log: switches can be far from the end
        except OSError:
            accts = []
        # The process environment is authoritative for the account (CODEX_HOME), logs are a fallback.
        env = sh(f"ps eww -o command= -p {pid}") if pid.isdigit() else ""
        home = re.search(r"CODEX_HOME=(\S+)", env)
        if home:
            hp = os.path.realpath(os.path.expanduser(home.group(1).rstrip("/")))
            for a in CODEX:
                if os.path.realpath(X(a["home"])) == hp:
                    accts.append(a.get("runnerAlias", a["label"]))
        num, title = pretty(name)
        parts = [int(x) for x in re.split(r"[-:]", line.split()[1])]
        secs = sum(p * mu for p, mu in zip(reversed(parts), [1, 60, 3600, 86400]))
        sd = session_details(session_for(name))
        task = {"summary": summary_for(num), "key": name, "id": num, "title": title, "account": codex_short(accts[-1] if accts else ""),
                "elapsed": secs, "steps": [], "note": "", "commands": sd["commands"], "tokens": sd["tokens"], "edits": sd["edits"],
                "recent": sd["recent"], "lastActive": sd["lastActive"], "estimated": True,
                "failovers": len(re.findall(r"hit its limit", txt))}
        try:
            with open(os.path.join(PROGRESS, name + ".json")) as fh:
                p = json.load(fh)
            task["steps"], task["note"] = with_eta(p.get("steps", []), str(p.get("note", ""))[:200])
            task["estimated"] = False
            task["title"] = str(p.get("title") or task["title"])[:80]
            task["metrics"] = metrics_of(name, p.get("metrics"))
            task["tags"] = [str(t)[:20] for t in (p.get("tags") or [])][:6]
            task["summary"] = str(p.get("summary") or summary_for(num))[:160]
        except Exception:
            ph = sd["phase"]
            task["steps"] = [{"title": n, "status": "done" if i < ph else "active" if i == ph else "pending"} for i, n in enumerate(PHASES)]
        if not task.get("metrics"):
            task["metrics"] = auto_metrics(name)
        if not task["note"]:
            task["note"] = sd["last"][:200]
        out.append(task)
    # Headless Claude workers (`claude -p "... docs/tasks/NNN-x.md ..."`): shown even before they write a card.
    for line in sh("pgrep -fl 'claude -p'").splitlines():
        m = re.search(CFG.get("headlessTaskPattern", r"docs/tasks/(\d{3}-[\w-]+)\.md"), line)
        if not m or "pgrep" in line:
            continue
        name = m.group(1)
        if any(t["key"] == name for t in out):
            continue
        card = os.path.join(PROGRESS, name + ".json")
        if os.path.exists(card):
            continue  # its interactive card is listed below
        num, title = pretty(name)
        out.append({"key": name, "id": num, "title": title, "summary": summary_for(num), "account": CFG.get("headlessClaudeLabel", "Claude (headless)"),
                    "elapsed": 0, "steps": [], "note": "Headless Claude worker running (no progress card yet)", "commands": 0, "tokens": 0,
                    "edits": 0, "recent": [], "lastActive": 0, "estimated": True, "failovers": 0})
    # Interactive Codex sessions do not run under `codex exec`.
    for path in glob.glob(os.path.join(PROGRESS, '*.json')):
        try:
            with open(path) as fh:
                p = json.load(fh)
            if p.get('source') != 'interactive' or not p.get('active'):
                continue
            name = os.path.basename(path)[:-5]
            if any(t['key'] == name for t in out):
                continue
            num, title = pretty(name)
            steps, note = with_eta(p.get('steps', []), str(p.get('note', ''))[:200])
            idle = int(NOW - os.path.getmtime(path))
            if idle > 1800:
                note = f"STALE: no update for {idle // 60} min · {note}"
            out.append({'key': name, 'id': num, 'title': str(p.get('title') or title)[:80], 'summary': str(p.get('summary') or summary_for(num))[:160],
                        'account': p.get('account', 'agent'),
                        'elapsed': max(0, int(NOW - p.get('startedAt', NOW))),
                        'steps': steps, 'note': note,
                        'metrics': metrics_of(name, p.get('metrics')) or auto_metrics(name),
                        'tags': [str(t)[:20] for t in (p.get('tags') or [])][:6],
                        'commands': 0, 'tokens': 0, 'edits': 0, 'recent': [],
                        'lastActive': max(0, int(NOW - os.path.getmtime(path))),
                        'estimated': False, 'failovers': 0})
        except (OSError, ValueError, TypeError):
            continue
    return out


def queued():
    """Cards waiting to start: status 'queued' (waits on a condition) or 'blocked' (waits on the owner)."""
    out = []
    for path in glob.glob(os.path.join(PROGRESS, '*.json')):
        try:
            with open(path) as fh:
                p = json.load(fh)
            st = p.get('status')
            if st not in ('queued', 'blocked') or p.get('active'):
                continue
            num, title = pretty(os.path.basename(path)[:-5])
            out.append({'id': num, 'title': str(p.get('title') or title)[:80], 'summary': str(p.get('summary') or summary_for(num))[:160],
                        'worker': str(p.get('account', ''))[:40], 'waitingFor': str(p.get('waitingFor') or p.get('note', ''))[:160],
                        'blocked': st == 'blocked'})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda q: q['id'])


def claude_agents():
    """Background Claude agents of this Claude Code session, from their local transcripts."""
    out = []
    for f in [f for g in CLAUDE_TASKS_GLOBS for f in glob.glob(g)]:
        try:
            if NOW - os.path.getmtime(f) > 120 or os.path.getsize(f) < 200:
                continue
            title, tools, recent = "", 0, []
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if not line.startswith("{"):
                        break  # plain command output, not an agent transcript
                    if not title and '"type":"user"' in line:
                        m = re.search(r'"(?:text|content)":"((?:[^"\\]|\\.){3,300})', line)
                        if m and not m.group(1).startswith("You are powered"):
                            title = re.sub(r"\\n.*", "", m.group(1))[:80]
                    if '"tool_use"' in line:
                        for mm in re.finditer(r'"type":"tool_use","id":"[^"]+","name":"(\w+)","input":\{(.{0,500})', line):
                            tools += 1
                            desc = re.search(r'"description":"((?:[^"\\]|\\.){1,110})"', mm.group(2)) or re.search(r'"(?:command|file_path|pattern|prompt)":"((?:[^"\\]|\\.){1,110})', mm.group(2))
                            recent.append(f"{mm.group(1)} · {desc.group(1) if desc else ''}".rstrip(" ·"))
            if tools or title:
                out.append({"title": title or "Claude agent", "tools": tools, "recent": recent[-6:][::-1], "lastActive": int(NOW - os.path.getmtime(f))})
        except OSError:
            pass
    return out


def finished():
    rows, seen = [], set()
    for f in sorted(log_files(), key=os.path.getmtime, reverse=True)[:12]:
        m = re.findall(r"\[(\d\d:\d\d:\d\d)\] (\S+) finished with exit (\d+)", tail(f))
        if m:
            t, name, code = m[-1]
            if name in seen:
                continue
            seen.add(name)
            try:
                with open(os.path.join(PROGRESS, name + ".json")) as fh:
                    if json.load(fh).get("active"):
                        continue  # paused/resuming task: it is shown in progress, not as finished
            except (OSError, ValueError):
                pass
            num, title = pretty(name)
            rows.append({"id": num, "title": title, "time": t[:5], "ok": code == "0", "_t": os.path.getmtime(f)})
    # Tasks finished by the orchestrator or Claude workers: progress cards marked done in the last 12 h.
    for path in glob.glob(os.path.join(PROGRESS, "*.json")):
        try:
            mt = os.path.getmtime(path)
            if NOW - mt > 12 * 3600:
                continue
            with open(path) as fh:
                p = json.load(fh)
            name = os.path.basename(path)[:-5]
            if p.get("active") or p.get("status") != "done" or name in seen or "demo" in name:
                continue
            seen.add(name)
            num, title = pretty(name)
            rows.append({"id": num, "title": str(p.get("title") or title)[:80], "time": time.strftime("%H:%M", time.localtime(mt)), "ok": True, "_t": mt})
        except (OSError, ValueError, TypeError):
            continue
    rows.sort(key=lambda r: -r["_t"])
    for r in rows:
        r.pop("_t", None)
    return rows[:8]


def usage():
    res, lim = [], []
    for f in sorted(log_files(), key=os.path.getmtime, reverse=True)[:8]:
        lim += re.findall(r"\[(\d\d:\d\d):\d\d\] '(\w+)' hit its limit", tail(f, 100000))
    for label, home in HOMES.items():
        p5 = p7 = 0.0
        reset = reset7 = 0
        # Several sessions can run on one account at once. Use the reading with the newest
        # timestamp across recently active sessions, not the last line of the newest file.
        latest = ""
        files = [f for f in glob.glob(f"{home}/sessions/*/*/*/*.jsonl") if NOW - os.path.getmtime(f) < 8 * 3600]
        for f in sorted(files, key=os.path.getmtime, reverse=True)[:12]:
            for line in tail(f, 400000).splitlines():
                if '"rate_limits"' not in line or '"primary":{' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                rl = (d.get("payload") or {}).get("rate_limits") or {}
                pr, se, ts = rl.get("primary"), rl.get("secondary") or {}, str(d.get("timestamp", ""))
                if pr and rl.get("limit_id", "codex") == "codex" and ts > latest:
                    latest = ts
                    p5, reset = float(pr.get("used_percent") or 0), int(pr.get("resets_at") or 0)
                    p7, reset7 = float(se.get("used_percent") or 0), int(se.get("resets_at") or 0)
        if reset and reset < NOW:
            p5, reset = 0.0, 0
        if reset7 and reset7 < NOW:
            p7 = 0.0
        alias = next((str(a.get("runnerAlias", "")).lower() for a in CODEX if a["label"] == label), "")
        l = [t for t, a in lim if a.lower() == alias]
        res.append({"name": label, "tool": "Codex", "five": p5, "seven": p7, "resetIn": max(0, int(reset - NOW)) if reset else 0, "lastLimit": l[-1] if l else ""})
    # One status file per Claude account (each account's statusline.sh writes its own).
    # Claude accounts: {"label": "Claude-A (orchestrator)", "statusFile": "/tmp/claude-status-a.json", "limitLogGlobs": ["…/*w3*.log"]}
    claude = [(a["label"], X(a["statusFile"])) for a in CFG.get("claudeAccounts", [])]
    for i, (label, path) in enumerate(claude):
        try:
            with open(path) as fh:
                c = json.load(fh)
            rl = c.get("rate_limits", {})
            five, seven = rl.get("five_hour"), rl.get("seven_day") or {}
            age = int(NOW - os.path.getmtime(path))
            reset = float((five or {}).get("resets_at") or 0)
            expired = bool(reset) and reset < NOW  # window rolled over since the reading: the old % no longer applies
            row = {"name": label, "tool": "Claude", "five": 0.0 if (expired or not five) else float(five.get("used_percentage") or 0),
                   "seven": float(seven.get("used_percentage") or 0), "resetIn": max(0, int(reset - NOW)) if reset else 0,
                   "lastLimit": "", "age": age, "unknown": expired or not five}
            hit = claude_limit_from_logs(label)  # from headless worker logs (configured per account)
            if hit:  # a headless run reported "You've hit your 5-hour limit · resets 3:50pm" and the reset is still ahead
                row.update(five=100.0, resetIn=hit, unknown=False)
            res.insert(i, row)
        except Exception:
            res.insert(i, {"name": label, "tool": "Claude", "five": 0.0, "seven": 0.0, "resetIn": 0, "lastLimit": "", "age": -1, "unknown": True})
    return res


def claude_limit_from_logs(label):
    """Seconds until reset if a recent headless Claude worker log says the 5-hour limit was hit."""
    globs = next((a.get("limitLogGlobs", []) for a in CFG.get("claudeAccounts", []) if a["label"] == label), [])
    newest = None
    for f in [f for g in globs for f in glob.glob(X(g))]:
        if NOW - os.path.getmtime(f) > 6 * 3600:
            continue
        m = re.findall(r"hit your 5-hour limit\D+resets (\d{1,2}):?(\d{2})?\s*([ap]m)", tail(f, 4000))
        if m and (newest is None or os.path.getmtime(f) > newest[0]):
            newest = (os.path.getmtime(f), m[-1])
    if not newest:
        return 0
    h, mi, ap = int(newest[1][0]) % 12 + (12 if newest[1][2] == "pm" else 0), int(newest[1][1] or 0), None
    t = time.localtime(newest[0])
    reset = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, -1))
    if reset < newest[0]:
        reset += 86400
    return int(reset - NOW) if reset > NOW else 0


def cached_usage(path=None, ttl=20):
    """Usage parsing is the slowest part; the panel refreshes every 2 s, so reuse it for 20 s."""
    path = path or X(CFG.get("usageCacheFile", "/tmp/agent-monitor-usage.json"))
    try:
        if NOW - os.path.getmtime(path) < ttl:
            with open(path) as fh:
                return json.load(fh)
    except (OSError, ValueError):
        pass
    res = usage()
    try:
        with open(path + ".tmp", "w") as fh:
            json.dump(res, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass
    return res


def repo_changes():
    areas = {}
    for l in (sh(f"git --no-optional-locks -C '{REPO}' status --short") if REPO else "").splitlines():
        parts = l[3:].strip().strip('"').split("/")
        key = "/".join(parts[:2]) if parts[0] in ("apps", "packages", "docs", "scripts") and len(parts) > 1 else parts[0]
        areas[key] = areas.get(key, 0) + 1
    return [{"area": k, "files": v} for k, v in sorted(areas.items(), key=lambda x: -x[1])[:8]]


def up(url):
    try:
        urllib.request.urlopen(url, timeout=0.8)
        return True
    except Exception:
        return False


WORKERS = [tuple(w) for w in CFG.get("workers", [])]  # [["Claude-A", "W0"], ["Codex-B", "W1"], …] prefixes shown on the panel


def numbered(label):
    """Prefix the worker number (W0 orchestrator, W1-W3 workers) to any account label that names one."""
    if not isinstance(label, str) or label[:1] == "W" and label[1:2].isdigit():
        return label
    for name, num in WORKERS:
        label = label.replace(name, f"{num} {name}")
    return label


def _number_all(d):
    for t in d["running"]:
        t["account"] = numbered(t.get("account", ""))
    for q in d["queued"]:
        q["worker"] = numbered(q.get("worker", ""))
    for a in d["accounts"]:
        a["name"] = numbered(a["name"])
    d["accounts"].sort(key=lambda a: a["name"][:2])
    return d


def snapshot():
    return _number_all(_snapshot())


def _snapshot():
    return {
    "time": time.strftime("%H:%M:%S"),
    "running": running(),
    "queued": queued(),
    "agents": claude_agents(),
    "finished": finished(),
    "accounts": cached_usage(),
    "commit": sh(f"git --no-optional-locks -C '{REPO}' log -1 --format='%h · %s · %ar'").strip()[:90] if REPO else "",
    "changes": repo_changes(),
    "title": CFG.get("title", "Agent monitor"),
    "health": [{"label": h["label"], "up": up(h["url"]), "group": h.get("group", "")} for h in CFG.get("health", [])],
    }


_USAGE = {"t": 0, "v": []}


def serve(interval=2.0):
    """Long-lived mode for the panel: one JSON line per tick, caches kept in memory (no process start per refresh)."""
    import sys
    global NOW, cached_usage
    file_cached = cached_usage

    def mem_usage():
        if NOW - _USAGE["t"] > 20:
            _USAGE["v"], _USAGE["t"] = file_cached(), NOW
        return _USAGE["v"]
    cached_usage = mem_usage
    while True:
        NOW = time.time()
        try:
            sys.stdout.write(json.dumps(snapshot()) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            return
        except Exception as e:  # never die on one bad file; report and keep going
            sys.stderr.write(f"status error: {e}\n")
        time.sleep(max(0.2, interval - (time.time() - NOW)))


if __name__ == "__main__":
    import sys
    if "--serve" in sys.argv:
        serve()
    else:
        print(json.dumps(snapshot()))
