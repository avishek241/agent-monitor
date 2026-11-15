"""Task state machine (design §5). Data only; guards and side effects run in ``deck.store``."""
from __future__ import annotations

import re
from dataclasses import dataclass

STATES = ("draft", "ready", "queued", "starting", "running", "blocked", "limit_paused", "hung", "review",
          "changes_requested", "merging", "done", "failed", "cancelled")
TERMINAL = ("done", "failed", "cancelled")
NON_TERMINAL = tuple(s for s in STATES if s not in TERMINAL)
# States in which the task holds a lease, domains and a slot (§5.1).
HOLDING = ("starting", "running", "hung")
ACTIVE_ATTEMPT = ("starting", "running", "hung")

BOARD_COLUMN = {
    "draft": "backlog", "ready": "backlog", "queued": "upNext", "changes_requested": "upNext",
    "starting": "running", "running": "running", "limit_paused": "running", "hung": "running",
    "blocked": "needsYou", "failed": "needsYou", "review": "review", "merging": "review",
    "done": "doneToday", "cancelled": None,
}

# Actor kinds of §5.2.
W0, OWN, DSP, SUP, ADP, WRK, MQ, TMR = "W0", "OWN", "DSP", "SUP", "ADP", "WRK", "MQ", "TMR"
HUMANS = (W0, OWN)


def actor_kind(actor: str) -> str:
    if actor in (W0, OWN, DSP, SUP, ADP, WRK, MQ, TMR):
        return actor
    if actor == "owner":
        return OWN
    if re.fullmatch(r"W[0-9]{1,2}", actor or ""):
        return WRK
    return actor or "?"


@dataclass(frozen=True)
class Row:
    id: str
    frm: tuple
    to: str
    actors: tuple
    guard: str = ""        # name of a guard in store._GUARDS ("" = none)
    note: str = ""


# Every row of the §5.2 table. Order matters where two rows share (from, to): the first row whose
# actor and guard pass is applied (e.g. T8 before T9).
ROWS = (
    Row("T1", ("draft",), "ready", (W0, OWN), "validated", "validator passes"),
    Row("T1x", ("draft",), "draft", (W0, OWN), "", "validation failed"),
    Row("T2", ("ready", "queued"), "draft", (SUP, W0, OWN), "no_running_attempt", "brief edited / unready"),
    Row("T3", ("ready",), "queued", (DSP,), "deps_done", "deps done, pilot done, project enabled"),
    Row("T4", ("ready", "queued"), "blocked", (DSP,), "dep_failed", "upstream failed"),
    Row("T5", ("queued",), "starting", (DSP,), "lease_only", "lease granted"),
    Row("T6", ("starting",), "running", (ADP, SUP, WRK, W0, OWN), "attempt_alive", "first output / claim"),
    Row("T7", ("starting",), "queued", (SUP, DSP), "attempts_left", "spawn failed / offer expired"),
    Row("T8", ("running",), "review", (ADP, SUP, WRK, W0), "valid_handoff", "valid handoff"),
    Row("T9", ("running",), "review", (SUP, ADP, WRK, W0), "no_valid_handoff", "no valid handoff"),
    Row("T10", ("running",), "blocked", (ADP, WRK), "blocked_handoff_or_question", "handoff blocked / ask"),
    Row("T11", ("running", "starting"), "limit_paused", (ADP, SUP), "", "limit hit"),
    Row("T12", ("running",), "hung", (SUP,), "quiet_and_idle", "no heartbeat, no CPU"),
    Row("T13", ("hung",), "running", (SUP,), "heartbeat_recent", "recovered"),
    Row("T14", ("hung",), "queued", (SUP,), "restart_killed", "killed, restart"),
    Row("T15", ("hung",), "blocked", (SUP,), "hang_block", "block / alert / exhausted"),
    Row("T16", ("running",), "queued", (SUP,), "attempts_left", "crash / attempt timeout"),
    Row("T17", ("running", "hung", "starting"), "failed", (SUP,), "exhausted_or_expired", "attempts exhausted"),
    Row("T18", ("limit_paused",), "queued", (TMR, DSP), "resume_due", "reset passed / failover"),
    Row("T19", ("blocked",), "queued", (W0, OWN), "", "answer / unblock / resume"),
    Row("T20", ("running",), "blocked", (W0, OWN), "", "paused"),
    Row("T21", ("review",), "merging", (W0, OWN, DSP, MQ), "approve_merge", "approved"),
    Row("T22", ("review",), "done", (W0, OWN), "approve_no_branch", "approved, no branch"),
    Row("T23", ("review",), "changes_requested", (W0, OWN), "note_given", "changes requested"),
    Row("T24", ("merging",), "done", (MQ,), "", "merged"),
    Row("T25", ("merging",), "changes_requested", (MQ,), "", "conflict / gate failed"),
    Row("T26", ("merging",), "review", (MQ,), "", "main checkout busy"),
    Row("T27", ("changes_requested",), "queued", (DSP,), "rework_due", "re-queue rework"),
    Row("T28", ("failed",), "queued", (W0,), "", "retry"),
    Row("T29", NON_TERMINAL, "cancelled", (W0, OWN), "", "cancelled"),
    Row("T30", ("running",), "queued", (W0, OWN), "reassign_allowed", "reassigned"),
)

ROW_BY_ID = {r.id: r for r in ROWS}


def rows_for(frm: str, to: str):
    return [r for r in ROWS if frm in r.frm and r.to == to]


def legal_pairs():
    return {(f, r.to) for r in ROWS for f in r.frm}
