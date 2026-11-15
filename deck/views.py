"""View models for the HTTP API, ``deck snapshot`` and both UIs (design §12).

The ETA / rate / time-left maths is ported from v1 ``with_eta`` and ``metrics_of``; rates now come
from the durable ``card_history`` instead of an in-memory cache.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Optional

from . import clock, policy, states
from .config import Config
from .errors import NotFound
from .events import rank

DAY = 86400
USAGE_BUCKET = 900          # usage history: one point per 15 min over 24 h (96 points at most)
BURN_WINDOW = 3600          # burn rate: pct5 slope over the last hour of the current 5-hour window
BURN_MIN_SPAN = 300         # ... measured over at least 5 minutes of readings


def usage_series(readings: list, limits: list, now: int) -> dict:
    """Workers-section usage block from raw `usage` rows (oldest first) and `limits` rows.

    points: last reading per 15-min bucket over 24 h, {ts, pct5, pct7}. resets: 5-hour reset times seen in the
    readings or limits, from 24 h ago up to the next reset. burn5PerHour: slope of pct5 over the last hour within
    the current window (None when there is under 5 min of readings or the window rolled over). minutesToLimit:
    (100 - pct5) / burn, None when burn is not positive."""
    t0 = now - DAY
    rows = [r for r in readings if r.get("read_at") is not None and t0 <= r["read_at"] <= now]
    buckets = {}
    for r in rows:
        buckets[(r["read_at"] - t0) // USAGE_BUCKET] = r
    points = [{"ts": r["read_at"], "pct5": r.get("pct5"), "pct7": r.get("pct7")} for _k, r in sorted(buckets.items())]
    nxt = None
    resets = set()
    for r in rows:
        if r.get("resets5"):
            resets.add(int(r["resets5"]))
    for lim in limits:
        if lim.get("resets_at"):
            resets.add(int(lim["resets_at"]))
    latest = rows[-1] if rows else None
    if latest and latest.get("resets5") and latest["resets5"] > now:
        nxt = int(latest["resets5"])
    resets = sorted(x for x in resets if t0 <= x <= (nxt or now))
    burn = mins = None
    if latest and latest.get("pct5") is not None and not (latest.get("resets5") and latest["resets5"] <= now):
        win = [r for r in rows if r["read_at"] >= now - BURN_WINDOW and r.get("pct5") is not None
               and r.get("resets5") == latest.get("resets5")]
        if len(win) >= 2 and win[-1]["read_at"] - win[0]["read_at"] >= BURN_MIN_SPAN:
            burn = max(0.0, (win[-1]["pct5"] - win[0]["pct5"]) / ((win[-1]["read_at"] - win[0]["read_at"]) / 3600))
            burn = round(burn, 1)
            if burn > 0:
                mins = max(0, round((100 - latest["pct5"]) / burn * 60))
    return {"points": points, "resets": resets, "burn5PerHour": burn, "minutesToLimit": mins}


def _midnight(now: int) -> int:
    lt = time.localtime(now)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _hm(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "?"


def step_eta(steps: list, now: int):
    """(remaining minutes or None if unknown, list of per-step remaining) — v1 ``with_eta`` maths."""
    left, known = 0.0, True
    for s in steps:
        if s.get("status") in ("done", "skipped", "failed"):
            continue
        eta = s.get("etaMin")
        if isinstance(eta, (int, float)):
            rem = float(eta)
            if s.get("status") == "active" and s.get("startedAt"):
                rem = eta - (now - float(s["startedAt"])) / 60
            left += max(1.0, rem)
        else:
            known = False
    return (left if known else None), left


def metric_rates(history: list, metrics: list, now: int) -> list:
    """Add ``rate`` (per minute) and ``left`` (minutes) from metric readings of the last 30 min."""
    out = []
    for m in metrics or []:
        pts = [(h["ts"], float(h["patch"]["args"].get("done", 0))) for h in history
               if h.get("op") == "metric" and h["patch"].get("args", {}).get("label") == m["label"] and now - h["ts"] <= 1800]
        item = {"label": m["label"], "done": m.get("done", 0), "total": m.get("total"), "kind": m.get("kind") or "count",
                "rate": None, "left": None}
        if len(pts) >= 2 and pts[-1][1] > pts[0][1] and pts[-1][0] > pts[0][0]:
            per_min = (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 60)
            item["rate"] = round(per_min, 3)
            if item["total"] and item["done"] < item["total"] and item["kind"] != "bad":
                item["left"] = round((item["total"] - item["done"]) / per_min, 1)
        out.append(item)
    return out


class Views:
    def __init__(self, cfg: Config, g=None, stores: Optional[dict] = None):
        from .store import GlobalStore
        self.cfg = cfg
        self.g = g or GlobalStore(cfg)
        self.stores = stores if stores is not None else {}

    def store(self, pid):
        from .store import ProjectStore
        if pid not in self.cfg.projects:
            raise NotFound(f"project {pid} is not configured")
        if pid not in self.stores:
            self.stores[pid] = ProjectStore(pid, self.cfg, self.g)
        return self.stores[pid]

    # ---------------- tasks
    def task_vm(self, s, t: dict, now: int, card=None, history=None) -> dict:
        card = card if card is not None else (s.card(t["id"]) or {})
        steps = card.get("steps") or []
        done = sum(1 for x in steps if x.get("status") in ("done", "skipped"))
        active = next((i for i, x in enumerate(steps) if x.get("status") == "active"), None)
        if active is None:
            active = next((i for i, x in enumerate(steps) if x.get("status") == "pending"), None)
        left, _ = step_eta(steps, now)
        a = (t.get("attempts") or [None])[-1] if t.get("attempts") else None
        last = (a or {}).get("lastHeartbeat")
        hist = history if history is not None else s.card_history(t["id"], 200)
        deps = []
        for d in t.get("deps") or []:
            try:
                deps.append({"id": d, "state": s.get(d)["state"]})
            except NotFound:
                deps.append({"id": d, "state": "missing"})
        warnings = list(card.get("warnings") or [])
        return {
            "id": t["id"], "num": int(t["id"].split("-")[0]), "title": t["title"], "summary": t.get("summary", ""),
            "tags": t.get("tags") or [], "priority": t["priority"], "size": t["size"], "state": t["state"],
            "stateReason": t.get("stateReason"), "worker": t.get("worker"),
            "progress": round(done / len(steps), 3) if steps else None,
            "step": ({"title": steps[active]["title"], "index": active + 1, "count": len(steps),
                      "etaMin": steps[active].get("etaMin")} if active is not None else None),
            "etaFinish": int(now + left * 60) if left is not None and steps and done < len(steps) else None,
            "metrics": metric_rates(hist, card.get("metrics") or [], now),
            "lastActiveSec": (now - last) if last and t["state"] in states.ACTIVE_ATTEMPT else None,
            "warnings": warnings, "deps": deps, "resumeAt": t.get("resumeAt"), "kind": t.get("kind") or "normal",
            "note": card.get("note"), "question": card.get("question"), "project": s.pid, "rev": t.get("rev"),
        }

    def review_vm(self, s, t: dict, now: int) -> dict:
        h = s.handoff(t["id"]) or {}
        d = h.get("data") or {}
        req = (t.get("review") or {}).get("requestedAt") or t.get("stateSince") or now
        return {"task": self.task_vm(s, t, now), "attempt": h.get("attempt"), "summary": d.get("summary", ""),
                "outcome": d.get("outcome", "unknown"), "valid": bool(h.get("valid")),
                "doneWhen": d.get("doneWhen") or [], "tests": d.get("tests") or [], "risks": d.get("risks") or [],
                "decisionsNeeded": d.get("decisionsNeeded") or [], "git": d.get("git") or {},
                "waitingSec": max(0, now - req), "pilot": t.get("kind") == "pilot", "unlocks": s.dependants(t["id"])}

    # ---------------- accounts
    def account_vm(self, a, now: int, titles=None, done_today=None) -> dict:
        r = self.g.usage.latest(a.id)
        lim = self.g.limits.active(a.id, now)
        st = self.g.account_state.get(a.id)
        pct5 = r["pct5"] if r else None
        confidence = "unknown"
        if lim:
            pct5, confidence = 100, "high"
        elif r:
            if r.get("resets5") and r["resets5"] <= now:
                pct5, confidence = 0, "low"
            else:
                confidence = "high" if now - r["read_at"] < 600 else "stale"
        state = "cooling" if lim else (st["state"] if st else ("unknown" if not r else "ok"))
        lanes = []
        used = 0
        for w in self.cfg.workers.values():
            if w.account != a.id:
                continue
            rows = {x["lane"]: x for x in self.g.lanes.of(w.id)}
            for n in range(1, w.lanes + 1):
                x = rows.get(n)
                if x and x["state"] == "running" and x.get("task"):
                    used += 1
                    lanes.append(self.lane_vm(w.id, n, x, now))
                elif lim:
                    lanes.append({"worker": w.id, "lane": n, "state": "cooling",
                                  "task": (x or {}).get("task"), "resumeAt": (lim[0]["resets_at"] or now) + 90})
                else:
                    lanes.append({"worker": w.id, "lane": n, "state": "free"})
        cap = dict((st or {}).get("capacity") or {})
        cap.setdefault("lanesUsed", used)
        cap.setdefault("lanesMax", a.parallel_max)
        cap.setdefault("spare", False)
        if lim:
            cap["why"] = cap.get("why") or f"limit — resumes {_hm((lim[0]['resets_at'] or now) + 90)}"
        cap.setdefault("why", None if a.dispatch else "dispatch off (usage shown only)")
        queued = cap.pop("queued", None)                      # the planner's per-account queue (dispatcher)
        hist = self.g.usage.since(a.id, now - DAY) if hasattr(self.g.usage, "since") else []
        lims = [dict(x) for x in self.g.conn.execute(
            "SELECT resets_at FROM limits WHERE account=? AND (resets_at IS NULL OR resets_at>=?)",
            (a.id, now - DAY))] if hasattr(self.g, "conn") else []
        out = {"id": a.id, "label": a.label, "tool": a.tool, "scope": a.scope, "pct5": pct5,
               "pct7": r["pct7"] if r else None, "resets5": (lim[0]["resets_at"] if lim else (r or {}).get("resets5")),
               "resets7": (r or {}).get("resets7"), "readAgeSec": (now - r["read_at"]) if r else None,
               "confidence": confidence, "state": state, "dispatch": a.dispatch, "capacity": cap, "lanes": lanes,
               "usage": usage_series(hist, lims, now) if hist or lims else None,
               "doneToday": (done_today or {}).get(a.id, 0) if done_today is not None else None,
               "queued": queued if queued is not None else []}
        if (a.raw or {}).get("color"):
            out["color"] = a.raw["color"]
        if lim and out["usage"]:
            out["usage"]["burn5PerHour"] = out["usage"]["minutesToLimit"] = None      # at the limit: no estimate
        return out

    def lane_vm(self, worker, n, row, now) -> dict:
        out = {"worker": worker, "lane": n, "state": "running", "project": row.get("project"), "task": row.get("task")}
        try:
            s = self.store(row["project"])
            t = s.get(row["task"])
            vm = self.task_vm(s, t, now)
            out.update(title=t["title"], step=(vm["step"] or {}).get("title"), stepIndex=(vm["step"] or {}).get("index"),
                       stepCount=(vm["step"] or {}).get("count"),
                       etaMin=round((vm["etaFinish"] - now) / 60) if vm["etaFinish"] else None,
                       lastActiveSec=vm["lastActiveSec"])
        except (NotFound, KeyError, TypeError):
            pass
        return out

    def done_today(self, now) -> dict:
        """{account: attempts that ended with a handoff since local midnight}, across every project."""
        mid = _midnight(now)
        out = {}
        for p in self.cfg.projects.values():
            try:
                rows = self.store(p.id).attempts_ended_since(mid)
            except (NotFound, AttributeError):
                continue
            for r in rows:
                if r["outcome"] == "handoff" and r["account"]:
                    out[r["account"]] = out.get(r["account"], 0) + r["n"]
        return out

    def accounts(self, now=None) -> list:
        now = clock.now() if now is None else now
        done = self.done_today(now)
        return [self.account_vm(a, now, done_today=done) for a in self.cfg.accounts.values()]

    def needs_you(self, now, limit=30) -> list:
        """Everything waiting on a person, across projects: questions, blocked/failed tasks, reviews, then recent
        alert events on live tasks. Items: {kind, project, task, num, title, text, rev, options?, eventId?}."""
        items = {"question": [], "blocked": [], "review": [], "alert": []}
        for p in self.cfg.projects.values():
            s = self.store(p.id)
            tasks = {t["id"]: t for t in s.list()}
            for t in tasks.values():
                base = {"project": p.id, "task": t["id"], "num": int(t["id"].split("-")[0]), "title": t["title"],
                        "rev": t.get("rev")}
                if t["state"] == "blocked":
                    q = (s.card(t["id"]) or {}).get("question")
                    if q:
                        who = (t.get("attempts") or [{}])[-1].get("worker") if t.get("attempts") else None
                        items["question"].append({**base, "kind": "question",
                                                  "text": (f"{who} asks · " if who else "") + str(q)[:160]})
                    else:
                        items["blocked"].append({**base, "kind": "blocked", "text": t.get("stateReason") or "blocked"})
                elif t["state"] == "failed":
                    items["blocked"].append({**base, "kind": "failed", "text": t.get("stateReason") or "failed"})
                elif t["state"] == "review":
                    h = (s.handoff(t["id"]) or {}).get("data") or {}
                    dw = h.get("doneWhen") or []
                    req = (t.get("review") or {}).get("requestedAt") or t.get("stateSince") or now
                    items["review"].append({**base, "kind": "review", "text": " · ".join(x for x in (
                        "PILOT" if t.get("kind") == "pilot" else "", h.get("outcome") or "",
                        f"{sum(1 for d in dw if d.get('met'))}/{len(dw)} done-when" if dw else "",
                        f"waiting {max(0, now - req) // 60} min") if x), "waitingSec": max(0, now - req)})
            seen = set()
            for e in reversed(s.events.last(200, min_severity="alert")):
                t = tasks.get(e.get("task") or "")
                if now - e["ts"] >= DAY or t is None or t["state"] in states.TERMINAL or e["task"] in seen:
                    continue
                if t["state"] in ("blocked", "failed", "review"):
                    continue                                    # already listed above
                seen.add(e["task"])
                items["alert"].append({"project": p.id, "task": t["id"], "num": int(t["id"].split("-")[0]),
                                       "title": t["title"], "rev": t.get("rev"), "kind": "alert",
                                       "text": e["text"][:200], "eventId": e.get("gid") or e["id"]})
        items["review"].sort(key=lambda x: -x["waitingSec"])
        return (items["question"] + items["blocked"] + items["review"] + items["alert"])[:limit]

    # ---------------- overview
    def overview(self, now=None) -> dict:
        now = clock.now() if now is None else now
        mid = _midnight(now)
        tiles, review_n, questions, eligible = [], 0, 0, 0
        for p in self.cfg.projects.values():
            s = self.store(p.id)
            tasks = s.list()
            counts = {k: 0 for k in ("running", "queued", "review", "blocked", "limit_paused", "hung")}
            counts["doneToday"] = 0
            for t in tasks:
                st = t["state"]
                if st in ("starting", "running"):
                    counts["running"] += 1
                elif st in ("queued", "changes_requested"):
                    counts["queued"] += 1
                elif st in ("review", "merging"):
                    counts["review"] += 1
                elif st in ("blocked", "failed"):
                    counts["blocked"] += 1
                elif st in counts:
                    counts[st] += 1
                if st == "done" and (t.get("stateSince") or 0) >= mid:
                    counts["doneToday"] += 1
                if st in ("ready", "queued") and p.enabled:
                    eligible += 1
            rv = [t for t in tasks if t["state"] == "review"]
            review_n += len(rv)
            qs = [t for t in tasks if t["state"] == "blocked" and (s.card(t["id"]) or {}).get("question")]
            questions += len(qs)
            alerts = [e for e in s.events.last(200, min_severity="alert") if now - e["ts"] < DAY]
            headline = ""
            if rv:
                headline = f"{rv[0]['id'].split('-')[0]}{' pilot' if rv[0].get('kind') == 'pilot' else ''} ready to review"
            elif qs:
                headline = f"{qs[0]['id'].split('-')[0]} has a question"
            else:
                run = [t for t in tasks if t["state"] == "running"]
                if run:
                    vm = self.task_vm(s, run[0], now)
                    stp = vm["step"]
                    headline = f"{run[0].get('worker') or '?'} on {run[0]['id'].split('-')[0]}" + (
                        f" · step {stp['index']}/{stp['count']}" if stp else "")
            lp = [t for t in tasks if t["state"] == "limit_paused" and t.get("resumeAt")]
            if lp:
                headline = (headline + " · " if headline else "") + f"{lp[0]['id'].split('-')[0]} resumes {_hm(lp[0]['resumeAt'])}"
            tiles.append({"id": p.id, "title": p.title, "scope": p.scope, "color": p.color, "icon": p.icon,
                          "enabled": p.enabled, "counts": counts, "headline": headline[:120], "alerts": len(alerts)})
        accounts = self.accounts(now)
        running = sum(a["capacity"].get("lanesUsed", 0) for a in accounts)
        spare_lanes = sum(max(0, a["capacity"].get("lanesMax", 0) - a["capacity"].get("lanesUsed", 0))
                          for a in accounts if a["dispatch"] and a["state"] != "cooling")
        target = self.cfg.scheduler.get("backlogTarget") or (spare_lanes + 2)
        glob_alerts = [e for e in self.g.events_since(max(0, self.g.max_event_id() - 2000), limit=2000)
                       if rank(e["severity"]) >= rank("warn") and now - e["ts"] < DAY][-20:]
        hint = "; ".join(f"{a['label']} {a['capacity'].get('lanesMax', 0) - a['capacity'].get('lanesUsed', 0)} lane(s)"
                         + (" (office projects only)" if a["scope"] == "office" else "")
                         for a in accounts if a["dispatch"] and a["state"] != "cooling"
                         and a["capacity"].get("lanesMax", 0) > a["capacity"].get("lanesUsed", 0))
        return {"time": now,
                "inbox": {"review": review_n, "questions": questions,
                          "alerts": sum(1 for e in glob_alerts if e["severity"] == "alert")},
                "projects": tiles, "accounts": accounts,
                "machine": {"running": running, "maxParallelTotal": self.cfg.scheduler.get("maxParallelTotal", 6),
                            "freeRamGb": free_ram_gb()},
                "backlog": {"eligible": eligible, "target": target, "low": eligible < target,
                            "hint": ("Spare: " + hint) if hint and eligible < target else None},
                "needsYou": self.needs_you(now),
                "alerts": list(reversed(glob_alerts))}

    # ---------------- project page
    def project(self, pid, now=None) -> dict:
        now = clock.now() if now is None else now
        s = self.store(pid)
        p = s.pcfg
        mid = _midnight(now)
        tasks = s.list()
        board = {c: [] for c in ("backlog", "upNext", "running", "review", "needsYou", "doneToday")}
        for t in tasks:
            col = states.BOARD_COLUMN.get(t["state"])
            if col is None or (col == "doneToday" and (t.get("stateSince") or 0) < mid):
                continue
            board[col].append(self.task_vm(s, t, now))
        inbox = [self.review_vm(s, t, now) for t in tasks if t["state"] == "review"]
        workers = []
        for w in self.cfg.workers.values():
            acct = self.cfg.account(w.account)
            ok, why = policy.allow(acct, p)
            rows = {x["lane"]: x for x in self.g.lanes.of(w.id)}
            lanes = [self.lane_vm(w.id, n, x, now) for n, x in rows.items() if x["state"] == "running" and x.get("task")]
            workers.append({"worker": w.id, "account": w.account, "label": acct.label if acct else w.account,
                            "scope": acct.scope if acct else None, "adapter": w.adapter,
                            "state": "active" if lanes else "idle", "allowed": ok, "reason": None if ok else why,
                            "lanes": lanes})
        lanes = [l for w in workers for l in w["lanes"] if l.get("project") == pid]
        doms = []
        for d in s.domains.held(now):
            doms.append({"name": d["name"], "kind": d["kind"], "holder": d["holder"], "since": d["since"],
                         "waiting": d["waiting"], "reserved": d["reserved"]})
        mq = s.merge_queue()
        active = next((m["task"] for m in mq if m["state"] == "active"), None)
        evs = s.events.last(400)
        return {"project": {"id": pid, "title": p.title, "scope": p.scope, "allowPersonalAccounts": p.allowPersonalAccounts,
                            "color": p.color, "icon": p.icon, "enabled": p.enabled, "repo": repo_info(p.repo),
                            "health": [{"label": h.get("label"), "url": h.get("url"), "group": h.get("group"), "ok": None}
                                       for h in p.health or []]},
                "workers": workers, "board": board, "inbox": inbox,
                "alerts": [e for e in evs if rank(e["severity"]) >= rank("warn") and now - e["ts"] < DAY][-50:][::-1],
                "timeline": [e for e in evs if rank(e["severity"]) >= rank("info")][-100:][::-1],
                "domains": doms,
                "merge": {"active": active, "queue": [{"task": m["task"], "state": m["state"], "enqueuedAt": m["enqueued_at"],
                                                       "note": m["note"]} for m in mq if m["state"] in ("queued", "active", "waiting")]},
                "lanes": lanes}

    def task(self, pid, id, now=None) -> dict:
        now = clock.now() if now is None else now
        s = self.store(pid)
        t = s.get(id)
        h = s.handoff(t["id"])
        return {"task": t, "card": s.card(t["id"]), "attempts": s.attempts(t["id"]), "handoff": h,
                "history": s.card_history(t["id"], 50), "events": s.events.last(50, task=t["id"]),
                "view": self.task_vm(s, t, now)}

    def eligible(self, pid, id) -> list:
        return self.store(pid).eligible(id)


def repo_info(repo: str) -> dict:
    if not repo:
        return {"commit": None, "changes": []}
    try:
        commit = subprocess.run(["git", "--no-optional-locks", "-C", repo, "log", "-1", "--format=%h %s"],
                                capture_output=True, text=True, timeout=3).stdout.strip() or None
        ch = subprocess.run(["git", "--no-optional-locks", "-C", repo, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=3).stdout.splitlines()
        return {"commit": commit, "changes": ch[:20]}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "changes": []}


def free_ram_gb() -> Optional[float]:
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2).stdout
        page = 4096
        if "page size of" in out:
            page = int(out.split("page size of")[1].split()[0])
        free = 0
        for line in out.splitlines():
            if line.startswith(("Pages free", "Pages inactive", "Pages speculative")):
                free += int(line.split(":")[1].strip().rstrip("."))
        return round(free * page / 1e9, 1) if free else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


# Module-level API (design §17): bind once, then call.
_default: Optional[Views] = None


def bind(cfg: Config, g=None, stores=None) -> Views:
    global _default
    _default = Views(cfg, g, stores)
    return _default


def _v() -> Views:
    global _default
    if _default is None:
        _default = Views(Config.load())
    return _default


def overview(now=None):
    return _v().overview(now)


def project(pid, now=None):
    return _v().project(pid, now)


def task(pid, id):
    return _v().task(pid, id)


def eligible(pid, id):
    return _v().eligible(pid, id)


def dumps(vm) -> str:
    return json.dumps(vm, ensure_ascii=False, separators=(",", ":"))
