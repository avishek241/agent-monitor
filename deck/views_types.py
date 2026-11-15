"""View models of the HTTP API (design §12). Frozen contract for B3 (PWA + PanelV2).

All keys camelCase; times are unix seconds; percentages 0..100; ``None`` serialises to null.
``REQUIRED`` lists the keys each VM always carries (``check_vm`` uses it in tests and in
``deck snapshot``); every other key may be absent or null.
"""
from __future__ import annotations

from typing import Any, List, Optional, TypedDict


class Counts(TypedDict, total=False):
    running: int; queued: int; review: int; blocked: int; limit_paused: int; hung: int; doneToday: int


class ProjectTile(TypedDict, total=False):
    id: str; title: str; scope: Optional[str]; color: Optional[str]; icon: Optional[str]; enabled: bool
    counts: Counts; headline: str; alerts: int


class Capacity(TypedDict, total=False):
    lanesUsed: int; lanesMax: int; spendable5: Optional[float]; committed5: Optional[float]
    minutesToReset: Optional[int]; urgency: Optional[float]; fitsSize: Optional[str]; spare: bool; why: Optional[str]


class LaneVM(TypedDict, total=False):
    worker: str; lane: int; state: str            # free | running | cooling
    project: Optional[str]; task: Optional[str]; title: Optional[str]
    step: Optional[str]; stepIndex: Optional[int]; stepCount: Optional[int]; etaMin: Optional[float]
    lastActiveSec: Optional[int]; resumeAt: Optional[int]


class AccountVM(TypedDict, total=False):
    id: str; label: str; tool: str; scope: Optional[str]
    pct5: Optional[float]; pct7: Optional[float]; resets5: Optional[int]; resets7: Optional[int]
    readAgeSec: Optional[int]; confidence: str    # high | stale | low | unknown
    state: str                                    # ok | tight | full | cooling | unknown
    dispatch: bool; capacity: Capacity; lanes: List[LaneVM]


class EventVM(TypedDict, total=False):
    id: int; ts: int; project: Optional[str]; type: str; severity: str; task: Optional[str]
    attempt: Optional[int]; worker: Optional[str]; text: str; data: dict


class OverviewVM(TypedDict, total=False):
    time: int
    inbox: dict                                   # {review, questions, alerts}
    projects: List[ProjectTile]
    accounts: List[AccountVM]
    machine: dict                                 # {running, maxParallelTotal, freeRamGb}
    backlog: dict                                 # {eligible, target, low, hint}
    alerts: List[EventVM]


class StepVM(TypedDict, total=False):
    title: str; index: int; count: int; etaMin: Optional[float]


class MetricVM(TypedDict, total=False):
    label: str; done: float; total: Optional[float]; kind: str; rate: Optional[float]; left: Optional[float]


class TaskVM(TypedDict, total=False):
    id: str; num: int; title: str; summary: str; tags: List[str]; priority: str; size: str
    state: str; stateReason: Optional[str]; worker: Optional[str]
    progress: Optional[float]; step: Optional[StepVM]; etaFinish: Optional[int]
    metrics: List[MetricVM]; lastActiveSec: Optional[int]; warnings: List[str]
    deps: List[dict]; resumeAt: Optional[int]; kind: str


class ReviewVM(TypedDict, total=False):
    task: TaskVM; attempt: Optional[int]; summary: str; outcome: str
    doneWhen: list; tests: list; risks: list; decisionsNeeded: list
    git: dict; waitingSec: int; pilot: bool; unlocks: List[str]


class WorkerVM(TypedDict, total=False):
    worker: str; account: str; label: str; scope: Optional[str]; adapter: str
    state: str; allowed: bool; reason: Optional[str]; lanes: List[LaneVM]


class EligibleVM(TypedDict, total=False):
    worker: str; account: str; label: str; scope: Optional[str]; allowed: bool; reason: Optional[str]


class DomainVM(TypedDict, total=False):
    name: str; kind: str; holder: Optional[str]; since: Optional[int]; waiting: List[str]; reserved: bool


class ProjectVM(TypedDict, total=False):
    project: dict          # {id,title,scope,allowPersonalAccounts,color,icon,enabled,repo:{commit,changes[]},health[]}
    workers: List[WorkerVM]
    board: dict            # {backlog,upNext,running,review,needsYou,doneToday: [TaskVM]}
    inbox: List[ReviewVM]
    alerts: List[EventVM]
    timeline: List[EventVM]
    domains: List[DomainVM]
    merge: dict            # {active, queue: [{task, state, enqueuedAt}]}
    lanes: List[LaneVM]


class TaskDetailVM(TypedDict, total=False):
    task: dict; card: Optional[dict]; attempts: list; handoff: Optional[dict]; history: list; events: list
    view: TaskVM


class ActionResult(TypedDict, total=False):
    ok: bool; task: dict; code: str; reason: str   # code: policy_denied | illegal | stale | invalid | not_found


ACTIONS = ("approve", "changes", "reject", "pause", "resume", "cancel", "retry", "answer", "priority", "reassign")

REQUIRED = {
    "OverviewVM": ("time", "inbox", "projects", "accounts", "machine", "backlog", "alerts"),
    "ProjectTile": ("id", "title", "scope", "enabled", "counts", "headline", "alerts"),
    "AccountVM": ("id", "label", "tool", "scope", "confidence", "state", "dispatch", "capacity", "lanes"),
    "LaneVM": ("worker", "lane", "state"),
    "ProjectVM": ("project", "workers", "board", "inbox", "alerts", "timeline", "domains", "merge", "lanes"),
    "TaskVM": ("id", "num", "title", "summary", "priority", "size", "state", "metrics", "warnings", "deps", "kind"),
    "ReviewVM": ("task", "summary", "outcome", "doneWhen", "tests", "risks", "decisionsNeeded", "git",
                 "waitingSec", "pilot", "unlocks"),
    "EligibleVM": ("worker", "account", "label", "scope", "allowed", "reason"),
    "TaskDetailVM": ("task", "card", "attempts", "handoff", "history", "events"),
}

BOARD_COLUMNS = ("backlog", "upNext", "running", "review", "needsYou", "doneToday")


def check_vm(name: str, vm: Any) -> List[str]:
    """Return a list of problems (empty = OK). Recurses into the nested VMs it knows."""
    errs: List[str] = []
    if not isinstance(vm, dict):
        return [f"{name}: not an object"]
    for k in REQUIRED.get(name, ()):
        if k not in vm:
            errs.append(f"{name}: missing {k}")
    nested = {
        "OverviewVM": [("projects", "ProjectTile"), ("accounts", "AccountVM")],
        "AccountVM": [("lanes", "LaneVM")],
        "ProjectVM": [("inbox", "ReviewVM"), ("lanes", "LaneVM")],
    }
    for key, sub in nested.get(name, []):
        for i, item in enumerate(vm.get(key) or []):
            errs += [f"{key}[{i}].{e}" for e in check_vm(sub, item)]
    if name == "ProjectVM":
        for col in BOARD_COLUMNS:
            if col not in (vm.get("board") or {}):
                errs.append(f"ProjectVM.board: missing {col}")
            for i, t in enumerate((vm.get("board") or {}).get(col) or []):
                errs += [f"board.{col}[{i}].{e}" for e in check_vm("TaskVM", t)]
    if name == "ReviewVM" and isinstance(vm.get("task"), dict):
        errs += [f"task.{e}" for e in check_vm("TaskVM", vm["task"])]
    return errs

# HTTP routes served by deckd (design §12). GET open on localhost; POST needs a token.
ROUTES = (
    ("GET", "/api/overview", "OverviewVM"),
    ("GET", "/api/projects/{p}", "ProjectVM"),
    ("GET", "/api/projects/{p}/tasks/{id}", "TaskDetailVM"),
    ("GET", "/api/projects/{p}/tasks/{id}/eligible-workers", "[EligibleVM]"),
    ("GET", "/api/accounts", "[AccountVM]"),
    ("GET", "/api/events?since=&project=&min=", "[EventVM] or SSE (event: deck, id: <event id>)"),
    ("POST", "/api/projects/{p}/tasks/{id}/actions",
     "{action, note?, worker?, priority?, decision?, option?, rev} -> ActionResult"),
    ("POST", "/api/projects/{p}/tasks/{id}/card", "{op, ...} -> {ok, card} | {ok:false, code, reason}"),
)
