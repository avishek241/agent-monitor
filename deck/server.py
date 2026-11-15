"""deckd HTTP API (design §12, §14): view models, actions, card ops, events (JSON or SSE), PWA files.

Auth rules
* GET is open from localhost. Any request from a non-loopback address (Tailscale) needs
  ``Authorization: Bearer <device token>`` (tokens from ``deck pair``, stored hashed).
* POST always needs a token: the local token (``<stateDir>/token``), a device token, or — for card
  ops only — the attempt token (``DECK_TOKEN``).
* ``Host`` must be localhost/127.0.0.1/::1 or the address the server is bound to. CORS is off.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import clock
from .config import Config
from .errors import DeckError, NotFound, ValidationError
from .store import GlobalStore
from .views import Views

WEB = Path(__file__).resolve().parent.parent / "web"
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------- tokens
def local_token(state_dir: Path, create: bool = True) -> Optional[str]:
    p = Path(state_dir) / "token"
    if p.exists():
        return p.read_text().strip()
    if not create:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    return tok


def _devices_path(state_dir) -> Path:
    return Path(state_dir) / "devices.json"


def load_devices(state_dir) -> list:
    try:
        return json.loads(_devices_path(state_dir).read_text()).get("devices", [])
    except (OSError, ValueError):
        return []


def start_pairing(state_dir, ttl: int = 300) -> str:
    """Create a 6-digit pairing code (shown by ``deck pair``); stored hashed, valid ``ttl`` seconds."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    p = Path(state_dir) / "pair.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hash": _h(code), "expires": clock.now() + ttl}))
    os.chmod(p, 0o600)
    return code


def complete_pairing(state_dir, code: str, name: str) -> Optional[str]:
    p = Path(state_dir) / "pair.json"
    try:
        pend = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if clock.now() > pend.get("expires", 0) or not secrets.compare_digest(_h(str(code)), pend.get("hash", "")):
        return None
    p.unlink(missing_ok=True)
    tok = secrets.token_urlsafe(32)
    devs = load_devices(state_dir)
    devs.append({"name": str(name)[:40] or "device", "hash": _h(tok), "pairedAt": clock.now()})
    dp = _devices_path(state_dir)
    dp.write_text(json.dumps({"devices": devs}, indent=1))
    os.chmod(dp, 0o600)
    return tok


# ---------------------------------------------------------------- app
class DeckApp:
    """Request logic, independent of the socket layer (one lock: SQLite connections are shared)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.g = GlobalStore(cfg)
        self.views = Views(cfg, self.g)
        self.lock = threading.RLock()
        self.token = local_token(cfg.state_dir)

    def token_kind(self, bearer: Optional[str]) -> Optional[str]:
        if not bearer:
            return None
        if self.token and secrets.compare_digest(bearer, self.token):
            return "local"
        hb = _h(bearer)
        if any(secrets.compare_digest(hb, d.get("hash", "")) for d in load_devices(self.cfg.state_dir)):
            return "device"
        return None

    def action(self, pid, tid, body: dict) -> dict:
        s = self.views.store(pid)
        act = body.get("action")
        rev = body.get("rev")
        actor = body.get("_actor", "owner")
        note = body.get("note")
        if act == "approve":
            t = s.review(tid, "approve", by=actor, note=note or "", rev=rev)
        elif act == "changes":
            t = s.review(tid, "changes", by=actor, note=note or "", rev=rev)
        elif act == "reject":
            t = s.review(tid, "reject", by=actor, note=note or "", rev=rev)
        elif act == "pause":
            t = s.transition(tid, "blocked", actor=actor, reason=f"paused by {actor}", rev=rev, t="T20")
        elif act == "resume":
            t = s.transition(tid, "queued", actor=actor, reason=f"resumed by {actor}", rev=rev)
        elif act == "cancel":
            t = s.transition(tid, "cancelled", actor=actor, reason=f"cancelled by {actor}", rev=rev, note=note)
        elif act == "retry":
            t = s.transition(tid, "queued", actor=actor, reason=f"retry by {actor}", rev=rev)
        elif act == "answer":
            t = s.transition(tid, "queued", actor=actor, reason=f"answered by {actor}", rev=rev,
                             answer=note, decision=body.get("decision"), option=body.get("option"))
        elif act == "priority":
            if body.get("priority") not in ("P1", "P2", "P3"):
                raise ValidationError("priority must be P1, P2 or P3")
            t = s.edit(tid, {"priority": body["priority"]}, by=actor, rev=rev)
        elif act == "reassign":
            w = body.get("worker")
            if not w:
                raise ValidationError("worker is required")
            cur = s.get(tid)
            if cur["state"] == "running":
                t = s.transition(tid, "queued", actor=actor, reason=f"reassigned to {w}", rev=rev, worker=w, t="T30")
            else:
                t = s.edit(tid, {"pinWorker": w}, by=actor, rev=rev)
        else:
            raise ValidationError(f"unknown action {act!r}")
        return {"ok": True, "task": t}

    def card(self, pid, tid, body: dict, bearer: Optional[str]) -> dict:
        s = self.views.store(pid)
        op = body.get("op")
        args = {k: v for k, v in body.items() if k not in ("op", "attempt")}
        kind = self.token_kind(bearer)
        # local/device tokens may write cards as W0/owner only when the attempt has no token
        card = s.card_op(tid, body.get("attempt"), op, args, None if kind else bearer)
        return {"ok": True, "card": card}


def _err(e: DeckError) -> tuple:
    status = {"policy_denied": 403, "illegal": 409, "stale": 409, "not_found": 404, "domain_busy": 409}.get(e.code, 400)
    code = "invalid" if e.code == "card_rejected" else e.code
    return status, {"ok": False, "code": code, "reason": e.reason}


def make_handler(app: DeckApp, bound_host: str):
    allowed_hosts = {"localhost", "127.0.0.1", "::1", "[::1]", bound_host}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "deckd"

        def log_message(self, *a):
            pass

        # ---- helpers
        def _json(self, status, obj):
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _bearer(self):
            h = self.headers.get("Authorization", "")
            return h[7:].strip() if h.lower().startswith("bearer ") else None

        def _local(self):
            return self.client_address[0] in LOOPBACK

        def _gate(self, write: bool) -> Optional[str]:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0] if not (self.headers.get("Host") or "").startswith("[") \
                else (self.headers.get("Host") or "").split("]")[0] + "]"
            if host not in allowed_hosts:
                self._json(403, {"ok": False, "code": "forbidden", "reason": "host not allowed"})
                return "deny"
            kind = app.token_kind(self._bearer())
            if not self._local() and kind != "device":
                self._json(401, {"ok": False, "code": "unauthorized", "reason": "device token required"})
                return "deny"
            return kind

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ValidationError("body too large")
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                raise ValidationError("body is not JSON")
            if not isinstance(body, dict):
                raise ValidationError("body must be an object")
            return body

        # ---- GET
        def do_GET(self):
            if self._gate(False) == "deny":
                return
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            try:
                if parts[:1] != ["api"]:
                    return self._static(u.path)
                if parts == ["api", "events"]:
                    return self._events(q)
                with app.lock:
                    if parts == ["api", "overview"]:
                        return self._json(200, app.views.overview())
                    if parts == ["api", "accounts"]:
                        return self._json(200, app.views.accounts())
                    if len(parts) == 3 and parts[1] == "projects":
                        return self._json(200, app.views.project(parts[2]))
                    if len(parts) == 5 and parts[1] == "projects" and parts[3] == "tasks":
                        return self._json(200, app.views.task(parts[2], parts[4]))
                    if len(parts) == 6 and parts[1] == "projects" and parts[3] == "tasks" and parts[5] == "eligible-workers":
                        return self._json(200, app.views.eligible(parts[2], parts[4]))
                self._json(404, {"ok": False, "code": "not_found", "reason": "no such endpoint"})
            except DeckError as e:
                self._json(*_err(e))

        def _events(self, q):
            since = int(self.headers.get("Last-Event-ID") or q.get("since") or 0)
            project, mn = q.get("project"), q.get("min")
            if "text/event-stream" not in (self.headers.get("Accept") or ""):
                with app.lock:
                    return self._json(200, app.g.events_since(since, project, mn, limit=int(q.get("limit", 500))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            deadline = time.time() + float(q["timeout"]) if q.get("timeout") else None
            last_ping = time.time()
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while deadline is None or time.time() < deadline:
                    with app.lock:
                        evs = app.g.events_since(since, project, mn, limit=200)
                    for ev in evs:
                        self.wfile.write(f"id: {ev['id']}\nevent: deck\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        since = ev["id"]
                    if evs:
                        self.wfile.flush()
                    if time.time() - last_ping > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _static(self, path):
            rel = path.lstrip("/") or "index.html"
            f = (WEB / rel).resolve()
            if not str(f).startswith(str(WEB.resolve())) or not f.is_file():
                if rel == "index.html":
                    data = b"<!doctype html><title>Agent Deck</title><p>Agent Deck API is running. The PWA (web/) is not installed.</p>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                return self._json(404, {"ok": False, "code": "not_found", "reason": "no such file"})
            data = f.read_bytes()
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if f.suffix == ".webmanifest" or f.name == "manifest.json":
                ctype = "application/manifest+json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ---- POST
        def do_POST(self):
            kind = self._gate(True)
            if kind == "deny":
                return
            u = urlparse(self.path)
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            try:
                body = self._body()
                if parts == ["api", "pair"]:
                    tok = complete_pairing(app.cfg.state_dir, body.get("code", ""), body.get("name", "phone"))
                    if not tok:
                        return self._json(403, {"ok": False, "code": "forbidden", "reason": "pairing code wrong or expired"})
                    return self._json(200, {"ok": True, "token": tok})
                is_card = len(parts) == 6 and parts[1] == "projects" and parts[3] == "tasks" and parts[5] == "card"
                if kind is None and not (is_card and self._bearer()):
                    return self._json(401, {"ok": False, "code": "unauthorized", "reason": "token required for writes"})
                with app.lock:
                    if len(parts) == 6 and parts[1] == "projects" and parts[3] == "tasks" and parts[5] == "actions":
                        body["_actor"] = "owner"
                        return self._json(200, app.action(parts[2], parts[4], body))
                    if is_card:
                        return self._json(200, app.card(parts[2], parts[4], body, self._bearer()))
                self._json(404, {"ok": False, "code": "not_found", "reason": "no such endpoint"})
            except DeckError as e:
                self._json(*_err(e))

    return H


def make_server(cfg: Config, host: Optional[str] = None, port: Optional[int] = None) -> ThreadingHTTPServer:
    host = host or cfg.listen.get("host", "127.0.0.1")
    port = cfg.listen.get("port", 7788) if port is None else port
    app = DeckApp(cfg)
    srv = ThreadingHTTPServer((host, port), make_handler(app, host))
    srv.daemon_threads = True
    srv.app = app
    return srv


def serve(cfg: Optional[Config] = None, host=None, port=None):
    cfg = cfg or Config.load()
    srv = make_server(cfg, host, port)
    print(f"deckd API on http://{srv.server_address[0]}:{srv.server_address[1]}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
