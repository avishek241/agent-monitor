"""T-LIM, T-HANG, T-ORPH, T-REC, T-QUO regression (supervisor side). Real-process tests use deck-run for real."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

from deck import clock
from deck import run as deckrun
from deck.dispatcher import Dispatcher
from tests.b2_helpers import T0, DeckCase, account, project, worker, write_exit, write_out

ROOT = Path(__file__).resolve().parent.parent
INIT = {"type": "system", "subtype": "init", "session_id": "sess-1"}


def rejected(resets):
    return {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": resets,
                                                            "rateLimitType": "five_hour"}}


class LimitPauseResume(DeckCase):
    def claude_setup(self, on_limit="wait", homes=None):
        homes = homes or {}
        self.build([account("cw", tool="claude", home=homes.get("cw")), account("cv", tool="claude", home=homes.get("cv"))],
                   [worker("W3", "cw", adapter="claude-headless"), worker("W5", "cv", adapter="claude-headless")],
                   [project("shop")])
        self.usage("cw", 5, 250)
        self.usage("cv", 70, 250)        # less urgent: the task goes to cw first
        self.add("shop", "t", workerClass=["claude-headless"], size="S", retry={"maxAttempts": 3, "onLimit": on_limit,
                                                                                  "onHang": "restart"})
        self.d.tick()
        self.assertEqual(self.launcher.calls[0]["spec"].env["CLAUDE_CONFIG_DIR"], self.cfg.account("cw").home)
        return self.launcher.calls[0]["run_dir"]

    def test_limit_pauses_cools_and_resumes_same_session(self):
        rd = self.claude_setup("wait")
        write_out(rd, [INIT, rejected(T0 + 4 * 3600)])
        write_exit(rd, 1)
        self.d.tick()
        t = self.stores["shop"].get("t")
        self.assertEqual(t["state"], "limit_paused")
        self.assertEqual(t["resumeAt"], T0 + 4 * 3600 + 90)
        self.assertEqual(t["attempts"][0]["outcome"], "limit")
        self.assertEqual(self.g.account_state.get("cw")["state"], "cooling")
        self.assertEqual(len(self.events("limit.hit")), 1)
        clock.set(T0 + 4 * 3600 + 89)
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "limit_paused")
        clock.set(T0 + 4 * 3600 + 91)
        self.d.tick()
        t = self.stores["shop"].get("t")
        self.assertEqual(t["state"], "starting")
        self.assertEqual(t["attempts"][-1]["worker"], "W3")
        argv = self.launcher.calls[-1]["spec"].argv
        self.assertEqual(argv[argv.index("--resume") + 1], "sess-1")
        self.assertIn("CONTINUATION of attempt 1 (limit)", self.launcher.calls[-1]["spec"].argv[-1])
        self.assertEqual(len([e for e in self.events("limit.reset") if e["data"].get("account") == "cw"]), 1)

    def test_auto_failover_when_reset_far_and_headroom(self):
        homes = {"cw": str(self.state / "home-cw"), "cv": str(self.state / "home-cv")}
        tr = Path(homes["cw"]) / "projects" / "-ws"
        tr.mkdir(parents=True)
        (tr / "sess-1.jsonl").write_text('{"type":"user"}\n')
        rd = self.claude_setup("auto", homes)
        write_out(rd, [INIT, rejected(T0 + 4 * 3600)])
        write_exit(rd, 1)
        self.d.tick()
        t = self.stores["shop"].get("t")
        self.assertEqual(t["state"], "starting")                   # failed over in the same tick
        self.assertEqual(t["attempts"][-1]["worker"], "W5")
        call = self.launcher.calls[-1]
        argv = call["spec"].argv
        resumed = argv[argv.index("--resume") + 1]
        self.assertEqual(resumed, str(call["run_dir"] / "sess-1.jsonl"))       # spike 3: copied transcript path
        self.assertTrue(Path(resumed).exists())
        self.assertEqual(call["spec"].env["CLAUDE_CONFIG_DIR"], homes["cv"])

    def test_auto_waits_when_reset_is_near(self):
        rd = self.claude_setup("auto")
        write_out(rd, [INIT, rejected(T0 + 30 * 60)])
        write_exit(rd, 1)
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "limit_paused")

    def test_wait_policy_never_fails_over(self):
        rd = self.claude_setup("wait")
        write_out(rd, [INIT, rejected(T0 + 4 * 3600)])
        write_exit(rd, 1)
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "limit_paused")
        self.assertEqual(len(self.launcher.calls), 1)

    def test_m4_regression_limit_line_never_reread(self):
        """M4: "resets 8:50pm" read again after resume / next day must never yield a new limit."""
        self.build([account("cw", tool="claude")], [worker("W3", "cw", adapter="claude-headless")], [project("shop")])
        self.usage("cw", 5, 250)
        self.add("shop", "t", workerClass=["claude-headless"], size="S")
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [INIT, "You've hit your 5-hour limit · resets 8:50pm"])
        self.d.tick()
        self.assertEqual(len(self.g.limits.rows), 1)
        first = self.g.limits.rows[0]["resets_at"]
        for day in range(1, 3):                      # the next days: more output, same old line still in the file
            clock.set(T0 + day * 86400)
            self.g.usage.put({"account": "cw", "read_at": clock.now(), "source": "statusline", "pct5": 10, "pct7": 10,
                              "resets5": clock.now() + 3600, "resets7": clock.now() + 9 * 86400})
            write_out(rd, [{"type": "assistant", "message": {"content": [{"type": "text", "text": "still working"}]}}])
            self.d.tick()
        self.assertEqual(len(self.g.limits.rows), 1)
        self.assertEqual(self.g.limits.rows[0]["resets_at"], first)
        self.assertFalse(self.d.eff("cw", clock.now())["cooling"])


class HangDetection(DeckCase):
    def one_task(self, **kw):
        self.build([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        self.add("shop", "t", size="S", timeouts={"attemptMin": 600, "taskHours": 48, "quietMin": 15}, **kw)
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th-1"}])
        return rd

    def test_silent_idle_agent_suspect_then_hung_then_restart(self):
        self.one_task()
        pid, rd = self.start_proc("shop", "t", cpu=1.0)
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "running")
        clock.advance(16 * 60)
        self.d.tick()
        sus = self.events("attempt.suspect")
        self.assertEqual(len(sus), 1)
        self.assertIn("curl", sus[0]["text"])
        clock.advance(15 * 60)
        self.d.tick()
        self.assertEqual(len(self.events("attempt.hung")), 1)
        self.assertEqual(self.probe.killed, [pid])
        self.d.tick()                                          # exit.json written by the kill → restart attempt 2
        t = self.stores["shop"].get("t")
        self.assertEqual([a["outcome"] for a in t["attempts"]], ["hung", "running"])
        argv = self.launcher.calls[-1]["spec"].argv
        self.assertEqual(argv[:4], ["codex", "exec", "resume", "th-1"])

    def test_silent_but_cpu_busy_is_not_hung(self):
        self.one_task()
        pid, rd = self.start_proc("shop", "t", cpu=1.0)
        for _ in range(8):
            clock.advance(5 * 60)
            self.probe.procs[pid]["cpu"] += 30           # a long build burns CPU
            self.d.tick()
        self.assertEqual(self.events("attempt.hung"), [])
        self.assertEqual(self.stores["shop"].get("t")["state"], "running")

    def test_on_hang_block(self):
        self.one_task(retry={"maxAttempts": 3, "onLimit": "auto", "onHang": "block"})
        self.start_proc("shop", "t")
        self.d.tick()
        clock.advance(31 * 60)
        self.d.tick()
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "blocked")

    def test_hung_recovers_when_output_resumes(self):
        self.one_task(retry={"maxAttempts": 3, "onLimit": "auto", "onHang": "alert"})
        _pid, rd = self.start_proc("shop", "t")
        self.d.tick()
        clock.advance(31 * 60)
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "hung")
        write_out(rd, [{"type": "item.started", "item": {"type": "command_execution", "command": "ls"}}])
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "running")

    def test_interactive_offer_expires(self):
        self.build([account("cl", tool="claude")], [worker("W0", "cl", adapter="interactive")], [project("shop")])
        self.usage("cl", 5, 250)
        self.add("shop", "t", workerClass=["interactive"], size="S")
        self.d.tick()
        self.assertEqual(self.stores["shop"].get("t")["state"], "starting")
        self.assertIn("deck claim t", self.events("attempt.started")[-1]["text"])
        clock.advance(16 * 60)
        self.d.tick()
        t = self.stores["shop"].get("t")
        self.assertEqual(t["state"], "queued")
        self.assertEqual(self.d.workers_for("cl", "shop", t, clock.now()), [])     # penalised for 30 min


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class RealProcesses(unittest.TestCase):
    """deck-run for real: stdin EOF, orphans by env marker and by slot port, exit.json."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.rd = Path(self.tmp.name) / "run"

    def tearDown(self):
        self.tmp.cleanup()

    def deck_run(self, code, ports=None, grace="0.5", run_id="shop:t:1", timeout=40):
        argv = [sys.executable, "-m", "deck.run", "--run-dir", str(self.rd), "--attempt-id", run_id, "--grace", grace]
        if ports:
            argv += ["--ports", f"{ports[0]}-{ports[-1]}"]
        argv += ["--", sys.executable, "-c", code]
        subprocess.run(argv, cwd=str(ROOT), timeout=timeout, stdin=subprocess.DEVNULL)
        return json.loads((self.rd / "exit.json").read_text())

    def test_stdin_is_dev_null_and_exit_written(self):
        ex = self.deck_run("import sys; print(repr(sys.stdin.read())); sys.exit(3)")
        self.assertEqual(ex["code"], 3)
        self.assertEqual((self.rd / "out.jsonl").read_text().strip(), "''")
        pj = json.loads((self.rd / "pid.json").read_text())
        self.assertEqual(pj["pid"], pj["pgid"])
        self.assertTrue(pj["start"])

    def test_orphans_killed_by_env_marker_and_port(self):
        port = free_port()
        code = f"""
import subprocess, sys, time, os
# escapee 1: its own session (like nohup/setsid) — found by the DECK_RUN_ID env marker
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
# escapee 2: a dev server on the slot port, env scrubbed — found by the listening port
env = {{k: v for k, v in os.environ.items() if k != 'DECK_RUN_ID'}}
subprocess.Popen([sys.executable, '-m', 'http.server', '{port}', '--bind', '127.0.0.1'], start_new_session=True,
                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
print('parent exits')
"""
        ex = self.deck_run(code, ports=[port])
        self.assertEqual(ex["code"], 0)
        self.assertGreaterEqual(ex["orphansKilled"], 2)
        orphans = json.loads((self.rd / "orphans.json").read_text())
        hows = {o["how"] for o in orphans}
        self.assertIn("env-marker", hows)
        self.assertIn("slot-port", hows)
        time.sleep(0.3)
        for o in orphans:
            self.assertFalse(deckrun.alive(o["pid"]), o)
        self.assertEqual(deckrun.find_marked("shop:t:1"), [])
        self.assertEqual(deckrun.listening_pids([port]), [])

    def test_marker_match_is_exact(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             env={**os.environ, "DECK_RUN_ID": "shop:t:10"}, start_new_session=True)
        try:
            time.sleep(0.3)
            self.assertNotIn(p.pid, deckrun.find_marked("shop:t:1"))
            self.assertIn(p.pid, deckrun.find_marked("shop:t:10"))
        finally:
            p.kill()
            p.wait()

    def test_marked_caller_does_not_find_its_own_ps(self):
        """W0 smoke regression: deck-run carries the marker, so its `ps` child did too and was reported killed."""
        code = ("import json, os, sys; sys.path.insert(0, %r); from deck import run; "
                "print(json.dumps(run.find_marked('shop:t:7', exclude={os.getpid()})))") % str(Path(deckrun.__file__).parent.parent)
        out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "DECK_RUN_ID": "shop:t:7"},
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(out.stdout), [], out.stderr)

    def test_kill_group_escalates_past_sigint(self):
        p = subprocess.Popen([sys.executable, "-c",
                              "import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)"],
                             start_new_session=True)
        time.sleep(0.3)
        how = deckrun.kill_group(p.pid, int_wait=0.5, term_wait=2)
        p.wait(timeout=5)
        self.assertEqual(how, "SIGTERM")

    def test_cpu_sampling_sees_busy_group(self):
        p = subprocess.Popen([sys.executable, "-c", "import time\nt=time.time()\nwhile time.time()-t<3: pass"],
                             start_new_session=True)
        try:
            time.sleep(2.5)
            self.assertGreaterEqual(deckrun.group_cpu_secs(p.pid), 1.5)
            self.assertIn("python", (deckrun.deepest_child(p.pid) or ("",))[0].lower())
        finally:
            p.wait()


class PauseCancel(DeckCase):
    def test_pause_and_cancel_stop_the_process(self):
        self.build([account("cx", max=2)], [worker("W1", "cx", lanes=2)], [project("shop")])
        self.usage("cx", 5, 250)
        self.add("shop", "p", size="S", createdAt=T0)
        self.add("shop", "c", size="S", createdAt=T0 + 1)
        self.d.tick()
        procs = {}
        for tid in ("p", "c"):
            rd = Path(self.stores["shop"].current_attempt(tid)["runDir"])
            write_out(rd, [{"type": "thread.started", "thread_id": f"th-{tid}"}])
            procs[tid], _ = self.start_proc("shop", tid)
        self.d.tick()
        self.stores["shop"].transition("p", "blocked", actor="W0", reason="paused by W0")       # T20
        self.stores["shop"].transition("c", "cancelled", actor="owner", reason="cancelled")     # T29
        self.d.tick()
        self.assertEqual(sorted(self.probe.killed), sorted(procs.values()))
        self.d.tick()
        self.assertEqual(len(self.probe.killed), 2)                     # once each
        self.assertEqual(self.stores["shop"].get("p")["state"], "blocked")


class Reconcile(DeckCase):
    def running_task(self):
        self.build([account("cx")], [worker("W1", "cx")], [project("shop")])
        self.usage("cx", 5, 250)
        self.add("shop", "t", size="S")
        self.d.tick()
        rd = self.launcher.calls[0]["run_dir"]
        write_out(rd, [{"type": "thread.started", "thread_id": "th-1"}])
        pid, _ = self.start_proc("shop", "t")
        self.d.tick()
        return rd, pid

    def restart(self):
        """A brand-new daemon (no memory) over the same stores."""
        self.d = Dispatcher(self.cfg, self.g, self.stores, state_dir=self.state, launcher=self.launcher,
                            probe=self.probe, ram=lambda: 16.0, read_usage=lambda a, n: None)
        return self.d.reconcile()

    def test_finished_while_daemon_down(self):
        rd, pid = self.running_task()
        self.probe.procs[pid]["alive"] = False
        (rd / "last-message.json").write_text(json.dumps({"outcome": "done", "summary": "All rows imported and checked.",
                                                          "doneWhen": [], "tests": [], "risks": [], "decisionsNeeded": []}))
        write_exit(rd, 0)
        counts = self.restart()
        self.assertEqual(counts["finished"], 1)
        self.assertEqual(self.stores["shop"].get("t")["state"], "review")
        self.assertEqual(len(self.events("review.requested")), 1)

    def test_alive_agent_readopted(self):
        rd, pid = self.running_task()
        counts = self.restart()
        self.assertEqual(counts["adopted"], 1)
        self.assertEqual(self.stores["shop"].get("t")["state"], "running")
        write_out(rd, [{"type": "item.started", "item": {"type": "command_execution", "command": "ls"}}])
        self.d.tick()                                     # keeps tailing from out_offset
        self.assertEqual(self.stores["shop"].current_attempt("t")["outOffset"], (rd / "out.jsonl").stat().st_size)

    def test_pid_reuse_is_crash_with_continuation(self):
        rd, pid = self.running_task()
        self.probe.procs[pid]["start"] = "Mon Nov 16 09:00:00 2026"        # same pid, different process
        counts = self.restart()
        self.assertEqual(counts["crashed"], 1)
        t = self.stores["shop"].get("t")
        self.assertEqual((t["state"], t["attempts"][0]["outcome"]), ("queued", "crashed"))
        clock.advance(3 * 60)
        self.d.tick()
        self.assertEqual(self.launcher.calls[-1]["spec"].argv[:4], ["codex", "exec", "resume", "th-1"])


class EndToEndShell(DeckCase):
    """Real spawn → deck-run → exit.json → supervisor → review, with the daemon 'down' while the job ends."""

    def test_shell_task_real_process(self):
        self.build([account("cx")], [worker("W9", "cx", adapter="shell")], [project("shop")])
        self.usage("cx", 5, 250)
        self.add("shop", "job", workerClass=["shell"], size="S", command=f"{sys.executable} -c \"print('rows: 12')\"")
        self.d.launcher = deckrun.spawn
        self.d.probe = __import__("deck.supervise", fromlist=["Probe"]).Probe(kill_waits=(1, 1))
        self.d.tick()
        rd = Path(self.stores["shop"].get("job")["attempts"][-1]["runDir"])
        deadline = time.time() + 30
        while not (rd / "exit.json").exists() and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue((rd / "exit.json").exists())
        d2 = Dispatcher(self.cfg, self.g, self.stores, state_dir=self.state, launcher=deckrun.spawn,
                        ram=lambda: 16.0, read_usage=lambda a, n: None)
        d2.reconcile()
        t = self.stores["shop"].get("job")
        self.assertEqual(t["state"], "review")
        self.assertIn("rows: 12", (rd / "out.jsonl").read_text())
        h = json.loads((rd / "handoff.json").read_text())
        self.assertEqual(h["outcome"], "partial")
        self.assertEqual(h["run"]["exitCode"], 0)
        self.assertEqual(json.loads((rd / "launch.json").read_text())["argv"][0], sys.executable)


if __name__ == "__main__":
    unittest.main()
