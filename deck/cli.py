"""`deck` CLI (design §11). Uses the store library directly, so it works while deckd is down.

Exit codes: 0 OK · 2 validation · 3 policy denied · 4 illegal transition / stale rev · 5 not found ·
6 domain busy. B2 adds the `run`, `daemon`, `hooks`, `capacity`, `plan` and `import-v1` subcommands
through ``EXTRA_COMMANDS``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import brief, clock, policy, states
from .config import Config, DEFAULT_CONFIG
from .errors import DeckError, DomainBusy, IllegalTransition, NotFound, PolicyDenied, ValidationError
from .events import rank
from .store import GlobalStore, ProjectStore

EXTRA_COMMANDS: dict = {}   # name -> (setup(subparsers), handler(ctx, args)) — B2 registers here


class Ctx:
    def __init__(self, args):
        self.args = args
        self.cfg = Config.load(getattr(args, "config", None))
        self._g = None
        self._stores = {}

    @property
    def g(self):
        if self._g is None:
            self._g = GlobalStore(self.cfg)
        return self._g

    def pid(self, required=True):
        p = getattr(self.args, "project", None) or os.environ.get("DECK_PROJECT")
        if not p:
            pc = self.cfg.project_for_cwd(os.getcwd())
            p = pc.id if pc else None
        if not p and required:
            raise NotFound("no project: pass --project, set DECK_PROJECT, or run inside a project repo")
        if p and p not in self.cfg.projects:
            raise NotFound(f"project {p} is not configured")
        return p

    def store(self, pid=None) -> ProjectStore:
        pid = pid or self.pid()
        if pid not in self._stores:
            self._stores[pid] = ProjectStore(pid, self.cfg, self.g)
        return self._stores[pid]

    def close(self):
        for st in self._stores.values():
            st.close()
        if self._g is not None:
            self._g.close()

    def actor(self):
        return getattr(self.args, "as_", None) or os.environ.get("DECK_WORKER") or "W0"

    def out(self, obj):
        print(json.dumps(obj, indent=2, ensure_ascii=False))


def _hm(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "—"


def daemon_running(cfg) -> bool:
    pidf = Path(cfg.state_dir) / "deckd.pid"
    try:
        os.kill(int(pidf.read_text().split()[0]), 0)
        return True
    except (OSError, ValueError):
        return False


def warn_no_daemon(ctx):
    if not daemon_running(ctx.cfg):
        print("deck: warning: deckd is not running — nothing will dispatch until it starts", file=sys.stderr)


def fmt_task(t):
    w = f" {t['worker']}" if t.get("worker") else ""
    reason = f" — {t['stateReason']}" if t.get("stateReason") and t["state"] not in ("draft", "ready") else ""
    return f"{t['id']:<32} {t['state']:<17} {t['priority']} {t['size']:<2}{w}  {t['title']}{reason}"


# ---------------------------------------------------------------- commands
def c_init(ctx, a):
    path = os.path.expanduser(a.config or os.environ.get("DECK_CONFIG") or DEFAULT_CONFIG)
    if not Path(path).exists():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"kind": "machine", "accounts": [], "workers": [], "projects": []}, indent=2) + "\n")
        os.chmod(path, 0o600)
        print(f"created {path}")
    cfg = Config.load(path)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    from .server import local_token
    local_token(cfg.state_dir)
    GlobalStore(cfg).close()
    print(f"state dir {cfg.state_dir}")
    print("next: add accounts/workers to the config (scope office|personal on every account), then\n"
          "      deck project add <id> --config <project.json> --scope personal|office")


def c_project(ctx, a):
    cfg = ctx.cfg
    if a.pcmd == "add":
        entries = [p for p in cfg.machine.setdefault("projects", []) if p["id"] != a.id]
        e = {"id": a.id, "config": a.config_file, "scope": a.scope}
        if a.allow_personal_accounts:
            e["allowPersonalAccounts"] = True
        entries.append(e)
        cfg.machine["projects"] = entries
        cfg.save()
        ctx.cfg = Config.load(cfg.path)
        ctx.store(a.id).events.emit("project.added", "info", f"project {a.id} added ({a.scope})")
        print(f"project {a.id} added ({a.scope})")
    elif a.pcmd == "list":
        for p in cfg.projects.values():
            print(f"{p.id:<20} {p.scope or 'UNTAGGED':<9} {'enabled' if p.enabled else 'disabled':<9} {p.title}  {p.repo}")
    elif a.pcmd == "disable":
        for p in cfg.machine.get("projects", []):
            if p["id"] == a.id:
                p["enabled"] = False
        cfg.save()
        print(f"project {a.id} disabled")


def _usage_rows(ctx):
    from .views import Views
    return Views(ctx.cfg, ctx.g, ctx._stores).accounts()


def c_account(ctx, a):
    rows = _usage_rows(ctx)
    if getattr(a, "json", False):
        return ctx.out(rows)
    for r in rows:
        pct = "?" if r["pct5"] is None else f"{r['pct5']:.0f}%"
        age = "" if r["readAgeSec"] is None else f" read {r['readAgeSec'] // 60}m ago"
        print(f"{r['id']:<18} {(r['scope'] or 'UNTAGGED').upper():<9} 5h {pct:>4} reset {_hm(r['resets5'])}  "
              f"7d {r['pct7'] if r['pct7'] is not None else '?'}  {r['state']}{age}"
              f"{'' if r['dispatch'] else '  (dispatch off)'}")


def c_workers(ctx, a):
    for w in ctx.cfg.workers.values():
        acct = ctx.cfg.account(w.account)
        lanes = [x for x in ctx.g.lanes.of(w.id) if x["state"] == "running"]
        ok_projects = [p.id for p in ctx.cfg.projects.values() if policy.allow(acct, p)[0]]
        busy = ", ".join(f"{w.id}·{x['lane']} {x['project']}/{x['task']}" for x in lanes) or "idle"
        print(f"{w.id:<4} {w.account:<18} {(acct.scope if acct else '?') or 'UNTAGGED':<9} {w.adapter:<16} {busy}"
              f"  may run: {', '.join(ok_projects) or 'nothing'}")


def c_new(ctx, a):
    s = ctx.store()
    f = {"title": a.title, "summary": a.summary, "size": a.size, "priority": a.prio, "capability": a.cap,
         "kind": a.kind}
    if a.deps:
        f["deps"] = [s.resolve(d) for d in a.deps.split(",") if d]
    if a.domains:
        f["domains"] = [d for d in a.domains.split(",") if d]
    if a.workers:
        f["workerClass"] = a.workers.split(",")
    if a.est_pct is not None or a.est_min is not None:
        f["estimate"] = {"pct5": a.est_pct, "minutes": a.est_min}
    if a.pilot_of:
        f["pilotOf"] = s.resolve(a.pilot_of)
        f["kind"] = "scale"
    t = s.create_task(f, by=ctx.actor())
    p = brief.create_brief_file(s, t)
    print(f"{t['id']} created (draft). Brief: {p}")
    if not a.no_edit and sys.stdin.isatty() and os.environ.get("EDITOR"):
        subprocess.call([os.environ["EDITOR"], str(p)])
    print(f"next: fill the brief, then  deck ready {t['id']}")


def _print_issues(res):
    for r, m in res["errors"]:
        print(f"  error {r}: {m}")
    for r, m in res["warnings"]:
        print(f"  warn  {r}: {m}")


def c_validate(ctx, a):
    s = ctx.store()
    res = brief.validate(s, s.get(a.task))
    _print_issues(res)
    if res["errors"]:
        raise ValidationError(f"{len(res['errors'])} error(s)")
    print("brief OK" + (f" (domains: {', '.join(res['domains'])})" if res["domains"] else ""))


def c_ready(ctx, a):
    s = ctx.store()
    try:
        t = brief.ready(s, s.resolve(a.task), actor=ctx.actor())
    except ValidationError as e:
        for r, m in e.data.get("errors", []):
            print(f"  error {r}: {m}")
        for r, m in e.data.get("warnings", []):
            print(f"  warn  {r}: {m}")
        raise
    for r, m in t.get("_warnings", []):
        print(f"  warn  {r}: {m}")
    print(f"{t['id']} ready · domains {', '.join(t.get('domains') or []) or 'none'}")
    warn_no_daemon(ctx)


def c_unready(ctx, a):
    t = ctx.store().transition(a.task, "draft", actor=ctx.actor(), reason="unready")
    print(f"{t['id']} back to draft")


def c_list(ctx, a):
    pids = list(ctx.cfg.projects) if a.all_projects else [ctx.pid()]
    sts = a.state.split(",") if a.state else None
    rows = []
    for pid in pids:
        for t in ctx.store(pid).list(sts):
            if not sts and t["state"] in ("cancelled",):
                continue
            rows.append((pid, t))
    if a.json:
        return ctx.out([t for _, t in rows])
    for pid, t in rows:
        print((f"[{pid}] " if a.all_projects else "") + fmt_task(t))


def c_board(ctx, a):
    s = ctx.store()
    cols = {c: [] for c in ("backlog", "upNext", "running", "review", "needsYou", "doneToday")}
    for t in s.list():
        c = states.BOARD_COLUMN.get(t["state"])
        if c:
            cols[c].append(t)
    for c, ts in cols.items():
        print(f"== {c.upper()} ({len(ts)})")
        for t in ts:
            print("  " + fmt_task(t))


def c_show(ctx, a):
    s = ctx.store()
    t = s.get(a.task)
    if a.json:
        return ctx.out(t)
    print(fmt_task(t))
    print(f"  summary: {t.get('summary')}\n  brief:   {t['brief'].get('path')}  snapshot: {t['brief'].get('snapshot') or '—'}")
    print(f"  deps: {', '.join(t.get('deps') or []) or '—'}   domains: {', '.join(t.get('domains') or []) or '—'}")
    for at in t.get("attempts") or []:
        print(f"  attempt {at['n']}: {at['worker']} {at.get('adapter')} {at['outcome']} started {_hm(at['startedAt'])}")
    card = s.card(t["id"])
    if card:
        for st in card["steps"]:
            print(f"   [{st['status']:<7}] {st['title']} ({st['etaMin']}m)")
        for m in card["metrics"]:
            print(f"   {m['label']}: {m['done']:g}" + (f"/{m['total']:g}" if m.get("total") else ""))
        if card.get("question"):
            print(f"  QUESTION: {card['question']}")


def _edit_list(cur, spec):
    cur = list(cur or [])
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if item.startswith("-"):
            cur = [x for x in cur if x != item[1:]]
        else:
            item = item[1:] if item.startswith("+") else item
            if item not in cur:
                cur.append(item)
    return cur


def c_edit(ctx, a):
    s = ctx.store()
    t = s.get(a.task)
    f = {}
    if a.prio:
        f["priority"] = a.prio
    if a.size:
        f["size"] = a.size
    if a.title:
        f["title"] = a.title
    if a.summary:
        f["summary"] = a.summary
    if a.cap:
        f["capability"] = a.cap
    if a.waiting is not None:
        f["waitingFor"] = a.waiting or None
    if a.deps:
        f["deps"] = [s.resolve(d) if not d.startswith(("-", "+")) else d for d in _edit_list(t.get("deps"), a.deps)]
    if a.domains:
        f["domains"] = _edit_list(t.get("domains"), a.domains)
    if a.pin is not None:
        f["pinWorker"] = a.pin or None
    t = s.edit(t["id"], f, by=ctx.actor(), rev=a.rev)
    print(f"{t['id']} updated (rev {t['rev']})")


def c_assign(ctx, a):
    s = ctx.store()
    t = s.get(a.task)
    s.edit(t["id"], {"pinWorker": a.worker}, by=ctx.actor())      # policy-checked: exit 3 + alert on deny
    t = s.get(t["id"])
    if t["state"] == "ready":
        try:
            t = s.transition(t["id"], "queued", actor="DSP", reason=f"assigned to {a.worker}")
        except IllegalTransition as e:
            print(f"deck: pinned to {a.worker}, but not queued yet: {e.reason}")
    print(f"{t['id']} pinned to {a.worker} ({t['state']}); the dispatcher leases it to {a.worker} next")
    warn_no_daemon(ctx)


def c_reassign(ctx, a):
    s = ctx.store()
    t = s.get(a.task)
    if t["state"] == "running":
        t = s.transition(t["id"], "queued", actor=ctx.actor(), reason=f"reassigned to {a.worker}", worker=a.worker, t="T30")
    else:
        t = s.edit(t["id"], {"pinWorker": a.worker}, by=ctx.actor())
    print(f"{t['id']} reassigned to {a.worker} ({t['state']})")


def c_simple(to, t_row=None, verb=""):
    def run(ctx, a):
        s = ctx.store()
        eff = {"t": t_row} if t_row else {}
        if verb == "retry" and a.attempts:
            eff["attempts_plus"] = int(str(a.attempts).lstrip("+"))
        t = s.transition(a.task, to, actor=ctx.actor(), reason=f"{verb} by {ctx.actor()}", **eff)
        print(f"{t['id']} → {t['state']}")
    return run


def _attempt_env(ctx):
    pid = os.environ.get("DECK_PROJECT") or ctx.pid()
    task = os.environ.get("DECK_TASK") or getattr(ctx.args, "task", None)
    att = os.environ.get("DECK_ATTEMPT")
    n = None
    if att:
        parts = att.split(":")
        n = int(parts[-1]) if parts[-1].isdigit() else None
        if len(parts) == 3:
            pid, task = parts[0], parts[1]
    if not task:
        raise NotFound("no task: run inside an attempt (DECK_TASK / DECK_ATTEMPT) or pass the task")
    return ctx.store(pid), task, n, os.environ.get("DECK_TOKEN")


def c_card(ctx, a):
    s, task, n, tok = _attempt_env(ctx)
    if a.ccmd == "show":
        card = s.card(s.resolve(task))
        return ctx.out(card) if a.json else c_show(ctx, argparse.Namespace(task=task, json=False))
    if a.ccmd == "step":
        if a.scmd == "add":
            op, args = "step.add", {"title": a.title, "eta": a.eta, "id": a.id}
        elif a.scmd == "eta":
            op, args = "step.eta", {"id": a.key, "eta": a.minutes}
        else:
            op, args = f"step.{a.scmd}", {"id": a.key}
    elif a.ccmd == "metric":
        op, args = "metric", {"label": a.label, "done": a.count, "total": a.total, "kind": a.kind}
    elif a.ccmd == "note":
        op, args = "note", {"text": " ".join(a.text)}
    else:
        raise ValidationError(f"unknown card command {a.ccmd}")
    s.card_op(task, n, op, args, tok)


def c_ask(ctx, a):
    s, task, n, tok = _attempt_env(ctx)
    s.card_op(task, n, "ask", {"question": " ".join(a.question)}, tok)
    print("question recorded; finish your turn with outcome 'blocked'")


def c_hb(ctx, a):
    s, task, n, tok = _attempt_env(ctx)
    tid = s.resolve(task)
    s.check_token(tid, n, tok)
    data = {}
    if a.turn_end:
        data["turnEnd"] = True
    if a.waiting:
        data["waiting"] = a.waiting
    if a.session_end:
        data["sessionEnd"] = True
    s.heartbeat(tid, n, **data)


def c_claim(ctx, a):
    s = ctx.store()
    home = os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("CODEX_HOME") or os.path.expanduser("~/.claude")
    acct = ctx.cfg.account_for_home(home)
    worker = a.worker or os.environ.get("DECK_WORKER")
    if not worker and acct:
        ws = [w.id for w in ctx.cfg.workers.values() if w.account == acct.id and w.adapter == "interactive"]
        worker = ws[0] if len(ws) == 1 else None
    if not worker:
        raise ValidationError("which worker are you? pass --worker or set DECK_WORKER")
    att = s.claim(a.task, home, worker)
    print(f"claimed {att['task']} as {worker} (attempt {att['n']}). For card ops in this session:")
    print(f"  export DECK_PROJECT={s.pid} DECK_TASK={att['task']} DECK_ATTEMPT={s.pid}:{att['task']}:{att['n']} "
          f"DECK_TOKEN={att['token']}")


def c_handoff(ctx, a):
    s, task, n, tok = _attempt_env(ctx)
    tid = s.resolve(task)
    att = s.check_token(tid, n, tok)
    raw = sys.stdin.read() if a.file == "-" else Path(a.file).read_text()
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValidationError(f"handoff is not JSON: {e}")
    nn = (att or {}).get("n") or n
    res = s.put_handoff(tid, nn, data)
    if not res["valid"]:
        for e in res["errors"][:10]:
            print(f"  {e}", file=sys.stderr)
        raise ValidationError("handoff invalid (see errors); fix and submit again")
    t = s.get(tid)
    # interactive and shell attempts end here; headless adapters report Final themselves (B2)
    if att and att.get("adapter") in ("interactive", "shell") and t["state"] == "running":
        who = att["worker"]
        if data.get("outcome") == "blocked":
            s.transition(tid, "blocked", actor=who, reason="worker blocked")
        else:
            s.transition(tid, "review", actor=who, reason="handoff submitted")
    print(f"handoff accepted for {tid} attempt {nn}")


def c_answer(ctx, a):
    s = ctx.store()
    text = " ".join(a.text)
    eff = {"answer": text}
    if len(a.text) == 2 and a.text[0].isdigit():
        eff = {"decision": int(a.text[0]), "option": a.text[1], "answer": text}
    t = s.transition(a.task, "queued", actor=ctx.actor(), reason="answered", **eff)
    print(f"{t['id']} → {t['state']}")


def c_review(ctx, a):
    s = ctx.store()
    from .views import Views
    v = Views(ctx.cfg, ctx.g, {s.pid: s})
    if a.rcmd in (None, "list"):
        for t in s.list(["review"]):
            r = v.review_vm(s, t, clock.now())
            print(f"{'PILOT ' if r['pilot'] else ''}{t['id']}  {r['outcome']:<8} waiting {r['waitingSec'] // 60}m  {r['summary'][:80]}")
        return
    t = s.get(a.task)
    r = v.review_vm(s, t, clock.now())
    if a.json:
        return ctx.out(r)
    print(f"{t['id']} {t['title']}\n  outcome {r['outcome']} (attempt {r['attempt']})\n  {r['summary']}")
    for d in r["doneWhen"]:
        print(f"  [{'x' if d.get('met') else ' '}] {d.get('item')} — {d.get('evidence')}")
    for x in r["tests"]:
        print(f"  test {x.get('command')}: {x.get('result')}")
    for x in r["risks"]:
        print(f"  risk: {x}")
    for i, d in enumerate(r["decisionsNeeded"], 1):
        print(f"  decision {i}: {d.get('question')} {d.get('options') or ''}")
    if r["unlocks"]:
        print(f"  approving unlocks {', '.join(r['unlocks'])}")


def c_decide(decision):
    def run(ctx, a):
        note = " ".join(getattr(a, "note", []) or [])
        t = ctx.store().review(a.task, decision, by=ctx.actor(), note=note)
        print(f"{t['id']} → {t['state']}")
    return run


def c_merge(ctx, a):
    s = ctx.store()
    if a.task == "status":
        for m in s.merge_queue():
            print(f"{m['id']:>3} {m['task']:<32} {m['state']:<9} {_hm(m['enqueued_at'])} {m['note'] or ''}")
        return
    t = s.get(a.task)
    if t["state"] == "review":
        t = s.review(t["id"], "approve", by=ctx.actor())
    print(f"{t['id']} {t['state']} (merge queue position {[m['task'] for m in s.merge_queue() if m['state'] == 'queued'].index(t['id']) + 1 if t['state'] == 'merging' else '—'})")
    warn_no_daemon(ctx)


def c_domain(ctx, a):
    s = ctx.store()
    if a.dcmd == "list":
        rows = s.domains.held() + [dict(d, name=d["name"]) for d in ctx.g.ext_domains.held()]
        for d in rows:
            print(f"{d['name']:<28} held by {', '.join(d['holders']) or '—':<24} waiting {', '.join(d['waiting']) or '—'}"
                  f"{'  RESERVED' if d['reserved'] else ''}")
        return
    if a.dcmd == "release":
        if not a.force:
            raise ValidationError("release needs --force (it frees a lock someone may hold)")
        n = (ctx.g.ext_domains if a.name.startswith("ext:") else s.domains).release(a.name)
        s.events.emit("lock.released", "warn", f"{a.name} force-released by {ctx.actor()}", data={"domain": a.name})
        print(f"released {n} holder(s) of {a.name}")
        return
    if a.dcmd == "run":
        cmd = list(a.cmd)
        opts = cmd[: cmd.index("--")] if "--" in cmd else []
        cmd = cmd[len(opts) + 1:] if "--" in a.cmd else cmd
        while opts:
            o = opts.pop(0)
            if o == "--no-wait":
                a.no_wait = True
            elif o == "--ttl" and opts:
                a.ttl = int(opts.pop(0))
            else:
                raise ValidationError(f"unknown option {o}")
        if not cmd:
            raise ValidationError("usage: deck domain run <name> [--ttl N] [--no-wait] -- <command…>")
        holder = f"manual-{os.getpid()}"
        ok, b = s.domains.try_acquire([a.name], holder, ttl=a.ttl)
        if not ok:
            if a.no_wait:
                s.domains.clear_waits(holder)
                raise DomainBusy(b["reason"])
            print(f"waiting: {b['reason']}", file=sys.stderr)
            while not ok:
                time.sleep(2)
                ok, b = s.domains.try_acquire([a.name], holder, ttl=a.ttl)
        try:
            rc = subprocess.call(cmd)
        finally:
            s.domains.release_all(holder)
            s.domains.clear_waits(holder)
        sys.exit(rc)


def c_events(ctx, a):
    pid = ctx.pid(required=False) if a.project else None
    evs = ctx.g.events_since(a.since or 0, pid, a.min, limit=a.limit)
    if a.json:
        return ctx.out(evs)
    for e in evs:
        print(f"{e['id']:>6} {_hm(e['ts'])} [{e['project'] or '-'}] {e['severity']:<6} {e['text']}")


def c_watch(ctx, a):
    since = ctx.g.max_event_id() if a.since is None else a.since
    try:
        while True:
            for e in ctx.g.events_since(since, a.project, a.min, limit=500, for_=a.for_):
                print(f"{_hm(e['ts'])} [{e['project'] or '-'}] {e['text']}", flush=True)
                since = e["id"]
            since = max(since, ctx.g.max_event_id()) if a.for_ or a.min else since
            if a.once:
                return
            time.sleep(1)
    except KeyboardInterrupt:
        return


def c_snapshot(ctx, a):
    from .views import Views
    from .views_types import check_vm
    v = Views(ctx.cfg, ctx.g, ctx._stores)
    if a.project_vm:
        vm, name = v.project(a.project_vm), "ProjectVM"
    else:
        vm, name = v.overview(), "OverviewVM"
    errs = check_vm(name, vm)
    ctx.out(vm)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        raise ValidationError(f"{name} does not match views_types")


def c_export(ctx, a):
    s = ctx.store()
    if a.what == "board":
        lines = [f"# Board — {s.pcfg.title}", "", "_Generated by `deck export board --md`; do not edit by hand._", ""]
        lines += ["| Task | Title | State | Priority | Size | Worker | Deps |", "|---|---|---|---|---|---|---|"]
        for t in s.list():
            if t["state"] == "cancelled":
                continue
            lines.append(f"| {t['id']} | {t['title']} | {t['state']} | {t['priority']} | {t['size']} | "
                         f"{t.get('worker') or ''} | {', '.join(t.get('deps') or [])} |")
        print("\n".join(lines))
    elif a.what == "titles":
        ctx.out({t["id"]: {"title": t["title"], "summary": t.get("summary"), "tags": t.get("tags") or []} for t in s.list()})


def c_doctor(ctx, a):
    problems, notes = [], []
    cfg = ctx.cfg
    for acc in cfg.accounts.values():
        if acc.scope not in policy.SCOPES:
            problems.append(f"account {acc.id} has no scope tag (it will run nothing)")
        if acc.home and not os.path.isdir(acc.home):
            notes.append(f"account {acc.id} home {acc.home} does not exist")
    for p in cfg.projects.values():
        if p.scope not in policy.SCOPES:
            problems.append(f"project {p.id} has no scope tag (nothing will dispatch)")
    for w in cfg.workers.values():
        if w.account not in cfg.accounts:
            problems.append(f"worker {w.id} uses unknown account {w.account}")
    files = [cfg.path] + [os.path.expanduser(p.config_path) for p in cfg.projects.values() if p.config_path]
    for f in files:
        if not f or not os.path.exists(f):
            continue
        for p in cfg.projects.values():
            if p.repo and os.path.realpath(f).startswith(os.path.realpath(p.repo) + os.sep):
                problems.append(f"config {f} sits inside repo {p.repo}")
        d = os.path.dirname(os.path.realpath(f))
        r = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", os.path.realpath(f)], capture_output=True)
        if r.returncode == 0:
            problems.append(f"config {f} is tracked by git")
    for pid in cfg.projects:
        try:
            ok = ctx.store(pid).conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                problems.append(f"project {pid} DB integrity: {ok}")
        except DeckError as e:
            problems.append(f"project {pid}: {e.reason}")
    # V7-style check: tasks no worker can run
    for pid in cfg.projects:
        for t in ctx.store(pid).list(["ready", "queued"]):
            if not brief.any_eligible(ctx.store(pid), t):
                problems.append(f"{pid}/{t['id']} cannot run on any worker (policy/capability/pin)")
    for n in notes:
        print(f"note: {n}")
    for p in problems:
        print(f"PROBLEM: {p}")
    if problems:
        raise ValidationError(f"{len(problems)} problem(s)")
    print("doctor: OK")


def c_pair(ctx, a):
    from .server import start_pairing
    code = start_pairing(ctx.cfg.state_dir)
    print(f"pairing code {code} (valid 5 min) — enter it in the phone deck")


def c_serve(ctx, a):
    from .server import serve
    serve(ctx.cfg, a.host, a.port)


# ---------------------------------------------------------------- parser
def build_parser():
    p = argparse.ArgumentParser(prog="deck", description="Agent Deck: tasks, workers, reviews")
    p.add_argument("--config", help="machine config (default $DECK_CONFIG or ~/.config/agent-deck/deck.json)")
    p.add_argument("--project", "-p")
    p.add_argument("--as", dest="as_", help="actor id (default $DECK_WORKER or W0); 'owner' for the owner")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(fn=fn)
        return sp

    add("init", c_init)
    sp = add("project", c_project)
    ps = sp.add_subparsers(dest="pcmd", required=True)
    x = ps.add_parser("add")
    x.add_argument("id")
    x.add_argument("--config", dest="config_file", required=True)
    x.add_argument("--scope", choices=["office", "personal"], required=True)
    x.add_argument("--allow-personal-accounts", action="store_true")
    ps.add_parser("list")
    x = ps.add_parser("disable")
    x.add_argument("id")
    sp = add("account", c_account)
    sp.add_argument("acmd", nargs="?", default="list", choices=["list"])
    sp.add_argument("--json", action="store_true")
    sp = add("usage", c_account)
    sp.add_argument("--json", action="store_true")
    add("workers", c_workers)

    sp = add("new", c_new)
    sp.add_argument("title")
    sp.add_argument("--summary", required=True)
    sp.add_argument("--size", choices=["S", "M", "L", "XL"], default="M")
    sp.add_argument("--prio", choices=["P1", "P2", "P3"], default="P2")
    sp.add_argument("--cap", choices=["code", "data", "ui", "infra", "browser", "research", "review"], default="code")
    sp.add_argument("--kind", choices=["normal", "pilot", "scale", "review", "chore"], default="normal")
    sp.add_argument("--deps")
    sp.add_argument("--domains")
    sp.add_argument("--workers")
    sp.add_argument("--est-pct", type=float)
    sp.add_argument("--est-min", type=float)
    sp.add_argument("--pilot-of")
    sp.add_argument("--no-edit", action="store_true")
    for name, fn in (("validate", c_validate), ("ready", c_ready), ("unready", c_unready)):
        add(name, fn).add_argument("task")
    sp = add("list", c_list)
    sp.add_argument("--state")
    sp.add_argument("--all-projects", action="store_true")
    sp.add_argument("--json", action="store_true")
    add("board", c_board)
    sp = add("show", c_show)
    sp.add_argument("task")
    sp.add_argument("--json", action="store_true")
    sp = add("edit", c_edit)
    sp.add_argument("task")
    for o in ("--prio", "--size", "--title", "--summary", "--cap", "--deps", "--domains", "--pin", "--waiting"):
        sp.add_argument(o)
    sp.add_argument("--rev", type=int)
    for name, fn in (("assign", c_assign), ("reassign", c_reassign)):
        sp = add(name, fn)
        sp.add_argument("task")
        sp.add_argument("worker")
    add("pause", c_simple("blocked", "T20", "paused")).add_argument("task")
    add("resume", c_simple("queued", None, "resumed")).add_argument("task")
    add("cancel", c_simple("cancelled", None, "cancelled")).add_argument("task")
    sp = add("retry", c_simple("queued", "T28", "retry"))
    sp.add_argument("task")
    sp.add_argument("--attempts")
    sp = add("claim", c_claim)
    sp.add_argument("task")
    sp.add_argument("--worker")
    sp = add("hb", c_hb)
    sp.add_argument("--turn-end", action="store_true")
    sp.add_argument("--waiting")
    sp.add_argument("--session-end", action="store_true")
    sp.add_argument("--codex-notify", nargs="?", const=True)

    sp = add("card", c_card)
    cs = sp.add_subparsers(dest="ccmd", required=True)
    st = cs.add_parser("step").add_subparsers(dest="scmd", required=True)
    x = st.add_parser("add")
    x.add_argument("title")
    x.add_argument("--eta", type=float)
    x.add_argument("--id")
    for k in ("start", "done", "fail", "skip"):
        st.add_parser(k).add_argument("key")
    x = st.add_parser("eta")
    x.add_argument("key")
    x.add_argument("minutes", type=float)
    x = cs.add_parser("metric")
    x.add_argument("label")
    x.add_argument("count", type=float)
    x.add_argument("--total", type=float)
    x.add_argument("--kind", choices=["count", "bad", "pass"])
    x = cs.add_parser("note")
    x.add_argument("text", nargs="+")
    x = cs.add_parser("show")
    x.add_argument("--json", action="store_true")
    sp = add("ask", c_ask)
    sp.add_argument("question", nargs="+")
    sp = add("handoff", c_handoff)
    sp.add_argument("hcmd", choices=["submit"])
    sp.add_argument("file")
    sp = add("answer", c_answer)
    sp.add_argument("task")
    sp.add_argument("text", nargs="+")

    sp = add("review", c_review)
    sp.add_argument("rcmd", nargs="?", choices=["list", "show"])
    sp.add_argument("task", nargs="?")
    sp.add_argument("--json", action="store_true")
    add("approve", c_decide("approve")).add_argument("task")
    sp = add("changes", c_decide("changes"))
    sp.add_argument("task")
    sp.add_argument("note", nargs="+")
    sp = add("reject", c_decide("reject"))
    sp.add_argument("task")
    sp.add_argument("note", nargs="*")
    sp = add("merge", c_merge)
    sp.add_argument("task", help="task id, or 'status'")

    sp = add("domain", c_domain)
    ds = sp.add_subparsers(dest="dcmd", required=True)
    ds.add_parser("list")
    x = ds.add_parser("run")
    x.add_argument("name")
    x.add_argument("--ttl", type=int, default=30)
    x.add_argument("--no-wait", action="store_true")
    x.add_argument("cmd", nargs=argparse.REMAINDER)
    x = ds.add_parser("release")
    x.add_argument("name")
    x.add_argument("--force", action="store_true")

    sp = add("events", c_events)
    sp.add_argument("--since", type=int)
    sp.add_argument("--min")
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--json", action="store_true")
    sp = add("watch", c_watch)
    sp.add_argument("--for", dest="for_")
    sp.add_argument("--min")
    sp.add_argument("--since", type=int)
    sp.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    sp = add("snapshot", c_snapshot)
    sp.add_argument("--overview", action="store_true")
    sp.add_argument("--project-vm", metavar="P", help="project page view model (alias of --project P with snapshot)")
    sp.add_argument("--json", action="store_true")
    sp = add("export", c_export)
    sp.add_argument("what", choices=["board", "titles"])
    sp.add_argument("--md", action="store_true")
    add("doctor", c_doctor)
    add("pair", c_pair)
    sp = add("serve", c_serve, help="HTTP API only (deckd adds the dispatcher)")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    for name, (setup, fn) in EXTRA_COMMANDS.items():
        sp = add(name, fn)
        setup(sp)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `deck snapshot --project p --json` → project VM (design §11 wording)
    if argv and "snapshot" in argv and "--project" in argv and "--project-vm" not in argv:
        i = argv.index("--project")
        if i + 1 < len(argv) and argv.index("snapshot") < i:
            argv[i] = "--project-vm"
    args = build_parser().parse_args(argv)
    ctx = None
    try:
        ctx = Ctx(args)
        args.fn(ctx, args)
        return 0
    except DeckError as e:
        print(f"deck: {e.reason}", file=sys.stderr)
        return e.exit_code
    except BrokenPipeError:
        return 0
    finally:
        if ctx is not None:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
