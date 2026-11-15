// Settings: theme picker (Deck · Mission Control · Paper light/dark/auto), device token, notifications.
import { html } from "../fmt.js";
import { THEMES, MODES, normalise } from "../theme.js";
import { lcd, label } from "./common.js";

export function settingsView(pref, { token = "", fixtures = false, conn = "live" } = {}) {
  const p = normalise(pref);
  return html`${lcd({ left: html`<a href="#/">◀ ALL</a>`, mid: "SETTINGS", right: "", big: "SETTINGS", bigClass: "proj", conn })}
    ${label("THEME")}
    <div class="themes" role="group" aria-label="Theme">${THEMES.map((t) => html`<button class="wk" data-theme-set="${t.id}" aria-pressed="${String(p.theme === t.id)}">
      <span class="t"><b>${t.name}</b><small>${t.note}</small></span><span aria-hidden="true">${p.theme === t.id ? "✓" : ""}</span></button>`)}</div>
    ${p.theme === "paper" ? html`${label("PAPER MODE")}<div role="group" aria-label="Paper mode">${MODES.map((m) =>
      html`<button class="chip${p.mode === m ? " on" : ""}" data-mode-set="${m}" aria-pressed="${String(p.mode === m)}">${m}</button>`)}</div>` : ""}
    <div class="hint">The ◐ key on every screen cycles Deck → Mission Control → Paper light → Paper dark.</div>
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
