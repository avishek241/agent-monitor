"""Capacity planner maths (design §6.2 steps 1–2). Pure functions of readings, running jobs, estimates and the clock.

    R5   = ceiling5 - reservePct - eff5            spendable 5-h % right now
    R7   = weeklyCeiling - pct7                    spendable 7-day %
    T5   = max(1, minutes until resets5)           unknown -> 300
    burn_j = est_j / D_j                           %/min
    committed5 = Σ burn_j × min(T5, max(0, D_j - elapsed_j))   only spend that lands before the reset
    committed7 = Σ est_j × k7
    admit ⇔ lanes < parallel.max ∧ machine ok ∧ committed5 + burn×min(T5, D) ≤ R5
            ∧ committed7 + est×k7 ≤ R7 (+5 for P0/P1) ∧ not cooling ∧ eff5 < ceiling
    waste = max(0, R5 - committed5) ; urgency = waste / max(T5, 15)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import quota

SIZES = ("S", "M", "L", "XL")
DEFAULT_K7 = 0.12
UNKNOWN_T5 = 300.0
P1_WEEKLY_GRACE = 5.0


@dataclass
class Job:
    est: float          # % of the 5-hour window
    minutes: float      # expected duration
    elapsed: float = 0  # minutes so far


@dataclass
class AccountInput:
    id: str
    eff5: Optional[float]          # None = unknown
    pct7: Optional[float] = None
    resets5: Optional[int] = None
    confidence: str = "high"
    cooling: bool = False
    cooling_until: Optional[int] = None
    parallel_max: int = 3
    reserve_pct: float = 5.0
    ceiling_pct: float = 92.0
    weekly_ceiling_pct: float = 95.0
    k7: float = DEFAULT_K7
    jobs: list = field(default_factory=list)       # [Job] running on this account
    dispatch: bool = True


@dataclass
class Machine:
    running_total: int = 0
    max_parallel_total: int = 6
    free_ram_gb: Optional[float] = None
    min_free_ram_gb: float = 2.0


@dataclass
class Capacity:
    account: str
    R5: float
    R7: float
    T5: float
    committed5: float
    committed7: float
    waste: float
    urgency: float
    lanes_used: int
    lanes_max: int
    unknown: bool
    cooling: bool
    hard_stop: Optional[str]       # reason nothing can be admitted, or None

    def as_vm(self, fits: Optional[str], spare: bool, why: Optional[str]) -> dict:
        return {"lanesUsed": self.lanes_used, "lanesMax": self.lanes_max, "spendable5": round(self.R5, 1),
                "committed5": round(self.committed5, 1), "minutesToReset": round(self.T5),
                "urgency": round(self.urgency, 3), "fitsSize": fits, "spare": spare, "why": why}


def account_capacity(a: AccountInput, now: int) -> Capacity:
    unknown = a.eff5 is None
    eff5 = quota.UNKNOWN_EFF5 if unknown else a.eff5
    R5 = a.ceiling_pct - a.reserve_pct - eff5
    R7 = a.weekly_ceiling_pct - (a.pct7 or 0.0)
    T5 = max(1.0, (a.resets5 - now) / 60.0) if (a.resets5 and not unknown) else UNKNOWN_T5
    committed5 = sum((j.est / j.minutes) * min(T5, max(0.0, j.minutes - j.elapsed)) for j in a.jobs)
    committed7 = sum(j.est * a.k7 for j in a.jobs)
    waste = max(0.0, R5 - committed5)
    urgency = waste / max(T5, 15.0)
    hard = None
    if not a.dispatch:
        hard = "dispatch off for this account (usage shown only)"
    elif a.cooling:
        hard = "limit — cooling" + (f" until {a.cooling_until}" if a.cooling_until else "")
    elif not unknown and eff5 >= a.ceiling_pct:
        hard = f"5-hour use {eff5:.0f}% ≥ ceiling {a.ceiling_pct:.0f}%"
    return Capacity(a.id, R5, R7, T5, committed5, committed7, waste, 0.0 if hard else urgency,
                    len(a.jobs), a.parallel_max, unknown, a.cooling, hard)


def admit(cap: Capacity, a: AccountInput, est: float, minutes: float, priority: str = "P2",
          machine: Optional[Machine] = None, ignore_lanes: bool = False) -> tuple:
    """(ok, why). `why` names the first failing condition in plain words."""
    if cap.hard_stop:
        return False, cap.hard_stop
    if not ignore_lanes:
        if cap.lanes_used >= cap.lanes_max:
            return False, f"all {cap.lanes_max} lanes busy"
        if cap.unknown and cap.lanes_used >= 1:
            return False, "usage unknown — at most 1 job"
    if machine is not None and not ignore_lanes:
        if machine.running_total >= machine.max_parallel_total:
            return False, f"machine cap {machine.max_parallel_total} jobs reached"
        if machine.free_ram_gb is not None and machine.free_ram_gb < machine.min_free_ram_gb:
            return False, f"free RAM {machine.free_ram_gb:.1f} GB < {machine.min_free_ram_gb} GB"
    burn = est / max(minutes, 1.0)
    need5 = burn * min(cap.T5, minutes)
    if cap.committed5 + need5 > cap.R5 + 1e-9:
        return False, f"5-hour quota: needs {need5:.1f}% before reset, {max(0.0, cap.R5 - cap.committed5):.1f}% free"
    grace = P1_WEEKLY_GRACE if priority in ("P0", "P1") else 0.0
    if cap.committed7 + est * a.k7 > cap.R7 + grace + 1e-9:
        return False, "weekly quota" + ("" if priority in ("P0", "P1") else " (only P0/P1 may use the 5-point grace)")
    return True, None


def fits_size(cap: Capacity, a: AccountInput, tool: str, history=None, account: Optional[str] = None) -> Optional[str]:
    """Largest size whose job fits before the reset (ignoring lanes): the backlog size-fit hint (§6.10)."""
    best = None
    for s in SIZES:
        est, mins = quota.estimate(s, tool, history, account=account)
        ok, _ = admit(cap, a, est, mins, ignore_lanes=True)
        if ok:
            best = s
    return best
