"""Frozen store API (design §17, B1). B2 and B3 code against these signatures.

This module defines the interface as abstract classes whose methods raise ``NotImplementedError``.
``deck.store`` provides the SQLite implementation; ``open_project`` / ``open_global`` here forward to it.

Conventions
-----------
* All times are unix seconds (int). ``now`` defaults to ``deck.clock.now()``.
* Tasks are returned as dicts shaped like ``task.schema.json`` (camelCase), with ``attempts`` filled.
* Attempts are returned as dicts shaped like ``task.schema.json#/$defs/attempt`` plus ``task``,
  ``slot`` and, ONLY on the dict returned by ``lease``/``claim``, the one-time plain ``token``
  (the store keeps a sha256 of it; adapters put it in ``DECK_TOKEN``).
* ``update_attempt`` takes snake_case column names of the ``attempts`` table (§3.2), e.g.
  ``session_id``, ``pid``, ``pgid``, ``pid_start``, ``run_dir``, ``lease_until``, ``last_heartbeat``,
  ``cpu_secs``, ``out_offset``, ``usage_start``, ``usage_end``, ``tokens``.
* Errors: ``deck.errors`` — IllegalTransition, StaleRev, PolicyDenied, CardRejected, NotFound,
  ValidationError, DomainBusy.
* Actors (``actor=``): 'W0', 'owner', a worker id 'W2' (= WRK), or one of the system actors
  'DSP' (dispatcher), 'SUP' (supervisor), 'ADP' (adapter), 'MQ' (merge queue), 'TMR' (timer).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


class DomainsAPI:
    """Conflict domains (§6.2.1). Same API for project domains and machine-wide ``ext:`` domains."""

    def try_acquire(self, names: Iterable[str], holder: str, ttl: Optional[int] = None,
                    now: Optional[int] = None) -> tuple[bool, Optional[dict]]:
        """All-or-nothing, in sorted order. Returns (True, None) or (False, blocker) where blocker =
        {name, holder, reason}. A failed try records a wait; after 30 min the waiter reserves."""
        raise NotImplementedError

    def release_all(self, holder: str) -> int:
        raise NotImplementedError

    def renew(self, holder: str, ttl: Optional[int] = None, now: Optional[int] = None) -> int:
        raise NotImplementedError

    @staticmethod
    def overlaps(glob_a: str, glob_b: str) -> bool:
        raise NotImplementedError

    def held(self) -> list[dict]:
        """[{name, holder, since, expires}] plus waiters — used by views."""
        raise NotImplementedError


class SlotsAPI:
    def take(self, holder: str) -> Optional[int]:
        raise NotImplementedError

    def release(self, holder: str) -> None:
        raise NotImplementedError


class EventsAPI:
    def emit(self, type: str, severity: str, text: str, task: Optional[str] = None,
             attempt: Optional[int] = None, worker: Optional[str] = None, for_: Iterable[str] = (),
             data: Optional[dict] = None) -> dict:
        """Append an event (event.schema.json) to the project log, the global mirror, and
        ``<stateDir>/inbox/<W>.jsonl`` for each ``for_`` entry. Returns the event dict."""
        raise NotImplementedError

    def since(self, since_id: int = 0, limit: int = 500, min_severity: Optional[str] = None,
              task: Optional[str] = None) -> list[dict]:
        raise NotImplementedError


class ProjectStoreAPI:
    pid: str
    domains: DomainsAPI
    slots: SlotsAPI
    events: EventsAPI

    def create_task(self, fields: dict, by: str) -> dict:
        """Allocate the next number (never reused), create a ``draft`` task. ``fields`` follow
        task.schema.json; ``id`` may be a slug ('supplier-import') or omitted (slug from title)."""
        raise NotImplementedError

    def get(self, id: str) -> dict:
        raise NotImplementedError

    def list(self, states: Optional[Iterable[str]] = None) -> list[dict]:
        raise NotImplementedError

    def edit(self, id: str, fields: dict, *, by: str, rev: Optional[int] = None) -> dict:
        """Field edits (priority, deps, domains, pinWorker, tags, ...). pinWorker checks policy."""
        raise NotImplementedError

    def transition(self, id: str, to: str, *, actor: str, reason: str = "", rev: Optional[int] = None,
                   **effects: Any) -> dict:
        """The single choke point for state changes (§5.2). Raises IllegalTransition, StaleRev,
        PolicyDenied. ``effects`` may carry: t='T16' (pick a row), note, worker, resets_at,
        failover, killed, cpu_delta, handoff, brief/doneWhen/stopPoint (T1), attempts_plus (T28)."""
        raise NotImplementedError

    def lease(self, id: str, worker: str, lane: int, account: str, *, slot: Optional[int],
              domains: Iterable[str], now: Optional[int] = None) -> dict:
        """T5 inside BEGIN IMMEDIATE: re-checks queued, lane free, **policy**, capability /
        workerClass / pin, attempts < max; inserts attempt n+1. Raises PolicyDenied (and emits
        ``policy.denied`` alert) or IllegalTransition."""
        raise NotImplementedError

    def claim(self, id: str, account_home: str, worker: str) -> dict:
        """Interactive claim (T5+T6 or T6 on an offered task). Account from the home path; policy."""
        raise NotImplementedError

    def update_attempt(self, task: str, n: int, **fields: Any) -> None:
        raise NotImplementedError

    def end_attempt(self, task: str, n: int, outcome: str, exit_code: Optional[int] = None) -> None:
        raise NotImplementedError

    def card_op(self, task: str, attempt: Optional[int], op: str, args: dict, token: Optional[str]) -> dict:
        """ops: step.add{title, eta, id?} step.start|step.done|step.fail|step.skip{id} step.eta{id, eta}
        metric{label, done, total?, kind?} note{text} ask{question} hb{}. Raises CardRejected."""
        raise NotImplementedError

    def put_handoff(self, task: str, n: int, data: dict, valid: Optional[bool] = None) -> dict:
        raise NotImplementedError

    def review(self, task: str, decision: str, by: str, note: str = "", rev: Optional[int] = None) -> dict:
        """decision: approve (T21/T22) | changes (T23) | reject (T29)."""
        raise NotImplementedError


class UsageAPI:
    def put(self, reading) -> None:
        """reading: dict or dataclass with account, read_at, source, pct5, pct7, resets5, resets7, confidence."""
        raise NotImplementedError

    def latest(self, account: str) -> Optional[dict]:
        raise NotImplementedError


class LimitsAPI:
    def add(self, account: str, kind: str, detected_at: int, resets_at: Optional[int], source: str,
            project: Optional[str] = None, task: Optional[str] = None, attempt: Optional[int] = None,
            raw: Optional[str] = None) -> int:
        raise NotImplementedError

    def active(self, account: str, now: Optional[int] = None) -> list[dict]:
        raise NotImplementedError

    def clear(self, limit_id: int, at: Optional[int] = None) -> None:
        raise NotImplementedError


class LanesAPI:
    def take(self, worker: str, project: Optional[str] = None, task: Optional[str] = None,
             attempt: Optional[int] = None) -> Optional[int]:
        """Lowest free lane of the worker, honouring worker.lanes and the account's parallel.max."""
        raise NotImplementedError

    def release(self, worker: str, lane: int) -> None:
        raise NotImplementedError


class AccountStateAPI:
    def put(self, account: str, state: str, capacity: dict) -> None:
        raise NotImplementedError

    def get(self, account: str) -> Optional[dict]:
        raise NotImplementedError


class GlobalStoreAPI:
    usage: UsageAPI
    limits: LimitsAPI
    lanes: LanesAPI
    account_state: AccountStateAPI
    ext_domains: DomainsAPI


def open_project(pid: str, cfg=None) -> ProjectStoreAPI:
    from . import store
    return store.open_project(pid, cfg)


def open_global(cfg=None) -> GlobalStoreAPI:
    from . import store
    return store.open_global(cfg)
