// Navigation model shared with the panel (design §13): Overview → Project page → Task detail → sheets.
// Pure functions only; app.js owns the DOM and the live state object.

export const TABS = ["workers", "board", "review", "alerts", "timeline", "repo"];
export const SHEETS = ["review", "reassign", "answer"];

/** Parse a location hash into a route. Unknown input falls back to the Overview. */
export function parseRoute(hash = "") {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "accounts") return { view: "accounts" };
  if (parts[0] === "settings" || parts[0] === "pair") return { view: "settings" };
  if (parts[0] === "p" && parts[1]) {
    const project = parts[1];
    if (parts[2] === "t" && parts[3]) {
      const sheet = SHEETS.includes(parts[4]) ? parts[4] : null;
      return { view: "task", project, task: parts[3], sheet };
    }
    return { view: "project", project, tab: TABS.includes(parts[2]) ? parts[2] : "board" };
  }
  return { view: "overview" };
}

/** Inverse of parseRoute. */
export function routeHash(r) {
  const e = encodeURIComponent;
  switch (r.view) {
    case "accounts": return "#/accounts";
    case "settings": return "#/settings";
    case "project": return `#/p/${e(r.project)}/${r.tab || "board"}`;
    case "task": return `#/p/${e(r.project)}/t/${e(r.task)}${r.sheet ? `/${r.sheet}` : ""}`;
    default: return "#/";
  }
}

/** The project to swipe to (dir +1 = next, -1 = previous), wrapping; disabled projects are skipped. */
export function neighbourProject(projects, current, dir) {
  const ids = (projects || []).filter((p) => p.enabled !== false).map((p) => p.id);
  if (!ids.length) return null;
  const i = ids.indexOf(current);
  return ids[(i + dir + ids.length) % ids.length];
}

/** Find a TaskVM by id across every board column of a ProjectVM. */
export function findTask(projectVm, id) {
  const b = projectVm?.board || {};
  for (const col of Object.values(b)) for (const t of col || []) if (t.id === id) return t;
  return null;
}

/** Server time now, given a view model's `time` and when it was fetched (local seconds). */
export const serverNow = (vmTime, fetchedAt, localNow = Date.now() / 1000) => Math.round(vmTime + (localNow - fetchedAt));
