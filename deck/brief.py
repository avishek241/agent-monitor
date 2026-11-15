"""Briefs: template, parser and validator V1–V13 (design §4)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from . import clock, policy, states
from .domains import kind_of
from .errors import NotFound, ValidationError

TEMPLATE = Path(__file__).parent / "brief_template.md"
REQUIRED_SECTIONS = ("goal", "rules", "done when", "stop point")
KNOWN_SECTIONS = ("goal", "context", "rules", "steps", "done when", "stop point", "not allowed", "needs from owner")
VAGUE = {"works", "it works", "done", "all done", "finished", "complete", "completed", "everything works", "ok",
         "all good", "looks good", "good", "fixed", "tests", "tested"}
SECRET_RES = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "API key (sk-…)"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[^\s'\"`]{6,}"), "credential assignment"),
    (re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{48,}={0,2}(?![A-Za-z0-9+/])"), "long base64 string"),
]
MAX_BYTES = 25 * 1024


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def render(task: dict) -> str:
    deps = ", ".join(task.get("deps") or []) or "none"
    doms = ", ".join(task.get("domains") or []) or "none"
    header = (f"{task['id']} · {task.get('priority')} · {task.get('size')} · {task.get('capability')} · deps {deps} · "
              f"domains {doms}")
    return TEMPLATE.read_text().format(header=header, title=task["title"], summary=task.get("summary", ""))


def sections(text: str) -> dict:
    """{'goal': body, 'done when': body, ...}; heading match is case-insensitive, hints in () ignored."""
    out: dict = {}
    cur = None
    buf: list = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            name = re.sub(r"\(.*?\)", "", m.group(1)).strip().lower()
            cur = next((k for k in KNOWN_SECTIONS if name == k or name.startswith(k)), name)
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    for k in list(out):
        out[k] = re.sub(r"<!--.*?-->", "", out[k], flags=re.S).strip()
    return out


def checklist(body: str) -> list:
    return [m.group(1).strip() for m in re.finditer(r"^\s*[-*]\s*\[[ xX]\]\s*(.*)$", body or "", re.M)]


def backtick_paths(text: str) -> list:
    out = []
    for m in re.finditer(r"`([^`\s]+)`", text):
        s = m.group(1)
        if "/" in s and not s.startswith(("http:", "https:", "-")) and not re.search(r"[(){}$=<>|;]", s):
            out.append(s.rstrip(".,:"))
    return out


def derive_areas(text: str) -> list:
    areas = []
    for p in backtick_paths(text):
        p = p.lstrip("./")
        if p.startswith("/") or ".." in p.split("/"):
            continue
        segs = [s for s in p.split("/") if s]
        if not segs:
            continue
        top = segs[:2] if len(segs) > 2 or (len(segs) == 2 and "." not in segs[-1]) else segs[:1]
        a = "area:" + "/".join(top) + "/**"
        if a not in areas:
            areas.append(a)
    return areas


def brief_path(store, task) -> Path:
    p = Path(task["brief"]["path"])
    if p.is_absolute():
        return p
    repo = store.pcfg.repo if store.pcfg else ""
    return Path(repo) / p


def validate(store, task: dict) -> dict:
    """Run V1–V13. Returns {errors:[(rule,msg)], warnings:[(rule,msg)], text, sha256, doneWhen, stopPoint,
    domains (possibly derived)}."""
    errors: list = []
    warns: list = []
    res = {"errors": errors, "warnings": warns, "text": None, "sha256": None, "doneWhen": [], "stopPoint": None,
           "domains": list(task.get("domains") or [])}
    pcfg = store.pcfg
    repo = os.path.realpath(pcfg.repo) if pcfg and pcfg.repo else None

    # V4 title/summary
    title, summary = task.get("title", ""), task.get("summary", "")
    if not 4 <= len(title) <= 80:
        errors.append(("V4", "title must be 4-80 characters"))
    if not 8 <= len(summary) <= 160:
        errors.append(("V4", "summary must be 8-160 characters"))
    if "`" in title or re.search(r"\S/\S", title) or re.search(r"\.(py|ts|js|md|json|sql)\b", title):
        errors.append(("V4", "title must be plain words (no paths or backticks)"))

    # V1 brief file
    p = brief_path(store, task)
    rp = os.path.realpath(p)
    text = None
    if task.get("isolation") == "none" and Path(task["brief"]["path"]).is_absolute():
        pass
    elif repo is None or not (rp == repo or rp.startswith(repo + os.sep)):
        errors.append(("V1", f"brief {task['brief']['path']} is not inside the project repo"))
    if not p.is_file():
        errors.append(("V1", f"brief file not found: {task['brief']['path']}"))
    else:
        text = p.read_text(errors="replace")
        res["text"] = text
        res["sha256"] = sha256(text)

    if text is not None:
        sec = sections(text)
        # V2 sections
        for name in REQUIRED_SECTIONS:
            if not sec.get(name):
                errors.append(("V2", f"section '{name.capitalize()}' is missing or empty"))
        # V3 done-when
        items = checklist(sec.get("done when", ""))
        items = [i for i in items if i]
        if sec.get("done when") is not None and not items:
            errors.append(("V3", "'Done when' needs at least one '- [ ]' item"))
        for it in items:
            if len(it) > 200:
                errors.append(("V3", f"done-when item longer than 200 chars: {it[:40]}…"))
            if re.sub(r"[^a-z ]", "", it.lower()).strip() in VAGUE:
                errors.append(("V3", f"done-when item is too vague: '{it}'"))
        res["doneWhen"] = items
        res["stopPoint"] = sec.get("stop point") or None
        # V9 secrets
        for rx, what in SECRET_RES:
            if rx.search(text):
                errors.append(("V9", f"brief looks like it contains a secret ({what})"))
        # V10 backtick paths
        for bp in backtick_paths(text):
            if any(ch in bp for ch in "*?["):
                continue
            base = Path(bp) if Path(bp).is_absolute() else Path(repo or ".") / bp
            if not base.exists():
                warns.append(("V10", f"path `{bp}` does not exist in the repo"))
        # V11 size and restated rules
        if len(text.encode()) > MAX_BYTES:
            warns.append(("V11", "brief is over 25 KB; link to files instead of pasting them"))
        rules_body = sec.get("rules", "")
        if pcfg and rules_body:
            restated = 0
            for rf in pcfg.rulesFiles or []:
                rpth = Path(repo or ".") / rf
                if rpth.is_file():
                    rtext = rpth.read_text(errors="replace")
                    restated += sum(1 for ln in rules_body.splitlines() if len(ln.strip()) > 30 and ln.strip() in rtext)
            if restated >= 2:
                warns.append(("V11", "the Rules section restates the project rules files; link to them instead"))

    # V5 deps
    for d in task.get("deps") or []:
        try:
            store.get(d)
        except NotFound:
            errors.append(("V5", f"dependency {d} does not exist in project {store.pid}"))
    graph = {t["id"]: list(t.get("deps") or []) for t in store.list()}
    graph[task["id"]] = list(task.get("deps") or [])
    if _cycle(graph, task["id"]):
        errors.append(("V5", "dependency graph has a cycle"))

    # V6 domains
    declared = (pcfg.domains if pcfg else {}) or {}
    for name in res["domains"]:
        k = kind_of(name)
        if name == "merge":
            errors.append(("V6", "'merge' is reserved for the merge queue"))
        elif k in ("seq", "ext", "res") and name not in declared:
            errors.append(("V6", f"domain {name} is not declared in the project config"))
        elif k == "area":
            g = name[5:]
            if g.startswith("/") or ".." in g.split("/"):
                errors.append(("V6", f"area glob must be relative to the repo: {name}"))
        elif k not in ("seq", "ext", "res", "area"):
            errors.append(("V6", f"unknown domain kind: {name}"))

    # V13 areas
    if task.get("capability") in ("code", "ui", "infra") and not any(kind_of(d) == "area" for d in res["domains"]):
        derived = derive_areas(text or "")
        if derived:
            res["domains"] = res["domains"] + derived
            warns.append(("V13", f"areas derived from the brief: {', '.join(derived)} (confirm or edit)"))
        else:
            res["domains"] = res["domains"] + ["area:**"]
            warns.append(("V13", "declare areas to allow parallel work (using area:**)"))

    # V7 eligible worker
    if not any_eligible(store, task):
        errors.append(("V7", "no configured worker can run it (capability, workerClass, account policy, dispatch)"))

    # V8 pilot
    if task.get("kind") == "scale":
        if not task.get("pilotOf"):
            errors.append(("V8", "kind 'scale' needs pilotOf"))
        else:
            try:
                store.get(task["pilotOf"])
            except NotFound:
                errors.append(("V8", f"pilot {task['pilotOf']} does not exist"))

    # V12 size
    if task.get("size") == "XL":
        warns.append(("V12", "size XL: consider a pilot plus phases"))
    return res


def any_eligible(store, task) -> bool:
    wc = task.get("workerClass") or ["claude-headless", "codex-exec"]
    for w in store.cfg.workers.values():
        acct = store.cfg.account(w.account)
        if acct is None or not acct.dispatch:
            continue
        if w.adapter not in wc:
            continue
        if w.capabilities and task.get("capability") not in w.capabilities:
            continue
        if task.get("pinWorker") not in (None, w.id):
            continue
        if policy.allow(acct, store.pcfg)[0]:
            return True
    return False


def _cycle(graph, start) -> bool:
    seen, stack = set(), set()

    def visit(n):
        if n in stack:
            return True
        if n in seen:
            return False
        seen.add(n)
        stack.add(n)
        for d in graph.get(n, []):
            if visit(d):
                return True
        stack.discard(n)
        return False
    return visit(start)


def fmt(issues) -> str:
    return "; ".join(f"{r}: {m}" for r, m in issues)


def ready(store, task_id: str, actor: str = "W0") -> dict:
    """Validate and move draft → ready (T1), or record T1x and raise ValidationError."""
    t = store.get(task_id)
    if t["state"] != "draft":
        from .errors import IllegalTransition
        raise IllegalTransition(f"{t['id']} is {t['state']}, not draft")
    res = validate(store, t)
    if res["errors"]:
        store.transition(t["id"], "draft", actor=actor, reason=fmt(res["errors"])[:300],
                         errors=[f"{r}: {m}" for r, m in res["errors"]])
        raise ValidationError(fmt(res["errors"]), errors=res["errors"], warnings=res["warnings"])
    snap = Path(store.state_dir) / "runs" / store.pid / t["id"] / "brief.md"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(res["text"])
    brief = {"sha256": res["sha256"], "snapshot": str(snap), "validatedAt": clock.now()}
    out = store.transition(t["id"], "ready", actor=actor, reason="validated", brief=brief,
                           doneWhen=res["doneWhen"], stopPoint=(res["stopPoint"] or "")[:2000],
                           domains=res["domains"], warnings=[f"{r}: {m}" for r, m in res["warnings"]])
    out["_warnings"] = res["warnings"]
    return out


def check_changed(store, task_id: str, actor: str = "SUP") -> Optional[str]:
    """If the brief changed after validation: ready/queued → draft (T2); running → warning only."""
    t = store.get(task_id)
    sha = (t.get("brief") or {}).get("sha256")
    if not sha:
        return None
    p = brief_path(store, t)
    cur = sha256(p.read_text(errors="replace")) if p.is_file() else None
    if cur == sha:
        return None
    if t["state"] in ("ready", "queued"):
        store.transition(t["id"], "draft", actor=actor, reason="brief edited after validation")
        return "draft"
    if t["state"] in states.ACTIVE_ATTEMPT:
        store.events.emit("task.invalid", "warn", f"{t['id']} brief edited while an attempt runs; the worker keeps the snapshot",
                          task=t["id"])
        return "warned"
    return None


def create_brief_file(store, task) -> Path:
    p = brief_path(store, task)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render(task))
    return p
