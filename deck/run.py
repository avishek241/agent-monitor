"""`deck-run`: wraps every agent process (design §6.6; M1, M8).

    python3 -m deck.run --run-dir R --attempt-id P:T:N [--ports 4210-4219] [--grace 5] -- <argv…>

1. The agent starts in its OWN session / process group (start_new_session), stdin=/dev/null,
   stdout → R/out.jsonl, stderr → R/err.log. deck-run itself stays outside that group so it can clean up.
2. Every descendant inherits DECK_RUN_ID=P:T:N (the marker for escapees). DECK_ATTEMPT keeps the attempt number
   that `deck card` reads.
3. R/pid.json {pid, pgid, start, wrapper}.
4. After exit: kill what is left of the group after `grace` seconds, then escapees found by the env marker
   (`ps eww`) or listening on the slot's ports (`lsof`). Count recorded.
5. R/exit.json {code, signal, endedAt, orphansKilled} written atomically — the durable completion signal: deckd sees
   it even if it was down when the agent finished.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

MARK = "DECK_RUN_ID"


def write_json_atomic(path: Path, data: dict):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def process_start(pid: int):
    """`ps -o lstart=`: detects PID reuse on reconcile."""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        s = out.stdout.strip()
        return s or None
    except (OSError, subprocess.SubprocessError):
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie still answers kill(0); treat it as dead
    try:
        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        return bool(st.strip()) and not st.strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return True


def group_pids(pgid: int) -> list:
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[1] == str(pgid) and not (len(parts) > 2 and parts[2].startswith("Z")):
            res.append(int(parts[0]))
    return res


def _cputime_secs(s: str) -> float:
    # ps TIME: [[dd-]hh:]mm:ss.cc
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [float(p) for p in s.split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return days * 86400 + secs


def group_cpu_secs(pgid: int) -> float:
    """Total CPU time of the live members of the process group."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pgid=,time="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    tot = 0.0
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[0] == str(pgid):
            try:
                tot += _cputime_secs(parts[1])
            except ValueError:
                pass
    return tot


def deepest_child(pgid: int):
    """(command, age_seconds) of the youngest process in the group — what the agent is waiting on."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,etime=,command="], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    best = None
    for ln in out.splitlines():
        parts = ln.split(None, 4)
        if len(parts) == 5 and parts[2] == str(pgid):
            age = _etime_secs(parts[3])
            if best is None or age < best[1]:
                best = (parts[4][:160], age)
    return best


def _etime_secs(s: str) -> int:
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    secs = 0
    for p in s.split(":"):
        secs = secs * 60 + int(p)
    return days * 86400 + secs


def kill_group(pgid: int, int_wait: float = 20, term_wait: float = 10) -> str:
    """SIGINT → wait → SIGTERM → wait → SIGKILL (design §6.5). Returns the signal that ended it."""
    for sig, wait in ((signal.SIGINT, int_wait), (signal.SIGTERM, term_wait)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return "gone"
        except PermissionError:
            return "denied"
        end = time.time() + wait
        while time.time() < end:
            if not group_pids(pgid):
                return sig.name
            time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return "SIGKILL"


def find_marked(run_id: str, exclude=()) -> list:
    """Processes whose environment carries DECK_RUN_ID=<run_id> (ps eww shows env after the command on macOS)."""
    try:
        out = subprocess.run(["ps", "eww", "-A", "-o", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pat = re.compile(rf"(?:^|\s){MARK}={re.escape(run_id)}(?:\s|$)")
    res = []
    for ln in out.splitlines():
        parts = ln.strip().split(None, 1)
        if len(parts) == 2 and pat.search(parts[1]):
            pid = int(parts[0])
            if pid not in exclude:
                res.append(pid)
    return res


def listening_pids(ports) -> list:
    ports = set(ports or [])
    if not ports:
        return []
    try:
        out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res, pid = set(), None
    for ln in out.splitlines():
        if ln.startswith("p"):
            pid = int(ln[1:])
        elif ln.startswith("n") and pid:
            m = re.search(r":(\d+)$", ln)
            if m and int(m.group(1)) in ports:
                res.add(pid)
    return sorted(res)


def kill_pids(pids, wait: float = 3) -> int:
    n = 0
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
            n += 1
        except (ProcessLookupError, PermissionError):
            pass
    end = time.time() + wait
    while time.time() < end and any(alive(p) for p in pids):
        time.sleep(0.1)
    for p in pids:
        if alive(p):
            try:
                os.kill(p, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    return n


def cleanup(pgid: int, run_id: str, ports=(), grace: float = 5, me: int = None) -> list:
    """Kill leftovers of an attempt. Returns [{pid, how}]."""
    killed = []
    if grace:
        end = time.time() + grace
        while time.time() < end and group_pids(pgid):
            time.sleep(0.1)
    left = group_pids(pgid)
    if left:
        kill_group(pgid, int_wait=0, term_wait=2)
        killed += [{"pid": p, "how": "group"} for p in left]
    me = me or os.getpid()
    marked = [p for p in find_marked(run_id, exclude={me}) if p not in left]
    if marked:
        kill_pids(marked)
        killed += [{"pid": p, "how": "env-marker"} for p in marked]
    port_pids = [p for p in listening_pids(ports) if p != me and p not in left and p not in marked]
    if port_pids:
        kill_pids(port_pids)
        killed += [{"pid": p, "how": "slot-port"} for p in port_pids]
    return killed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deck-run")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument("--ports", default="")
    ap.add_argument("--grace", type=float, default=5)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    run = Path(a.run_dir)
    run.mkdir(parents=True, exist_ok=True)
    ports = []
    if a.ports:
        lo, _, hi = a.ports.partition("-")
        ports = list(range(int(lo), int(hi or lo) + 1))
    env = dict(os.environ)
    env[MARK] = a.attempt_id
    out = open(run / "out.jsonl", "ab", buffering=0)
    err = open(run / "err.log", "ab", buffering=0)
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env, start_new_session=True)
    except OSError as e:
        err.write(f"deck-run: spawn failed: {e}\n".encode())
        write_json_atomic(run / "exit.json", {"code": 127, "signal": None, "endedAt": int(time.time()),
                                              "orphansKilled": 0, "spawnError": str(e)})
        return 127
    write_json_atomic(run / "pid.json", {"pid": p.pid, "pgid": p.pid, "start": process_start(p.pid),
                                         "wrapper": os.getpid()})

    def forward(sig, _frame):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, forward)
    while True:
        try:
            rc = p.wait()
            break
        except InterruptedError:
            continue
    ended = int(time.time())
    killed = cleanup(p.pid, a.attempt_id, ports, a.grace)
    if killed:
        with open(run / "orphans.json", "w") as fh:
            json.dump(killed, fh)
    write_json_atomic(run / "exit.json", {"code": rc if rc >= 0 else None, "signal": -rc if rc < 0 else None,
                                          "endedAt": ended, "orphansKilled": len(killed)})
    return 0


def spawn(spec, run_dir: Path, run_id: str, ports=None, grace: float = 5, python=None) -> int:
    """Start deck-run detached from the caller (its own session), so deckd restarts never kill agents."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    argv = [python or sys.executable, "-m", "deck.run", "--run-dir", str(run_dir), "--attempt-id", run_id,
            "--grace", str(grace)]
    if ports:
        argv += ["--ports", f"{min(ports)}-{max(ports)}"]
    argv += ["--", *spec.argv]
    env = dict(spec.env)
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with open(run_dir / "wrapper.log", "ab") as log:
        w = subprocess.Popen(argv, cwd=str(spec.cwd), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
    _children.append(w)
    return w.pid


_children = []


def reap():
    """Collect finished deck-run wrappers so the daemon leaves no zombies."""
    _children[:] = [c for c in _children if c.poll() is None]


if __name__ == "__main__":
    sys.exit(main())
