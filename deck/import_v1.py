"""v1 importer, observe mode (design §8 "v1 compatibility", §15 S0).

Reads v1 cards from the project's `legacy.progressDir/*.json`, normalises them (name/label → title, dict metrics →
list, missing etaMin → warning) and mirrors them read-only:
- `<stateDir>/legacy/<project>/<name>.json`: the normalised v2 card + its warnings + the v1 status;
- one `draft` task per card, tagged `legacy` (drafts are never dispatched), created once and never touched by v1 again.
Nothing in v1 is modified.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

STATUS = {"done": "done", "finished": "done", "complete": "done", "completed": "done", "failed": "failed",
          "error": "failed", "blocked": "blocked", "running": "running", "active": "running"}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "card"


def normalise(raw: dict, name: str) -> dict:
    """Use B1's cards.normalize_v1 when present; otherwise the same rules, minimal."""
    try:
        from .cards import normalize_v1          # B1
        card = normalize_v1(raw, name)
    except ImportError:
        warnings, steps = [], []
        for i, s in enumerate(raw.get("steps") or []):
            if not isinstance(s, dict):
                continue
            title = s.get("title")
            for alt in ("name", "label"):
                if not title and s.get(alt):
                    title = s[alt]
                    warnings.append(f"step key '{alt}' renamed to 'title'")
            if not isinstance(s.get("etaMin"), (int, float)):
                warnings.append(f"step '{title}' has no etaMin")
            steps.append({"id": _slug(str(title or i)), "title": str(title or f"Step {i + 1}")[:70],
                          "status": s.get("status", "pending"), "etaMin": s.get("etaMin")})
        m = raw.get("metrics") or []
        if isinstance(m, dict):
            warnings.append("metrics given as a dict; converted to a list")
            m = [{"label": k, "done": v} if not isinstance(v, dict) else {"label": k, **v} for k, v in m.items()]
        card = {"task": name, "steps": steps, "metrics": m, "warnings": warnings}
        if raw.get("note"):
            card["note"] = str(raw["note"])[:200]
    return card


def scan(progress_dir: str):
    for p in sorted(Path(os.path.expanduser(progress_dir)).glob("*.json")):
        try:
            raw = json.loads(p.read_text())
        except (OSError, ValueError):
            yield p.stem, None, ["unreadable JSON"]
            continue
        if isinstance(raw, dict):
            yield p.stem, raw, []


def import_project(pid, store, pcfg, state_dir, now=None) -> dict:
    progress = (pcfg.legacy or {}).get("progressDir")
    out = {"cards": 0, "created": 0, "warnings": 0}
    if not progress:
        return out
    mirror = Path(state_dir) / "legacy" / pid
    mirror.mkdir(parents=True, exist_ok=True)
    existing = {t["id"]: t for t in store.list()}
    for name, raw, errs in scan(progress):
        if raw is None:
            out["warnings"] += 1
            continue
        card = normalise(raw, name)
        status = STATUS.get(str(raw.get("status") or raw.get("state") or "").lower(), "unknown")
        doc = {"name": name, "v1Status": status, "card": card, "title": raw.get("title") or raw.get("name") or name}
        (mirror / f"{name}.json").write_text(json.dumps(doc, indent=1))
        out["cards"] += 1
        out["warnings"] += len(card.get("warnings") or [])
        tid = f"legacy-{_slug(name)}"
        if tid not in existing:
            title = str(doc["title"])
            title = (title if len(title) >= 4 else f"Legacy {title}")[:80].replace("`", "")
            try:
                store.create_task({"id": tid, "title": title, "summary": f"Imported from v1 card {name}"[:160],
                                   "tags": ["legacy"], "isolation": "none"}, by="W0")
                out["created"] += 1
            except Exception:  # noqa: BLE001 — a bad card never stops the import
                out["warnings"] += 1
    return out
