"""deckd: one daemon per machine (design §1, §6.1). Dispatcher + supervisor + merge queue + HTTP API + notifications.

    deckd [--foreground] [--config PATH] [--no-http]

- Writes <stateDir>/deckd.pid (B1's `deck` CLI reads it to warn when nothing will dispatch).
- Reconciles every live attempt on start (§6.7), then loops: polls <stateDir>/wake by mtime every 250 ms (CLI writes
  touch it → immediate re-plan), safety tick every 5 s.
- The HTTP API (B1 `server.make_server`) runs in a thread on its own connections.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import clock
from .config import Config
from .dispatcher import Dispatcher
from .mergeq import MergeQueue
from .notify import Notifier

TICK_SEC = 5.0
WAKE_POLL = 0.25


def open_stores(cfg):
    from . import store                       # B1
    g = store.open_global(cfg)
    if hasattr(g, "sync_config"):
        g.sync_config()
    return g, {pid: store.ProjectStore(pid, cfg, g) for pid in cfg.projects}


class Daemon:
    def __init__(self, cfg, g=None, stores=None, http=True, notifier=None, **dkw):
        self.cfg = cfg
        if g is None:
            g, stores = open_stores(cfg)
        self.g, self.stores = g, stores
        self.d = Dispatcher(cfg, g, stores, **dkw)
        self.d.mergeq = MergeQueue(self.d)
        self.notifier = notifier or Notifier(cfg)
        self.http = http
        self.stop = False
        self.wake = Path(cfg.state_dir) / "wake"
        self.pidfile = Path(cfg.state_dir) / "deckd.pid"
        self._wake_mtime = 0.0
        self._last_event = 0

    def _events_since(self):
        if hasattr(self.g, "events_since"):
            return self.g.events_since(self._last_event, limit=500)
        rows = getattr(getattr(self.g, "events", None), "rows", [])
        return [e for e in rows if e["id"] > self._last_event]

    def pump_events(self):
        for ev in self._events_since():
            self._last_event = max(self._last_event, ev["id"])
            self.notifier.deliver(ev)

    def start(self):
        Path(self.cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self.pidfile.write_text(f"{os.getpid()} {clock.now()}\n")
        if hasattr(self.g, "max_event_id"):
            self._last_event = self.g.max_event_id()     # notify only what happens from now on
        self.d.emit(None, "daemon.started", "info", f"deckd started (pid {os.getpid()})")
        self.d.reconcile()
        if self.http:
            try:
                from . import server             # B1
                srv = server.make_server(self.cfg)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
            except Exception as e:  # noqa: BLE001 — dispatch keeps working without the API
                self.d.emit(None, "daemon.error", "warn", f"HTTP API not started: {e}"[:200])

    def wake_changed(self):
        try:
            m = self.wake.stat().st_mtime
        except OSError:
            return False
        if m != self._wake_mtime:
            self._wake_mtime = m
            return True
        return False

    def step(self):
        try:
            self.d.tick()
        except Exception as e:  # noqa: BLE001 — a bad tick is logged, the loop continues
            self.d.emit(None, "daemon.error", "alert", f"tick failed: {type(e).__name__}: {e}"[:200], for_=["W0"])
        self.pump_events()

    def run(self):
        self.start()
        last = 0.0
        try:
            while not self.stop:
                now = time.time()
                if self.wake_changed() or self.d.replan or now - last >= TICK_SEC:
                    time.sleep(self.cfg.scheduler.get("replanDebounceMs", 500) / 1000 if now - last < TICK_SEC else 0)
                    self.step()
                    last = time.time()
                time.sleep(WAKE_POLL)
        finally:
            try:
                self.pidfile.unlink()
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deckd")
    ap.add_argument("--foreground", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--no-http", action="store_true")
    a = ap.parse_args(argv)
    cfg = Config.load(a.config)
    dmn = Daemon(cfg, http=not a.no_http)

    def _stop(*_):
        dmn.stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    dmn.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
