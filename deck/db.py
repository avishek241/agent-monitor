"""SQLite helpers: WAL connections, ordered migrations, nested BEGIN IMMEDIATE transactions."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection, kind: str) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    have = int(row[0]) if row else 0
    files = sorted((MIGRATIONS / kind).glob("*.sql"))
    for f in files:
        n = int(f.stem)
        if n <= have:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split(f.read_text()):
                conn.execute(stmt)
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(n),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        have = n
    return have


def _split(sql: str):
    """Split a migration into statements, keeping CREATE TRIGGER ... END; blocks whole."""
    out, buf = [], []
    for line in sql.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        buf.append(line)
        joined = "\n".join(buf)
        if s.endswith(";") and sqlite3.complete_statement(joined):
            out.append(joined)
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


class Tx:
    """Re-entrant BEGIN IMMEDIATE on one connection; runs after-commit hooks."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.depth = 0
        self.after: list = []
        self.after_rollback: list = []
        self.lock = threading.RLock()

    @contextmanager
    def __call__(self):
        with self.lock:
            outer = self.depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self.depth += 1
            try:
                yield self.conn
            except BaseException:
                self.depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                    self.after.clear()
                    hooks, self.after_rollback = self.after_rollback, []
                    for h in hooks:
                        h()
                raise
            self.depth -= 1
            if outer:
                self.conn.execute("COMMIT")
                hooks, self.after = self.after, []
                self.after_rollback.clear()
                for h in hooks:
                    h()

    def on_commit(self, fn):
        if self.depth == 0:
            fn()
        else:
            self.after.append(fn)

    def on_rollback(self, fn):
        """Run fn after the outer transaction rolls back (e.g. persist a policy.denied alert)."""
        if self.depth == 0:
            fn()
        else:
            self.after_rollback.append(fn)
