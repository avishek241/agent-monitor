import { WEB, fixture } from "./helpers.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { createApi, actionBody, sseParser } from "../../web/api.js";

/** Stub deckd: records requests; serves web/ files; answers actions from a table; streams SSE. */
function stub(handler) {
  const seen = [];
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const rec = { method: req.method, url: req.url, headers: req.headers, body: body ? JSON.parse(body) : null };
      seen.push(rec);
      handler(req, res, rec);
    });
  });
  return new Promise((ok) => srv.listen(0, "127.0.0.1", () => ok({ srv, seen, base: `http://127.0.0.1:${srv.address().port}/` })));
}
const json = (res, code, obj) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(obj)); };
const serveWeb = (req, res) => {
  const f = path.join(WEB, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (f.startsWith(WEB) && fs.existsSync(f) && fs.statSync(f).isFile()) { res.writeHead(200); res.end(fs.readFileSync(f)); }
  else { res.writeHead(404); res.end(); }
};

test("actionBody builds exactly the §12 bodies and refuses incomplete ones", () => {
  assert.deepEqual(actionBody("approve", 21), { action: "approve", rev: 21 });
  assert.deepEqual(actionBody("changes", 3, { note: "fix totals" }), { action: "changes", rev: 3, note: "fix totals" });
  assert.deepEqual(actionBody("reassign", 4, { worker: "W2" }), { action: "reassign", rev: 4, worker: "W2" });
  assert.deepEqual(actionBody("priority", 4, { priority: "P1" }), { action: "priority", rev: 4, priority: "P1" });
  assert.deepEqual(actionBody("answer", 5, { decision: 0, option: "skip" }), { action: "answer", rev: 5, decision: 0, option: "skip" });
  assert.deepEqual(actionBody("answer", 5, { note: "inactive" }), { action: "answer", rev: 5, note: "inactive" });
  assert.deepEqual(actionBody("pause", 1, { junk: 1 }), { action: "pause", rev: 1 });
  assert.throws(() => actionBody("changes", 3), /note/);
  assert.throws(() => actionBody("reassign", 3), /worker/);
  assert.throws(() => actionBody("priority", 3, { priority: "P9" }), /P0\.\.P3/);
  assert.throws(() => actionBody("answer", 3), /note/);
  assert.deepEqual(actionBody("approve"), { action: "approve" }, "rev is optional (B1 contract)");
  assert.throws(() => actionBody("approve", "7"), /rev/);
  assert.throws(() => actionBody("delete", 1), /unknown/);
});

test("actions POST the right body with the bearer token to the right path", async () => {
  const { srv, seen, base } = await stub((req, res) => json(res, 200, { ok: true, task: { id: "107-supplier-import", state: "merging" } }));
  try {
    const api = createApi({ base, token: "dev-token" });
    const r = await api.action("shop-ops", "107-supplier-import", actionBody("approve", 21));
    assert.equal(r.ok, true);
    assert.equal(seen[0].method, "POST");
    assert.equal(seen[0].url, "/api/projects/shop-ops/tasks/107-supplier-import/actions");
    assert.equal(seen[0].headers.authorization, "Bearer dev-token");
    assert.equal(seen[0].headers["content-type"], "application/json");
    assert.deepEqual(seen[0].body, { action: "approve", rev: 21 });
  } finally { srv.close(); }
});

test("a policy refusal comes back with the server's reason verbatim", async () => {
  const reason = "Office account: never runs personal projects (account policy)";
  const { srv, base } = await stub((req, res) => json(res, 409, { ok: false, code: "policy_denied", reason }));
  try {
    const r = await createApi({ base, token: "t" }).action("shop-ops", "110-attendance-history", actionBody("reassign", 4, { worker: "W4" }));
    assert.deepEqual(r, { ok: false, code: "policy_denied", reason });
  } finally { srv.close(); }
});

test("401 → auth error; unreachable → offline", async () => {
  const { srv, base } = await stub((req, res) => { res.writeHead(401); res.end(); });
  try {
    const api = createApi({ base });
    assert.equal((await api.action("p", "t", actionBody("pause", 1))).code, "auth");
    await assert.rejects(api.overview(), (e) => e.code === "auth");
  } finally { srv.close(); }
  const dead = createApi({ base: "http://127.0.0.1:9/" });
  assert.equal((await dead.action("p", "t", actionBody("pause", 1))).code, "offline");
});

test("live GETs hit the §12 endpoints", async () => {
  const { srv, seen, base } = await stub((req, res) => json(res, 200, []));
  try {
    const api = createApi({ base });
    await api.overview(); await api.project("shop-ops"); await api.task("shop-ops", "106-match-receipts");
    await api.eligible("shop-ops", "110-attendance-history"); await api.accounts();
    assert.deepEqual(seen.map((r) => r.url), ["/api/overview", "/api/projects/shop-ops", "/api/projects/shop-ops/tasks/106-match-receipts",
      "/api/projects/shop-ops/tasks/110-attendance-history/eligible-workers", "/api/accounts"]);
    assert.ok(seen.every((r) => !r.headers.authorization), "no token configured → none sent");
  } finally { srv.close(); }
});

test("fixture mode reads web/fixtures, never mixes projects, and simulates POSTs", async () => {
  const { srv, seen, base } = await stub(serveWeb);
  try {
    const api = createApi({ base, fixtures: true });
    assert.equal((await api.overview()).time, fixture("overview.json").time);
    assert.equal((await api.project("shop-ops")).project.id, "shop-ops");
    assert.equal((await api.project("client-portal")).project.id, "client-portal");
    assert.equal(await api.project("home-lab"), null);
    assert.equal((await api.task("shop-ops", "106-match-receipts")).task.id, "106-match-receipts");
    assert.equal((await api.task("shop-ops", "999-x")).task, null);
    assert.equal((await api.eligible("shop-ops", "110")).length, 5);
    const n = seen.length;
    const r = await api.action("shop-ops", "107", actionBody("approve", 21));
    assert.equal(r.simulated, true);
    assert.equal(seen.length, n, "no request sent in fixture mode");
  } finally { srv.close(); }
});

test("pairing posts the 6-digit code and returns the device token", async () => {
  const { srv, seen, base } = await stub((req, res) => json(res, 200, { ok: true, token: "dev-abc" }));
  try {
    const api = createApi({ base });
    assert.deepEqual(await api.pair("123456", "phone"), { ok: true, token: "dev-abc" });
    assert.equal(seen[0].url, "/api/pair");
    assert.deepEqual(seen[0].body, { code: "123456", name: "phone" });
    assert.equal(seen[0].headers.authorization, undefined);
    assert.equal((await api.pair("12ab", "x")).code, "invalid");
    assert.equal(seen.length, 1, "a malformed code is never sent");
  } finally { srv.close(); }
  const { srv: s2, base: b2 } = await stub((req, res) => json(res, 403, { ok: false, code: "invalid", reason: "code expired" }));
  try { assert.equal((await createApi({ base: b2 }).pair("123456")).reason, "code expired"); } finally { s2.close(); }
});

test("sseParser handles chunks split mid-line, comments, multi-line data and ids", () => {
  const got = [];
  const feed = sseParser((m) => got.push(m));
  feed(": keepalive\n\nid: 7\nevent: de");
  feed("ck\ndata: {\"id\":7,\n");
  feed("data: \"text\":\"x\"}\n\r\n");
  feed("data: plain\n\n");
  assert.deepEqual(got, [{ event: "deck", data: '{"id":7,\n"text":"x"}', id: "7" }, { event: "message", data: "plain", id: null }]);
});

test("stream() delivers deck events, reports live/offline and resumes with Last-Event-ID", async () => {
  let calls = 0;
  const { srv, seen, base } = await stub((req, res) => {
    calls++;
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    const id = calls === 1 ? 41 : 42;
    res.end(`id: ${id}\nevent: deck\ndata: ${JSON.stringify({ id, ts: 1, type: "review.requested", severity: "action", text: `e${id}` })}\n\n`);
  });
  const ctl = new AbortController();
  const events = [], states = [];
  try {
    await new Promise((done) => {
      createApi({ base, token: "t" }).stream({
        since: 40, signal: ctl.signal, retryMs: 10,
        onEvent: (e) => { events.push(e.id); if (events.length === 2) { ctl.abort(); done(); } },
        onState: (st) => states.push(st),
      });
    });
    assert.deepEqual(events, [41, 42]);
    assert.ok(states.includes("live") && states.includes("offline"));
    assert.equal(seen[0].url, "/api/events?since=40");
    assert.equal(seen[0].headers.accept, "text/event-stream");
    assert.equal(seen[1].headers["last-event-id"], "41");
    assert.equal(seen[1].headers.authorization, "Bearer t");
  } finally { ctl.abort(); srv.close(); }
});
