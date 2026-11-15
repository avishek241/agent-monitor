"""Quota model (design §6.3): readings, effective usage, limits converted to absolute time ONCE (M4, M9).

Ported from v1 `status.py` `usage()` / `claude_limit_from_logs()` with the absolute-time fix: a relative
"resets 8:50pm" is turned into an epoch at detection time (`detected_at`) and never re-read from text again.
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics
import time
from typing import Optional

from .adapters.base import UsageReading

STALE_SEC = 600
UNKNOWN_EFF5 = 50.0

# "You've hit your 5-hour limit · resets 1:50am (Asia/Calcutta)", "You've reached your weekly limit, resets at 8pm"
LIMIT_RE = re.compile(
    r"(hit your|reached your)\s+(5-hour|weekly|usage|session)\s+limit.*?resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?",
    re.I | re.S)
LIMIT_ANY_RE = re.compile(r"(hit your|reached your)\s+(5-hour|weekly|usage|session)\s+limit", re.I)


def clock_time_to_epoch(hour: int, minute: int, ampm: Optional[str], detected_at: int) -> int:
    """Next occurrence of local hh:mm after `detected_at` (today if still ahead, else tomorrow). Called once."""
    h = hour % 12 + (12 if (ampm or "").lower() == "pm" else 0) if ampm else hour
    t = time.localtime(detected_at)
    reset = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, minute, 0, 0, 0, -1)))
    if reset <= detected_at:
        t2 = time.localtime(detected_at + 86400)
        reset = int(time.mktime((t2.tm_year, t2.tm_mon, t2.tm_mday, h, minute, 0, 0, 0, -1)))
    return reset


def parse_limit_text(text: str, detected_at: int):
    """(kind, resets_at|None, raw) if the text announces a usage limit, else None."""
    if not text:
        return None
    m = LIMIT_RE.search(text)
    if m:
        kind = {"5-hour": "five_hour", "weekly": "weekly"}.get(m.group(2).lower(), "usage")
        return kind, clock_time_to_epoch(int(m.group(3)), int(m.group(4) or 0), m.group(5), detected_at), m.group(0)[:200]
    m = LIMIT_ANY_RE.search(text)
    if m:
        kind = {"5-hour": "five_hour", "weekly": "weekly"}.get(m.group(2).lower(), "usage")
        return kind, None, m.group(0)[:200]
    return None


# ---------------------------------------------------------------- readings
def claude_status_reading(account_id: str, path: str, now: int) -> Optional[UsageReading]:
    """Reading from the statusline file `bin/claude-statusline.sh` writes for that account (office included)."""
    try:
        with open(os.path.expanduser(path)) as fh:
            c = json.load(fh)
        read_at = int(os.path.getmtime(os.path.expanduser(path)))
    except (OSError, ValueError):
        return None
    rl = c.get("rate_limits") or {}
    five, seven = rl.get("five_hour") or {}, rl.get("seven_day") or {}
    if not five:
        return None
    return UsageReading(account=account_id, read_at=read_at, source="statusline",
                        pct5=float(five.get("used_percentage") or 0), pct7=float(seven.get("used_percentage") or 0),
                        resets5=int(five.get("resets_at") or 0) or None, resets7=int(seven.get("resets_at") or 0) or None)


def codex_rollout_reading(account_id: str, home: str, now: int, sessions_glob: Optional[str] = None,
                          max_age_h: float = 8) -> Optional[UsageReading]:
    """Newest-timestamp `rate_limits` across recently active rollout files (several sessions run at once)."""
    pattern = os.path.expanduser(sessions_glob or os.path.join(home, "sessions/*/*/*/*.jsonl"))
    files = [f for f in glob.glob(pattern) if now - os.path.getmtime(f) < max_age_h * 3600]
    latest, best = "", None
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:12]:
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - 400000))
                data = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        for line in data.splitlines():
            if '"rate_limits"' not in line or '"primary"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            rl = (d.get("payload") or {}).get("rate_limits") or {}
            pr, se, ts = rl.get("primary"), rl.get("secondary") or {}, str(d.get("timestamp", ""))
            if pr and (rl.get("limit_id") or "codex") == "codex" and ts > latest:
                latest = ts
                best = (pr, se, f)
    if not best:
        return None
    pr, se, f = best
    return UsageReading(account=account_id, read_at=int(os.path.getmtime(f)), source="rollout",
                        pct5=float(pr.get("used_percent") or 0), pct7=float(se.get("used_percent") or 0),
                        resets5=int(pr.get("resets_at") or 0) or None, resets7=int(se.get("resets_at") or 0) or None)


def read_account(account, now: int) -> Optional[UsageReading]:
    u = getattr(account, "usage", {}) or {}
    if account.tool == "claude":
        path = u.get("statusFile")
        return claude_status_reading(account.id, path, now) if path else None
    if account.tool == "codex":
        return codex_rollout_reading(account.id, account.home, now, u.get("sessionsGlob"))
    return None


# ---------------------------------------------------------------- effective usage
def _get(r, k):
    if r is None:
        return None
    return r.get(k) if isinstance(r, dict) else getattr(r, k, None)


def effective(reading, active_limits, now: int) -> dict:
    """eff5 per §6.3. Returns {eff5, pct7, resets5, resets7, confidence, cooling, coolingUntil, readAgeSec}."""
    lims = [l for l in (active_limits or []) if (l.get("resets_at") or 0) > now and not l.get("cleared_at")]
    pct7 = _get(reading, "pct7")
    resets7 = _get(reading, "resets7")
    if resets7 and resets7 <= now:
        pct7 = 0.0
    out = {"pct7": pct7 if pct7 is not None else None, "resets7": resets7, "readAgeSec": None}
    if reading is not None:
        out["readAgeSec"] = max(0, now - int(_get(reading, "read_at") or now))
    if lims:
        until = max(l["resets_at"] for l in lims)
        out.update(eff5=100.0, resets5=until, confidence="high", cooling=True, coolingUntil=until)
        return out
    out.update(cooling=False, coolingUntil=None)
    if reading is None:
        out.update(eff5=None, resets5=None, confidence="unknown")
        return out
    resets5 = _get(reading, "resets5")
    if resets5 and resets5 <= now:
        out.update(eff5=0.0, resets5=None, confidence="low")       # window rolled over since the reading
        return out
    age = out["readAgeSec"]
    out.update(eff5=float(_get(reading, "pct5") or 0), resets5=resets5,
               confidence="high" if age is not None and age < STALE_SEC else "stale")
    return out


def limit_cleared_by(limit: dict, reading) -> bool:
    """A newer reading under 100 % taken after detection clears a limit (§6.3)."""
    if reading is None:
        return False
    return (_get(reading, "pct5") or 0) < 100 and int(_get(reading, "read_at") or 0) > int(limit.get("detected_at") or 0)


# ---------------------------------------------------------------- learned cost per size
DEFAULT_COST = {"S": (8.0, 30.0), "M": (20.0, 90.0), "L": (40.0, 180.0), "XL": (70.0, 360.0)}


ACCOUNT_MIN_ROWS = 3   # learned rows an account needs before its own numbers replace the per-tool ones


def estimate(size: str, tool: str, history=None, override: Optional[dict] = None, account: Optional[str] = None):
    """(est % of the 5-hour window, minutes). Medians of the last 20 completed attempts for (account, size) when
    the account has at least ACCOUNT_MIN_ROWS of them, else for (tool, size), else the S/M/L/XL defaults."""
    est, mins = DEFAULT_COST.get(size or "M", DEFAULT_COST["M"])
    hist = history or []
    rows = []
    if account is not None:
        rows = [r for r in hist if r.get("account") == account and r.get("size") == size]
        if len(rows) < ACCOUNT_MIN_ROWS:
            rows = []
    if not rows:
        rows = [r for r in hist if r.get("account_tool") == tool and r.get("size") == size]
    rows = sorted(rows, key=lambda r: r.get("ts") or 0)[-20:]
    d5 = [r["delta5"] for r in rows if r.get("delta5") is not None and r["delta5"] >= 0]
    dm = [r["minutes"] for r in rows if r.get("minutes")]
    if d5:
        est = float(statistics.median(d5))
    if dm:
        mins = float(statistics.median(dm))
    override = override or {}
    if override.get("pct5") is not None:
        est = float(override["pct5"])
    if override.get("minutes") is not None:
        mins = float(override["minutes"])
    return est, max(1.0, mins)
