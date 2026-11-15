"""Conflict domains (design §6.2.1): area:/seq:/ext:/res:/merge, all-or-nothing, anti-starvation.

``Domains`` serves both the project table (``domains``) and the machine-wide table (``ext_domains``).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from . import clock

RESERVE_AFTER_SEC = 30 * 60
DEFAULT_TTL_MIN = 240
_GLOB = re.compile(r"[*?\[]")


def kind_of(name: str) -> str:
    if name == "merge":
        return "merge"
    return name.split(":", 1)[0] if ":" in name else "?"


def _literal_prefix(glob: str) -> str:
    """Directory-aligned literal prefix of an area glob ('' = repo root)."""
    g = glob[5:] if glob.startswith("area:") else glob
    while g.startswith("./"):
        g = g[2:]
    m = _GLOB.search(g)
    if m:
        lit = g[:m.start()]
        g = lit[: lit.rfind("/") + 1] if "/" in lit else ""
    return g.strip("/")


def overlaps(glob_a: str, glob_b: str) -> bool:
    """Two area globs overlap when one's directory-aligned literal prefix is an ancestor of (or equal
    to) the other's, on path-segment boundaries. ``apps/api/**`` vs ``apps/api/src/x/**`` → True;
    ``apps/api/**`` vs ``apps/web/**`` → False; ``**`` overlaps everything. Unsure means overlap.
    """
    pa, pb = _literal_prefix(glob_a), _literal_prefix(glob_b)
    if not pa or not pb or pa == pb:
        return True
    return (pb + "/").startswith(pa + "/") or (pa + "/").startswith(pb + "/")


class Domains:
    """One domain table. ``tx`` is the owning store's Tx; ``capacity(name)`` gives the cap
    (0 = unlimited); ``emit`` logs events; ``extra`` is (column, value) for holder_kind/project."""

    def __init__(self, conn, tx, table: str, waits: str, capacity, emit=None, extra=("holder_kind", "task"),
                 ttl_default=None, external=None):
        self.conn, self.tx, self.table, self.waits = conn, tx, table, waits
        self.capacity = capacity
        self.emit = emit
        self.extra = extra
        self.ttl_default = ttl_default or (lambda name: DEFAULT_TTL_MIN)
        self.external = external   # Domains for ext: names (project table delegates to the global one)
        self.ext_holder = None     # fn(holder) -> holder used in the external table

    overlaps = staticmethod(overlaps)

    # ---- internals ----
    def _live(self, now):
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM {self.table} WHERE expires_at IS NULL OR expires_at > ?", (now,))]

    def _blocker(self, name, holder, live, waits):
        k = kind_of(name)
        others = [r for r in live if r["holder"] != holder]
        if k == "area":
            for r in others:
                if kind_of(r["name"]) == "area" and overlaps(name, r["name"]):
                    return {"name": name, "holder": r["holder"], "reason": f"domain {r['name']} held by {r['holder']}"}
            for w in waits:
                if w["reserved"] and w["task"] != holder and kind_of(w["name"]) == "area" and overlaps(name, w["name"]):
                    return {"name": name, "holder": w["task"], "reason": f"domain {w['name']} reserved by {w['task']}"}
            return None
        cap = 1 if k == "merge" else self.capacity(name)
        if k == "merge" and any(r["name"] == "merge" and r["holder"] != holder for r in live):
            h = next(r["holder"] for r in others if r["name"] == "merge")
            return {"name": name, "holder": h, "reason": f"merge held by {h}"}
        same = [r for r in others if r["name"] == name]
        if cap and len(same) >= cap:
            return {"name": name, "holder": same[0]["holder"], "reason": f"domain {name} held by {same[0]['holder']}"}
        for w in waits:
            if w["reserved"] and w["task"] != holder and w["name"] == name and cap and len(same) + 1 >= cap:
                return {"name": name, "holder": w["task"], "reason": f"domain {name} reserved by {w['task']}"}
        return None

    def check(self, names, holder, now):
        live = self._live(now)
        waits = [dict(r) for r in self.conn.execute(f"SELECT * FROM {self.waits}")]
        mine = {r["name"] for r in live if r["holder"] == holder}
        for name in names:
            if name in mine:
                continue
            b = self._blocker(name, holder, live, waits)
            if b:
                return b
        return None

    def _record_wait(self, name, holder, now):
        row = self.conn.execute(f"SELECT since, reserved FROM {self.waits} WHERE name=? AND task=?",
                                (name, holder)).fetchone()
        if row is None:
            self.conn.execute(f"INSERT INTO {self.waits}(name, task, since, reserved) VALUES(?,?,?,0)", (name, holder, now))
            return
        if not row["reserved"] and now - row["since"] >= RESERVE_AFTER_SEC:
            self.conn.execute(f"UPDATE {self.waits} SET reserved=1 WHERE name=? AND task=?", (name, holder))
            if self.emit:
                self.emit("domain.waiting", "warn", f"{holder} waited 30 min for {name}; reserved it",
                          task=holder, data={"domain": name})

    def _insert(self, names, holder, ttl, now):
        col, val = self.extra
        for name in names:
            t = ttl if ttl is not None else self.ttl_default(name)
            if val is None and "/" in holder:     # machine-wide table: holder is "<project>/<task>"
                val = holder.split("/", 1)[0]
            self.conn.execute(
                f"INSERT OR REPLACE INTO {self.table}(name, holder, {col}, acquired_at, expires_at) VALUES(?,?,?,?,?)",
                (name, holder, val, now, now + int(t) * 60 if t else None))
        self.conn.execute(f"DELETE FROM {self.waits} WHERE task=?", (holder,))

    # ---- API ----
    def try_acquire(self, names: Iterable[str], holder: str, ttl: Optional[int] = None,
                    now: Optional[int] = None):
        now = clock.now() if now is None else now
        names = sorted(set(names))
        local = [n for n in names if not (self.external and kind_of(n) == "ext")]
        ext = [n for n in names if self.external and kind_of(n) == "ext"]
        with self.tx():
            self.conn.execute(f"DELETE FROM {self.table} WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
            b = self.check(local, holder, now)
            if b:
                self._record_wait(b["name"], holder, now)
                return False, b
            if ext:
                ok, b = self.external.try_acquire(ext, self.ext_holder(holder) if self.ext_holder else holder, ttl, now)
                if not ok:
                    self.conn.execute(f"INSERT OR IGNORE INTO {self.waits}(name, task, since, reserved) VALUES(?,?,?,0)",
                                      (b["name"], holder, now))
                    return False, b
            self._insert(local, holder, ttl, now)
        if self.emit and names:
            self.emit("lock.acquired", "debug", f"{holder} holds {', '.join(names)}", task=holder,
                      data={"domains": names})
        return True, None

    def release_all(self, holder: str) -> int:
        with self.tx():
            n = self.conn.execute(f"DELETE FROM {self.table} WHERE holder=?", (holder,)).rowcount
        if self.external:
            n += self.external.release_all(self.ext_holder(holder) if self.ext_holder else holder)
        return n

    def clear_waits(self, holder: str):
        with self.tx():
            self.conn.execute(f"DELETE FROM {self.waits} WHERE task=?", (holder,))
        if self.external:
            self.external.clear_waits(self.ext_holder(holder) if self.ext_holder else holder)

    def release(self, name: str, holder: Optional[str] = None) -> int:
        with self.tx():
            if holder is None:
                return self.conn.execute(f"DELETE FROM {self.table} WHERE name=?", (name,)).rowcount
            return self.conn.execute(f"DELETE FROM {self.table} WHERE name=? AND holder=?", (name, holder)).rowcount

    def renew(self, holder: str, ttl: Optional[int] = None, now: Optional[int] = None) -> int:
        now = clock.now() if now is None else now
        n = 0
        with self.tx():
            for r in self.conn.execute(f"SELECT name FROM {self.table} WHERE holder=?", (holder,)).fetchall():
                t = ttl if ttl is not None else self.ttl_default(r["name"])
                n += self.conn.execute(f"UPDATE {self.table} SET expires_at=? WHERE name=? AND holder=?",
                                       (now + int(t) * 60 if t else None, r["name"], holder)).rowcount
        if self.external:
            n += self.external.renew(self.ext_holder(holder) if self.ext_holder else holder, ttl, now)
        return n

    def held(self, now: Optional[int] = None) -> list:
        now = clock.now() if now is None else now
        live = self._live(now)
        waits = [dict(r) for r in self.conn.execute(f"SELECT * FROM {self.waits} ORDER BY since")]
        out = []
        names = sorted({r["name"] for r in live} | {w["name"] for w in waits})
        for name in names:
            hs = [r for r in live if r["name"] == name]
            ws = [w for w in waits if w["name"] == name]
            out.append({"name": name, "kind": kind_of(name),
                        "holder": hs[0]["holder"] if hs else None,
                        "holders": [r["holder"] for r in hs],
                        "since": hs[0]["acquired_at"] if hs else None,
                        "expires": hs[0]["expires_at"] if hs else None,
                        "waiting": [w["task"] for w in ws],
                        "reserved": any(w["reserved"] for w in ws)})
        return out

    def holding(self, holder: str) -> list:
        return [r["name"] for r in self.conn.execute(f"SELECT name FROM {self.table} WHERE holder=?", (holder,))]
