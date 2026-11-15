// Layout registry. A layout's render(route, ctx) returns the whole screen's HTML (chrome + content) or null to
// fall back to the shared Studio views. ctx = { o, projects, details, eligible, now, conn, pref, token, fixtures }.
// Contract for layout authors: reuse the shared action attributes (data-act, data-hold, data-pair,
// data-layout-*/data-mode-*, .task-root[data-p][data-t][data-rev]) so app.js handles every action unchanged.
import * as mission from "./mission.js";
import * as paper from "./paper.js";
export const RENDERERS = { mission: mission.render, paper: paper.render };
