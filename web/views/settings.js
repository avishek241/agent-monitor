// Settings: layout (Studio · Mission Control · Paper) and mode (Auto · Light · Dark) chosen separately, device token, notifications.
import { html } from "../fmt.js";
import { LAYOUTS, MODES, normalise } from "../theme.js";
import { lcd, label } from "./common.js";

export function settingsView(pref, { token = "", fixtures = false, conn = "live" } = {}) {
  const p = normalise(pref);
  return html`${lcd({ left: html`<a href="#/">◀ ALL</a>`, mid: "SETTINGS", right: "", big: "SETTINGS", bigClass: "proj", conn })}
    ${label("LAYOUT")}
    <div class="themes" role="group" aria-label="Layout">${LAYOUTS.map((l) => html`<button class="wk" data-layout-set="${l.id}" aria-pressed="${String(p.layout === l.id)}">
      <span class="t"><b>${l.name}</b><small>${l.note}</small></span><span aria-hidden="true">${p.layout === l.id ? "✓" : ""}</span></button>`)}</div>
    ${label("MODE")}
    <div role="group" aria-label="Mode">${MODES.map((m) => html`<button class="chip${p.mode === m.id ? " on" : ""}" data-mode-set="${m.id}" aria-pressed="${String(p.mode === m.id)}">${m.name}</button>`)}</div>
    <div class="hint">Auto follows the phone's light/dark setting. Quick keys on every screen: ▦ next layout, ◐ next mode.</div>
    ${label("PAIR THIS DEVICE")}
    <div class="sheet"><p class="summary">Run <code>deck pair</code> on the Mac and type the 6-digit code. GETs on localhost need no token; actions always do.</p>
      <input id="paircode" class="notefield" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" placeholder="6-digit code" aria-label="Pairing code">
      <input id="pairname" class="notefield" autocomplete="off" placeholder="device name (e.g. phone)" aria-label="Device name">
      <div class="btns"><button class="btn go" data-pair="code">PAIR</button><button class="btn" data-pair="clear">FORGET</button></div>
      <div class="hint">${token ? "This device is paired." : "Not paired yet."}</div>
      <details><summary class="muted">Paste a token instead</summary>
        <input id="tok" class="notefield" autocomplete="off" placeholder="device token" value="${token}" aria-label="Device token">
        <div class="btns"><button class="btn" data-pair="save">SAVE TOKEN</button></div></details>
      <div class="btns"><button class="btn" data-pair="notify">ENABLE NOTIFICATIONS</button></div>
      ${fixtures ? html`<div class="hint">Fixture mode: actions are simulated, nothing is sent.</div>` : ""}</div>`;
}
