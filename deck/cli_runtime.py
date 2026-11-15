"""B2 subcommands for the `deck` CLI, registered through B1's `cli.EXTRA_COMMANDS` (design §11).

    deck daemon start|stop|status     deck capacity [--json]     deck plan --dry-run
    deck hooks install --account ID   deck import-v1             deck run …   (= python3 -m deck.run)
Integration: B1's cli.py must `import deck.cli_runtime` (one line) before building its parser.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from . import capacity, clock, quota


def _dispatcher(ctx, dry=True):
    from .dispatcher import Dispatcher
    stores = {pid: ctx.store(pid) for pid in ctx.cfg.projects} if hasattr(ctx, "store") else {}
    return Dispatcher(ctx.cfg, ctx.g, stores, launcher=lambda *a, **k: None)


def cmd_capacity(ctx, args):
    d = _dispatcher(ctx)
    now = clock.now()
    rows = []
    running = d._running()
    for a in ctx.cfg.accounts.values():
        ai = d.account_input(a.id, now, running)
        cap = capacity.account_capacity(ai, now)
        fits = capacity.fits_size(cap, ai, a.tool, d._history(), account=a.id)
        rows.append({"account": a.id, "scope": a.scope, "dispatch": a.dispatch, "eff5": ai.eff5,
                     "confidence": ai.confidence, **cap.as_vm(fits, cap.lanes_used < cap.lanes_max and fits is not None,
                                                              cap.hard_stop)})
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            e = "?" if r["eff5"] is None else f"{r['eff5']:.0f}%"
            print(f"{r['account']:<14} {r['scope'] or '-':<8} 5h {e:>5} lanes {r['lanesUsed']}/{r['lanesMax']} "
                  f"R5 {r['spendable5']:>6} T5 {r['minutesToReset']:>4}m urg {r['urgency']:.2f} fits {r['fitsSize'] or '-'}"
                  + (f"  · {r['why']}" if r["why"] else ""))
    return 0


def cmd_plan(ctx, args):
    """What the planner would start now, and why each other queued task waits. Leases nothing."""
    d = _dispatcher(ctx)
    now = clock.now()
    running = d._running()
    for pid, t in d.candidates(now):
        placed = None
        for a in sorted(ctx.cfg.accounts.values(), key=lambda a: a.id):
            if not a.dispatch:
                continue
            ws = d.workers_for(a.id, pid, t, now)
            if not ws:
                continue
            from . import policy
            ok, why = policy.allow(a, ctx.cfg.project(pid))
            if not ok:
                continue
            ai = d.account_input(a.id, now, running)
            ok, why = capacity.admit(capacity.account_capacity(ai, now), ai,
                                     *quota.estimate(t.get("size"), a.tool, d._history(), t.get("estimate"), account=a.id),
                                     t.get("priority", "P2"), d.machine(len(running)))
            if ok:
                placed = f"would start on {ws[0].id} ({a.id})"
                break
            placed = placed or f"waits: {a.id}: {why}"
        print(f"{pid}/{t['id']}: {placed or 'waits: ' + d._no_account_reason(pid, t, {}, now)}")
    return 0


def cmd_hooks(ctx, args):
    from .adapters.interactive import install_hooks
    a = ctx.cfg.account(args.account)
    if a is None:
        print(f"unknown account {args.account}", file=sys.stderr)
        return 5
    print(f"hooks written to {install_hooks(a, args.deck)}")
    return 0


def cmd_import(ctx, args):
    from .import_v1 import import_project
    for pid, p in ctx.cfg.projects.items():
        print(pid, import_project(pid, ctx.store(pid), p, ctx.cfg.state_dir))
    return 0


def cmd_daemon(ctx, args):
    pidf = Path(ctx.cfg.state_dir) / "deckd.pid"
    pid = None
    try:
        pid = int(pidf.read_text().split()[0])
        os.kill(pid, 0)
    except (OSError, ValueError):
        pid = None
    if args.action == "status":
        print(f"deckd running (pid {pid})" if pid else "deckd not running — nothing will dispatch")
        return 0 if pid else 1
    if args.action == "stop":
        if pid:
            os.kill(pid, signal.SIGTERM)
        return 0
    if pid:
        print(f"deckd already running (pid {pid})")
        return 0
    root = Path(__file__).resolve().parent.parent
    log = open(Path(ctx.cfg.state_dir) / "deckd.log", "ab")
    env = dict(os.environ, PYTHONPATH=str(root))
    p = subprocess.Popen([sys.executable, "-m", "deck.deckd", "--foreground"] +
                         (["--config", ctx.cfg.path] if ctx.cfg.path else []),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=env)
    print(f"deckd started (pid {p.pid})")
    return 0


def cmd_run(ctx, args):
    from . import run
    return run.main(args.rest)


def register(extra: dict):
    extra["capacity"] = (lambda sp: sp.add_argument("--json", action="store_true"), cmd_capacity)
    extra["plan"] = (lambda sp: sp.add_argument("--dry-run", action="store_true"), cmd_plan)

    def hooks_setup(sp):
        sp.add_argument("what", choices=["install"])
        sp.add_argument("--account", required=True)
        sp.add_argument("--deck", default="deck", help="command the hooks call")
    extra["hooks"] = (hooks_setup, cmd_hooks)
    extra["import-v1"] = (lambda sp: sp.add_argument("--watch", action="store_true"), cmd_import)
    extra["daemon"] = (lambda sp: sp.add_argument("action", choices=["start", "stop", "status"]), cmd_daemon)
    extra["run"] = (lambda sp: sp.add_argument("rest", nargs="...", default=[]), cmd_run)


try:                                            # B1's CLI, when present
    from . import cli as _cli
    register(_cli.EXTRA_COMMANDS)
except ImportError:
    pass
