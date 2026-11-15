// Agent Deck v2 — floating panel (macOS, SwiftUI). Design §13.1; mockup docs/v2/mockups/panel.html.
// Reads ONLY the deckd HTTP API (§12): /api/overview, /api/projects/{p}, …/eligible-workers, POST …/actions.
// No files, no DB, no network beyond localhost, no tokens spent.
//
//   PanelV2 [--url http://127.0.0.1:7788] [--state-dir ~/.local/share/agent-deck]
//   PanelV2 --fixtures web/fixtures            # render the B3 fixture JSON, actions simulated
//   PanelV2 --check-fixtures web/fixtures      # decode every fixture + self-test, print a summary, exit 0/1
import AppKit
import SwiftUI

// MARK: - View models (§12). Optional wherever the server may send null or omit a key.

struct Capacity: Decodable, Hashable {
  let lanesUsed: Int?, lanesMax: Int?, spendable5: Double?, committed5: Double?, minutesToReset: Double?, urgency: Double?
  let fitsSize: String?, spare: Bool?, spareSinceSec: Int?, why: String?, whyKind: String?
}
struct Lane: Decodable, Identifiable, Hashable {
  var id: String { "\(worker)·\(lane)" }
  let worker: String, lane: Int, state: String
  let project: String?, task: String?, title: String?, step: String?, stepIndex: Int?, stepCount: Int?, etaMin: Double?, lastActiveSec: Int?, resumeAt: Int?
}
struct Account: Decodable, Identifiable, Hashable {
  let id: String, label: String, tool: String?, scope: String?
  let pct5: Double?, pct7: Double?, resets5: Int?, readAgeSec: Int?, confidence: String?, state: String?, dispatch: Bool?
  let capacity: Capacity?, lanes: [Lane]?
}
struct Counts: Decodable, Hashable { let running: Int?, queued: Int?, review: Int?, blocked: Int?, limit_paused: Int?, hung: Int?, doneToday: Int? }
struct ProjectTile: Decodable, Identifiable, Hashable {
  let id: String, title: String, short: String?, scope: String?, color: String?, icon: String?, enabled: Bool?, counts: Counts?, headline: String?, alerts: Int?
  var name: String { short ?? title }
}
struct DeckEvent: Decodable, Identifiable, Hashable {
  let id: Int, ts: Int, type: String, severity: String, text: String, project: String?, task: String?, worker: String?
}
struct NeedsItem: Decodable, Identifiable, Hashable {
  var id: String { kind + (task ?? "") + text }
  let kind: String, project: String?, task: String?, num: Int?, title: String, text: String, rev: Int?, options: [String]?, eventId: Int?
}
struct Inbox: Decodable, Hashable { let review: Int?, questions: Int?, alerts: Int? }
struct Machine: Decodable, Hashable { let running: Int?, lanesTotal: Int?, maxParallelTotal: Int?, freeRamGb: Double?, plannedAt: Int? }
struct Backlog: Decodable, Hashable { let eligible: Int?, target: Int?, low: Bool?, hint: String? }
struct OverviewVM: Decodable {
  let time: Int, inbox: Inbox?, projects: [ProjectTile], accounts: [Account], machine: Machine?, backlog: Backlog?
  let needsYou: [NeedsItem]?, alerts: [DeckEvent]?
}
struct StepVM: Decodable, Hashable { let title: String, index: Int, count: Int, etaMin: Double? }
/// rate is per minute and left is in minutes (numbers, B1 contract).
struct MetricVM: Decodable, Hashable { let label: String, done: Double, total: Double?, kind: String?, rate: Double?, left: Double? }
struct DepVM: Decodable, Hashable { let id: String, state: String }
struct Question: Decodable, Hashable { let text: String, options: [String]?, `default`: String? }
struct TaskVM: Decodable, Identifiable, Hashable {
  let id: String, num: Int?, title: String, summary: String?, tags: [String]?, priority: String?, size: String?, state: String
  let stateReason: String?, worker: String?, progress: Double?, step: StepVM?, etaFinish: Int?, metrics: [MetricVM]?
  let lastActiveSec: Int?, warnings: [String]?, deps: [DepVM]?, resumeAt: Int?, kind: String?, rev: Int?, question: Question?
  var numText: String { num.map(String.init) ?? String(id.split(separator: "-").first ?? "") }
}
struct Board: Decodable { let backlog: [TaskVM]?, upNext: [TaskVM]?, running: [TaskVM]?, review: [TaskVM]?, needsYou: [TaskVM]?, doneToday: [TaskVM]? }
struct DoneWhen: Decodable, Hashable { let item: String, met: Bool, evidence: String }
struct TestRun: Decodable, Hashable { let command: String, result: String, passed: Int?, failed: Int? }
struct Decision: Decodable, Hashable { let question: String, options: [String]?, `default`: String? }
struct GitFacts: Decodable, Hashable { let commits: Int?, filesChanged: Int?, uncommitted: Int?, conflictsWithMain: Bool? }
struct ReviewVM: Decodable, Identifiable {
  var id: String { task.id }
  let task: TaskVM, attempt: Int?, summary: String, outcome: String?, doneWhen: [DoneWhen]?, tests: [TestRun]?, risks: [String]?
  let decisionsNeeded: [Decision]?, git: GitFacts?, waitingSec: Int?, pilot: Bool?, unlocks: [String]?, worker: String?, account: String?
}
struct WorkerVM: Decodable, Identifiable {
  var id: String { worker + account }
  let worker: String, account: String, label: String, scope: String?, adapter: String?, state: String?, allowed: Bool?, reason: String?, lanes: [Lane]?
  /// B1's WorkerVM carries no usage: join it with the Overview's AccountVM for the quota bar and planner reasons.
  func asAccount(_ accounts: [Account]) -> Account {
    let a = accounts.first { $0.id == account }
    return Account(id: account, label: label, tool: a?.tool, scope: scope ?? a?.scope, pct5: a?.pct5, pct7: a?.pct7, resets5: a?.resets5,
                   readAgeSec: a?.readAgeSec, confidence: a?.confidence, state: a?.state ?? state, dispatch: a?.dispatch, capacity: a?.capacity,
                   lanes: (lanes ?? []).isEmpty ? a?.lanes : lanes)
  }
}
/// B1 sends ok (null = not checked); older fixtures used up.
struct Health: Decodable, Identifiable, Hashable { var id: String { label }; let label: String, ok: Bool?, up: Bool?, group: String?; var isUp: Bool? { ok ?? up } }
struct Change: Decodable, Identifiable, Hashable { var id: String { area }; let area: String, files: Int }
struct Repo: Decodable { let commit: String?, changes: [Change]? }
struct ProjectInfo: Decodable { let id: String, title: String, short: String?, scope: String?, allowPersonalAccounts: Bool?, repo: Repo?, health: [Health]? }
struct DomainVM: Decodable, Identifiable { var id: String { name }; let name: String, holder: String?, since: Int?, waiting: [String]?, reserved: Bool?, note: String? }
struct MergeActive: Decodable { let task: String, gatesDone: Int?, gatesTotal: Int? }
struct MergeVM: Decodable { let active: MergeActive? }
struct ProjectVM: Decodable {
  let time: Int?, project: ProjectInfo, workers: [WorkerVM]?, board: Board, inbox: [ReviewVM]?, alerts: [DeckEvent]?, timeline: [DeckEvent]?
  let domains: [DomainVM]?, merge: MergeVM?
}
struct Eligible: Decodable, Identifiable { var id: String { worker }; let worker: String, account: String?, label: String, scope: String?, allowed: Bool, reason: String?, detail: String?, kind: String? }
struct ActionResult: Decodable { let ok: Bool, code: String?, reason: String? }

// MARK: - Pure helpers (also exercised by --check-fixtures)

let actionNames: Set<String> = ["approve", "changes", "reject", "pause", "resume", "cancel", "retry", "answer", "priority", "reassign"]

/// Same rules as web/api.js actionBody: returns nil when a required field is missing.
func actionBody(_ action: String, rev: Int?, note: String? = nil, worker: String? = nil, priority: String? = nil,
                decision: Int? = nil, option: String? = nil) -> [String: Any]? {
  guard actionNames.contains(action) else { return nil }
  var b: [String: Any] = ["action": action]
  if let rev = rev { b["rev"] = rev }   // optional in B1; when sent, a stale view gets 409 "stale"
  if let v = note, !v.isEmpty { b["note"] = v }
  if let v = worker { b["worker"] = v }
  if let v = priority { b["priority"] = v }
  if let d = decision, let o = option { b["decision"] = d; b["option"] = o }
  switch action {
  case "changes": if b["note"] == nil { return nil }
  case "reassign": if b["worker"] == nil { return nil }
  case "priority": if !["P0", "P1", "P2", "P3"].contains(priority ?? "") { return nil }
  case "answer": if b["note"] == nil && b["decision"] == nil { return nil }
  default: break
  }
  return b
}
func bump(_ p: String?, up: Bool) -> String {
  let n = Int((p ?? "P2").dropFirst()) ?? 2
  return "P\(min(3, max(0, n + (up ? -1 : 1))))"
}
let clockFmt: DateFormatter = { let f = DateFormatter(); f.dateFormat = "HH:mm"; return f }()
let clockSecFmt: DateFormatter = { let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f }()
func hhmm(_ ts: Int?) -> String { ts.map { clockFmt.string(from: Date(timeIntervalSince1970: TimeInterval($0))) } ?? "—" }
func dur(_ s: Int?) -> String {
  guard let s0 = s else { return "—" }
  let s = max(0, s0)
  if s < 60 { return "\(s)s" }
  let m = Int((Double(s) / 60).rounded())
  if m < 60 { return "\(m)m" }
  let h = m / 60, mm = m % 60
  if h < 48 { return mm == 0 ? "\(h)h" : String(format: "%dh%02d", h, mm) }
  return "\(h / 24)d\(h % 24)h"
}
func pct(_ v: Double?) -> String { v.map { "\(Int($0.rounded()))%" } ?? "—" }

/// Free-lane explanation from the planner's own output only (never computed in the UI).
func freeLaneText(_ a: Account) -> (kind: String, text: String) {
  let c = a.capacity
  if a.state == "cooling" { return ("cool", "⏸ cooling · limit resets \(hhmm(a.resets5))") }
  if let why = c?.why {
    let kind = c?.whyKind ?? (why.lowercased().contains("policy") ? "policy" : "other")
    if kind == "policy" { return ("policy", "🔒 free · \(why)") }
    if kind == "cooling" { return ("cool", "⏸ \(why)") }
    return ("idle", "○ free · \(why)")
  }
  if let fits = c?.fitsSize {
    var t = "○ free · \(Int(c?.spendable5 ?? 0))% to spend before \(hhmm(a.resets5)) · \(fits) fits"
    if c?.spare == true, let s = c?.spareSinceSec { t += " · ⚠ spare \(dur(s))" }
    return (c?.spare == true ? "spare" : "idle", t)
  }
  return ("idle", "○ free")
}
func quotaText(_ a: Account, now: Int) -> String {
  if a.state == "cooling" { return "LIMIT · resets \(hhmm(a.resets5)) (in \(dur((a.resets5 ?? now) - now)))" }
  var p = ["5h \(pct(a.pct5))"]
  if a.pct7 != nil { p.append("7d \(pct(a.pct7))") }
  if let r = a.resets5 { p.append("↻ \(dur(r - now))") }
  if a.dispatch == false { p.append("usage only") } else if let m = a.capacity?.lanesMax, m > 0 { p.append("\(a.capacity?.lanesUsed ?? 0)/\(m)") }
  if let age = a.readAgeSec, age > 600 { p.append("reading \(dur(age)) ago") }
  return p.joined(separator: " · ")
}

// MARK: - Store: polls deckd every 2 s (or reads fixtures), posts actions with the local token.

final class DeckStore: ObservableObject {
  @Published var overview: OverviewVM?
  @Published var project: ProjectVM?
  @Published var selected: String? = nil { didSet { project = nil; tab = "board"; refresh() } }
  @Published var tab = "board"
  @Published var offline = false
  @Published var lastUpdate: Int?
  @Published var toast: String?
  @Published var collapsed = false { didSet { onCollapse?(collapsed) } }
  @Published var reassign: (task: TaskVM, rows: [Eligible])?
  /// deck | mission | paper-light | paper-dark | auto (Auto follows the macOS appearance: Paper light / Paper dark).
  @Published var themeChoice = UserDefaults.standard.string(forKey: "deck.theme") ?? "deck" {
    didSet { UserDefaults.standard.set(themeChoice, forKey: "deck.theme"); applyTheme() }
  }
  @Published var systemDark = false { didSet { applyTheme() } }
  var onTheme: (() -> Void)?
  func applyTheme() { T = resolveTheme(themeChoice, systemDark: systemDark); onTheme?() }
  func cycleTheme() { let ids = THEME_CHOICES.map { $0.0 }; themeChoice = ids[((ids.firstIndex(of: themeChoice) ?? 0) + 1) % ids.count] }
  var onCollapse: ((Bool) -> Void)?
  var fetchedAt = Date()
  let base: URL?, fixtures: URL?, token: String?

  init(base: URL?, fixtures: URL?, token: String?) {
    self.base = base; self.fixtures = fixtures; self.token = token
    applyTheme()
    refresh()
    Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.refresh() }
  }
  var now: Int { (overview?.time ?? Int(Date().timeIntervalSince1970)) + Int(Date().timeIntervalSince(fetchedAt)) }

  func get<T: Decodable>(_ path: String, fixture: [String], as: T.Type, done: @escaping (T?) -> Void) {
    if let dir = fixtures {
      for name in fixture {
        if let d = try? Data(contentsOf: dir.appendingPathComponent("\(name).json")), let v = try? JSONDecoder().decode(T.self, from: d) { return done(v) }
      }
      return done(nil)
    }
    guard let base = base else { return done(nil) }
    var req = URLRequest(url: base.appendingPathComponent(path), timeoutInterval: 4)
    if let t = token { req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
    URLSession.shared.dataTask(with: req) { data, resp, _ in
      let ok = (resp as? HTTPURLResponse)?.statusCode == 200
      let v = ok ? data.flatMap { try? JSONDecoder().decode(T.self, from: $0) } : nil
      DispatchQueue.main.async { done(v) }
    }.resume()
  }

  func refresh() {
    get("api/overview", fixture: ["overview"], as: OverviewVM.self) { [weak self] o in
      guard let self = self else { return }
      if let o = o { self.overview = o; self.fetchedAt = Date(); self.lastUpdate = o.time; self.offline = false } else { self.offline = true }
    }
    guard let p = selected else { return }
    get("api/projects/\(p)", fixture: ["project.\(p)", "project"], as: ProjectVM.self) { [weak self] vm in
      guard let self = self, self.selected == p else { return }
      if let vm = vm, vm.project.id == p { self.project = vm }   // never show one project's fixture under another
    }
  }

  func openReassign(_ t: TaskVM) {
    guard let p = selected else { return }
    get("api/projects/\(p)/tasks/\(t.id)/eligible-workers", fixture: ["eligible"], as: [Eligible].self) { [weak self] rows in
      self?.reassign = (t, rows ?? [])
    }
  }

  func act(_ project: String?, _ task: String, _ body: [String: Any]?) {
    guard let body = body, let project = project ?? selected else { flash("Missing a required field"); return }
    if fixtures != nil {
      let s = (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])).flatMap { String(data: $0, encoding: .utf8) } ?? ""
      FileHandle.standardError.write("fixtures: would POST \(project)/\(task) \(s)\n".data(using: .utf8)!)
      flash("Fixtures: would POST \(s)"); return
    }
    guard let base = base else { return }
    var req = URLRequest(url: base.appendingPathComponent("api/projects/\(project)/tasks/\(task)/actions"), timeoutInterval: 6)
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    if let t = token { req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
    req.httpBody = try? JSONSerialization.data(withJSONObject: body)
    URLSession.shared.dataTask(with: req) { [weak self] data, resp, err in
      let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
      let r = data.flatMap { try? JSONDecoder().decode(ActionResult.self, from: $0) }
      DispatchQueue.main.async {
        if err != nil { self?.flash("Deck is offline — action not sent") }
        else if code == 401 || code == 403 { self?.flash("No token: \(stateDir.path)/token is missing or wrong") }
        else if let r = r, !r.ok { self?.flash(r.reason ?? r.code ?? "Refused") }   // policy reason verbatim
        else { self?.flash("\(body["action"] ?? "") ✓"); self?.refresh() }
      }
    }.resume()
  }
  func flash(_ s: String) {
    toast = s
    DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in if self?.toast == s { self?.toast = nil } }
  }
}

// MARK: - Themes (design tokens; same set as web/themes.css). One component structure, four token sets.

struct Theme {
  let id: String, name: String, dark: Bool
  // colours as 0xRRGGBB so --check-fixtures can verify WCAG AA contrast
  let bg: UInt32, card: UInt32, line: UInt32, tx: UInt32, dim: UInt32, muted: UInt32
  let accent: UInt32, onAccent: UInt32, ok: UInt32, onOk: UInt32, okTx: UInt32, warn: UInt32, bad: UInt32, badTx: UInt32, onBad: UInt32
  let office: UInt32, personal: UInt32, onScope: UInt32, alertCard: UInt32, reviewCard: UInt32, keyBg: UInt32, keyTx: UInt32
  let body: Font.Design, title: Font.Design, mono: Bool
  let base: CGFloat, radius: CGFloat, gap: CGFloat, pad: CGFloat
}
let THEMES: [Theme] = [
  Theme(id: "deck", name: "Deck", dark: true, bg: 0x171717, card: 0x272727, line: 0x454545, tx: 0xffffff, dim: 0xdddddd, muted: 0xa8a8a8,
        accent: 0xff730d, onAccent: 0x141414, ok: 0x34c759, onOk: 0x0b1a10, okTx: 0x4ade80, warn: 0xffcc00, bad: 0xff453a, badTx: 0xff7b72, onBad: 0x1a0000,
        office: 0x3a5fd6, personal: 0xc2410c, onScope: 0xffffff, alertCard: 0x3a1a1a, reviewCard: 0x33261a, keyBg: 0x3a3a3a, keyTx: 0xffffff,
        body: .default, title: .default, mono: false, base: 14, radius: 10, gap: 16, pad: 12),
  Theme(id: "mission", name: "Mission Control", dark: true, bg: 0x0a0c0f, card: 0x0f1318, line: 0x2c3744, tx: 0xdbe4ee, dim: 0xc0ccd8, muted: 0x93a1b2,
        accent: 0x2ee6ff, onAccent: 0x001318, ok: 0x3dff9a, onOk: 0x00160a, okTx: 0x3dff9a, warn: 0xffc53d, bad: 0xff5872, badTx: 0xff7a8e, onBad: 0x1a0006,
        office: 0xb9a2ff, personal: 0x2ee6ff, onScope: 0x05070a, alertCard: 0x1c1016, reviewCard: 0x121a22, keyBg: 0x1f2731, keyTx: 0xdbe4ee,
        body: .monospaced, title: .monospaced, mono: true, base: 13, radius: 3, gap: 12, pad: 12),
  Theme(id: "paper-light", name: "Paper light", dark: false, bg: 0xf6f2ea, card: 0xfffdf8, line: 0xe6dfd2, tx: 0x2a2723, dim: 0x3d3933, muted: 0x5e584f,
        accent: 0x58429a, onAccent: 0xffffff, ok: 0x2c6a3d, onOk: 0xffffff, okTx: 0x2c6a3d, warn: 0x735600, bad: 0xa02d33, badTx: 0xa02d33, onBad: 0xffffff,
        office: 0x2a5a88, personal: 0x934514, onScope: 0xffffff, alertCard: 0xf8dfdf, reviewCard: 0xebe5f6, keyBg: 0xece9e3, keyTx: 0x2a2723,
        body: .default, title: .serif, mono: false, base: 15, radius: 16, gap: 16, pad: 14),
  Theme(id: "paper-dark", name: "Paper dark", dark: true, bg: 0x1b1a17, card: 0x25231f, line: 0x38352f, tx: 0xefe9df, dim: 0xd8d1c5, muted: 0xb0a89c,
        accent: 0xcbbcf2, onAccent: 0x1b1a17, ok: 0xa3d9b1, onOk: 0x1b1a17, okTx: 0xa3d9b1, warn: 0xead17c, bad: 0xf4a6ac, badTx: 0xf4a6ac, onBad: 0x1b1a17,
        office: 0xa8caef, personal: 0xf3bb8f, onScope: 0x1b1a17, alertCard: 0x40232a, reviewCard: 0x2e2842, keyBg: 0x2f2d29, keyTx: 0xefe9df,
        body: .default, title: .serif, mono: false, base: 15, radius: 16, gap: 16, pad: 14),
]
func themeById(_ id: String?) -> Theme { THEMES.first { $0.id == id } ?? THEMES[0] }
/// The picker: four themes plus Auto.
let THEME_CHOICES: [(String, String)] = [("deck", "Deck"), ("mission", "Mission Control"), ("paper-light", "Paper light"), ("paper-dark", "Paper dark"), ("auto", "Auto")]
func resolveTheme(_ choice: String, systemDark: Bool) -> Theme { choice == "auto" ? themeById(systemDark ? "paper-dark" : "paper-light") : themeById(choice) }
/// The active theme. The root view is re-identified on change, so every view re-reads it.
var T = THEMES[0]

func rgb(_ v: UInt32) -> Color { Color(red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255) }
func contrast(_ a: UInt32, _ b: UInt32) -> Double {
  func lum(_ v: UInt32) -> Double {
    let c = [(v >> 16) & 255, (v >> 8) & 255, v & 255].map { x -> Double in let d = Double(x) / 255; return d <= 0.03928 ? d / 12.92 : pow((d + 0.055) / 1.055, 2.4) }
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
  }
  let x = lum(a), y = lum(b)
  return (max(x, y) + 0.05) / (min(x, y) + 0.05)
}
/// Every text-on-background pair the panel draws; all must reach 4.5:1 (WCAG AA) in every theme.
func contrastPairs(_ t: Theme) -> [(String, UInt32, UInt32)] {
  [("tx/bg", t.tx, t.bg), ("tx/card", t.tx, t.card), ("dim/card", t.dim, t.card), ("muted/bg", t.muted, t.bg), ("muted/card", t.muted, t.card),
   ("accent/bg", t.accent, t.bg), ("accent/card", t.accent, t.card), ("onAccent/accent", t.onAccent, t.accent), ("onOk/ok", t.onOk, t.ok),
   ("okTx/card", t.okTx, t.card), ("warn/card", t.warn, t.card), ("warn/bg", t.warn, t.bg), ("badTx/card", t.badTx, t.card), ("badTx/bg", t.badTx, t.bg),
   ("onBad/bad", t.onBad, t.bad), ("onScope/office", t.onScope, t.office), ("onScope/personal", t.onScope, t.personal), ("keyTx/keyBg", t.keyTx, t.keyBg),
   ("tx/alertCard", t.tx, t.alertCard), ("muted/alertCard", t.muted, t.alertCard), ("badTx/alertCard", t.badTx, t.alertCard),
   ("tx/reviewCard", t.tx, t.reviewCard), ("muted/reviewCard", t.muted, t.reviewCard), ("dim/reviewCard", t.dim, t.reviewCard)]
}

var orange: Color { rgb(T.accent) }
var bg: Color { rgb(T.bg) }
var card: Color { rgb(T.card) }
var line: Color { rgb(T.line) }
var dim: Color { rgb(T.dim) }
var faint: Color { rgb(T.muted) }
var okC: Color { rgb(T.ok) }
var okTx: Color { rgb(T.okTx) }
var warnC: Color { rgb(T.warn) }
var badC: Color { rgb(T.badTx) }
var officeC: Color { rgb(T.office) }
var personalC: Color { rgb(T.personal) }
var txC: Color { rgb(T.tx) }
/// Type scale: body text never below 13 pt (owner rule); only badges use 11 pt.
func f(_ d: CGFloat = 0, _ w: Font.Weight = .regular, mono: Bool = false) -> Font {
  let size = d <= -4 ? 11 : max(13, T.base + d)
  return .system(size: size, weight: w, design: mono || T.mono ? .monospaced : T.body)
}
func ft(_ d: CGFloat = 0) -> Font { .system(size: max(14, T.base + 1 + d), weight: .bold, design: T.title) }
func usageColor(_ p: Double?) -> Color { guard let p = p else { return faint }; return p >= 90 ? rgb(T.bad) : p >= 60 ? warnC : okC }
/// Project colour, optionally darkened (multiplied toward black) so white text on it keeps AA contrast.
func hex(_ s: String?, darken k: Double = 1) -> Color {
  guard let s = s, s.hasPrefix("#"), s.count == 7, let v = Int(s.dropFirst(), radix: 16) else { return orange }
  return Color(red: Double((v >> 16) & 255) / 255 * k, green: Double((v >> 8) & 255) / 255 * k, blue: Double(v & 255) / 255 * k)
}

struct Bar: View {
  let value: Double; let color: Color; var height: CGFloat = 7
  var body: some View {
    GeometryReader { g in
      ZStack(alignment: .leading) {
        Capsule().fill(line)
        Capsule().fill(color).frame(width: max(height, g.size.width * min(1, max(0, value))))
      }
    }.frame(height: height)
  }
}
struct Badge: View {
  let text: String; let color: Color; var fg: Color = rgb(T.onAccent)
  var body: some View { Text(text).font(f(-4, .heavy)).padding(.horizontal, 7).padding(.vertical, 3).background(color).foregroundColor(fg).clipShape(Capsule()) }
}
struct ScopeBadge: View {
  let scope: String?; var short = false
  var body: some View {
    let o = scope == "office"
    Badge(text: short ? (o ? "O" : scope == "personal" ? "P" : "?") : (o ? "OFFICE" : scope == "personal" ? "PERSONAL" : "UNTAGGED"),
          color: o ? officeC : scope == "personal" ? personalC : rgb(T.bad), fg: scope == nil ? rgb(T.onBad) : rgb(T.onScope))
  }
}
struct NumChip: View {
  let text: String
  var body: some View {
    Text(text).font(f(-1, .heavy, mono: true)).padding(.horizontal, 6).padding(.vertical, 2).background(orange).foregroundColor(rgb(T.onAccent))
      .clipShape(RoundedRectangle(cornerRadius: min(5, T.radius)))
  }
}
struct Section<Content: View>: View {
  let title: String; let trailing: String; let content: Content
  init(_ title: String, _ trailing: String = "", @ViewBuilder content: () -> Content) { self.title = title; self.trailing = trailing; self.content = content() }
  var body: some View {
    VStack(alignment: .leading, spacing: T.gap * 0.75) {
      HStack(alignment: .firstTextBaseline) { Text(title).font(f(-1, .heavy)).foregroundColor(orange).tracking(1.2); Spacer(); Text(trailing).font(f(-1, mono: true)).foregroundColor(faint).multilineTextAlignment(.trailing) }
      content
    }
  }
}
enum KeyRole { case plain, go, warn, danger }
struct Key: View {
  let title: String; var role: KeyRole = .plain; var disabled = false; let action: () -> Void
  var colors: (Color, Color) {
    switch role {
    case .plain: return (rgb(T.keyBg), rgb(T.keyTx))
    case .go: return (okC, rgb(T.onOk))
    case .warn: return (orange, rgb(T.onAccent))
    case .danger: return (rgb(T.bad), rgb(T.onBad))
    }
  }
  var body: some View {
    Button(action: action) {
      Text(title).font(f(-1, .bold)).padding(.horizontal, 12).padding(.vertical, 6).frame(minHeight: 28)
        .background(colors.0).foregroundColor(colors.1).clipShape(RoundedRectangle(cornerRadius: min(8, T.radius)))
    }.buttonStyle(.plain).disabled(disabled).opacity(disabled ? 0.45 : 1)
  }
}
/// One line of text that never hides content silently: truncated text always carries a tooltip.
func clip(_ s: String, _ font: Font, _ color: Color = txC, lines: Int = 1) -> some View {
  Text(s).font(font).foregroundColor(color).lineLimit(lines).truncationMode(.tail).help(s)
}

// MARK: - Accounts and lanes

struct LaneRow: View {
  let l: Lane; let a: Account; let projects: [String: String]
  var body: some View {
    HStack(alignment: .top, spacing: 8) {
      Text("\(l.worker)·\(l.lane)").font(f(-1, .heavy, mono: true)).frame(width: 44, alignment: .leading)
      Text(text).font(f(-1, mono: true)).foregroundColor(color).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 0)
    }.padding(.vertical, 4).padding(.horizontal, 8)
      .background(RoundedRectangle(cornerRadius: 7).strokeBorder(color.opacity(l.state == "running" ? 0 : 0.6), style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
  }
  var proj: String { l.project.map { "\(projects[$0] ?? $0) · " } ?? "" }
  var num: String { l.task.map { String($0.split(separator: "-").first ?? "") } ?? "" }
  var text: String {
    switch l.state {
    case "running":
      let step = l.stepCount.map { " · \(l.stepIndex ?? 0)/\($0)" } ?? ""
      let eta = l.etaMin.map { " ~\(Int($0))m" } ?? ""
      let quiet = (l.lastActiveSec ?? 0) > 600 ? " · quiet \(dur(l.lastActiveSec))" : ""
      return "▶ \(proj)\(num) \(l.title ?? "")\(step)\(eta)\(quiet)"
    case "cooling": return "⏸ \(proj)\(num) \(l.title ?? "") · resumes \(hhmm(l.resumeAt ?? a.resets5)), same session"
    default: return freeLaneText(a).text
    }
  }
  var color: Color {
    switch l.state {
    case "running": return (l.lastActiveSec ?? 0) > 600 ? warnC : txC
    case "cooling": return badC
    default:
      switch freeLaneText(a).kind { case "spare": return orange; case "cool": return badC; default: return faint }
    }
  }
}

struct AccountBlock: View {
  let a: Account; let now: Int; let projects: [String: String]; var allowed = true; var reason: String? = nil
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        if !allowed { Text("🔒") }
        Text(a.label).font(f(0, .bold))
        ScopeBadge(scope: a.scope)
        Spacer()
        Text(quotaText(a, now: now)).font(f(-1, .semibold, mono: true)).foregroundColor(a.state == "cooling" ? badC : dim).multilineTextAlignment(.trailing).fixedSize(horizontal: false, vertical: true)
      }
      Bar(value: (a.state == "cooling" ? 100 : (a.pct5 ?? 0)) / 100, color: a.state == "cooling" ? badC : usageColor(a.pct5))
      if let r = reason, !allowed { Text(r).font(f(-1, .bold)).foregroundColor(badC) }
      ForEach(a.lanes ?? []) { LaneRow(l: $0, a: a, projects: projects) }
    }.opacity(allowed ? 1 : 0.55)
  }
}

// MARK: - Tasks

struct TaskRow: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM; let now: Int
  @State private var open = false
  var icon: String {
    ["running": "▶", "starting": "▶", "queued": "⏳", "ready": "⏳", "blocked": "✋", "limit_paused": "⏸", "hung": "!", "review": "▣",
     "merging": "⇄", "done": "✓", "failed": "✗", "draft": "✎", "changes_requested": "↺"][t.state] ?? "·"
  }
  var line: String {
    var b: [String] = [t.state.replacingOccurrences(of: "_", with: " "), "\(t.priority ?? "") \(t.size ?? "")"]
    if t.kind == "pilot" { b.append("PILOT") }
    if let w = t.worker { b.append(w) }
    if t.state == "limit_paused", let r = t.resumeAt { b.append("resumes \(hhmm(r)) (in \(dur(r - now)))") }
    else if let s = t.stateReason { b.append(s) }
    return b.joined(separator: " · ")
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        Text(icon).font(f(0)).frame(width: 16)
        NumChip(text: t.numText)
        clip(t.title, ft())
        Spacer()
        if let e = t.etaFinish, t.state == "running" { Text("ETA \(hhmm(e))").font(f(-1, mono: true)).foregroundColor(faint) }
      }.contentShape(Rectangle()).onTapGesture { open.toggle() }
      Text(line).font(f(-1)).foregroundColor(faint).lineLimit(open ? nil : 1).help(line)
      if let p = t.progress, t.state == "running" { Bar(value: p, color: orange, height: 5) }
      if open { detail }
    }.padding(T.pad).background(RoundedRectangle(cornerRadius: T.radius).fill(card))
  }
  @ViewBuilder var detail: some View {
    if let s = t.summary, !s.isEmpty { Text(s).font(f(-1)).foregroundColor(dim).fixedSize(horizontal: false, vertical: true) }
    if let st = t.step { Text("◉ \(st.index)/\(st.count) \(st.title)\(st.etaMin.map { " · ~\(Int($0))m" } ?? "")").font(f(-1)).foregroundColor(orange) }
    ForEach(t.metrics ?? [], id: \.self) { m in
      VStack(alignment: .leading, spacing: 3) {
        HStack {
          Text(m.label).font(f(-1)).foregroundColor(m.kind == "bad" && m.done > 0 ? badC : txC)
          Spacer()
          Text("\(Int(m.done))\(m.total.map { " / \(Int($0))" } ?? "")\(m.rate.map { " · ≈\(String(format: $0 < 10 ? "%.1f" : "%.0f", $0))/min" } ?? "")\(m.left.map { " · ~\(dur(Int($0 * 60))) left" } ?? "")").font(f(-1, .bold, mono: true))
        }
        if let tot = m.total, tot > 0 { Bar(value: m.done / tot, color: okC, height: 5) }
      }
    }
    ForEach(t.warnings ?? [], id: \.self) { Text("⚠ \($0)").font(f(-1, .bold)).foregroundColor(warnC) }
    if let q = t.question {
      Text("? \(q.text)").font(f(-1, .bold)).foregroundColor(warnC)
      HStack { ForEach(q.options ?? [], id: \.self) { o in Key(title: o, role: o == q.default ? .warn : .plain) { store.act(nil, t.id, actionBody("answer", rev: t.rev, note: o)) } } }
    }
    TaskActions(t: t)
  }
}

struct TaskActions: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM
  @State private var armCancel = false
  var body: some View {
    HStack(spacing: 8) {
      if ["running", "starting", "queued", "ready"].contains(t.state) { Key(title: "Pause") { store.act(nil, t.id, actionBody("pause", rev: t.rev)) } }
      if ["blocked", "limit_paused"].contains(t.state) && t.question == nil { Key(title: "Resume", role: .go) { store.act(nil, t.id, actionBody("resume", rev: t.rev)) } }
      if ["hung", "failed", "changes_requested"].contains(t.state) { Key(title: "Retry", role: .warn) { store.act(nil, t.id, actionBody("retry", rev: t.rev)) } }
      if !["done", "cancelled", "merging", "review"].contains(t.state) {
        Key(title: "Reassign…") { store.openReassign(t) }
        Key(title: "▲", disabled: t.priority == "P0") { store.act(nil, t.id, actionBody("priority", rev: t.rev, priority: bump(t.priority, up: true))) }
        Key(title: "▼", disabled: t.priority == "P3") { store.act(nil, t.id, actionBody("priority", rev: t.rev, priority: bump(t.priority, up: false))) }
        Spacer()
        // Two-step confirm in place of the phone's hold-to-cancel.
        Key(title: armCancel ? "Confirm cancel" : "Cancel", role: armCancel ? .danger : .plain) {
          if armCancel { store.act(nil, t.id, actionBody("cancel", rev: t.rev)) } else { armCancel = true }
        }
      }
    }
  }
}

struct ReviewCard: View {
  @EnvironmentObject var store: DeckStore
  let r: ReviewVM
  @State private var note = ""
  @State private var writing = false
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        Text(r.task.state == "merging" ? "⇄" : "▣"); NumChip(text: r.task.numText)
        if r.pilot == true { Badge(text: "PILOT", color: orange) }
        clip(r.task.title, ft())
        Spacer()
        Text("\(r.worker ?? "") \(r.account ?? "")").font(f(-1)).foregroundColor(faint)
      }
      Text(r.summary).font(f(-1)).foregroundColor(dim).fixedSize(horizontal: false, vertical: true)
      let dw = r.doneWhen ?? []
      Text("\(dw.filter { $0.met }.count)/\(dw.count) done-when ✓ · tests \((r.tests ?? []).reduce(0) { $0 + ($1.passed ?? 0) }) · waiting \(dur(r.waitingSec))\(r.unlocks?.isEmpty == false ? " · approving starts \(r.unlocks!.map { String($0.split(separator: "-").first ?? "") }.joined(separator: ", "))" : "")")
        .font(f(-1)).foregroundColor(faint)
      ForEach(dw, id: \.self) { d in Text("\(d.met ? "✓" : "✗") \(d.item) — \(d.evidence)").font(f(-1)).foregroundColor(d.met ? okTx : badC).fixedSize(horizontal: false, vertical: true) }
      if let u = r.git?.uncommitted, u > 0 { Text("⚠ \(u) uncommitted change\(u > 1 ? "s" : "") will not be merged").font(f(-1, .bold)).foregroundColor(warnC) }
      ForEach(r.risks ?? [], id: \.self) { Text("• \($0)").font(f(-1)).foregroundColor(dim) }
      ForEach(Array((r.decisionsNeeded ?? []).enumerated()), id: \.offset) { i, d in
        Text(d.question).font(f(-1, .semibold))
        HStack { ForEach(d.options ?? [], id: \.self) { o in
          Key(title: o + (o == d.default ? " (default)" : ""), role: o == d.default ? .warn : .plain) {
            store.act(nil, r.task.id, actionBody("answer", rev: r.task.rev, decision: i, option: o))
          }
        } }
      }
      if writing {
        TextField("What should change?", text: $note).textFieldStyle(.roundedBorder).font(f(-1))
      }
      if r.task.state != "merging" {
        HStack {
          Key(title: "Approve ✓", role: .go) { store.act(nil, r.task.id, actionBody("approve", rev: r.task.rev)) }
          Key(title: writing ? "Send changes" : "Changes…", role: .warn) {
            if writing { store.act(nil, r.task.id, actionBody("changes", rev: r.task.rev, note: note)); writing = false } else { writing = true }
          }
        }
      }
    }.padding(T.pad).background(RoundedRectangle(cornerRadius: T.radius).fill(Color(red: 0.2, green: 0.15, blue: 0.1)))
  }
}

struct EventRow: View {
  let e: DeckEvent
  var body: some View {
    HStack(alignment: .top, spacing: 6) {
      Text(hhmm(e.ts)).font(f(-1, mono: true)).foregroundColor(faint)
      Text(e.severity == "alert" ? "!" : e.severity == "warn" ? "⚠" : e.severity == "action" ? "▣" : "·")
      Text(e.text).font(f(-1)).foregroundColor(e.severity == "alert" || e.severity == "warn" ? badC : dim).fixedSize(horizontal: false, vertical: true)
    }
  }
}

// MARK: - Reassign sheet: disallowed rows greyed with lock + server reason, never hidden, not clickable.

struct ReassignSheet: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM; let rows: [Eligible]
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack { NumChip(text: t.numText); Text("Reassign · \(t.title)").font(f(0, .bold)); Spacer(); Key(title: "Close") { store.reassign = nil } }
      ForEach(rows) { w in
        Button(action: { store.act(nil, t.id, actionBody("reassign", rev: t.rev, worker: w.worker)); store.reassign = nil }) {
          HStack {
            Text(w.allowed ? "🟢" : w.kind == "cooling" ? "⏸" : "🔒")
            VStack(alignment: .leading, spacing: 2) {
              Text("\(w.worker) · \(w.label)").font(f(-1, .bold))
              Text(w.allowed ? "\(w.scope == "office" ? "OFFICE" : "PERSONAL")\(w.detail.map { " · \($0)" } ?? "")" : (w.reason ?? "not allowed"))
                .font(f(-1, w.allowed ? .regular : .bold)).foregroundColor(w.allowed ? faint : badC)
            }
            Spacer()
          }.padding(T.pad).background(RoundedRectangle(cornerRadius: T.radius).fill(w.allowed ? card : rgb(T.bg)))
        }.buttonStyle(.plain).disabled(!w.allowed).opacity(w.allowed ? 1 : 0.55)
      }
      Text("Greyed rows can't be clicked. The CLI and API refuse them too and log an alert.").font(f(-1)).foregroundColor(faint)
    }.padding(T.pad).background(RoundedRectangle(cornerRadius: T.radius).fill(card)).overlay(RoundedRectangle(cornerRadius: T.radius).stroke(orange, lineWidth: 1))
  }
}

// MARK: - Panel

/// Re-identified when the theme changes, so every view re-reads the active tokens.
struct RootView: View {
  @ObservedObject var store: DeckStore
  var body: some View {
    ScrollView { PanelView().environmentObject(store) }.background(bg).padding(.top, 14)
      .id("\(store.themeChoice)-\(store.systemDark)")
  }
}

struct PanelView: View {
  @EnvironmentObject var store: DeckStore
  var body: some View {
    VStack(alignment: .leading, spacing: T.gap + 4) {
      header
      if let o = store.overview {
        if store.collapsed { collapsedBar(o) } else {
          chips(o)
          if let r = store.reassign { ReassignSheet(t: r.task, rows: r.rows) }
          if store.selected == nil { overview(o) } else if let p = store.project { projectPage(p, o) } else { Text("Loading project…").foregroundColor(faint) }
        }
      } else {
        Text(store.offline ? "Can't reach deckd at \(store.base?.absoluteString ?? "?") — retrying every 2 s" : "Loading…").foregroundColor(faint)
      }
      if let t = store.toast { Text(t).font(f(-1, .semibold)).foregroundColor(rgb(T.keyTx)).padding(T.pad).frame(maxWidth: .infinity, alignment: .leading).background(rgb(T.keyBg)).clipShape(RoundedRectangle(cornerRadius: T.radius)) }
    }.padding(16).foregroundColor(txC).lineSpacing(T.base * 0.25)
  }

  var header: some View {
    let ib = store.overview?.inbox
    return VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        Image(systemName: "gearshape.2.fill").foregroundColor(orange).font(f(4))
        Text(store.project?.project.short ?? store.project?.project.title ?? "Agent Deck").font(ft(4))
        if let s = store.project?.project.scope { ScopeBadge(scope: s) }
        Spacer()
        Text(clockSecFmt.string(from: Date(timeIntervalSince1970: TimeInterval(store.now)))).font(f(-1, mono: true)).foregroundColor(faint)
        // quick toggle + picker (Deck, Mission Control, Paper light, Paper dark, Auto); the choice is saved in UserDefaults
        Button(action: { store.cycleTheme() }) { Image(systemName: "circle.lefthalf.filled").font(f(3)).foregroundColor(orange) }
          .buttonStyle(.plain).help("Switch theme (now: \(THEME_CHOICES.first { $0.0 == store.themeChoice }?.1 ?? "Deck"))")
        Menu {
          ForEach(THEME_CHOICES, id: \.0) { c in
            Button(action: { store.themeChoice = c.0 }) { Text((store.themeChoice == c.0 ? "✓ " : "   ") + c.1) }
          }
        } label: { Image(systemName: "paintpalette") }.menuStyle(.borderlessButton).fixedSize().help("Theme")
        Button(action: { store.collapsed.toggle() }) {
          Image(systemName: store.collapsed ? "chevron.down.circle.fill" : "chevron.up.circle.fill").font(f(6)).foregroundColor(orange)
        }.buttonStyle(.plain).help(store.collapsed ? "Expand" : "Collapse")
      }
      HStack(spacing: 8) {
        if (ib?.review ?? 0) > 0 { Badge(text: "\(ib!.review!) review", color: orange) }
        if (ib?.questions ?? 0) > 0 { Badge(text: "\(ib!.questions!) ?", color: rgb(T.keyBg), fg: rgb(T.keyTx)) }
        if (ib?.alerts ?? 0) > 0 { Badge(text: "\(ib!.alerts!) !", color: rgb(T.bad), fg: rgb(T.onBad)) }
        if store.offline { Badge(text: "OFFLINE · last update \(hhmm(store.lastUpdate))", color: rgb(T.bad), fg: rgb(T.onBad)) }
        else if store.fixtures != nil { Badge(text: "FIXTURES", color: rgb(T.keyBg), fg: rgb(T.keyTx)) }
      }
    }
  }

  func chips(_ o: OverviewVM) -> some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        chip("All", on: store.selected == nil, color: orange, fg: rgb(T.onAccent)) { store.selected = nil }
        ForEach(o.projects) { p in
          chip("\(p.icon ?? "") \(p.name) \(p.scope == "office" ? "O" : "P")\(p.enabled == false ? " · paused" : "")", on: store.selected == p.id, color: hex(p.color, darken: 0.72)) {
            if p.enabled != false { store.selected = p.id }
          }.opacity(p.enabled == false ? 0.45 : 1)
        }
      }
    }
  }
  func chip(_ s: String, on: Bool, color: Color, fg: Color = .white, _ tap: @escaping () -> Void) -> some View {
    Button(action: tap) {
      Text(s).font(f(-1, .semibold)).padding(.horizontal, 12).padding(.vertical, 6)
        .background(on ? color : rgb(T.keyBg)).foregroundColor(on ? fg : rgb(T.keyTx)).clipShape(Capsule())
    }.buttonStyle(.plain)
  }

  func collapsedBar(_ o: OverviewVM) -> some View {
    let running = o.machine?.running ?? 0
    let cooling = o.accounts.first { $0.state == "cooling" }
    let spare = o.accounts.first { $0.capacity?.spare == true && $0.capacity?.why == nil }
    return HStack(spacing: 12) {
      Text("▶\(running) running")
      Text("▣\(o.inbox?.review ?? 0) review").foregroundColor(orange)
      Text("! \(o.inbox?.alerts ?? 0)").foregroundColor(badC)
      Text("lanes \(running)/\(o.machine?.lanesTotal ?? 0)")
      if let c = cooling { Text("\(c.label) LIMIT \(hhmm(c.resets5))").foregroundColor(badC) }
      if let s = spare { Text("⚡ \(s.label) spare").foregroundColor(orange) }
    }.font(f(-1, .semibold, mono: true)).lineLimit(1)
  }

  var projectNames: [String: String] { Dictionary(uniqueKeysWithValues: (store.overview?.projects ?? []).map { ($0.id, $0.name) }) }

  @ViewBuilder func overview(_ o: OverviewVM) -> some View {
    let needs = o.needsYou ?? (o.alerts ?? []).map { NeedsItem(kind: "alert", project: $0.project, task: $0.task, num: nil, title: $0.text, text: $0.type, rev: nil, options: nil, eventId: $0.id) }
    Section("NEEDS YOU · \(needs.count)", "newest first") {
      if needs.isEmpty { Text("Nothing needs you").font(f(-1)).foregroundColor(faint) }
      ForEach(needs) { n in NeedsRow(n: n) }
    }
    Section("ACCOUNTS · PARALLEL LANES", "\(o.machine?.running ?? 0) / \(o.machine?.maxParallelTotal ?? 0) jobs · RAM \(o.machine?.freeRamGb.map { String(format: "%.1f", $0) } ?? "—") GB free") {
      ForEach(o.accounts) { AccountBlock(a: $0, now: store.now, projects: projectNames) }
    }
    if let b = o.backlog {
      HStack { Text("\(b.low == true ? "▲ " : "")BACKLOG").font(f(-1, .heavy)); Text("\(b.eligible ?? 0) ready / target \(b.target ?? 0)").font(f(-1, .bold)).foregroundColor(orange)
        Text(b.hint ?? "").font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true) }
    }
    Section("PROJECTS") {
      ForEach(o.projects) { p in
        Button(action: { if p.enabled != false { store.selected = p.id } }) {
          HStack(spacing: 8) {
            Text(p.icon ?? "◆"); Text(p.name).font(f(0, .bold)); ScopeBadge(scope: p.scope, short: true)
            clip(p.headline ?? "", f(-1), faint)
            Spacer()
            Text(counts(p.counts)).font(f(-1, .bold, mono: true))
          }.padding(8).background(RoundedRectangle(cornerRadius: T.radius).fill(card)).opacity(p.enabled == false ? 0.45 : 1)
        }.buttonStyle(.plain)
      }
    }
  }
  func counts(_ c: Counts?) -> String {
    guard let c = c else { return "" }
    return [("▶", c.running), ("⏳", c.queued), ("▣", c.review), ("✋", c.blocked), ("⏸", c.limit_paused), ("!", c.hung), ("✓", c.doneToday)]
      .compactMap { ($0.1 ?? 0) > 0 ? "\($0.0)\($0.1!)" : nil }.joined(separator: " ")
  }

  @ViewBuilder func projectPage(_ p: ProjectVM, _ o: OverviewVM) -> some View {
    Picker("", selection: $store.tab) {
      Text("Workers").tag("workers"); Text("Board").tag("board"); Text("Review \(p.inbox?.count ?? 0)").tag("review")
      Text("Alerts \(p.alerts?.count ?? 0)").tag("alerts"); Text("Log").tag("timeline"); Text("Repo").tag("repo")
    }.pickerStyle(.segmented).labelsHidden()
    switch store.tab {
    case "workers":
      Section("WORKERS · PARALLEL LANES") {
        ForEach(p.workers ?? []) { w in AccountBlock(a: w.asAccount(store.overview?.accounts ?? []), now: store.now, projects: projectNames, allowed: w.allowed != false, reason: w.reason) }
      }
    case "review":
      Section("REVIEW INBOX · \(p.inbox?.count ?? 0)") {
        if (p.inbox ?? []).isEmpty { Text("Inbox empty").foregroundColor(faint) }
        ForEach(p.inbox ?? []) { ReviewCard(r: $0) }
      }
    case "alerts":
      Section("ALERTS") { ForEach(p.alerts ?? []) { EventRow(e: $0) } }
    case "timeline":
      Section("TIMELINE") { ForEach((p.timeline ?? []).sorted { $0.ts > $1.ts }) { EventRow(e: $0) } }
    case "repo":
      repo(p)
    default:
      board(p)
    }
  }

  @ViewBuilder func board(_ p: ProjectVM) -> some View {
    let cols: [(String, [TaskVM]?)] = [("RUNNING", p.board.running), ("REVIEW", p.board.review), ("NEEDS YOU", p.board.needsYou),
                                        ("UP NEXT", p.board.upNext), ("BACKLOG", p.board.backlog)]
    ForEach(cols, id: \.0) { col in
      if let items = col.1, !items.isEmpty {
        Section("\(col.0) · \(items.count)") { ForEach(items) { TaskRow(t: $0, now: store.now) } }
      }
    }
    if let d = p.board.doneToday, !d.isEmpty {
      Section("DONE TODAY · \(d.count)") { Text(d.map { "✓ \($0.numText) \($0.title)" }.joined(separator: "   ")).font(f(-1)).foregroundColor(faint) }
    }
  }

  @ViewBuilder func repo(_ p: ProjectVM) -> some View {
    Section("DOMAINS & MERGE") {
      ForEach(p.domains ?? []) { d in
        HStack {
          Text(d.name).font(f(-1, mono: true)); Spacer()
          Text(d.holder.map { "\($0.split(separator: "-").first ?? "") since \(hhmm(d.since))" } ?? (d.note ?? "free")).font(f(-1)).foregroundColor(dim)
          if let w = d.waiting, !w.isEmpty { Text("· \(w.map { String($0.split(separator: "-").first ?? "") }.joined(separator: ", ")) waiting").font(f(-1)).foregroundColor(warnC) }
        }
      }
      if let m = p.merge?.active { Text("merge · \(m.task.split(separator: "-").first ?? "") (gates \(m.gatesDone ?? 0)/\(m.gatesTotal ?? 0))").font(f(-1, mono: true)) }
    }
    Section("REPO & HEALTH") {
      Text(p.project.repo?.commit ?? "—").font(f(-1, mono: true)).foregroundColor(dim)
      if (p.project.repo?.changes ?? []).isEmpty { Text("working tree clean").font(f(-1)).foregroundColor(faint) }
      ForEach(p.project.repo?.changes ?? []) { c in HStack { Text(c.area).font(f(-1, mono: true)); Spacer(); Text("\(c.files) files changed").font(f(-1)).foregroundColor(warnC) } }
      HStack(spacing: 14) {
        ForEach(p.project.health ?? []) { h in Text("\(h.isUp == true ? "●" : h.isUp == false ? "✗" : "?") \(h.label)\(h.isUp == nil ? " (not checked)" : "")").font(f(-1)).foregroundColor(h.isUp == true ? okTx : h.isUp == false ? badC : faint) }
      }
    }
  }
}

struct NeedsRow: View {
  @EnvironmentObject var store: DeckStore
  let n: NeedsItem
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        Text(n.kind == "review" ? "▣" : n.kind == "question" ? "?" : "!").foregroundColor(n.kind == "alert" ? badC : orange)
        if let t = n.task { NumChip(text: n.num.map(String.init) ?? String(t.split(separator: "-").first ?? "")) }
        clip(n.title, ft())
        Spacer()
      }
      Text(n.text).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
      HStack(spacing: 8) {
        if n.kind == "review", let t = n.task { Key(title: "Approve", role: .go) { store.act(n.project, t, actionBody("approve", rev: n.rev)) } }
        if n.kind == "question", let t = n.task {
          ForEach(n.options ?? [], id: \.self) { o in Key(title: o) { store.act(n.project, t, actionBody("answer", rev: n.rev, note: o)) } }
        }
        Key(title: "Open") { store.selected = n.project; store.tab = n.kind == "review" ? "review" : n.kind == "alert" ? "alerts" : "board" }
      }
    }.padding(T.pad).background(RoundedRectangle(cornerRadius: T.radius).fill(n.kind == "alert" ? Color(red: 0.25, green: 0.1, blue: 0.1) : card))
  }
}

// MARK: - Entry point

func arg(_ name: String) -> String? {
  let a = CommandLine.arguments
  guard let i = a.firstIndex(of: name), i + 1 < a.count else { return nil }
  return a[i + 1]
}
let stateDirRaw: String = arg("--state-dir") ?? ProcessInfo.processInfo.environment["AGENT_DECK_STATE"] ?? "~/.local/share/agent-deck"
let stateDir = URL(fileURLWithPath: (stateDirRaw as NSString).expandingTildeInPath)

/// Decode every fixture and run the shared helpers; prints a summary. Used by start.sh/CI as the "decode test".
func checkFixtures(_ dir: URL) -> Bool {
  var ok = true
  func check(_ cond: Bool, _ what: String) { print("\(cond ? "ok  " : "FAIL") \(what)"); if !cond { ok = false } }
  func load<T: Decodable>(_ n: String, _ t: T.Type) -> T? {
    do { return try JSONDecoder().decode(T.self, from: Data(contentsOf: dir.appendingPathComponent(n))) } catch { print("FAIL decode \(n): \(error)"); ok = false; return nil }
  }
  if let o = load("overview.json", OverviewVM.self) {
    check(o.projects.count == 3 && o.accounts.count == 5, "overview: \(o.projects.count) projects, \(o.accounts.count) accounts")
    check(o.accounts.contains { $0.scope == "office" && $0.pct5 != nil }, "office account present with usage")
    check(o.accounts.contains { ($0.lanes ?? []).filter { $0.state == "running" }.count >= 2 }, "parallel lanes on one account")
    if let office = o.accounts.first(where: { $0.id == "claude-office" }) { check(freeLaneText(office).kind == "policy", "office free lane explained by policy") }
    if let a = o.accounts.first(where: { $0.id == "codex-a" }) { check(freeLaneText(a).kind == "spare", "codex-a free lane is spare: \(freeLaneText(a).text)") }
    if let w = o.accounts.first(where: { $0.state == "cooling" }) { check(quotaText(w, now: o.time).hasPrefix("LIMIT"), "cooling account shows LIMIT countdown") }
  }
  for n in (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? [] where n.hasPrefix("project") {
    if let p = load(n, ProjectVM.self) {
      let tasks = [p.board.backlog, p.board.upNext, p.board.running, p.board.review, p.board.needsYou, p.board.doneToday].compactMap { $0 }.joined()
      check(!tasks.isEmpty, "\(n): \(p.project.id), \(tasks.count) tasks, \(p.workers?.count ?? 0) workers, \(p.inbox?.count ?? 0) reviews")
      check(tasks.allSatisfy { $0.rev != nil }, "\(n): every TaskVM carries rev")
    }
  }
  if let e = load("eligible.json", [Eligible].self) {
    check(e.filter { !$0.allowed }.allSatisfy { ($0.reason ?? "").count > 5 }, "eligible: disallowed rows have reasons")
    check(e.contains { $0.scope == "office" && !$0.allowed }, "eligible: office account refused for a personal project")
  }
  _ = load("review.json", ReviewVM.self)
  check(actionBody("approve", rev: 21).map { NSDictionary(dictionary: $0) == ["action": "approve", "rev": 21] } == true, "actionBody approve")
  check(actionBody("changes", rev: 3) == nil, "actionBody changes needs a note")
  check(actionBody("reassign", rev: 3, worker: "W2")?["worker"] as? String == "W2", "actionBody reassign")
  check(actionBody("priority", rev: 3, priority: "P9") == nil, "actionBody priority range")
  check(actionBody("answer", rev: 5, decision: 0, option: "skip")?["option"] as? String == "skip", "actionBody answer decision")
  check(actionBody("approve", rev: nil).map { NSDictionary(dictionary: $0) == ["action": "approve"] } == true, "actionBody rev optional (B1)")
  check(bump("P2", up: true) == "P1" && bump("P0", up: true) == "P0" && bump("P3", up: false) == "P3", "priority bump clamps")
  check(dur(540) == "9m" && dur(12000) == "3h20" && dur(45) == "45s", "dur formatting")
  check(THEME_CHOICES.map { $0.1 } == ["Deck", "Mission Control", "Paper light", "Paper dark", "Auto"], "theme picker lists four themes plus Auto")
  check(resolveTheme("auto", systemDark: true).id == "paper-dark" && resolveTheme("auto", systemDark: false).id == "paper-light", "Auto follows the macOS appearance")
  check(resolveTheme("nonsense", systemDark: false).id == "deck", "unknown theme falls back to Deck")
  for t in THEMES {
    let bad = contrastPairs(t).filter { contrast($0.1, $0.2) < 4.5 }.map { "\($0.0)=\(String(format: "%.2f", contrast($0.1, $0.2)))" }
    check(bad.isEmpty, "WCAG AA contrast, theme \(t.name)\(bad.isEmpty ? "" : ": " + bad.joined(separator: ", "))")
    check(max(13, t.base - 1) >= 13, "theme \(t.name): body text ≥ 13 pt")
  }
  return ok
}

if let dir = arg("--check-fixtures") { exit(checkFixtures(URL(fileURLWithPath: dir)) ? 0 : 1) }

let fixturesDir = arg("--fixtures").map { URL(fileURLWithPath: $0) }
let token = (try? String(contentsOf: stateDir.appendingPathComponent("token"), encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
let store = DeckStore(base: fixturesDir == nil ? URL(string: (arg("--url") ?? "http://127.0.0.1:7788") + "/") : nil, fixtures: fixturesDir, token: token)

let width: CGFloat = 520
/// --snapshot out.png [--project id] [--tab t]: render the panel offscreen to a PNG (fixture review without a screen).
if let t = arg("--theme") { store.themeChoice = t }
if let out = arg("--snapshot") {
  if let p = arg("--project") { store.selected = p }
  if let t = arg("--tab") { store.tab = t }
  let png: Data? = MainActor.assumeIsolated {
    let r = ImageRenderer(content: PanelView().environmentObject(store).frame(width: width).background(bg))
    r.scale = 2
    return r.nsImage?.tiffRepresentation.flatMap { NSBitmapImageRep(data: $0)?.representation(using: .png, properties: [:]) }
  }
  guard let data = png else { exit(1) }
  try? data.write(to: URL(fileURLWithPath: out)); exit(0)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
let panel = NSPanel(contentRect: NSRect(x: screen.maxX - width, y: screen.minY, width: width, height: screen.height),
                    styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
panel.titleVisibility = .hidden
panel.titlebarAppearsTransparent = true
panel.level = .floating
panel.isFloatingPanel = true
panel.hidesOnDeactivate = false
panel.isMovableByWindowBackground = true
panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
func paintWindow() {
  panel.appearance = NSAppearance(named: T.dark ? .darkAqua : .aqua)
  panel.backgroundColor = NSColor(red: CGFloat((T.bg >> 16) & 255) / 255, green: CGFloat((T.bg >> 8) & 255) / 255, blue: CGFloat(T.bg & 255) / 255, alpha: 1)
}
store.onTheme = paintWindow
// Auto follows the macOS appearance (checked against the app, which tracks the system setting).
func readSystemDark() -> Bool { NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua }
store.systemDark = readSystemDark()
let appearanceWatch = DistributedNotificationCenter.default().addObserver(forName: Notification.Name("AppleInterfaceThemeChangedNotification"), object: nil, queue: .main) { _ in
  DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { store.systemDark = readSystemDark() }
}
paintWindow()
panel.contentView = NSHostingView(rootView: RootView(store: store))
panel.setFrame(NSRect(x: screen.maxX - width, y: screen.minY, width: width, height: screen.height), display: true)
store.onCollapse = { c in
  let h: CGFloat = c ? 96 : screen.height
  panel.setFrame(NSRect(x: screen.maxX - width, y: screen.maxY - h, width: width, height: h), display: true, animate: true)
}
panel.orderFrontRegardless()
final class Closer: NSObject, NSWindowDelegate { func windowWillClose(_ n: Notification) { NSApp.terminate(nil) } }
let closer = Closer(); panel.delegate = closer
app.run()
