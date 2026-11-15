// Agent Deck HTTP client (design §12). The only module that talks to deckd.
// Fixture mode (?fixtures=1) reads web/fixtures/*.json and simulates POSTs, so the UI runs with python3 -m http.server.

export const ACTIONS = ["approve", "changes", "reject", "pause", "resume", "cancel", "retry", "answer", "priority", "reassign"];

/**
 * Build the POST body for /api/projects/{p}/tasks/{id}/actions.
 * Throws on a missing required field so a bad tap never reaches the server half-formed.
 */
export function actionBody(action, rev, extra = {}) {
  if (!ACTIONS.includes(action)) throw new Error(`unknown action ${action}`);
  // rev is optional in B1's contract; when we know it, send it so a stale view gets 409 "stale" instead of acting.
  if (rev != null && !Number.isInteger(rev)) throw new Error("rev must be an integer");
  const body = rev == null ? { action } : { action, rev };
  const need = (k) => {
    if (extra[k] == null || extra[k] === "") throw new Error(`${action} needs ${k}`);
    body[k] = extra[k];
  };
  if (action === "changes") need("note");
  if (action === "reassign") need("worker");
  if (action === "priority") {
    need("priority");
    if (!/^P[0-3]$/.test(body.priority)) throw new Error("priority must be P0..P3");
  }
  if (action === "answer") {
    if (extra.decision != null) { need("decision"); need("option"); }
    else need("note");
  }
  for (const k of ["note", "worker", "priority", "decision", "option"]) if (extra[k] != null && body[k] == null) body[k] = extra[k];
  return body;
}

const enc = encodeURIComponent;

export function createApi({ base = "", fixtures = false, token = null, fetchImpl = globalThis.fetch?.bind(globalThis) } = {}) {
  const headers = (extra = {}) => (token ? { ...extra, Authorization: `Bearer ${token}` } : extra);
  const fx = (name) => `${base}fixtures/${name}.json`;

  async function getJson(url) {
    const r = await fetchImpl(url, { headers: headers({ Accept: "application/json" }), cache: "no-store" });
    if (r.status === 401 || r.status === 403) throw Object.assign(new Error("not paired"), { code: "auth" });
    if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { code: "http", status: r.status });
    return r.json();
  }
  /** First fixture that exists; null when none do. */
  async function firstFixture(names) {
    for (const n of names) {
      const r = await fetchImpl(fx(n), { cache: "no-store" });
      if (r.ok) return r.json();
    }
    return null;
  }

  return {
    fixtures,
    overview: () => (fixtures ? getJson(fx("overview")) : getJson(`${base}api/overview`)),
    async project(p) {
      if (!fixtures) return getJson(`${base}api/projects/${enc(p)}`);
      const vm = await firstFixture([`project.${p}`, "project"]);
      // The default fixture belongs to one project; never show it under another project's name.
      return vm && vm.project?.id === p ? vm : null;
    },
    async task(p, id) {
      if (!fixtures) return getJson(`${base}api/projects/${enc(p)}/tasks/${enc(id)}`);
      const d = await firstFixture([`task.${id}`, "task"]);
      return d && d.task?.id === id ? d : { task: null, card: null, attempts: [], handoff: null, history: [], events: [] };
    },
    eligible: (p, id) => (fixtures ? getJson(fx("eligible")) : getJson(`${base}api/projects/${enc(p)}/tasks/${enc(id)}/eligible-workers`)),
    accounts: () => (fixtures ? getJson(fx("overview")).then((o) => o.accounts) : getJson(`${base}api/accounts`)),

    /** Exchange the 6-digit code from `deck pair` for a device token: POST /api/pair {code, name} → {ok, token}. */
    async pair(code, name) {
      if (!/^\d{6}$/.test(String(code || ""))) return { ok: false, code: "invalid", reason: "Enter the 6-digit code shown by `deck pair`." };
      if (fixtures) return { ok: true, simulated: true, token: "fixture-token" };
      try {
        const r = await fetchImpl(`${base}api/pair`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: String(code), name: name || "phone" }) });
        const d = await r.json().catch(() => ({}));
        return d.ok && d.token ? { ok: true, token: d.token } : { ok: false, code: d.code || "http", reason: d.reason || `Pairing refused (HTTP ${r.status})` };
      } catch { return { ok: false, code: "offline", reason: "Deck is offline — can't pair." }; }
    },

    /** POST an action. Resolves {ok, task} or {ok:false, code, reason}; never throws for server refusals. */
    async action(p, id, body) {
      if (fixtures) return { ok: true, simulated: true, body };
      let r;
      try {
        r = await fetchImpl(`${base}api/projects/${enc(p)}/tasks/${enc(id)}/actions`, {
          method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
        });
      } catch (e) {
        return { ok: false, code: "offline", reason: "Deck is offline — action not sent." };
      }
      if (r.status === 401 || r.status === 403) return { ok: false, code: "auth", reason: "This device is not paired (run `deck pair`)." };
      let data = null;
      try { data = await r.json(); } catch { /* empty body */ }
      if (data && typeof data.ok === "boolean") return data;
      return r.ok ? { ok: true, task: data?.task } : { ok: false, code: "http", reason: data?.reason || `HTTP ${r.status}` };
    },

    /**
     * Stream events over SSE with fetch (EventSource cannot send Authorization).
     * Calls onEvent(event) per `deck` event and onState('live'|'offline'). Reconnects with Last-Event-ID.
     */
    stream({ since = 0, onEvent, onState, signal, retryMs = 3000 }) {
      if (fixtures) { onState?.("fixtures"); return; }
      let last = since;
      const loop = async () => {
        while (!signal?.aborted) {
          try {
            const r = await fetchImpl(`${base}api/events?since=${last}`, {
              headers: headers({ Accept: "text/event-stream", ...(last ? { "Last-Event-ID": String(last) } : {}) }), signal,
            });
            if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
            onState?.("live");
            const parser = sseParser((m) => {
              if (m.id) last = Number(m.id) || last;
              if (m.event === "deck" || m.event === "message") {
                try { onEvent?.(JSON.parse(m.data)); } catch { /* ignore a bad line */ }
              }
            });
            const reader = r.body.getReader();
            const dec = new TextDecoder();
            for (;;) {
              const { value, done } = await reader.read();
              if (done) break;
              parser(dec.decode(value, { stream: true }));
            }
          } catch (e) {
            if (signal?.aborted) return;
          }
          onState?.("offline");
          await new Promise((res) => setTimeout(res, retryMs));
        }
      };
      loop();
    },
  };
}

/** Incremental text/event-stream parser. Feed it chunks; it calls emit({event, data, id}) per message. */
export function sseParser(emit) {
  let buf = "";
  let msg = { event: "message", data: [], id: null };
  return (chunk) => {
    buf += chunk;
    let i;
    while ((i = buf.search(/\r?\n/)) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + (buf[i] === "\r" ? 2 : 1));
      if (line === "") {
        if (msg.data.length) emit({ event: msg.event, data: msg.data.join("\n"), id: msg.id });
        msg = { event: "message", data: [], id: null };
        continue;
      }
      if (line.startsWith(":")) continue;
      const c = line.indexOf(":");
      const field = c < 0 ? line : line.slice(0, c);
      const val = c < 0 ? "" : line.slice(c + 1).replace(/^ /, "");
      if (field === "data") msg.data.push(val);
      else if (field === "event") msg.event = val;
      else if (field === "id") msg.id = val;
    }
  };
}
