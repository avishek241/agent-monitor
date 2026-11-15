"""Card protocol v2 (design §8): validated ops merged into the stored card; never reset.

``apply(card, op, args, attempt, now)`` is pure and returns ``(new_card, patch)``; ``patch`` is
``None`` for an accepted no-op (duplicate ``step.add``). The store writes the card and appends the
patch to ``card_history``.
"""
from __future__ import annotations

import copy
import re
from typing import Optional

from . import schema
from .errors import CardRejected

OP_ARGS = {
    "step.add": ({"title", "eta"}, {"id"}),
    "step.start": ({"id"}, set()),
    "step.done": ({"id"}, set()),
    "step.fail": ({"id"}, set()),
    "step.skip": ({"id"}, set()),
    "step.eta": ({"id", "eta"}, set()),
    "metric": ({"label", "done"}, {"total", "kind"}),
    "note": ({"text"}, set()),
    "ask": ({"question"}, set()),
    "answer": (set(), {"text"}),
    "hb": (set(), {"turnEnd", "waiting", "sessionEnd"}),
}
STEP_STATUS = {"step.start": "active", "step.done": "done", "step.fail": "failed", "step.skip": "skipped"}


def empty(task: str) -> dict:
    return {"schema": "deck.card/2", "task": task, "steps": [], "metrics": [], "warnings": [], "rev": 0}


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (s or "step")[:32].strip("-") or "step"


def _num(v, what):
    if isinstance(v, bool):
        raise CardRejected(f"{what} must be a number")
    try:
        return float(v) if not isinstance(v, (int, float)) else v
    except (TypeError, ValueError):
        raise CardRejected(f"{what} must be a number, got {v!r}")


def _find_step(card, key):
    if key is None:
        raise CardRejected("step id or title required")
    for s in card["steps"]:
        if s["id"] == key or s["title"] == key:
            return s
    raise CardRejected(f"no step '{key}'")


def apply(card: dict, op: str, args: dict, attempt: Optional[int], now: int):
    if op not in OP_ARGS:
        raise CardRejected(f"unknown card op '{op}'")
    need, opt = OP_ARGS[op]
    args = {k: v for k, v in (args or {}).items() if v is not None or k in need}
    unknown = set(args) - need - opt
    if unknown:
        raise CardRejected(f"unknown option(s) for {op}: {', '.join(sorted(unknown))}")
    missing = [k for k in need if args.get(k) in (None, "")]
    if missing:
        raise CardRejected(f"{op} needs {', '.join('--' + m for m in sorted(missing))}")
    c = copy.deepcopy(card)
    patch: dict = {"op": op, "args": args}

    if op == "step.add":
        title = str(args["title"]).strip()
        if not 2 <= len(title) <= 70:
            raise CardRejected("step title must be 2-70 characters")
        eta = _num(args["eta"], "--eta")
        if not 0 < eta <= 2880:
            raise CardRejected("--eta must be > 0 and ≤ 2880 minutes")
        if any(s["title"] == title for s in c["steps"]):
            return card, None                       # duplicate title → no-op
        sid = args.get("id") or slug(title)
        if not re.fullmatch(r"[a-z0-9-]{1,32}", sid):
            raise CardRejected("step id must match [a-z0-9-]{1,32}")
        base, k = sid, 2
        while any(s["id"] == sid for s in c["steps"]):
            sid = f"{base[:29]}-{k}"
            k += 1
        if len(c["steps"]) >= 20:
            raise CardRejected("a card holds at most 20 steps")
        st = {"id": sid, "title": title, "status": "pending", "etaMin": eta, "startedAt": None, "endedAt": None}
        if attempt:
            st["attempt"] = attempt
        c["steps"].append(st)
        patch["step"] = sid
    elif op in STEP_STATUS:
        st = _find_step(c, args["id"])
        st["status"] = STEP_STATUS[op]
        if op == "step.start":
            st["startedAt"] = now
            st["endedAt"] = None
        else:
            st["endedAt"] = now
        patch["step"] = st["id"]
    elif op == "step.eta":
        st = _find_step(c, args["id"])
        eta = _num(args["eta"], "eta")
        if not 0 < eta <= 2880:
            raise CardRejected("eta must be > 0 and ≤ 2880 minutes")
        st["etaMin"] = eta
        patch["step"] = st["id"]
    elif op == "metric":
        label = str(args["label"]).strip()
        if not 2 <= len(label) <= 40:
            raise CardRejected("metric label must be 2-40 characters")
        done = _num(args["done"], "count")
        if done < 0:
            raise CardRejected("metric count must be ≥ 0")
        total = args.get("total")
        if total is not None:
            total = _num(total, "--total")
            if total <= 0:
                raise CardRejected("--total must be > 0")
        kind = args.get("kind") or None
        if kind is not None and kind not in ("count", "bad", "pass"):
            raise CardRejected("--kind must be count, bad or pass")
        m = next((x for x in c["metrics"] if x["label"] == label), None)
        if m is None:
            if len(c["metrics"]) >= 8:
                raise CardRejected("a card holds at most 8 metrics")
            m = {"label": label, "kind": kind or "count"}
            c["metrics"].append(m)
        m["done"] = done
        if total is not None:
            m["total"] = total
        m.setdefault("total", None)
        if kind:
            m["kind"] = kind
        m["updatedAt"] = now
    elif op == "note":
        text = str(args["text"]).strip()
        if len(text) > 200:
            raise CardRejected("note must be ≤ 200 characters")
        c["note"] = text
    elif op == "ask":
        q = str(args["question"]).strip()
        if len(q) > 500:
            raise CardRejected("question must be ≤ 500 characters")
        c["question"] = q
    elif op == "answer":
        c["question"] = None
    elif op == "hb":
        return card, None
    if attempt:
        c["attempt"] = attempt
    c["updatedAt"] = now
    c["rev"] = int(c.get("rev") or 0) + 1
    errs = schema.validate(c, "card")
    if errs:
        raise CardRejected("card invalid: " + "; ".join(errs[:3]))
    return c, patch


def normalize_v1(raw: dict, task: str) -> dict:
    """v1 card (progressDir/*.json) → v2 card, recording every normalisation in ``warnings``."""
    warnings = []
    c = empty(task)
    for i, s in enumerate(raw.get("steps") or []):
        if not isinstance(s, dict):
            warnings.append(f"step {i + 1} is not an object; skipped")
            continue
        title = s.get("title")
        for alt in ("name", "label"):
            if not title and s.get(alt):
                title = s[alt]
                warnings.append(f"step key '{alt}' renamed to 'title'")
        title = str(title or f"Step {i + 1}")[:70]
        if len(title) < 2:
            title = f"Step {i + 1}"
        eta = s.get("etaMin")
        if not isinstance(eta, (int, float)) or isinstance(eta, bool) or eta <= 0:
            warnings.append(f"step '{title}' has no etaMin")
            eta = None
        status = s.get("status", "pending")
        status = {"in-progress": "active", "running": "active", "complete": "done", "completed": "done"}.get(status, status)
        if status not in ("pending", "active", "done", "skipped", "failed"):
            warnings.append(f"step '{title}' status '{status}' unknown; shown as pending")
            status = "pending"
        sid, k = slug(title), 2
        while any(x["id"] == sid for x in c["steps"]):
            sid, k = f"{slug(title)[:29]}-{k}", k + 1
        c["steps"].append({"id": sid, "title": title, "status": status, "etaMin": eta,
                           "startedAt": s.get("startedAt"), "endedAt": None})
    metrics = raw.get("metrics") or []
    if isinstance(metrics, dict):
        warnings.append("metrics was an object; converted to a list")
        metrics = [{"label": k, **(v if isinstance(v, dict) else {"done": v})} for k, v in metrics.items()]
    for m in metrics:
        if not isinstance(m, dict):
            continue
        label = m.get("label") or m.get("name") or m.get("title")
        if not m.get("label") and label:
            warnings.append("metric key 'name' renamed to 'label'")
        try:
            done = float(m.get("done", m.get("value", 0)) or 0)
        except (TypeError, ValueError):
            done = 0
            warnings.append(f"metric '{label}' count not a number")
        total = m.get("total")
        c["metrics"].append({"label": str(label or "metric")[:40], "done": done,
                             "total": float(total) if isinstance(total, (int, float)) and total > 0 else None,
                             "kind": m.get("kind") if m.get("kind") in ("count", "bad", "pass") else "count"})
    if raw.get("note"):
        c["note"] = str(raw["note"])[:200]
    c["warnings"] = sorted(set(warnings), key=warnings.index)
    return c
