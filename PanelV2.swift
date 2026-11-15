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
  /// Optional (newer deckd): colour override, usage history for the graph, jobs done today, queued pipeline.
  var color: String? = nil, usage: UsageVM? = nil, doneToday: Int? = nil, queued: [QueuedVM]? = nil
}
struct UsagePoint: Decodable, Hashable { let ts: Int, pct5: Double?, pct7: Double? }
struct UsageVM: Decodable, Hashable { let points: [UsagePoint]?, resets: [Int]?, burn5PerHour: Double?, minutesToLimit: Double? }
struct QueuedVM: Decodable, Hashable { let project: String?, task: String, title: String? }
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
                   lanes: (lanes ?? []).isEmpty ? a?.lanes : lanes, color: a?.color, usage: a?.usage, doneToday: a?.doneToday, queued: a?.queued)
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
  @Published var selected: String? = nil { didSet { project = nil; focus = nil; refresh() } }
  /// overview | projects | workers | board | review | alerts | timeline | repo. Each layout maps these to its own tabs.
  @Published var tab = "overview"
  /// Paper board: which column page is showing (0 ready, 1 running, 2 review, 3 blocked).
  @Published var paperCol = 1
  /// Task opened from a compact card (Studio board grid).
  @Published var focus: String?
  /// Workers section: which accounts are expanded (kept across the 2-s refresh).
  @Published var expandedWorkers: Set<String> = []
  @Published var offline = false
  @Published var lastUpdate: Int?
  @Published var toast: String?
  @Published var collapsed = false { didSet { onCollapse?(collapsed) } }
  @Published var reassign: (task: TaskVM, rows: [Eligible])?
  /// Layout (studio | mission | paper) and mode (auto | light | dark) are chosen separately; Auto follows the macOS appearance.
  @Published var layout: String { didSet { prefs?.set(layout, forKey: "deck.layout"); applyTheme() } }
  @Published var mode: String { didSet { prefs?.set(mode, forKey: "deck.mode"); applyTheme() } }
  @Published var systemDark = false { didSet { applyTheme() } }
  var onTheme: (() -> Void)?
  func applyTheme() { T = resolveTheme(layout: layout, mode: mode, systemDark: systemDark); onTheme?() }
  func cycleLayout() { let ids = LAYOUTS.map { $0.0 }; layout = ids[((ids.firstIndex(of: layout) ?? 0) + 1) % ids.count] }
  func cycleMode() { let ids = MODES.map { $0.0 }; mode = ids[((ids.firstIndex(of: mode) ?? 0) + 1) % ids.count] }
  /// nil = do not persist (snapshots).
  let prefs: UserDefaults?
  var onCollapse: ((Bool) -> Void)?
  var fetchedAt = Date()
  let base: URL?, fixtures: URL?, token: String?

  init(base: URL?, fixtures: URL?, token: String?, prefs: UserDefaults?) {
    self.base = base; self.fixtures = fixtures; self.token = token; self.prefs = prefs
    let p = prefs.map(loadPrefs) ?? (layout: "studio", mode: "auto")
    layout = p.layout; mode = p.mode
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
        else if code == 401 || code == 403 { self?.flash("Not authorised (HTTP \(code)): run `deck init`, or check \(stateDir.path)/token") }
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

// MARK: - Themes: three layouts × light/dark = six token sets (mirror web/themes.css). Auto follows the macOS appearance.
//   studio  = docs/v2/mockups/panel.html     dark = the v1 panel look, light = PhoneDeck Studio (warm grey, ink text, orange)
//   mission = docs/v2/mockups/alt-a          dark = original, light = added
//   paper   = docs/v2/mockups/alt-b          light and dark from the mockup

/// A Paper pastel pair: soft background + ink that reads on it (and on cards).
struct Tone { let bg: UInt32, ink: UInt32 }
struct Theme {
  let layout: String, mode: String
  var id: String { "\(layout)-\(mode)" }
  var dark: Bool { mode == "dark" }
  var name: String { "\(LAYOUTS.first { $0.0 == layout }?.1 ?? layout) \(mode)" }
  // colours as 0xRRGGBB so --check-fixtures can verify WCAG AA contrast
  let bg: UInt32, bg2: UInt32, bg3: UInt32, card: UInt32, line: UInt32, rule2: UInt32, tx: UInt32, dim: UInt32, muted: UInt32
  /// accent = fills (with onAccent text); accentTx = accent-coloured text on bg/card.
  let accent: UInt32, onAccent: UInt32, accentTx: UInt32, ok: UInt32, onOk: UInt32, okTx: UInt32, warn: UInt32, bad: UInt32, badTx: UInt32, onBad: UInt32
  let office: UInt32, personal: UInt32, onScope: UInt32, alertCard: UInt32, reviewCard: UInt32, keyBg: UInt32, keyTx: UInt32
  /// Mission LED / text hues (gr cy am rd mg or vi); onHue = text on a filled hue badge.
  let hues: [String: UInt32], onHue: UInt32
  /// Paper pastel pairs (sage sky lilac peach rose butter teal indigo grey).
  let tones: [String: Tone]
  let body: Font.Design, mono: Bool
  let base: CGFloat, radius: CGFloat, gap: CGFloat, pad: CGFloat
}
let LAYOUTS: [(String, String)] = [("studio", "Studio"), ("mission", "Mission Control"), ("paper", "Paper")]
let MODES: [(String, String)] = [("auto", "Auto"), ("light", "Light"), ("dark", "Dark")]
let PAPER_LIGHT: [String: Tone] = [
  "sage": Tone(bg: 0xe2efe1, ink: 0x2c6a3d), "sky": Tone(bg: 0xe0ebf7, ink: 0x2a5a88), "lilac": Tone(bg: 0xebe5f6, ink: 0x58429a),
  "peach": Tone(bg: 0xfbe5d4, ink: 0x934514), "rose": Tone(bg: 0xf8dfdf, ink: 0xa02d33), "butter": Tone(bg: 0xf6edcb, ink: 0x735600),
  "teal": Tone(bg: 0xdbeeec, ink: 0x1d625c), "indigo": Tone(bg: 0xe5e8f7, ink: 0x3a478c), "grey": Tone(bg: 0xece9e3, ink: 0x5e584f)]
let PAPER_DARK: [String: Tone] = [
  "sage": Tone(bg: 0x22362a, ink: 0xa3d9b1), "sky": Tone(bg: 0x1e2f42, ink: 0xa8caef), "lilac": Tone(bg: 0x2e2842, ink: 0xcbbcf2),
  "peach": Tone(bg: 0x3e2b1d, ink: 0xf3bb8f), "rose": Tone(bg: 0x40232a, ink: 0xf4a6ac), "butter": Tone(bg: 0x3a3219, ink: 0xead17c),
  "teal": Tone(bg: 0x1c3634, ink: 0x97d8d1), "indigo": Tone(bg: 0x272c47, ink: 0xb6c0f4), "grey": Tone(bg: 0x2f2d29, ink: 0xb0a89c)]
let THEMES: [Theme] = [
  Theme(layout: "studio", mode: "dark", bg: 0x171717, bg2: 0x202020, bg3: 0x1f1f1f, card: 0x272727, line: 0x454545, rule2: 0x555555,
        tx: 0xffffff, dim: 0xdddddd, muted: 0xa8a8a8,
        accent: 0xff730d, onAccent: 0x141414, accentTx: 0xff8a3d, ok: 0x34c759, onOk: 0x0b1a10, okTx: 0x4ade80, warn: 0xffcc00,
        bad: 0xff453a, badTx: 0xff7b72, onBad: 0x1a0000, office: 0x3a5fd6, personal: 0xc2410c, onScope: 0xffffff,
        alertCard: 0x3a1a1a, reviewCard: 0x33261a, keyBg: 0x3a3a3a, keyTx: 0xffffff, hues: [:], onHue: 0x141414, tones: [:],
        body: .default, mono: false, base: 14, radius: 12, gap: 20, pad: 12),
  Theme(layout: "studio", mode: "light", bg: 0xf5f4f0, bg2: 0xe6e4dd, bg3: 0xecebe6, card: 0xfbfaf7, line: 0xc4c0b6, rule2: 0xa8a49a,
        tx: 0x141414, dim: 0x2e2c28, muted: 0x5c584f,
        accent: 0xff5a1f, onAccent: 0x141414, accentTx: 0xa63a0a, ok: 0x12b76a, onOk: 0x141414, okTx: 0x0a7a45, warn: 0x8a5a00,
        bad: 0xd4382c, badTx: 0xb42318, onBad: 0xffffff, office: 0x3a5fd6, personal: 0xc2410c, onScope: 0xffffff,
        alertCard: 0xfff0ed, reviewCard: 0xfff3ea, keyBg: 0xe6e4dd, keyTx: 0x141414, hues: [:], onHue: 0x141414, tones: [:],
        body: .default, mono: false, base: 14, radius: 12, gap: 20, pad: 12),
  Theme(layout: "mission", mode: "dark", bg: 0x0a0c0f, bg2: 0x0f1318, bg3: 0x141a21, card: 0x0f1318, line: 0x1f2731, rule2: 0x2c3744,
        tx: 0xdbe4ee, dim: 0xc0ccd8, muted: 0x93a1b2,
        accent: 0x2ee6ff, onAccent: 0x001318, accentTx: 0x2ee6ff, ok: 0x3dff9a, onOk: 0x00160a, okTx: 0x3dff9a, warn: 0xffc53d,
        bad: 0xff5872, badTx: 0xff7a8e, onBad: 0x1a0006, office: 0xb9a2ff, personal: 0x2ee6ff, onScope: 0x05070a,
        alertCard: 0x1c1016, reviewCard: 0x121a22, keyBg: 0x1f2731, keyTx: 0xdbe4ee,
        hues: ["gr": 0x3dff9a, "cy": 0x2ee6ff, "am": 0xffc53d, "rd": 0xff5872, "mg": 0xff7ad9, "or": 0xff9a4a, "vi": 0xb9a2ff], onHue: 0x05070a, tones: [:],
        body: .monospaced, mono: true, base: 13, radius: 3, gap: 12, pad: 12),
  Theme(layout: "mission", mode: "light", bg: 0xeef2f6, bg2: 0xe3e9ef, bg3: 0xffffff, card: 0xffffff, line: 0xcfd8e2, rule2: 0xb9c4d0,
        tx: 0x0f1720, dim: 0x26323f, muted: 0x4a5868,
        accent: 0x006b80, onAccent: 0xffffff, accentTx: 0x006b80, ok: 0x07703f, onOk: 0xffffff, okTx: 0x07703f, warn: 0x8a5a00,
        bad: 0xb4233c, badTx: 0xb4233c, onBad: 0xffffff, office: 0x5b3fb8, personal: 0x006b80, onScope: 0xffffff,
        alertCard: 0xfcebee, reviewCard: 0xf3eefc, keyBg: 0xe3e9ef, keyTx: 0x0f1720,
        hues: ["gr": 0x07703f, "cy": 0x006b80, "am": 0x8a5a00, "rd": 0xb4233c, "mg": 0x9b2475, "or": 0xa3470c, "vi": 0x5b3fb8], onHue: 0xffffff, tones: [:],
        body: .monospaced, mono: true, base: 13, radius: 3, gap: 12, pad: 12),
  Theme(layout: "paper", mode: "light", bg: 0xf6f2ea, bg2: 0xece9e3, bg3: 0xfffdf8, card: 0xfffdf8, line: 0xe6dfd2, rule2: 0xd8d0c0,
        tx: 0x2a2723, dim: 0x3d3933, muted: 0x5e584f,
        accent: 0x58429a, onAccent: 0xffffff, accentTx: 0x58429a, ok: 0x2c6a3d, onOk: 0xfffdf8, okTx: 0x2c6a3d, warn: 0x735600,
        bad: 0xa02d33, badTx: 0xa02d33, onBad: 0xffffff, office: 0x2a5a88, personal: 0x934514, onScope: 0xffffff,
        alertCard: 0xf8dfdf, reviewCard: 0xebe5f6, keyBg: 0xece9e3, keyTx: 0x2a2723, hues: [:], onHue: 0xfffdf8, tones: PAPER_LIGHT,
        body: .default, mono: false, base: 15, radius: 16, gap: 16, pad: 14),
  Theme(layout: "paper", mode: "dark", bg: 0x1b1a17, bg2: 0x2f2d29, bg3: 0x25231f, card: 0x25231f, line: 0x38352f, rule2: 0x4a463f,
        tx: 0xefe9df, dim: 0xd8d1c5, muted: 0xb0a89c,
        accent: 0xcbbcf2, onAccent: 0x1b1a17, accentTx: 0xcbbcf2, ok: 0xa3d9b1, onOk: 0x1b1a17, okTx: 0xa3d9b1, warn: 0xead17c,
        bad: 0xf4a6ac, badTx: 0xf4a6ac, onBad: 0x1b1a17, office: 0xa8caef, personal: 0xf3bb8f, onScope: 0x1b1a17,
        alertCard: 0x40232a, reviewCard: 0x2e2842, keyBg: 0x2f2d29, keyTx: 0xefe9df, hues: [:], onHue: 0x1b1a17, tones: PAPER_DARK,
        body: .default, mono: false, base: 15, radius: 16, gap: 16, pad: 14),
]
func themeFor(_ layout: String, _ mode: String) -> Theme { THEMES.first { $0.layout == layout && $0.mode == mode } ?? THEMES[0] }
/// Unknown layout → Studio; mode auto (or unknown) → the macOS appearance.
func resolveTheme(layout: String, mode: String, systemDark: Bool) -> Theme {
  let l = LAYOUTS.contains { $0.0 == layout } ? layout : "studio"
  let m = mode == "light" || mode == "dark" ? mode : (systemDark ? "dark" : "light")
  return themeFor(l, m)
}
/// The old single "deck.theme" picker value → (layout, mode).
func migrateTheme(_ old: String) -> (String, String) {
  switch old {
  case "deck": return ("studio", "dark")
  case "mission": return ("mission", "dark")
  case "paper-light": return ("paper", "light")
  case "paper-dark": return ("paper", "dark")
  case "auto": return ("paper", "auto")   // old Auto meant Paper light/dark
  default: return ("studio", "auto")
  }
}
/// Reads deck.layout / deck.mode, migrating a leftover deck.theme once (and removing it).
func loadPrefs(_ d: UserDefaults) -> (layout: String, mode: String) {
  if d.string(forKey: "deck.layout") == nil, let old = d.string(forKey: "deck.theme") {
    let m = migrateTheme(old)
    d.set(m.0, forKey: "deck.layout"); d.set(m.1, forKey: "deck.mode")
  }
  d.removeObject(forKey: "deck.theme")
  return (d.string(forKey: "deck.layout") ?? "studio", d.string(forKey: "deck.mode") ?? "auto")
}
/// The active theme. The root view is re-identified on change, so every view re-reads it.
var T = THEMES[0]

func rgb(_ v: UInt32) -> Color { Color(red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255) }
/// a over b at alpha k (for tinted pills: hue at 16% over the card).
func mix(_ a: UInt32, _ b: UInt32, _ k: Double) -> UInt32 {
  func ch(_ s: UInt32) -> UInt32 { UInt32((Double((a >> s) & 255) * k + Double((b >> s) & 255) * (1 - k)).rounded()) }
  return (ch(16) << 16) | (ch(8) << 8) | ch(0)
}
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
  var p: [(String, UInt32, UInt32)] = [
    ("tx/bg", t.tx, t.bg), ("tx/card", t.tx, t.card), ("tx/bg2", t.tx, t.bg2), ("tx/bg3", t.tx, t.bg3), ("dim/card", t.dim, t.card), ("dim/bg", t.dim, t.bg),
    ("muted/bg", t.muted, t.bg), ("muted/card", t.muted, t.card), ("muted/bg2", t.muted, t.bg2), ("muted/bg3", t.muted, t.bg3),
    ("accentTx/bg", t.accentTx, t.bg), ("accentTx/card", t.accentTx, t.card), ("accentTx/bg2", t.accentTx, t.bg2), ("onAccent/accent", t.onAccent, t.accent),
    ("onOk/ok", t.onOk, t.ok), ("okTx/card", t.okTx, t.card), ("okTx/bg", t.okTx, t.bg), ("warn/card", t.warn, t.card), ("warn/bg", t.warn, t.bg),
    ("badTx/card", t.badTx, t.card), ("badTx/bg", t.badTx, t.bg), ("onBad/bad", t.onBad, t.bad), ("onScope/office", t.onScope, t.office),
    ("onScope/personal", t.onScope, t.personal), ("keyTx/keyBg", t.keyTx, t.keyBg),
    ("tx/alertCard", t.tx, t.alertCard), ("muted/alertCard", t.muted, t.alertCard), ("badTx/alertCard", t.badTx, t.alertCard),
    ("tx/reviewCard", t.tx, t.reviewCard), ("muted/reviewCard", t.muted, t.reviewCard), ("dim/reviewCard", t.dim, t.reviewCard),
    ("accentTx/tint", t.accentTx, mix(t.accent, t.card, 0.15)), ("bg/tx (toast)", t.bg, t.tx)]
  for (k, h) in t.hues.sorted(by: { $0.key < $1.key }) {
    p += [("\(k)/bg", h, t.bg), ("\(k)/card", h, t.card), ("\(k)/bg2", h, t.bg2), ("\(k)/bg3", h, t.bg3),
          ("\(k)/tint", h, mix(h, t.card, 0.16)), ("onHue/\(k)", t.onHue, h)]
  }
  for (k, x) in t.tones.sorted(by: { $0.key < $1.key }) {
    p += [("\(k)Ink/\(k)", x.ink, x.bg), ("tx/\(k)", t.tx, x.bg), ("muted/\(k)", t.muted, x.bg), ("\(k)Ink/card", x.ink, t.card),
          ("\(k)Ink/bg", x.ink, t.bg), ("card/\(k)Ink", t.card, x.ink)]
  }
  return p
}

var accent: Color { rgb(T.accent) }      // fills
var accTx: Color { rgb(T.accentTx) }     // accent-coloured text
var bg: Color { rgb(T.bg) }
var bg2: Color { rgb(T.bg2) }
var bg3: Color { rgb(T.bg3) }
var card: Color { rgb(T.card) }
var line: Color { rgb(T.line) }
var rule2: Color { rgb(T.rule2) }
var dim: Color { rgb(T.dim) }
var faint: Color { rgb(T.muted) }
var okC: Color { rgb(T.ok) }
var okTx: Color { rgb(T.okTx) }
var warnC: Color { rgb(T.warn) }
var badC: Color { rgb(T.badTx) }
var officeC: Color { rgb(T.office) }
var personalC: Color { rgb(T.personal) }
var txC: Color { rgb(T.tx) }
/// Mission hue by name; other layouts fall back to the matching semantic token.
func hue(_ k: String) -> Color {
  if let v = T.hues[k] { return rgb(v) }
  switch k { case "gr": return okTx; case "am": return warnC; case "rd": return badC; case "off": return faint; default: return accTx }
}
func tone(_ k: String) -> Tone { T.tones[k] ?? T.tones["grey"] ?? Tone(bg: T.bg2, ink: T.muted) }
func toneBg(_ k: String) -> Color { rgb(tone(k).bg) }
func toneInk(_ k: String) -> Color { rgb(tone(k).ink) }

/// Type scale: body text never below 13 pt (owner rule); only badges and tracked labels use 11 pt.
func fontSize(_ d: CGFloat) -> CGFloat { d <= -4 ? 11 : max(13, T.base + d) }
func f(_ d: CGFloat = 0, _ w: Font.Weight = .regular, mono: Bool = false) -> Font {
  .system(size: fontSize(d), weight: w, design: mono || T.mono ? .monospaced : T.body)
}
/// Title face per layout: Studio bold sans, Mission condensed sans, Paper serif.
func ft(_ d: CGFloat = 0) -> Font {
  let size = max(14, T.base + 1 + d)
  switch T.layout {
  case "mission": return .system(size: size + 2, weight: .semibold).width(.condensed)
  case "paper": return .system(size: size, weight: .medium, design: .serif)
  default: return .system(size: size, weight: .bold)
  }
}
func serif(_ size: CGFloat, _ w: Font.Weight = .regular) -> Font { .system(size: size, weight: w, design: .serif) }
func cond(_ size: CGFloat, _ w: Font.Weight = .semibold) -> Font { .system(size: size, weight: w).width(.condensed) }
/// Tracked uppercase label (badge size).
func fl(_ w: Font.Weight = .semibold) -> Font { .system(size: 11, weight: w, design: T.mono ? .monospaced : T.body) }
func usageColor(_ p: Double?) -> Color { guard let p = p else { return faint }; return p >= 90 ? rgb(T.bad) : p >= 60 ? warnC : okC }
/// Project colour, optionally darkened (multiplied toward black) so white text on it keeps AA contrast.
func hex(_ s: String?, darken k: Double = 1) -> Color {
  guard let s = s, s.hasPrefix("#"), s.count == 7, let v = Int(s.dropFirst(), radix: 16) else { return accent }
  return Color(red: Double((v >> 16) & 255) / 255 * k, green: Double((v >> 8) & 255) / 255 * k, blue: Double(v & 255) / 255 * k)
}
func shortId(_ id: String?) -> String { id.map { String($0.split(separator: "-").first ?? "") } ?? "" }
func words(_ n: Int, cap: Bool = false) -> String {
  let w = n >= 0 && n < 13 ? ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"][n] : "\(n)"
  return cap ? w.prefix(1).uppercased() + w.dropFirst() : w
}

// MARK: - Shared building blocks (styled by the active layout)

/// Wrapping row (chips, pills); avoids horizontal ScrollViews, which also render blank in snapshots.
struct Flow: Layout {
  var spacing: CGFloat = 8, rowSpacing: CGFloat = 8
  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let maxW = proposal.width ?? .infinity
    var x: CGFloat = 0, y: CGFloat = 0, rowH: CGFloat = 0, w: CGFloat = 0
    for s in subviews {
      var sz = s.sizeThatFits(.unspecified); sz.width = min(sz.width, maxW)
      if x > 0 && x + sz.width > maxW { y += rowH + rowSpacing; x = 0; rowH = 0 }
      x += sz.width + spacing; rowH = max(rowH, sz.height); w = max(w, x - spacing)
    }
    return CGSize(width: min(w, maxW), height: y + rowH)
  }
  func placeSubviews(in b: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    var x = b.minX, y = b.minY, rowH: CGFloat = 0
    for s in subviews {
      var sz = s.sizeThatFits(.unspecified); sz.width = min(sz.width, b.width)
      if x > b.minX && x + sz.width > b.maxX { y += rowH + rowSpacing; x = b.minX; rowH = 0 }
      s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(sz)); x += sz.width + spacing; rowH = max(rowH, sz.height)
    }
  }
}
struct Bar: View {
  let value: Double; let color: Color; var height: CGFloat = 7; var track: Color? = nil
  var body: some View {
    GeometryReader { g in
      ZStack(alignment: .leading) {
        Capsule().fill(track ?? line)
        Capsule().fill(color).frame(width: max(height, g.size.width * min(1, max(0, value))))
      }
    }.frame(height: height)
  }
}
struct Badge: View {
  let text: String; let color: Color; var fg: Color = rgb(T.onAccent)
  var body: some View { Text(text).font(fl(.heavy)).padding(.horizontal, 7).padding(.vertical, 3).background(color).foregroundColor(fg).clipShape(Capsule()).fixedSize() }
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
    Text(text).font(.system(size: 13, weight: .heavy, design: .monospaced)).padding(.horizontal, 6).padding(.vertical, 2).background(accent).foregroundColor(rgb(T.onAccent))
      .clipShape(RoundedRectangle(cornerRadius: min(5, T.radius)))
  }
}
/// Studio section: orange tracked title, trailing note, content.
struct Block<Content: View>: View {
  let title: String; let trailing: String; let content: Content
  init(_ title: String, _ trailing: String = "", @ViewBuilder content: () -> Content) { self.title = title; self.trailing = trailing; self.content = content() }
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .firstTextBaseline) {
        Text(title).font(f(-1, .heavy)).foregroundColor(accTx).tracking(1.4); Spacer()
        Text(trailing).font(f(-1, .semibold)).foregroundColor(faint).multilineTextAlignment(.trailing)
      }
      content
    }
  }
}
enum KeyRole { case plain, go, warn, danger }
/// A button, drawn the way each layout draws its buttons.
struct Key: View {
  let title: String; var role: KeyRole = .plain; var disabled = false; var wide = false; let action: () -> Void
  var body: some View {
    Button(action: action) { label }.buttonStyle(.plain).disabled(disabled).opacity(disabled ? 0.45 : 1)
  }
  @ViewBuilder var label: some View {
    switch T.layout {
    case "mission":
      let c: Color = role == .go ? hue("gr") : role == .warn ? hue("am") : role == .danger ? hue("rd") : txC
      Text(title.uppercased()).font(f(-1, .semibold)).tracking(1.2).foregroundColor(c).padding(.horizontal, 12).frame(minHeight: 32)
        .frame(maxWidth: wide ? .infinity : nil).background(role == .plain ? bg2 : c.opacity(0.08))
        .overlay(RoundedRectangle(cornerRadius: 3).stroke(role == .plain ? rule2 : c.opacity(0.7), lineWidth: 1))
    case "paper":
      let fill: Color = role == .go ? okC : role == .warn ? accent : role == .danger ? rgb(T.bad) : card
      let fg: Color = role == .go ? rgb(T.onOk) : role == .warn ? rgb(T.onAccent) : role == .danger ? rgb(T.onBad) : txC
      Text(title).font(f(-1, .semibold)).foregroundColor(fg).padding(.horizontal, 14).frame(minHeight: 36).frame(maxWidth: wide ? .infinity : nil)
        .background(RoundedRectangle(cornerRadius: 12).fill(fill)).overlay(RoundedRectangle(cornerRadius: 12).stroke(role == .plain ? line : .clear, lineWidth: 1.5))
    default:
      let c: (Color, Color) = role == .go ? (okC, rgb(T.onOk)) : role == .warn ? (accent, rgb(T.onAccent)) : role == .danger ? (rgb(T.bad), rgb(T.onBad)) : (rgb(T.keyBg), rgb(T.keyTx))
      Text(title).font(f(-1, .bold)).padding(.horizontal, 12).frame(minHeight: 30).frame(maxWidth: wide ? .infinity : nil)
        .background(c.0).foregroundColor(c.1).clipShape(RoundedRectangle(cornerRadius: 8))
    }
  }
}
/// A choice among options (decision / question answers).
struct Opt: View {
  let title: String; let on: Bool; let action: () -> Void
  var body: some View {
    Button(action: action) {
      switch T.layout {
      case "mission":
        Text(title).font(f(-1)).foregroundColor(on ? hue("cy") : txC).padding(.horizontal, 10).frame(minHeight: 32)
          .background(on ? hue("cy").opacity(0.1) : .clear).overlay(RoundedRectangle(cornerRadius: 2).stroke(on ? hue("cy") : rule2, lineWidth: 1))
      case "paper":
        Text(title).font(f(-1, on ? .semibold : .regular)).foregroundColor(on ? toneInk("lilac") : txC).padding(.horizontal, 14).frame(minHeight: 34)
          .background(Capsule().fill(on ? toneBg("lilac") : .clear)).overlay(Capsule().stroke(on ? toneInk("lilac") : line, lineWidth: 1.5))
      default:
        Text(title).font(f(-1, .bold)).padding(.horizontal, 12).frame(minHeight: 30).background(on ? accent : rgb(T.keyBg))
          .foregroundColor(on ? rgb(T.onAccent) : rgb(T.keyTx)).clipShape(RoundedRectangle(cornerRadius: 8))
      }
    }.buttonStyle(.plain)
  }
}
/// One line of text that never hides content silently: truncated text always carries a tooltip.
func clip(_ s: String, _ font: Font, _ color: Color = txC, lines: Int = 1) -> some View {
  Text(s).font(font).foregroundColor(color).lineLimit(lines).truncationMode(.tail).help(s)
}
/// Mission LED (glows in dark mode).
struct Led: View {
  let c: Color; var on = true; var size: CGFloat = 8
  var body: some View {
    Circle().fill(on ? c : rule2).frame(width: size, height: size).shadow(color: T.dark && on ? c.opacity(0.85) : .clear, radius: 3)
  }
}
/// Mission outline tag (PERS / OFFC / PILOT …).
struct MTag: View {
  let text: String; let c: Color
  var body: some View {
    Text(text).font(fl()).tracking(1.2).foregroundColor(c).padding(.horizontal, 4).padding(.vertical, 2)
      .overlay(RoundedRectangle(cornerRadius: 2).stroke(c.opacity(0.55), lineWidth: 1)).fixedSize()
  }
}
func mScope(_ s: String?) -> MTag { s == "office" ? MTag(text: "OFFC", c: hue("vi")) : s == "personal" ? MTag(text: "PERS", c: hue("cy")) : MTag(text: "UNTAGGED", c: hue("rd")) }
/// Mission tinted state pill.
struct MSt: View {
  let text: String; let c: Color
  var body: some View {
    Text(text).font(fl()).tracking(1.2).foregroundColor(c).padding(.horizontal, 5).padding(.vertical, 2).background(c.opacity(0.16))
      .overlay(RoundedRectangle(cornerRadius: 2).stroke(c.opacity(0.45), lineWidth: 1)).fixedSize()
  }
}
/// Paper pastel pill.
struct Pill: View {
  let text: String; let tone: String
  var body: some View {
    Text(text).font(.system(size: 12, weight: .semibold)).foregroundColor(toneInk(tone)).padding(.horizontal, 9).padding(.vertical, 3)
      .background(Capsule().fill(toneBg(tone))).fixedSize()
  }
}
func pScope(_ s: String?) -> Pill { Pill(text: s == "office" ? "Office" : s == "personal" ? "Personal" : "Untagged", tone: s == "office" ? "indigo" : s == "personal" ? "teal" : "rose") }
/// Paper progress ring for an account's 5-hour quota.
struct Ring: View {
  let value: Double; let tone: String; let size: CGFloat; let inner: String; var sub: String? = nil
  var body: some View {
    let w: CGFloat = size > 80 ? 9 : 6
    ZStack {
      Circle().stroke(line, lineWidth: w)
      Circle().trim(from: 0, to: min(1, max(0, value / 100))).stroke(toneInk(tone), style: StrokeStyle(lineWidth: w, lineCap: .round)).rotationEffect(.degrees(-90))
      VStack(spacing: 0) {
        Text(inner).font(.system(size: size > 80 ? 22 : 13, weight: .semibold)).foregroundColor(txC)
        if let s = sub { Text(s).font(.system(size: 11)).foregroundColor(faint) }
      }
    }.frame(width: size - w, height: size - w).padding(w / 2)
  }
}
/// Mission 20-segment quota bar.
struct QBar: View {
  let pct: Double; let c: Color
  var body: some View {
    let on = Int((pct / 5).rounded())
    HStack(spacing: 2) { ForEach(0..<20, id: \.self) { i in Rectangle().fill(i < on ? c : line).frame(height: 8) } }
  }
}
struct HRule: View { var c: Color = line; var body: some View { Rectangle().fill(c).frame(height: 1) } }
struct VRule: View { var body: some View { Rectangle().fill(line).frame(width: 1) } }

/// Layout + mode controls: one quick toggle each, plus a picker menu with both choices.
struct ThemeControls: View {
  @EnvironmentObject var store: DeckStore
  var pills = false
  var tint: Color = faint
  var layoutName: String { LAYOUTS.first { $0.0 == store.layout }?.1 ?? "Studio" }
  var modeName: String { MODES.first { $0.0 == store.mode }?.1 ?? "Auto" }
  var modeIcon: String { store.mode == "light" ? "sun.max" : store.mode == "dark" ? "moon" : "circle.lefthalf.filled" }
  var body: some View {
    HStack(spacing: 8) {
      Button(action: { store.cycleLayout() }) {
        if pills { pill(layoutName) } else { Image(systemName: "square.grid.2x2").font(.system(size: 14)).foregroundColor(tint) }
      }.buttonStyle(.plain).help("Layout: \(layoutName) — click for the next layout")
      Button(action: { store.cycleMode() }) {
        if pills { pill(modeName) } else { Image(systemName: modeIcon).font(.system(size: 14)).foregroundColor(tint) }
      }.buttonStyle(.plain).help("Mode: \(modeName)\(store.mode == "auto" ? " (follows macOS)" : "") — click for the next mode")
if isSnapshot { Image(systemName: "paintpalette").font(.system(size: 14)).foregroundColor(tint) }   // ImageRenderer cannot draw a Menu
      else {
            Menu {
        SwiftUI.Section("Layout") {
          ForEach(LAYOUTS, id: \.0) { c in Button(action: { store.layout = c.0 }) { Text((store.layout == c.0 ? "✓ " : "   ") + c.1) } }
        }
        SwiftUI.Section("Mode") {
          ForEach(MODES, id: \.0) { c in Button(action: { store.mode = c.0 }) { Text((store.mode == c.0 ? "✓ " : "   ") + c.1 + (c.0 == "auto" ? " (follows macOS)" : "")) } }
        }
      } label: { Image(systemName: "paintpalette") }.menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().help("Layout and mode")
      }
    }
  }
  func pill(_ s: String) -> some View {
    Text(s).font(.system(size: 12)).foregroundColor(faint).padding(.horizontal, 9).padding(.vertical, 3).overlay(Capsule().stroke(line, lineWidth: 1))
  }
}
struct CollapseButton: View {
  @EnvironmentObject var store: DeckStore
  var tint: Color = accTx
  var body: some View {
    Button(action: { store.collapsed.toggle() }) {
      Image(systemName: store.collapsed ? "chevron.down.circle.fill" : "chevron.up.circle.fill").font(.system(size: 18)).foregroundColor(tint)
    }.buttonStyle(.plain).help(store.collapsed ? "Expand" : "Collapse")
  }
}
/// The window's traffic lights sit over the top-left corner; snapshots draw stand-ins so the strip reads the same.
struct TrafficSpace: View {
  var body: some View {
    HStack(spacing: 7) {
      if isSnapshot { ForEach(0..<3, id: \.self) { _ in Circle().fill(rule2).frame(width: 11, height: 11) } }
    }.frame(width: 64, alignment: .leading)
  }
}

// MARK: - Shared data helpers

infix operator ++: AdditionPrecedence
/// Text concatenation (Text + Text is deprecated on macOS 26).
func ++ (a: Text, b: Text) -> Text { Text("\(a)\(b)") }
func sumCounts(_ o: OverviewVM, _ k: (Counts) -> Int?) -> Int { o.projects.reduce(0) { $0 + ($1.counts.flatMap(k) ?? 0) } }

extension DeckStore {
  var projectNames: [String: String] { Dictionary(uniqueKeysWithValues: (overview?.projects ?? []).map { ($0.id, $0.name) }) }
  var accounts: [Account] { overview?.accounts ?? [] }
  func tile(_ id: String?) -> ProjectTile? { overview?.projects.first { $0.id == id } }
  /// Accounts for the workers view: the project's eligibility (allowed / reason) when one is selected, else every account.
  var workerRows: [(a: Account, allowed: Bool, reason: String?)] {
    if let ws = project?.workers, !ws.isEmpty { return ws.map { ($0.asAccount(accounts), $0.allowed != false, $0.reason) } }
    return accounts.map { ($0, true, nil) }
  }
  /// Lanes (from the overview's accounts) currently working on one project.
  func lanes(on p: String) -> [(a: Account, l: Lane)] { accounts.flatMap { a in (a.lanes ?? []).filter { $0.project == p }.map { (a, $0) } } }
  func countdown(_ ts: Int?) -> String { ts.map { dur($0 - now) } ?? "—" }
}
func workerId(_ a: Account) -> String { a.lanes?.first?.worker ?? String(a.label.prefix(2)).uppercased() }
func allTasks(_ b: Board) -> [TaskVM] { [b.running, b.review, b.needsYou, b.upNext, b.backlog, b.doneToday].compactMap { $0 }.flatMap { $0 } }
func isQuiet(_ t: TaskVM) -> Bool { t.state == "hung" || (t.state == "running" && (t.lastActiveSec ?? 0) > 600) }
func greyedWhy(_ a: Account) -> String? {
  if a.capacity?.whyKind == "policy" { return a.capacity?.why }
  return a.dispatch == false ? "usage only — this account is never leased work" : nil
}

// MARK: - Shared task / review pieces

/// The expanded part of a task: summary, step, metrics, warnings, question, actions.
struct TaskDetail: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if let s = t.summary, !s.isEmpty { Text(s).font(f(-1)).foregroundColor(dim).fixedSize(horizontal: false, vertical: true) }
      if let st = t.step { Text("◉ \(st.index)/\(st.count) \(st.title)\(st.etaMin.map { " · ~\(Int($0))m" } ?? "")").font(f(-1, .semibold)).foregroundColor(accTx) }
      ForEach(t.metrics ?? [], id: \.self) { m in
        VStack(alignment: .leading, spacing: 4) {
          HStack {
            Text(m.label).font(f(-1)).foregroundColor(m.kind == "bad" && m.done > 0 ? badC : txC)
            Spacer()
            Text(metricText(m)).font(f(-1, .bold, mono: true))
          }
          if let tot = m.total, tot > 0 { Bar(value: m.done / tot, color: okC, height: 5) }
        }
      }
      ForEach(t.warnings ?? [], id: \.self) { Text("⚠ \($0)").font(f(-1, .bold)).foregroundColor(warnC) }
      if let d = t.deps, !d.isEmpty { Text("after " + d.map { "\(shortId($0.id)) (\($0.state.replacingOccurrences(of: "_", with: " ")))" }.joined(separator: ", ")).font(f(-1)).foregroundColor(faint) }
      if let q = t.question {
        Text("? \(q.text)").font(f(-1, .bold)).foregroundColor(warnC).fixedSize(horizontal: false, vertical: true)
        Flow { ForEach(q.options ?? [], id: \.self) { o in Opt(title: o + (o == q.default ? " (default)" : ""), on: o == q.default) { store.act(nil, t.id, actionBody("answer", rev: t.rev, note: o)) } } }
      }
      TaskActions(t: t)
    }
  }
}
/// Merging tasks: "merging · retry in 1m · main checkout busy" from resumeAt + stateReason
/// ("merge waits: main checkout busy · retry 21:44" → prefix and trailing retry clock stripped).
func mergeText(_ t: TaskVM, now: Int) -> String {
  var reason = t.stateReason ?? ""
  if reason.lowercased().hasPrefix("merge waits:") { reason = String(reason.dropFirst("merge waits:".count)) }
  if let r = reason.range(of: #"\s*·\s*retry\s+\d{1,2}:\d{2}\s*$"#, options: .regularExpression) { reason.removeSubrange(r) }
  reason = reason.trimmingCharacters(in: .whitespaces)
  var parts = ["merging"]
  if let r = t.resumeAt { parts.append("retry in \(dur(r - now))") }
  if !reason.isEmpty { parts.append(reason) }
  return parts.joined(separator: " · ")
}
/// One-line status for compact cards; merging gets its retry/reason form.
func statusText(_ t: TaskVM, now: Int) -> String { t.state == "merging" ? mergeText(t, now: now) : (t.stateReason ?? t.state.replacingOccurrences(of: "_", with: " ")) }
func metricText(_ m: MetricVM) -> String {
  "\(Int(m.done))\(m.total.map { " / \(Int($0))" } ?? "")\(m.rate.map { " · ≈\(String(format: $0 < 10 ? "%.1f" : "%.0f", $0))/min" } ?? "")\(m.left.map { " · ~\(dur(Int($0 * 60))) left" } ?? "")"
}

struct TaskActions: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM
  @State private var armCancel = false
  var body: some View {
    Flow(spacing: 8) {
      if ["running", "starting", "queued", "ready"].contains(t.state) { Key(title: "Pause") { store.act(nil, t.id, actionBody("pause", rev: t.rev)) } }
      if ["blocked", "limit_paused"].contains(t.state) && t.question == nil { Key(title: "Resume", role: .go) { store.act(nil, t.id, actionBody("resume", rev: t.rev)) } }
      if ["hung", "failed", "changes_requested"].contains(t.state) { Key(title: "Retry", role: .warn) { store.act(nil, t.id, actionBody("retry", rev: t.rev)) } }
      if !["done", "cancelled", "merging", "review"].contains(t.state) {
        Key(title: "Reassign…") { store.openReassign(t) }
        Key(title: "▲ \(bump(t.priority, up: true))", disabled: t.priority == "P0") { store.act(nil, t.id, actionBody("priority", rev: t.rev, priority: bump(t.priority, up: true))) }
          .help("Raise priority")
        Key(title: "▼ \(bump(t.priority, up: false))", disabled: t.priority == "P3") { store.act(nil, t.id, actionBody("priority", rev: t.rev, priority: bump(t.priority, up: false))) }
          .help("Lower priority")
        // Two-step confirm in place of the phone's hold-to-cancel.
        Key(title: armCancel ? "Confirm cancel" : "Cancel", role: armCancel ? .danger : .plain) {
          if armCancel { store.act(nil, t.id, actionBody("cancel", rev: t.rev)) } else { armCancel = true }
        }
      }
    }
  }
}

/// Approve / request changes (with note) for one review; merging items show their merge state instead.
struct ReviewActions: View {
  @EnvironmentObject var store: DeckStore
  let r: ReviewVM
  @State private var note = ""
  @State private var writing = false
  var body: some View {
    if r.task.state == "merging" {
      Text("⇄ Approved · \(mergeText(r.task, now: store.now))").font(f(-1, .semibold)).foregroundColor(okTx).fixedSize(horizontal: false, vertical: true)
    } else {
      VStack(alignment: .leading, spacing: 8) {
        if writing {
          TextField(T.layout == "paper" ? "What should change? Your note goes to the worker with the next attempt." : "What should change?", text: $note)
            .textFieldStyle(.roundedBorder).font(f(-1))
        }
        HStack(spacing: 8) {
          if T.layout == "studio" { approve; changes } else { changes; approve }
        }
      }
    }
  }
  var wide: Bool { T.layout != "studio" }
  var approve: some View { Key(title: T.layout == "paper" ? "Approve" : "Approve ✓", role: .go, wide: wide) { store.act(nil, r.task.id, actionBody("approve", rev: r.task.rev)) } }
  var changes: some View {
    Key(title: writing ? "Send changes" : (T.layout == "studio" ? "Changes…" : "Request changes"), role: T.layout == "paper" ? .plain : .warn, wide: wide) {
      if writing { store.act(nil, r.task.id, actionBody("changes", rev: r.task.rev, note: note)); writing = false } else { writing = true }
    }
  }
}
/// Decision questions on a review, answered in place (default option marked).
struct Decisions: View {
  @EnvironmentObject var store: DeckStore
  let r: ReviewVM
  var body: some View {
    ForEach(Array((r.decisionsNeeded ?? []).enumerated()), id: \.offset) { i, d in
      VStack(alignment: .leading, spacing: 8) {
        Text((T.layout == "mission" ? "◆ " : "") + d.question).font(f(-1, .semibold)).fixedSize(horizontal: false, vertical: true)
        Flow {
          ForEach(d.options ?? [], id: \.self) { o in
            Opt(title: o + (o == d.default ? " (default)" : ""), on: o == d.default) { store.act(nil, r.task.id, actionBody("answer", rev: r.task.rev, decision: i, option: o)) }
          }
        }
      }
    }
  }
}
/// The act a NeedsItem offers, the same in every layout.
struct NeedActions: View {
  @EnvironmentObject var store: DeckStore
  let n: NeedsItem
  var body: some View {
    Flow(spacing: 8) {
      if n.kind == "review", let t = n.task { Key(title: "Approve", role: .go) { store.act(n.project, t, actionBody("approve", rev: n.rev)) } }
      if n.kind == "question", let t = n.task {
        ForEach(n.options ?? [], id: \.self) { o in Key(title: o) { store.act(n.project, t, actionBody("answer", rev: n.rev, note: o)) } }
      }
      Key(title: "Open") { store.selected = n.project; store.tab = n.kind == "review" ? "review" : n.kind == "alert" ? "alerts" : "board" }
    }
  }
}
func needsList(_ o: OverviewVM) -> [NeedsItem] {
  o.needsYou ?? (o.alerts ?? []).map { NeedsItem(kind: "alert", project: $0.project, task: $0.task, num: nil, title: $0.text, text: $0.type, rev: nil, options: nil, eventId: $0.id) }
}

// MARK: - Reassign sheet: disallowed rows greyed with lock + server reason, never hidden, not clickable.

struct ReassignSheet: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM; let rows: [Eligible]
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack { NumChip(text: t.numText); clip("Reassign · \(t.title)", f(0, .bold)); Spacer(); Key(title: "Close") { store.reassign = nil } }
      ForEach(rows) { w in
        Button(action: { store.act(nil, t.id, actionBody("reassign", rev: t.rev, worker: w.worker)); store.reassign = nil }) {
          HStack {
            Text(w.allowed ? "🟢" : w.kind == "cooling" ? "⏸" : "🔒")
            VStack(alignment: .leading, spacing: 2) {
              Text("\(w.worker) · \(w.label)").font(f(-1, .bold))
              Text(w.allowed ? "\(w.scope == "office" ? "OFFICE" : "PERSONAL")\(w.detail.map { " · \($0)" } ?? "")" : (w.reason ?? "not allowed"))
                .font(f(-1, w.allowed ? .regular : .bold)).foregroundColor(w.allowed ? faint : badC).fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
          }.padding(T.pad).background(RoundedRectangle(cornerRadius: T.radius).fill(w.allowed ? card : bg))
            .overlay(RoundedRectangle(cornerRadius: T.radius).stroke(line, style: StrokeStyle(lineWidth: 1, dash: w.allowed ? [] : [4, 3])))
        }.buttonStyle(.plain).disabled(!w.allowed).opacity(w.allowed ? 1 : 0.55)
      }
      Text("Greyed rows can't be clicked. The CLI and API refuse them too and log an alert.").font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
    }.padding(T.pad).background(RoundedRectangle(cornerRadius: T.radius).fill(card)).overlay(RoundedRectangle(cornerRadius: T.radius).stroke(accTx, lineWidth: 1))
  }
}
struct Toast: View {
  let text: String
  var body: some View {
    Text(text).font(f(-1, .semibold)).foregroundColor(T.layout == "paper" ? bg : rgb(T.keyTx)).padding(.horizontal, 14).padding(.vertical, 10)
      .frame(maxWidth: .infinity, alignment: .leading).background(T.layout == "paper" ? txC : rgb(T.keyBg))
      .clipShape(RoundedRectangle(cornerRadius: T.layout == "mission" ? 3 : 14))
  }
}

// MARK: - Studio layout (docs/v2/mockups/panel.html): gear header with inbox pills, project chips, orange section titles, rounded cards.

struct StudioChrome: View {
  @EnvironmentObject var store: DeckStore
  var body: some View {
    let ib = store.overview?.inbox
    VStack(alignment: .leading, spacing: 12) {
      HStack(spacing: 8) {
        Image(systemName: "gearshape.fill").foregroundColor(accTx).font(.system(size: 18))
        clip(store.project?.project.short ?? store.project?.project.title ?? "Agent Deck", .system(size: 19, weight: .heavy))
        if let s = store.project?.project.scope { ScopeBadge(scope: s) }
        if store.selected == nil { pills(ib) }
        Spacer(minLength: 4)
        Text(clockSecFmt.string(from: Date(timeIntervalSince1970: TimeInterval(store.now)))).font(f(-1, mono: true)).foregroundColor(faint).fixedSize()
        ThemeControls(tint: faint)
        CollapseButton()
      }
      HStack(spacing: 8) {
        if store.selected != nil { pills(ib) }
        if store.offline { Badge(text: "OFFLINE · last update \(hhmm(store.lastUpdate))", color: rgb(T.bad), fg: rgb(T.onBad)) }
        else if store.fixtures != nil && !store.collapsed { Badge(text: "FIXTURES", color: rgb(T.keyBg), fg: rgb(T.keyTx)) }
      }
      if store.collapsed { if let o = store.overview { StudioCollapsed(o: o) } }
      else if let o = store.overview {
        Flow(spacing: 8) {
          chip(nil, nil, on: store.selected == nil) { Text("All") }
          ForEach(o.projects) { p in
            chip(p.id, p.color, on: store.selected == p.id, off: p.enabled == false) {
              Text("\(p.icon ?? "") \(p.name)\(p.enabled == false ? " · paused" : "")")
              ScopeBadge(scope: p.scope, short: true)
            }
          }
        }
        if store.selected != nil {
          let cur = ["workers", "review", "alerts", "timeline"].contains(store.tab) ? store.tab : "board"
          let tabs: [(String, String)] = [("board", "Board"), ("workers", "Workers"), ("review", "Review \(store.project?.inbox?.count ?? 0)"),
                                          ("alerts", "Alerts \(store.project?.alerts?.count ?? 0)"), ("timeline", "Log")]
          HStack(spacing: 2) {
            ForEach(tabs, id: \.0) { t in
              Button(action: { store.tab = t.0 }) {
                Text(t.1).font(f(-1, cur == t.0 ? .bold : .medium)).foregroundColor(cur == t.0 ? rgb(T.onAccent) : dim)
                  .frame(maxWidth: .infinity, minHeight: 28).background(RoundedRectangle(cornerRadius: 7).fill(cur == t.0 ? accent : .clear)).contentShape(Rectangle())
              }.buttonStyle(.plain)
            }
          }.padding(3).background(RoundedRectangle(cornerRadius: 9).fill(card)).overlay(RoundedRectangle(cornerRadius: 9).stroke(line, lineWidth: 1))
        }
      }
      if let t = store.toast { Toast(text: t) }
    }.padding(.horizontal, 16).padding(.top, isSnapshot ? 16 : 30).padding(.bottom, 12)
  }
  @ViewBuilder func pills(_ ib: Inbox?) -> some View {
    if (ib?.review ?? 0) > 0 { Badge(text: "\(ib!.review!) review", color: accent) }
    if (ib?.questions ?? 0) > 0 { Badge(text: "\(ib!.questions!) ?", color: warnC, fg: rgb(T.bg)) }
    if (ib?.alerts ?? 0) > 0 { Badge(text: "\(ib!.alerts!) !", color: rgb(T.bad), fg: rgb(T.onBad)) }
  }
  func chip<L: View>(_ id: String?, _ color: String?, on: Bool, off: Bool = false, @ViewBuilder label: () -> L) -> some View {
    Button(action: { if !off { store.selected = id } }) {
      HStack(spacing: 6) {
        if let c = color { Circle().fill(hex(c)).frame(width: 8, height: 8) }
        label()
      }.font(f(-1, .semibold)).padding(.horizontal, 10).padding(.vertical, 5)
        .background(Capsule().fill(on ? accent.opacity(0.15) : card)).overlay(Capsule().stroke(on ? accTx : line, lineWidth: 1))
    }.buttonStyle(.plain).opacity(off ? 0.5 : 1)
  }
}

struct StudioCollapsed: View {
  let o: OverviewVM
  var body: some View {
    let running = o.machine?.running ?? 0
    let cooling = o.accounts.first { $0.state == "cooling" }
    let spare = o.accounts.first { $0.capacity?.spare == true && $0.capacity?.why == nil }
    Flow(spacing: 14, rowSpacing: 4) {
      Text("▶\(running) running")
      Text("▣\(o.inbox?.review ?? 0) review").foregroundColor(accTx)
      Text("! \(o.inbox?.alerts ?? 0)").foregroundColor(badC)
      Text("lanes \(running)/\(o.machine?.lanesTotal ?? 0)")
      if let c = cooling { Text("\(c.label) LIMIT \(hhmm(c.resets5))").foregroundColor(badC) }
      if let s = spare { Text("⚡ \(s.label) spare").foregroundColor(warnC) }
    }.font(f(-1, .semibold, mono: true))
  }
}

struct StudioBody: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: T.gap) {
      if store.selected == nil { overview }
      else if let p = store.project {
        switch store.tab {
        case "workers":
          Block("WORKERS · PARALLEL LANES", "greyed = not allowed here") {
            ForEach(store.workerRows, id: \.a.id) { w in StudioAccount(a: w.a, allowed: w.allowed, reason: w.reason) }
          }
        case "review":
          Block("REVIEW INBOX · \(p.inbox?.count ?? 0)", "oldest first") {
            if (p.inbox ?? []).isEmpty { Text("Inbox empty").font(f(-1)).foregroundColor(faint) }
            ForEach((p.inbox ?? []).sorted { ($0.waitingSec ?? 0) > ($1.waitingSec ?? 0) }) { StudioReview(r: $0) }
          }
        case "alerts": Block("ALERTS · \(p.alerts?.count ?? 0)") { ForEach(p.alerts ?? []) { StudioEvent(e: $0) } }
        case "timeline": Block("TIMELINE") { ForEach((p.timeline ?? []).sorted { $0.ts > $1.ts }) { StudioEvent(e: $0) } }
        default: StudioBoard(p: p)
        }
      } else { Text("Loading project…").font(f(-1)).foregroundColor(faint) }
    }.padding(.horizontal, 16).padding(.bottom, 20)
  }

  @ViewBuilder var overview: some View {
    let needs = needsList(o)
    Block("NEEDS YOU · \(needs.count)", "newest first") {
      if needs.isEmpty { Text("Nothing needs you").font(f(-1)).foregroundColor(faint) }
      ForEach(needs) { StudioNeed(n: $0) }
    }
    Block("ACCOUNTS · PARALLEL LANES", "\(o.machine?.running ?? 0) / \(o.machine?.maxParallelTotal ?? 0) jobs · RAM \(o.machine?.freeRamGb.map { String(format: "%.1f", $0) } ?? "—") GB free") {
      VStack(alignment: .leading, spacing: 16) { ForEach(o.accounts) { StudioAccount(a: $0) } }
      if let b = o.backlog {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
          Text("\(b.low == true ? "▲ " : "")Backlog \(b.eligible ?? 0) ready / target \(b.target ?? 0)").font(f(-1, .bold))
          Spacer()
          Text(b.hint ?? "").font(f(-1)).foregroundColor(faint).multilineTextAlignment(.trailing).fixedSize(horizontal: false, vertical: true)
        }.padding(.horizontal, 10).padding(.vertical, 8).background(RoundedRectangle(cornerRadius: 10).fill(accent.opacity(0.1)))
          .overlay(RoundedRectangle(cornerRadius: 10).stroke(accTx.opacity(0.7), style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
      }
    }
    WorkersSection(o: o)
    Block("PROJECTS") {
      ForEach(o.projects) { p in
        Button(action: { if p.enabled != false { store.selected = p.id } }) {
          HStack(spacing: 8) {
            Text(p.icon ?? "◆"); Text(p.name).font(f(0, .bold)).fixedSize(); ScopeBadge(scope: p.scope, short: true)
            clip(p.headline ?? "", f(-1), faint)
            Spacer(minLength: 4)
            Text(countsText(p.counts)).font(f(-1, .semibold, mono: true)).foregroundColor(dim).fixedSize()
          }.padding(.horizontal, 10).padding(.vertical, 9).background(RoundedRectangle(cornerRadius: 10).fill(card))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(line, lineWidth: 1)).opacity(p.enabled == false ? 0.5 : 1)
        }.buttonStyle(.plain)
      }
    }
  }
}
func countsText(_ c: Counts?) -> String {
  guard let c = c else { return "" }
  return [("▶", c.running), ("⏳", c.queued), ("▣", c.review), ("✋", c.blocked), ("⏸", c.limit_paused), ("!", c.hung), ("✓", c.doneToday)]
    .compactMap { ($0.1 ?? 0) > 0 ? "\($0.0)\($0.1!)" : nil }.joined(separator: " ")
}
/// Studio card: card fill with a 1-px border, optionally tinted by state.
func studioCard<V: View>(_ border: Color = line, @ViewBuilder _ v: () -> V) -> some View {
  v().padding(12).frame(maxWidth: .infinity, alignment: .leading).background(RoundedRectangle(cornerRadius: 12).fill(card))
    .overlay(RoundedRectangle(cornerRadius: 12).stroke(border, lineWidth: 1))
}

struct StudioNeed: View {
  let n: NeedsItem
  var body: some View {
    studioCard(n.kind == "alert" ? badC.opacity(0.7) : n.kind == "question" ? warnC.opacity(0.7) : accTx.opacity(0.7)) {
      VStack(alignment: .leading, spacing: 8) {
        HStack(spacing: 8) {
          if let t = n.task { NumChip(text: n.num.map(String.init) ?? shortId(t)) } else { Text("!").font(f(0, .heavy)).foregroundColor(badC) }
          clip(n.title, ft(-1))
          Spacer(minLength: 0)
        }
        Text(n.text).font(f(-1)).foregroundColor(dim).fixedSize(horizontal: false, vertical: true)
        NeedActions(n: n)
      }
    }
  }
}

struct StudioLane: View {
  @EnvironmentObject var store: DeckStore
  let l: Lane; let a: Account; let projects: [String: String]
  var body: some View {
    let free = freeLaneText(a)
    let dashed = l.state != "running"
    let c: Color = l.state == "cooling" ? badC : l.state == "running" ? txC : free.kind == "spare" ? accTx : free.kind == "cool" ? badC : faint
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      Text("\(l.worker)·\(l.lane)").font(f(-1, .heavy, mono: true)).foregroundColor(store.wText(worker: l.worker) ?? faint).frame(width: 46, alignment: .leading)
      Text(text(free.text)).font(f(-1)).foregroundColor(c).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 4)
      if l.state == "running" {
        let quiet = (l.lastActiveSec ?? 0) > 600
        Text(quiet ? "quiet \(dur(l.lastActiveSec))" : l.etaMin.map { "~\(Int($0))m" } ?? "").font(f(-1, mono: true)).foregroundColor(quiet ? badC : accTx)
      }
    }.padding(.vertical, 6).padding(.horizontal, 10)
      .background(RoundedRectangle(cornerRadius: 8).fill(dashed ? Color.clear : bg2))
      .overlay(RoundedRectangle(cornerRadius: 8).stroke(dashed ? c.opacity(0.6) : .clear, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
      .padding(.leading, 12)
  }
  func text(_ freeText: String) -> String {
    let proj = l.project.map { "\(projects[$0] ?? $0) · " } ?? ""
    switch l.state {
    case "running": return "▶ \(proj)\(shortId(l.task)) \(l.title ?? "")\(l.stepCount.map { " · \(l.stepIndex ?? 0)/\($0)" } ?? "")"
    case "cooling": return "⏸ \(proj)\(shortId(l.task)) \(l.title ?? "") · resumes \(hhmm(l.resumeAt ?? a.resets5)), same session"
    default: return freeText
    }
  }
}

struct StudioAccount: View {
  @EnvironmentObject var store: DeckStore
  let a: Account; var allowed = true; var reason: String? = nil
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        if !allowed { Text("🔒") }
        Circle().fill(store.wLine(account: a.id) ?? faint).frame(width: 9, height: 9)
        Text(a.label).font(f(0, .semibold)).fixedSize()
        ScopeBadge(scope: a.scope)
        Spacer(minLength: 4)
        Text(quotaText(a, now: store.now)).font(f(-1, mono: true)).foregroundColor(a.state == "cooling" ? badC : dim).multilineTextAlignment(.trailing).fixedSize(horizontal: false, vertical: true)
      }
      Bar(value: (a.state == "cooling" ? 100 : (a.pct5 ?? 0)) / 100, color: a.state == "cooling" ? rgb(T.bad) : usageColor(a.pct5), height: 8)
      if let r = reason, !allowed { Text(r).font(f(-1, .bold)).foregroundColor(badC).fixedSize(horizontal: false, vertical: true) }
      ForEach(a.lanes ?? []) { StudioLane(l: $0, a: a, projects: store.projectNames) }
    }.opacity(allowed ? 1 : 0.55)
  }
}

/// Full task card (tap the title row to open steps, numbers and actions).
struct TaskRow: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM; let now: Int
  @State private var open: Bool
  init(t: TaskVM, now: Int, open: Bool = false) { self.t = t; self.now = now; _open = State(initialValue: open) }
  var icon: String {
    ["running": "▶", "starting": "▶", "queued": "⏳", "ready": "⏳", "blocked": "✋", "limit_paused": "⏸", "hung": "!", "review": "▣",
     "merging": "⇄", "done": "✓", "failed": "✗", "draft": "✎", "changes_requested": "↺"][t.state] ?? "·"
  }
  var lineText: String {
    var b: [String] = [t.state.replacingOccurrences(of: "_", with: " "), "\(t.priority ?? "") \(t.size ?? "")"]
    if t.kind == "pilot" { b.append("PILOT") }
    if let w = t.worker { b.append(w) }
    if t.state == "merging" { b[0] = mergeText(t, now: now) }
    else if t.state == "limit_paused", let r = t.resumeAt { b.append("resumes \(hhmm(r)) (in \(dur(r - now)))") }
    else if let s = t.stateReason { b.append(s) }
    return b.joined(separator: " · ")
  }
  var body: some View {
    studioCard(isQuiet(t) ? badC.opacity(0.7) : t.state == "review" ? accTx.opacity(0.7) : t.state == "blocked" ? warnC.opacity(0.6) : line) {
      VStack(alignment: .leading, spacing: 8) {
        HStack(spacing: 8) {
          Text(icon).font(f(0)).frame(width: 16)
          NumChip(text: t.numText)
          clip(t.title, ft(-1))
          Spacer(minLength: 4)
          WorkerBadge(worker: t.worker)
          if let e = t.etaFinish, t.state == "running" { Text("ETA \(hhmm(e))").font(f(-1, mono: true)).foregroundColor(faint).fixedSize() }
        }.contentShape(Rectangle()).onTapGesture { open.toggle() }
        Text(lineText).font(f(-1)).foregroundColor(isQuiet(t) ? badC : faint).lineLimit(open ? nil : 1).help(lineText)
        if let p = t.progress, t.state == "running" {
          HStack { Text("Progress").font(f(-1)).foregroundColor(dim); Spacer(); Text("\(Int(p * 100))%").font(f(-1, .bold)) }
          Bar(value: p, color: accent, height: 6)
        }
        if open { TaskDetail(t: t) }
      }
    }.overlay(stripe(store.wLine(worker: t.worker)).clipShape(RoundedRectangle(cornerRadius: 12)))
  }
}

/// Studio board: running cards, then a two-column grid of the other columns (tap a card to open it).
struct StudioBoard: View {
  @EnvironmentObject var store: DeckStore
  let p: ProjectVM
  var body: some View {
    let running = p.board.running ?? []
    let cols: [(String, [TaskVM])] = [("UP NEXT", p.board.upNext ?? []), ("REVIEW", p.board.review ?? []), ("NEEDS YOU", p.board.needsYou ?? []),
                                       ("BACKLOG", p.board.backlog ?? []), ("DONE TODAY", p.board.doneToday ?? [])].filter { !$0.1.isEmpty }
    Block("RUNNING · \(running.count)", "tap a card for steps & numbers") {
      if running.isEmpty { Text("Nothing running").font(f(-1)).foregroundColor(faint) }
      ForEach(running) { TaskRow(t: $0, now: store.now) }
    }
    Block("BOARD") {
      LazyVGrid(columns: [GridItem(.flexible(), spacing: 8, alignment: .top), GridItem(.flexible(), spacing: 8, alignment: .top)], alignment: .leading, spacing: 8) {
        ForEach(cols, id: \.0) { col in
          VStack(alignment: .leading, spacing: 6) {
            Text("\(col.0) · \(col.1.count)").font(fl(.heavy)).tracking(1.4).foregroundColor(faint)
            if col.0 == "DONE TODAY" {
              mini(nil, title: col.1.map { $0.numText }.joined(separator: " · "), sub: "merged today")
            } else {
              ForEach(col.1) { t in mini(t, title: "\(t.numText) \(t.title)", sub: statusText(t, now: store.now)) }
            }
          }.padding(8).frame(maxWidth: .infinity, alignment: .topLeading).background(RoundedRectangle(cornerRadius: 10).fill(bg2))
        }
      }
      if let id = store.focus, let t = allTasks(p.board).first(where: { $0.id == id }) {
        TaskRow(t: t, now: store.now, open: true).id(id)
      }
    }
    Block("DOMAINS & MERGE") {
      ForEach(p.domains ?? []) { d in
        HStack(alignment: .firstTextBaseline, spacing: 8) {
          clip(d.name, f(-1, mono: true), dim)
          Spacer(minLength: 4)
          Text(d.holder.map { "\(shortId($0)) since \(hhmm(d.since))" } ?? (d.note ?? "free")).font(f(-1, mono: true)).foregroundColor(dim).fixedSize()
          if let w = d.waiting, !w.isEmpty { Text("\(w.map { shortId($0) }.joined(separator: ", ")) waiting").font(f(-1, mono: true)).foregroundColor(warnC).fixedSize() }
        }
      }
      if let m = p.merge?.active { Text("merge · \(shortId(m.task)) (gates \(m.gatesDone ?? 0)/\(m.gatesTotal ?? 0))").font(f(-1, mono: true)).foregroundColor(dim) }
    }
    Block("REPO & HEALTH") {
      Text("\(p.project.repo?.commit ?? "—")\((p.project.repo?.changes ?? []).isEmpty ? " · working tree clean" : "")").font(f(-1)).foregroundColor(dim)
      ForEach(p.project.repo?.changes ?? []) { c in HStack { Text(c.area).font(f(-1, mono: true)); Spacer(); Text("\(c.files) files changed").font(f(-1)).foregroundColor(warnC) } }
      Flow(spacing: 14) {
        ForEach(p.project.health ?? []) { h in
          HStack(spacing: 4) {
            Text(h.isUp == true ? "●" : h.isUp == false ? "●" : "○").foregroundColor(h.isUp == true ? okTx : h.isUp == false ? badC : faint)
            Text("\(h.label)\(h.isUp == nil ? " (not checked)" : "")").foregroundColor(dim)
          }.font(f(-1))
        }
      }
    }
  }
  func mini(_ t: TaskVM?, title: String, sub: String) -> some View {
    let border: Color = t?.state == "review" || t?.state == "merging" ? accTx.opacity(0.7) : t?.state == "blocked" ? warnC.opacity(0.6) : line
    return Button(action: { if let t = t { store.focus = store.focus == t.id ? nil : t.id } }) {
      VStack(alignment: .leading, spacing: 3) {
        clip(title, f(-1, .semibold), txC, lines: 2)
        clip(sub, f(-1), faint, lines: 2)
      }.padding(.horizontal, 8).padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(card))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(store.focus == t?.id && t != nil ? accTx : border, lineWidth: 1))
    }.buttonStyle(.plain)
  }
}

struct StudioReview: View {
  let r: ReviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 8) {
        Text(r.task.state == "merging" ? "⇄" : "▣"); NumChip(text: r.task.numText)
        if r.pilot == true { Badge(text: "PILOT", color: accent) }
        clip(r.task.title, ft(-1))
        Spacer(minLength: 4)
        Text("\(r.worker ?? "") \(r.account ?? "")").font(f(-1)).foregroundColor(faint).fixedSize()
      }
      Text(r.summary).font(f(-1)).foregroundColor(dim).fixedSize(horizontal: false, vertical: true)
      let dw = r.doneWhen ?? []
      Text("\(dw.filter { $0.met }.count)/\(dw.count) done-when ✓ · tests \((r.tests ?? []).reduce(0) { $0 + ($1.passed ?? 0) }) · waiting \(dur(r.waitingSec))\(r.unlocks?.isEmpty == false ? " · approving starts \(r.unlocks!.map { shortId($0) }.joined(separator: ", "))" : "")")
        .font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
      ForEach(dw, id: \.self) { d in Text("\(d.met ? "✓" : "✗") \(d.item) — \(d.evidence)").font(f(-1)).foregroundColor(d.met ? okTx : badC).fixedSize(horizontal: false, vertical: true) }
      if let u = r.git?.uncommitted, u > 0 { Text("⚠ \(u) uncommitted change\(u > 1 ? "s" : "") will not be merged").font(f(-1, .bold)).foregroundColor(warnC) }
      ForEach(r.risks ?? [], id: \.self) { Text("• \($0)").font(f(-1)).foregroundColor(dim).fixedSize(horizontal: false, vertical: true) }
      Decisions(r: r)
      ReviewActions(r: r)
    }.padding(12).background(RoundedRectangle(cornerRadius: 12).fill(rgb(T.reviewCard))).overlay(RoundedRectangle(cornerRadius: 12).stroke(accTx.opacity(0.7), lineWidth: 1))
  }
}

struct StudioEvent: View {
  @EnvironmentObject var store: DeckStore
  let e: DeckEvent
  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      Text(hhmm(e.ts)).font(f(-1, mono: true)).foregroundColor(faint)
      Text(e.worker ?? "").font(f(-1, .bold, mono: true)).foregroundColor(store.wText(worker: e.worker) ?? faint).frame(width: 28, alignment: .leading)
      Text(e.severity == "alert" ? "!" : e.severity == "warn" ? "⚠" : e.severity == "action" ? "▣" : "·").font(f(-1))
      Text(e.text).font(f(-1)).foregroundColor(e.severity == "alert" || e.severity == "warn" ? badC : dim).fixedSize(horizontal: false, vertical: true)
    }
  }
}

// MARK: - Mission Control layout (docs/v2/mockups/alt-a/panel.html): title strip, ticker, scope strip, tab strip,
// KPI grids, thin-ruled rows with LEDs and right-aligned numbers, dense mono type.

/// Task state → (hue, label) as in the mockup's ST table.
func mState(_ t: TaskVM) -> (String, String) {
  if isQuiet(t) { return ("rd", "HUNG") }
  switch t.state {
  case "running", "starting": return ("gr", "RUN")
  case "limit_paused": return ("am", "LIMIT")
  case "queued", "ready": return ("cy", "READY")
  case "review": return ("mg", "REVIEW")
  case "merging": return ("gr", "MERGE")
  case "blocked": return ("or", "BLOCKED")
  case "failed": return ("rd", "FAILED")
  case "changes_requested": return ("am", "CHANGES")
  case "done": return ("off", "DONE")
  default: return ("off", t.state.uppercased())
  }
}
func mDepState(_ s: String) -> (String, String) {
  switch s {
  case "done": return ("gr", "DONE"); case "running": return ("gr", "RUN"); case "review", "merging": return ("mg", "REVIEW")
  case "limit_paused": return ("am", "LIMIT"); case "blocked": return ("or", "BLOCKED"); default: return ("cy", s.uppercased())
  }
}
func mPctHue(_ a: Account) -> String { a.state == "cooling" ? "rd" : (a.pct5 ?? 0) >= 50 ? "am" : "gr" }
func mTileHue(_ p: ProjectTile) -> String { (p.counts?.hung ?? 0) > 0 ? "rd" : (p.alerts ?? 0) > 0 ? "am" : (p.counts?.running ?? 0) > 0 ? "gr" : "cy" }

/// Section head: count · LABEL ────── trailing
struct MHead: View {
  let n: String?; let title: String; var trailing: String? = nil; var trailingColor: Color = faint
  var body: some View {
    HStack(spacing: 8) {
      if let n = n { Text(n).font(fl()).foregroundColor(accTx) }
      Text(title.uppercased()).font(fl()).tracking(2.2).foregroundColor(faint).fixedSize()
      HRule().frame(minWidth: 12)
      if let t = trailing { Text(t).font(f(-1)).foregroundColor(trailingColor).lineLimit(2).multilineTextAlignment(.trailing) }
    }.padding(.horizontal, 12).padding(.top, 16).padding(.bottom, 8)
  }
}
/// Ruled grid of KPI cells (value + LED label).
struct MKpis: View {
  let cells: [(String, String, String)]; var cols = 4; var led = true
  var body: some View {
    let rows = stride(from: 0, to: cells.count, by: cols).map { Array(cells[$0..<min($0 + cols, cells.count)]) }
    VStack(spacing: 0) {
      ForEach(Array(rows.enumerated()), id: \.offset) { ri, row in
        HStack(spacing: 0) {
          ForEach(Array(row.enumerated()), id: \.offset) { i, c in
            VStack(alignment: .leading, spacing: 6) {
              Text(c.0).font(.system(size: 22, weight: .semibold, design: .monospaced)).foregroundColor(hue(c.2)).lineLimit(1).minimumScaleFactor(0.7)
              HStack(spacing: 5) { if led { Led(c: hue(c.2), size: 6) }; Text(c.1).font(fl()).tracking(1.4).foregroundColor(faint).lineLimit(1) }
            }.padding(.horizontal, 10).padding(.vertical, 10).frame(maxWidth: .infinity, alignment: .leading)
            if i < row.count - 1 { VRule() }
          }
        }.fixedSize(horizontal: false, vertical: true)
        if ri < rows.count - 1 { HRule() }
      }
      HRule()
    }
  }
}
/// Ruled row container.
func mRow<V: View>(_ v: V) -> some View { VStack(spacing: 0) { v.padding(.horizontal, 12).padding(.vertical, 9); HRule() } }

struct MissionChrome: View {
  @EnvironmentObject var store: DeckStore
  var body: some View {
    VStack(spacing: 0) {
      HStack(spacing: 8) {
        TrafficSpace()
        Spacer()
        Text("AGENT DECK — MISSION CONTROL").font(fl()).tracking(1.8).foregroundColor(faint).lineLimit(1)
        Spacer()
        ThemeControls(tint: faint)
        CollapseButton(tint: faint)
      }.padding(.horizontal, 10).frame(height: 32).background(bg2)
      HRule()
      if store.collapsed { if let o = store.overview { MissionCollapsed(o: o) } }
      else {
        top
        if let o = store.overview { ticker(o); scope(o); tabs(o) }
      }
      if let t = store.toast { Toast(text: t).padding(8) }
    }
  }
  var top: some View {
    VStack(spacing: 0) {
      HStack(spacing: 8) {
        Led(c: store.offline ? hue("rd") : hue("gr"))
        (Text("AGENT") ++ Text("/").foregroundColor(hue("cy")) ++ Text("DECK")).font(cond(19)).tracking(2.4)
        if store.offline { Text("● OFFLINE · last \(hhmm(store.lastUpdate))").font(fl()).tracking(1.4).foregroundColor(hue("rd")) }
        else { Text(store.fixtures != nil ? "● FIXTURES" : "● LIVE").font(fl()).tracking(1.4).foregroundColor(store.fixtures != nil ? hue("am") : hue("gr")) }
        Spacer()
        Text(clockSecFmt.string(from: Date(timeIntervalSince1970: TimeInterval(store.now)))).font(.system(size: 17, weight: .semibold, design: .monospaced))
      }.padding(.horizontal, 12).padding(.vertical, 9)
      HRule()
    }
  }
  func ticker(_ o: OverviewVM) -> some View {
    let evs = ((store.project?.timeline ?? []) + (o.alerts ?? [])).sorted { $0.ts > $1.ts }.prefix(6)
    let text = evs.map { "\(hhmm($0.ts)) \($0.text)" }.joined(separator: "   ·   ")
    return VStack(spacing: 0) {
      HStack(spacing: 10) {
        Text("LIVE").font(fl(.bold)).tracking(1.6).foregroundColor(rgb(T.onAccent)).padding(.horizontal, 8).frame(maxHeight: .infinity).background(accent)
        if let first = evs.first {
          (Text(hhmm(first.ts) + " ").foregroundColor(txC).fontWeight(.semibold) ++ Text(text.dropFirst(hhmm(first.ts).count + 1)).foregroundColor(faint))
            .font(f(-1)).lineLimit(1).help(text)
        } else { Text("quiet — no events").font(f(-1)).foregroundColor(faint) }
        Spacer(minLength: 0)
      }.frame(height: 28).background(bg2)
      HRule()
    }
  }
  func scope(_ o: OverviewVM) -> some View {
    VStack(spacing: 0) {
      HStack(spacing: 0) {
        scopeCell(nil, name: "ALL", sub: "\(o.projects.count) projects", hueName: nil, off: false)
        ForEach(o.projects) { p in
          VRule()
          scopeCell(p.id, name: (p.short ?? p.title).uppercased(), sub: "\(p.counts?.running ?? 0) run · \(p.alerts ?? 0) alert\(p.alerts == 1 ? "" : "s")",
                    hueName: p.enabled == false ? "off" : mTileHue(p), off: p.enabled == false)
        }
      }.fixedSize(horizontal: false, vertical: true)
      HRule()
    }
  }
  func scopeCell(_ id: String?, name: String, sub: String, hueName: String?, off: Bool) -> some View {
    let on = store.selected == id
    return Button(action: { if !off { store.selected = id } }) {
      VStack(alignment: .leading, spacing: 4) {
        HStack(spacing: 5) {
          if let h = hueName { Led(c: hue(h), on: h != "off", size: 7) }
          Text(name).font(cond(15)).tracking(1.4).foregroundColor(on ? hue("cy") : txC).lineLimit(1)
        }
        Text(off ? "paused" : sub).font(fl(.regular)).foregroundColor(faint).lineLimit(1).help(sub)
      }.padding(.horizontal, 9).padding(.vertical, 8).frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
        .background(on ? bg3 : Color.clear).overlay(alignment: .bottom) { Rectangle().fill(on ? hue("cy") : .clear).frame(height: 2) }
        .contentShape(Rectangle())
    }.buttonStyle(.plain).opacity(off ? 0.45 : 1)
  }
  func tabs(_ o: OverviewVM) -> some View {
    let rv = store.selected == nil ? (o.inbox?.review ?? 0) : (store.project?.inbox?.count ?? 0)
    let al = store.selected == nil ? (o.inbox?.alerts ?? (o.alerts ?? []).count) : (store.project?.alerts?.count ?? 0)
    let cur = ["alerts", "timeline", "repo"].contains(store.tab) ? "ops" : ["workers", "board", "review"].contains(store.tab) ? store.tab : "overview"
    let items: [(String, String, Int, String)] = [("overview", store.selected == nil ? "OVR" : "PRJ", 0, ""), ("workers", "WRK", 0, ""), ("board", "BRD", 0, ""),
                                                  ("review", "REV", rv, "mg"), ("ops", "OPS", al, "rd")]
    return VStack(spacing: 0) {
      HStack(spacing: 0) {
        ForEach(Array(items.enumerated()), id: \.offset) { i, t in
          if i > 0 { VRule() }
          Button(action: { store.tab = t.0 == "ops" ? (["alerts", "timeline", "repo"].contains(store.tab) ? store.tab : "alerts") : t.0 }) {
            HStack(spacing: 6) {
              Text(t.1).font(f(-1, .medium)).tracking(1.8).foregroundColor(cur == t.0 ? hue("cy") : faint)
              if t.2 > 0 { Text("\(t.2)").font(fl(.bold)).foregroundColor(rgb(T.onHue)).padding(.horizontal, 4).frame(minWidth: 16, minHeight: 16).background(RoundedRectangle(cornerRadius: 2).fill(hue(t.3))) }
            }.frame(maxWidth: .infinity, minHeight: 38).background(cur == t.0 ? bg3 : Color.clear)
              .overlay(alignment: .top) { Rectangle().fill(cur == t.0 ? hue("cy") : .clear).frame(height: 2).padding(.horizontal, 18).shadow(color: T.dark ? hue("cy") : .clear, radius: 4) }
              .contentShape(Rectangle())
          }.buttonStyle(.plain)
        }
      }.fixedSize(horizontal: false, vertical: true).background(bg2)
      HRule()
    }
  }
}

struct MissionCollapsed: View {
  let o: OverviewVM
  var body: some View {
    let running = o.machine?.running ?? 0
    let cooling = o.accounts.first { $0.state == "cooling" }
    let spare = o.accounts.first { $0.capacity?.spare == true && $0.capacity?.why == nil }
    Flow(spacing: 12, rowSpacing: 4) {
      Led(c: hue("gr")).padding(.top, 4)
      Text("▶\(running) run")
      Text("▣\(o.inbox?.review ?? 0) review").foregroundColor(hue("mg"))
      Text("!\(o.inbox?.alerts ?? 0)").foregroundColor(hue("rd"))
      Text("lanes \(running)/\(o.machine?.lanesTotal ?? 0)")
      if let c = cooling { Text("\(workerId(c)) LIMIT → \(hhmm(c.resets5))").foregroundColor(hue("am")) }
      if let s = spare { Text("⚡ \(workerId(s)) spare").foregroundColor(hue("am")) }
    }.font(f(-1, .medium)).padding(.horizontal, 12).padding(.vertical, 10).frame(maxWidth: .infinity, alignment: .leading)
  }
}

struct MissionBody: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      switch store.tab {
      case "workers": MissionWorkers(o: o)
      case "board": if let p = store.project { MissionBoard(p: p) } else if store.selected != nil { loading } else { MissionBoardAll(o: o) }
      case "review": MissionReview(o: o)
      case "alerts", "timeline", "repo": MissionOps(o: o)
      default: if store.selected == nil { MissionOverview(o: o) } else if let p = store.project { MissionProject(p: p, o: o) } else { loading }
      }
    }.padding(.bottom, 18)
  }
  var loading: some View { Text("Loading project…").font(f(-1)).foregroundColor(faint).padding(12) }
}

struct MissionOverview: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    let needs = needsList(o)
    return VStack(alignment: .leading, spacing: 0) {
      MKpis(cells: [("\(sumCounts(o) { $0.running })", "RUNNING", "gr"), ("\(sumCounts(o) { $0.queued })", "READY", "cy"), ("\(sumCounts(o) { $0.review })", "REVIEW", "mg"),
                    ("\(sumCounts(o) { $0.blocked })", "BLOCKED", "or"), ("\(o.inbox?.alerts ?? (o.alerts ?? []).count)", "ALERTS", "rd"),
                    ("\(o.machine?.running ?? 0)/\(o.machine?.lanesTotal ?? 0)", "LANES", "cy"), ("\(sumCounts(o) { $0.doneToday })", "DONE TODAY", "gr"),
                    ("\((o.inbox?.review ?? 0) + (o.inbox?.questions ?? 0))", "INBOX", "mg")])
      MHead(n: "\(o.projects.count)", title: "Projects")
      HRule()
      ForEach(o.projects) { MProjectRow(p: $0) }
      MHead(n: "\(o.accounts.count)", title: "Accounts · 5h burn")
      HRule()
      ForEach(o.accounts) { MAcctMini(a: $0) }
      ForEach(o.accounts.filter { greyedWhy($0) != nil }) { a in
        mRow(HStack(alignment: .top, spacing: 9) {
          Led(c: faint, on: false).padding(.top, 5)
          Text("\(workerId(a)) is greyed: \(greyedWhy(a) ?? "")").font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
          Spacer(minLength: 0)
        })
      }
      WorkersSection(o: o)
      MHead(n: "\(needs.count)", title: "Needs you")
      HRule()
      if needs.isEmpty { Text("Nothing waits for you.").font(f(-1)).foregroundColor(faint).padding(12) }
      ForEach(needs) { MNeedRow(n: $0) }
    }
  }
}

struct MProjectRow: View {
  @EnvironmentObject var store: DeckStore
  let p: ProjectTile
  var body: some View {
    Button(action: { if p.enabled != false { store.selected = p.id; store.tab = "overview" } }) {
      mRow(HStack(spacing: 9) {
        Led(c: hue(mTileHue(p)), on: p.enabled != false)
        VStack(alignment: .leading, spacing: 3) {
          HStack(spacing: 6) { Text("\(p.icon ?? "") \(p.title)").font(cond(16)).lineLimit(1); mScope(p.scope) }
          clip(p.enabled == false ? "paused (disabled)" : (p.headline ?? ""), f(-1), faint)
        }
        Spacer(minLength: 6)
        VStack(alignment: .trailing, spacing: 2) {
          kv("R", p.counts?.running, "Q", p.counts?.queued)
          kv("V", p.counts?.review, "B", p.counts?.blocked)
        }.font(f(-1)).fixedSize()
      }.contentShape(Rectangle())).opacity(p.enabled == false ? 0.5 : 1)
    }.buttonStyle(.plain)
  }
  func kv(_ a: String, _ x: Int?, _ b: String, _ y: Int?) -> Text {
    Text(a).foregroundColor(faint) ++ Text("\(x ?? 0) ").foregroundColor(txC) ++ Text(b).foregroundColor(faint) ++ Text("\(y ?? 0)").foregroundColor(txC)
  }
}

struct MAcctMini: View {
  @EnvironmentObject var store: DeckStore
  let a: Account
  var body: some View {
    let off = a.dispatch == false
    let h = mPctHue(a)
    mRow(HStack(spacing: 10) {
      Led(c: hue(off ? "off" : h), on: !off)
      VStack(alignment: .leading, spacing: 2) {
        HStack(spacing: 5) { Text(workerId(a)).font(f(-1, .semibold)).foregroundColor(store.wText(account: a.id) ?? txC); if a.scope == "office" { mScope(a.scope) } }
        clip(a.label, f(-1), faint)
      }.frame(width: 150, alignment: .leading)
      QBar(pct: a.state == "cooling" ? 100 : (a.pct5 ?? 0), c: hue(h)).opacity(off ? 0.45 : 1).saturation(off ? 0 : 1)
      VStack(alignment: .trailing, spacing: 2) { Text(pct(a.pct5)).font(f(-1, .semibold)); Text("5h").font(fl(.regular)).foregroundColor(faint) }.frame(width: 46, alignment: .trailing)
      VStack(alignment: .trailing, spacing: 2) { Text(store.countdown(a.resets5)).font(f(-1, .semibold)); Text("reset").font(fl(.regular)).foregroundColor(faint) }.frame(width: 62, alignment: .trailing)
    })
  }
}

struct MNeedRow: View {
  let n: NeedsItem
  var body: some View {
    let h = n.kind == "review" ? "mg" : n.kind == "question" ? "or" : "rd"
    mRow(VStack(alignment: .leading, spacing: 8) {
      HStack(alignment: .top, spacing: 9) {
        Led(c: hue(h)).padding(.top, 6)
        VStack(alignment: .leading, spacing: 3) {
          (Text(n.task.map { "\(n.num.map(String.init) ?? shortId($0)) " } ?? "").foregroundColor(hue("cy")) ++ Text(n.title)).font(cond(16)).lineLimit(1).help(n.title)
          Text(n.text).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
        }
        Spacer(minLength: 6)
        Text(n.kind == "review" ? "REVIEW" : n.kind == "question" ? "ANSWER" : "ALERT").font(f(-1, .semibold)).foregroundColor(txC)
      }
      NeedActions(n: n).padding(.leading, 17)
    })
  }
}

/// One lane inside the ruled lanes box.
struct MLane: View {
  @EnvironmentObject var store: DeckStore
  let a: Account; let l: Lane
  var body: some View {
    let free = freeLaneText(a)
    let quiet = l.state == "running" && (l.lastActiveSec ?? 0) > 600
    let c: Color = l.state == "cooling" ? hue("am") : quiet ? txC : l.state == "running" ? txC : free.kind == "spare" ? hue("am") : free.kind == "cool" ? hue("am") : faint
    VStack(spacing: 0) {
      HStack(alignment: .firstTextBaseline, spacing: 8) {
        Text("\(l.worker)·\(l.lane)").font(f(-1, .semibold)).foregroundColor(store.wText(worker: l.worker) ?? faint).frame(width: 46, alignment: .leading)
        main.font(f(-1)).foregroundColor(c).lineLimit(l.state == "free" ? nil : 1).fixedSize(horizontal: false, vertical: l.state == "free")
        Spacer(minLength: 4)
        Text(right(quiet)).font(f(-1)).foregroundColor(quiet ? hue("rd") : faint).fixedSize()
      }.padding(.horizontal, 8).padding(.vertical, 7).frame(minHeight: 34)
        .background(quiet ? hue("rd").opacity(0.08) : l.state == "free" && free.kind == "spare" ? hue("am").opacity(0.08) : Color.clear)
      HRule()
    }.help(helpText(free.text))
  }
  var main: Text {
    switch l.state {
    case "running": return Text("▶ ") ++ Text(shortId(l.task)).foregroundColor(hue("cy")).fontWeight(.semibold) ++ Text(" \(l.title ?? "")\(l.stepCount.map { " · \(l.stepIndex ?? 0)/\($0)" } ?? "")")
    case "cooling": return Text("⏸ ") ++ Text(shortId(l.task)).foregroundColor(hue("cy")) ++ Text(" \(l.title ?? "") · resumes at reset")
    default:
      let f = freeLaneText(a)
      return Text(f.kind == "spare" ? "○ SPARE · " + f.text.replacingOccurrences(of: "○ free · ", with: "") : f.text)
    }
  }
  func right(_ quiet: Bool) -> String {
    switch l.state {
    case "running": return quiet ? "HUNG \(dur(l.lastActiveSec))" : l.etaMin.map { "~\(Int($0))m" } ?? ""
    case "cooling": return store.countdown(l.resumeAt ?? a.resets5)
    default: return freeLaneText(a).kind == "spare" ? "⚠" : "—"
    }
  }
  func helpText(_ freeText: String) -> String { l.state == "free" ? freeText : "\(shortId(l.task)) \(l.title ?? "")" }
}
func mLanesBox<V: View>(@ViewBuilder _ v: () -> V) -> some View {
  VStack(spacing: 0) { v() }.padding(.bottom, -1).clipped().overlay(Rectangle().stroke(line, lineWidth: 1))
}

struct MissionProject: View {
  @EnvironmentObject var store: DeckStore
  let p: ProjectVM; let o: OverviewVM
  var body: some View {
    let tile = store.tile(p.project.id)
    let health = p.project.health ?? []
    let hh = health.contains { $0.isUp == false } ? "am" : "gr"
    let lanes = store.lanes(on: p.project.id)
    let b = p.board
    let now = (b.running ?? []) + (b.upNext ?? [])
    let needCount = (p.inbox ?? []).count + (b.needsYou ?? []).count
    VStack(alignment: .leading, spacing: 0) {
      VStack(alignment: .leading, spacing: 6) {
        HStack(spacing: 8) { mScope(p.project.scope); Text(p.project.allowPersonalAccounts == false ? "OFFICE ACCOUNTS ONLY" : "PERSONAL ACCOUNTS ALLOWED").font(fl()).tracking(1.4).foregroundColor(faint) }
        Text("\(tile?.icon ?? "") \(p.project.title.uppercased())").font(cond(30)).tracking(1.2).lineLimit(1).minimumScaleFactor(0.7)
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          Led(c: hue(hh), size: 7)
          Text("\(p.project.repo?.commit ?? "—")\(health.filter { $0.isUp == false }.map { " · \($0.label) down" }.joined())").font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
        }
      }.padding(12)
      HRule()
      MKpis(cells: [("\((b.running ?? []).count)", "RUNNING", "gr"), ("\((b.upNext ?? []).count + (b.backlog ?? []).count)", "READY", "cy"),
                    ("\((b.review ?? []).count)", "REVIEW", "mg"), ("\((b.needsYou ?? []).count)", "BLOCKED", "or"),
                    ("\((p.alerts ?? []).count)", "ALERTS", "rd"), ("\(lanes.count)", "LANES", "cy"), ("\((b.doneToday ?? []).count)", "DONE TODAY", "gr"),
                    ("\((p.inbox ?? []).count)", "INBOX", "mg")])
      MHead(n: "\(lanes.count)", title: "Lanes on this project")
      if lanes.isEmpty { Text("No lanes running here.").font(f(-1)).foregroundColor(faint).padding(.horizontal, 12) }
      else { mLanesBox { ForEach(lanes, id: \.l.id) { MLane(a: $0.a, l: $0.l) } }.padding(.horizontal, 12) }
      MHead(n: "\(now.count)", title: "Board · now and next")
      HRule()
      ForEach(now) { MTaskRow(t: $0) }
      MHead(n: "\(needCount)", title: "Needs you")
      HRule()
      if needCount == 0 { Text("Nothing waits for you.").font(f(-1)).foregroundColor(faint).padding(12) }
      ForEach(p.inbox ?? []) { r in
        Button(action: { store.tab = "review" }) {
          mRow(HStack(spacing: 9) {
            Led(c: hue("mg"))
            VStack(alignment: .leading, spacing: 3) {
              (Text("\(r.task.numText) ").foregroundColor(hue("cy")) ++ Text(r.task.title + (r.pilot == true ? " · PILOT" : ""))).font(cond(16)).lineLimit(1).help(r.task.title)
              Text("\(r.worker ?? "") · \((r.doneWhen ?? []).filter { $0.met }.count)/\((r.doneWhen ?? []).count) done-when · waiting \(dur(r.waitingSec))").font(f(-1)).foregroundColor(faint)
            }
            Spacer(minLength: 4)
            Text("REVIEW").font(f(-1, .semibold))
          }.contentShape(Rectangle()))
        }.buttonStyle(.plain)
      }
      ForEach(b.needsYou ?? []) { MTaskRow(t: $0) }
      MHead(n: nil, title: "Accounts allowed here")
      HRule()
      ForEach(store.workerRows, id: \.a.id) { w in
        mRow(HStack(alignment: .top, spacing: 9) {
          Led(c: hue(w.allowed ? "gr" : "off"), on: w.allowed).padding(.top, 5)
          VStack(alignment: .leading, spacing: 3) {
            Text("\(workerId(w.a)) · \(w.a.label)").font(f(-1, .semibold))
            Text(w.allowed ? "Allowed · \(freeLaneText(w.a).text)" : "🔒 \(w.reason ?? "not allowed")").font(f(-1)).foregroundColor(w.allowed ? faint : hue("rd")).fixedSize(horizontal: false, vertical: true)
          }
          Spacer(minLength: 4)
          Text(pct(w.a.pct5)).font(f(-1)).foregroundColor(faint)
        }).opacity(w.allowed ? 1 : 0.8)
      }
    }
  }
}

/// Ruled task row: LED · id + title / meta · right-aligned number. Tap to open steps, numbers and actions.
struct MTaskRow: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM
  @State private var open = false
  var body: some View {
    let s = mState(t)
    mRow(VStack(alignment: .leading, spacing: 8) {
      HStack(alignment: .top, spacing: 9) {
        Led(c: hue(s.0), on: s.0 != "off").padding(.top, 6)
        VStack(alignment: .leading, spacing: 3) {
          (Text("\(t.numText) ").foregroundColor(hue("cy")) ++ Text(t.title)).font(cond(16)).lineLimit(1).help(t.title)
          clip(meta, f(-1), isQuiet(t) ? hue("rd") : faint, lines: open ? 4 : 1)
        }
        Spacer(minLength: 6)
        WorkerBadge(worker: t.worker).padding(.top, 3)
        right.multilineTextAlignment(.trailing).fixedSize()
      }.contentShape(Rectangle()).onTapGesture { open.toggle() }
      let chips = chipsList
      if !chips.isEmpty {
        Flow(spacing: 4, rowSpacing: 4) {
          ForEach(chips, id: \.0) { c in
            Text(c.0).font(fl(.regular)).foregroundColor(hue(c.1)).padding(.horizontal, 6).padding(.vertical, 2)
              .overlay(RoundedRectangle(cornerRadius: 2).stroke(c.1 == "off" ? rule2 : hue(c.1).opacity(0.5), lineWidth: 1))
          }
        }.padding(.leading, 17)
      }
      if open { TaskDetail(t: t).padding(.leading, 17) }
    }.overlay(alignment: .leading) { Rectangle().fill(store.wLine(worker: t.worker) ?? .clear).frame(width: 3).padding(.leading, -12).padding(.vertical, -9) })
  }
  var meta: String {
    switch t.state {
    case "running" where t.step != nil: return "\(t.worker ?? "") · \(t.step!.index)/\(t.step!.count) \(t.step!.title)\(isQuiet(t) ? " · quiet \(dur(t.lastActiveSec))" : "")"
    case "limit_paused": return "\(t.worker ?? "") · \(t.stateReason ?? "limit") · resumes \(hhmm(t.resumeAt))"
    default: return statusText(t, now: store.now)
    }
  }
  var right: some View {
    let top: (String, Color), sub: String
    if isQuiet(t) { top = (dur(t.lastActiveSec), hue("rd")); sub = "quiet" }
    else if t.state == "running" { top = (t.step?.etaMin.map { "~\(Int($0))m" } ?? "—", txC); sub = "\(t.size ?? "")·\(t.priority ?? "")" }
    else if t.state == "limit_paused" { top = (store.countdown(t.resumeAt), hue("am")); sub = "to reset" }
    else if t.state == "review" { top = (t.kind == "pilot" ? "PILOT" : "REVIEW", txC); sub = t.priority ?? "" }
    else if t.state == "merging" { top = ("MERGE", txC); sub = t.resumeAt.map { "retry \(store.countdown($0))" } ?? "gates" }
    else { top = (t.size ?? "—", txC); sub = t.priority ?? "" }
    return VStack(alignment: .trailing, spacing: 2) { Text(top.0).font(f(-1, .semibold)).foregroundColor(top.1); Text(sub).font(fl(.regular)).foregroundColor(faint) }
  }
  var chipsList: [(String, String)] {
    var c: [(String, String)] = (t.deps ?? []).map { ("⟵ \(shortId($0.id)) \($0.state.replacingOccurrences(of: "_", with: " "))", $0.state == "done" ? "gr" : "am") }
    if let m = t.metrics?.first, let tot = m.total { c.append(("\(m.label) \(Int(m.done))/\(Int(tot))", "off")) }
    if t.kind == "pilot" { c.append(("PILOT", "am")) }
    return c
  }
}

struct MissionWorkers: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    let spare = o.accounts.filter { $0.capacity?.spare == true && $0.capacity?.why == nil }.count
    let cooling = o.accounts.filter { $0.state == "cooling" }.count
    VStack(alignment: .leading, spacing: 0) {
      MKpis(cells: [("\(o.machine?.running ?? 0)/\(o.machine?.lanesTotal ?? 0)", "LANES BUSY", "gr"), ("\(spare)", "SPARE", "am"),
                    ("\(cooling)", "COOLING", "am"), ("\(o.machine?.maxParallelTotal ?? 0)", "MACHINE CAP", "cy")], led: false)
      if let b = o.backlog {
        MHead(n: nil, title: "Backlog", trailing: "\(b.eligible ?? 0) ready / target \(b.target ?? 0)\(b.hint.map { " · \($0)" } ?? "")", trailingColor: hue("am"))
        HRule()
      }
      ForEach(store.workerRows, id: \.a.id) { w in MAcct(a: w.a, allowed: w.allowed, reason: w.reason) }
    }
  }
}

struct MAcct: View {
  @EnvironmentObject var store: DeckStore
  let a: Account; let allowed: Bool; let reason: String?
  var body: some View {
    let off = a.dispatch == false
    let h = mPctHue(a)
    let role = off ? "USAGE ONLY" : a.state == "cooling" ? "AT LIMIT" : "\(a.capacity?.lanesUsed ?? 0)/\(a.capacity?.lanesMax ?? 0) LANES"
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 7) {
        Led(c: hue(off ? "off" : h), on: !off)
        Text(workerId(a)).font(f(-1, .semibold)).foregroundColor(store.wText(account: a.id) ?? hue("cy"))
        Text(a.label).font(cond(17)).tracking(0.6).lineLimit(1)
        mScope(a.scope)
        Spacer(minLength: 4)
        Text(role).font(fl()).tracking(1.2).foregroundColor(faint)
      }
      Group {
        HStack(alignment: .bottom) {
          VStack(alignment: .leading, spacing: 3) {
            Text(a.state == "cooling" ? "LIMIT" : pct(a.pct5)).font(.system(size: 26, weight: .semibold, design: .monospaced)).foregroundColor(hue(h))
            Text("5H USED").font(fl()).tracking(1.6).foregroundColor(faint)
          }
          Spacer()
          VStack(alignment: .trailing, spacing: 3) {
            Text(store.countdown(a.resets5)).font(.system(size: 16, weight: .semibold, design: .monospaced))
            Text("RESETS \(hhmm(a.resets5))").font(fl()).tracking(1.4).foregroundColor(faint)
          }
        }
        QBar(pct: a.state == "cooling" ? 100 : (a.pct5 ?? 0), c: hue(h))
        HStack(spacing: 8) {
          Text("7D").font(fl()).foregroundColor(faint)
          Bar(value: (a.pct7 ?? 0) / 100, color: faint, height: 2, track: line)
          Text("\(pct(a.pct7)) · read \(dur(a.readAgeSec)) ago").font(f(-1)).foregroundColor(faint).fixedSize()
        }
      }.opacity(off || !allowed ? 0.55 : 1).saturation(off ? 0 : 1)
      if let lanes = a.lanes, !lanes.isEmpty { mLanesBox { ForEach(lanes) { MLane(a: a, l: $0) } } }
      else { Text(off ? "Usage only · never leased" : "No lanes").font(f(-1)).foregroundColor(faint) }
      if !allowed, let r = reason {
        HStack(alignment: .top, spacing: 7) { Text("🔒"); (Text("Not allowed here. ").fontWeight(.semibold).foregroundColor(txC) ++ Text(r)).font(f(-1)).foregroundColor(hue("rd")).fixedSize(horizontal: false, vertical: true) }
          .padding(8).frame(maxWidth: .infinity, alignment: .leading).overlay(Rectangle().stroke(hue("rd").opacity(0.5), style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
      } else if let why = greyedWhy(a), off {
        HStack(alignment: .top, spacing: 7) { Text("🔒"); (Text("Greyed out. ").fontWeight(.semibold).foregroundColor(txC) ++ Text(why)).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true) }
          .padding(8).frame(maxWidth: .infinity, alignment: .leading).overlay(Rectangle().stroke(rule2, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
      }
    }.padding(12).overlay(alignment: .bottom) { HRule() }
  }
}

struct MissionBoard: View {
  @EnvironmentObject var store: DeckStore
  let p: ProjectVM
  var body: some View {
    let b = p.board
    let ready = (b.upNext ?? []) + (b.backlog ?? [])
    let cols: [(String, String, [TaskVM])] = [("READY", "cy", ready), ("RUNNING", "gr", b.running ?? []), ("REVIEW", "mg", b.review ?? []), ("BLOCKED", "or", b.needsYou ?? [])]
    let all = allTasks(b)
    let edges: [(DepVM, TaskVM)] = all.flatMap { t in (t.deps ?? []).filter { $0.state != "done" }.map { ($0, t) } }
    VStack(alignment: .leading, spacing: 0) {
      MKpis(cells: cols.map { ("\($0.2.count)", $0.0, $0.1) }, led: false)
      MHead(n: "\(edges.count)", title: "Open dependencies")
      VStack(alignment: .leading, spacing: 6) {
        if edges.isEmpty { Text("None.").font(f(-1)).foregroundColor(faint) }
        ForEach(Array(edges.enumerated()), id: \.offset) { _, e in
          let ds = mDepState(e.0.state)
          HStack(spacing: 7) {
            Led(c: hue(ds.0), size: 7)
            (Text(shortId(e.0.id) + " ").foregroundColor(hue("cy")).fontWeight(.semibold) ++ Text(ds.1)).font(f(-1)).fixedSize()
            ZStack(alignment: .trailing) {
              Rectangle().fill(LinearGradient(colors: [rule2, hue("cy")], startPoint: .leading, endPoint: .trailing)).frame(height: 1)
              Image(systemName: "arrowtriangle.right.fill").font(.system(size: 7)).foregroundColor(hue("cy"))
            }.frame(minWidth: 24)
            (Text(e.1.numText + " ").foregroundColor(hue("cy")).fontWeight(.semibold) ++ Text(e.1.title)).font(f(-1)).lineLimit(1).help(e.1.title)
          }
        }
      }.padding(.horizontal, 12).padding(.bottom, 4)
      ForEach(cols, id: \.0) { c in
        MHead(n: "\(c.2.count)", title: c.0)
        HRule()
        if c.2.isEmpty { Text("Empty.").font(f(-1)).foregroundColor(faint).padding(12) }
        ForEach(c.2) { MTaskRow(t: $0) }
      }
      if let d = b.doneToday, !d.isEmpty {
        MHead(n: "\(d.count)", title: "Done today")
        HRule()
        ForEach(d) { MTaskRow(t: $0) }
      }
    }
  }
}

/// Board with ALL in scope: per-project column counts (boards are per project in the API).
struct MissionBoardAll: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    return VStack(alignment: .leading, spacing: 0) {
      MKpis(cells: [("\(sumCounts(o) { $0.queued })", "READY", "cy"), ("\(sumCounts(o) { $0.running })", "RUNNING", "gr"), ("\(sumCounts(o) { $0.review })", "REVIEW", "mg"), ("\(sumCounts(o) { $0.blocked })", "BLOCKED", "or")], led: false)
      MHead(n: "\(o.projects.count)", title: "Boards", trailing: "pick a project in the strip above")
      HRule()
      ForEach(o.projects) { MProjectRow(p: $0) }
    }
  }
}

struct MissionReview: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if store.selected == nil {
        let items = needsList(o).filter { $0.kind != "alert" }
        MHead(n: "\(items.count)", title: "Review inbox · all projects")
        HRule()
        if items.isEmpty { Text("Review inbox is empty.").font(f(-1)).foregroundColor(faint).padding(12) }
        ForEach(items) { MNeedRow(n: $0) }
      } else if let p = store.project {
        let items = (p.inbox ?? []).sorted { ($0.waitingSec ?? 0) > ($1.waitingSec ?? 0) }
        MHead(n: "\(items.count)", title: "Review inbox · oldest first")
        HRule()
        if items.isEmpty { Text("Review inbox is empty for this project.").font(f(-1)).foregroundColor(faint).padding(12) }
        ForEach(items) { MReviewCard(r: $0, scope: p.project.scope, projectTitle: p.project.title) }
      }
    }
  }
}

struct MReviewCard: View {
  let r: ReviewVM; let scope: String?; let projectTitle: String
  var body: some View {
    let dw = r.doneWhen ?? []
    let g = r.git
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 7) {
        Led(c: hue("mg"))
        Text(r.task.numText).font(f(-1, .semibold)).foregroundColor(hue("cy"))
        Text(r.task.title).font(cond(18)).lineLimit(1).help(r.task.title)
        if r.pilot == true { MSt(text: "PILOT", c: hue("am")) }
        mScope(scope)
        Spacer(minLength: 0)
      }
      Text("\(projectTitle) · \(r.worker ?? "") \(r.account ?? "") · waiting \(dur(r.waitingSec))\(r.unlocks?.isEmpty == false ? " · approving unlocks \(r.unlocks!.map { shortId($0) }.joined(separator: ", "))" : "")")
        .font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
      Text(r.summary).font(.system(size: 14)).fixedSize(horizontal: false, vertical: true)
      HStack(spacing: 8) {
        Text("\(dw.filter { $0.met }.count)/\(dw.count)").font(fl()).foregroundColor(hue("cy"))
        Text("DONE WHEN").font(fl()).tracking(2).foregroundColor(faint); HRule()
      }.padding(.top, 4)
      ForEach(dw, id: \.self) { d in
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          Text(d.met ? "[✓]" : "[✗]").font(f(-1)).foregroundColor(hue(d.met ? "gr" : "rd"))
          VStack(alignment: .leading, spacing: 1) {
            Text(d.item).font(f(-1)).fixedSize(horizontal: false, vertical: true)
            Text(d.evidence).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
          }
        }
      }
      HStack(spacing: 0) {
        cell("\(g?.commits ?? 0)", "COMMITS", txC); VRule(); cell("\(g?.filesChanged ?? 0)", "FILES", txC); VRule()
        cell("\(g?.uncommitted ?? 0)", "UNCOMMIT", (g?.uncommitted ?? 0) > 0 ? hue("am") : txC); VRule()
        cell(g?.conflictsWithMain == true ? "YES" : "0", "CONFLICTS", g?.conflictsWithMain == true ? hue("rd") : hue("gr"))
      }.fixedSize(horizontal: false, vertical: true).overlay(Rectangle().stroke(line, lineWidth: 1))
      let tests = r.tests ?? []
      if !tests.isEmpty {
        Text("TESTS " + tests.map { "\($0.command) \($0.passed ?? 0)✓\(($0.failed ?? 0) > 0 ? " \($0.failed!)✗" : "")" }.joined(separator: " · ")).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
      }
      if let u = g?.uncommitted, u > 0 { Text("⚠ \(u) uncommitted change\(u > 1 ? "s" : "") will not be merged").font(f(-1)).foregroundColor(hue("am")) }
      ForEach(r.risks ?? [], id: \.self) { Text("▲ RISK · \($0)").font(f(-1)).foregroundColor(hue("am")).fixedSize(horizontal: false, vertical: true) }
      Decisions(r: r)
      ReviewActions(r: r).padding(.top, 4)
    }.padding(12).overlay(alignment: .bottom) { HRule() }
  }
  func cell(_ v: String, _ l: String, _ c: Color) -> some View {
    VStack(alignment: .leading, spacing: 2) { Text(v).font(f(1, .semibold)).foregroundColor(c); Text(l).font(fl()).tracking(1.2).foregroundColor(faint) }
      .padding(.horizontal, 8).padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
  }
}

struct MissionOps: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    let alerts = store.selected == nil ? (o.alerts ?? []) : (store.project?.alerts ?? [])
    VStack(alignment: .leading, spacing: 0) {
      HStack(spacing: 0) {
        sub("alerts", "ALERTS \(alerts.count)"); VRule(); sub("timeline", "TIMELINE"); VRule(); sub("repo", "REPO")
      }.fixedSize(horizontal: false, vertical: true)
      HRule()
      switch store.tab {
      case "timeline":
        let evs = (store.selected == nil ? (o.alerts ?? []) : (store.project?.timeline ?? [])).sorted { $0.ts > $1.ts }
        if store.selected == nil { Text("The timeline is kept per project; showing the overview's alerts. Pick a project above for its log.").font(f(-1)).foregroundColor(faint).padding(12) }
        ForEach(evs) { e in
          VStack(spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
              Text(clockSecFmt.string(from: Date(timeIntervalSince1970: TimeInterval(e.ts)))).font(f(-1)).foregroundColor(faint).frame(width: 72, alignment: .leading)
              Text(e.worker ?? "—").font(f(-1, .semibold)).foregroundColor(store.wText(worker: e.worker) ?? faint).frame(width: 28, alignment: .leading)
              Text(e.type.uppercased()).font(fl()).tracking(1).foregroundColor(hue(evHue(e))).frame(width: 64, alignment: .leading).lineLimit(1).help(e.type)
              Text(e.text).font(f(-1)).fixedSize(horizontal: false, vertical: true)
              Spacer(minLength: 0)
            }.padding(.horizontal, 12).padding(.vertical, 6)
            Rectangle().fill(line.opacity(0.6)).frame(height: 1)
          }
        }
      case "repo":
        if let p = store.project { MRepo(p: p) } else { Text("Repo health is per project — pick one in the strip above.").font(f(-1)).foregroundColor(faint).padding(12) }
      default:
        if alerts.isEmpty { Text("No alerts.").font(f(-1)).foregroundColor(faint).padding(12) }
        ForEach(alerts) { e in
          mRow(HStack(alignment: .top, spacing: 10) {
            Text(e.type.uppercased()).font(fl()).tracking(1).foregroundColor(hue(evHue(e))).lineLimit(1).frame(width: 64).padding(.vertical, 4)
              .overlay(Rectangle().stroke(hue(evHue(e)).opacity(0.5), lineWidth: 1)).help(e.type)
            VStack(alignment: .leading, spacing: 4) {
              Text(e.text).font(cond(16)).fixedSize(horizontal: false, vertical: true)
              Text("\(hhmm(e.ts)) · \(store.projectNames[e.project ?? ""] ?? e.project ?? "deck") · severity \(e.severity)").font(f(-1)).foregroundColor(faint)
              if e.task != nil, let p = e.project { Key(title: "Open") { store.selected = p; store.tab = "board" } }
            }
            Spacer(minLength: 0)
          })
        }
      }
    }
  }
  func sub(_ id: String, _ label: String) -> some View {
    Button(action: { store.tab = id }) {
      Text(label).font(f(-1, .medium)).tracking(1.6).foregroundColor(store.tab == id ? hue("cy") : faint).frame(maxWidth: .infinity, minHeight: 40)
        .overlay(alignment: .bottom) { Rectangle().fill(store.tab == id ? hue("cy") : .clear).frame(height: 2) }.contentShape(Rectangle())
    }.buttonStyle(.plain)
  }
}
func evHue(_ e: DeckEvent) -> String { e.severity == "alert" ? "rd" : e.severity == "warn" ? "am" : e.severity == "action" ? "mg" : "cy" }

struct MRepo: View {
  @EnvironmentObject var store: DeckStore
  let p: ProjectVM
  var body: some View {
    let health = p.project.health ?? []
    let changes = p.project.repo?.changes ?? []
    VStack(alignment: .leading, spacing: 10) {
      HStack(spacing: 7) {
        Led(c: hue(health.contains { $0.isUp == false } ? "am" : "gr"))
        Text(p.project.title).font(cond(17)); mScope(p.project.scope); Spacer()
      }
      VStack(spacing: 0) {
        HStack(spacing: 0) {
          cell("HEAD", p.project.repo?.commit ?? "—", txC); VRule()
          cell("TREE", changes.isEmpty ? "clean" : "\(changes.reduce(0) { $0 + $1.files }) changed", changes.isEmpty ? hue("gr") : hue("am"))
        }.fixedSize(horizontal: false, vertical: true)
        HRule()
        HStack(spacing: 0) {
          cell("MERGE", p.merge?.active.map { "\(shortId($0.task)) · gates \($0.gatesDone ?? 0)/\($0.gatesTotal ?? 0)" } ?? "idle", txC); VRule()
          cell("SERVICES", "\(health.filter { $0.isUp == true }.count)/\(health.count) up", health.contains { $0.isUp == false } ? hue("rd") : hue("gr"))
        }.fixedSize(horizontal: false, vertical: true)
      }.overlay(Rectangle().stroke(line, lineWidth: 1))
      Flow(spacing: 12) {
        ForEach(health) { h in HStack(spacing: 5) { Led(c: hue(h.isUp == true ? "gr" : h.isUp == false ? "rd" : "off"), on: h.isUp != nil, size: 7); Text(h.label + (h.isUp == nil ? " (not checked)" : "")).font(f(-1)) } }
      }
      ForEach(changes) { c in HStack { Text(c.area).font(f(-1)).foregroundColor(hue("cy")); Spacer(); Text("\(c.files) files changed").font(f(-1)).foregroundColor(hue("am")) } }
      VStack(alignment: .leading, spacing: 4) {
        ForEach(p.domains ?? []) { d in
          HStack(alignment: .firstTextBaseline, spacing: 8) {
            clip(d.name, f(-1), hue("cy")).frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .leading, spacing: 1) {
              Text(d.holder.map { "\(shortId($0)) since \(hhmm(d.since))" } ?? (d.note ?? "free")).font(f(-1))
              if let w = d.waiting, !w.isEmpty { Text("\(w.map { shortId($0) }.joined(separator: ", ")) waiting").font(f(-1)).foregroundColor(hue("am")) }
            }.frame(maxWidth: .infinity, alignment: .leading)
          }
        }
      }
    }.padding(12)
  }
  func cell(_ l: String, _ v: String, _ c: Color) -> some View {
    VStack(alignment: .leading, spacing: 2) { Text(l).font(fl()).tracking(1.4).foregroundColor(faint); clip(v, f(-1, .semibold), c) }
      .padding(.horizontal, 8).padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
  }
}

// MARK: - Paper layout (docs/v2/mockups/alt-b/panel.html): serif greeting + lede from real counts, soft rounded cards with
// coloured left borders and kickers, progress rings per account, segmented top tabs.

func pTone(_ t: TaskVM) -> (String, String) {
  if isQuiet(t) { return ("rose", "Looks stuck") }
  switch t.state {
  case "running", "starting", "merging": return ("sage", "Running")
  case "limit_paused": return ("butter", "Resting")
  case "queued", "ready": return ("sky", "Ready")
  case "review": return ("lilac", "In review")
  case "blocked": return ("peach", "Blocked")
  case "failed": return ("rose", "Failed")
  case "done": return ("grey", "Done")
  default: return ("grey", t.state.replacingOccurrences(of: "_", with: " ").capitalized)
  }
}
func pAccTone(_ a: Account) -> String { a.dispatch == false ? "grey" : a.state == "cooling" ? "rose" : (a.pct5 ?? 0) >= 50 ? "butter" : "sage" }
func pEvTone(_ e: DeckEvent) -> (String, String) {
  switch e.severity { case "alert": return ("rose", "◼"); case "warn": return ("butter", "◌"); case "action": return ("lilac", "▣"); default: return ("sky", "◷") }
}
let paperDateFmt: DateFormatter = { let f = DateFormatter(); f.dateFormat = "EEEE d MMMM"; return f }()

/// Soft card: rounded, shadowed, optionally with a coloured left border.
func pCard<V: View>(_ border: String? = nil, fill: Color? = nil, @ViewBuilder _ v: () -> V) -> some View {
  v().padding(.vertical, 13).padding(.leading, border == nil ? 15 : 19).padding(.trailing, 15).frame(maxWidth: .infinity, alignment: .leading)
    .background(fill ?? card)
    .overlay(alignment: .leading) { if let b = border { Rectangle().fill(toneInk(b)).frame(width: 5) } }
    .clipShape(RoundedRectangle(cornerRadius: 16))
    .shadow(color: .black.opacity(T.dark ? 0.25 : 0.06), radius: 8, y: 3)
}
struct PH2: View {
  let title: String; var small: String = ""
  var body: some View {
    HStack(alignment: .firstTextBaseline) {
      Text(title).font(serif(19, .medium)); Spacer()
      Text(small).font(f(-2)).foregroundColor(faint)
    }.padding(.top, 22).padding(.bottom, 10).padding(.horizontal, 2)
  }
}
struct PKicker: View {
  let text: String; let tone: String
  var body: some View { Text(text.uppercased()).font(fl(.bold)).tracking(1).foregroundColor(toneInk(tone)) }
}
func pProjPill(_ store: DeckStore, _ id: String?) -> some View {
  let t = store.tile(id)
  return Group { if let t = t { Pill(text: "\(t.icon ?? "") \(t.title)", tone: "grey") } }
}

struct PaperChrome: View {
  @EnvironmentObject var store: DeckStore
  var body: some View {
    VStack(spacing: 8) {
      HStack(spacing: 8) {
        TrafficSpace()
        Spacer()
        Text(store.project?.project.title ?? "Agent Deck").font(f(-2)).foregroundColor(faint).tracking(0.4).lineLimit(1)
        Spacer()
        ThemeControls(pills: true)
        CollapseButton(tint: faint)
      }.padding(.horizontal, 14).padding(.top, 8)
      if store.collapsed { if let o = store.overview { PaperCollapsed(o: o) } }
      else if let o = store.overview { seg(o) }
      if store.offline { Pill(text: "Offline · last update \(hhmm(store.lastUpdate)) · retrying", tone: "rose") }
      if let t = store.toast { Toast(text: t).padding(.horizontal, 14) }
    }.padding(.bottom, 6)
  }
  var current: String {
    switch store.tab {
    case "workers": return "accounts"
    case "board": return "board"
    case "review", "alerts": return "inbox"
    case "projects", "repo", "timeline": return "projects"
    default: return store.selected == nil ? "today" : "projects"
    }
  }
  func seg(_ o: OverviewVM) -> some View {
    let n = (o.inbox?.review ?? 0) + (o.inbox?.alerts ?? 0)
    let items: [(String, String)] = [("today", "Today"), ("projects", "Projects"), ("accounts", "Accounts"), ("board", "Board"), ("inbox", "Inbox")]
    return HStack(spacing: 4) {
      ForEach(items, id: \.0) { it in
        Button(action: { go(it.0) }) {
          HStack(spacing: 5) {
            Text(it.1).font(f(-2, current == it.0 ? .semibold : .regular)).foregroundColor(current == it.0 ? txC : faint)
            if it.0 == "inbox" && n > 0 { Text("\(n)").font(fl(.bold)).foregroundColor(card).padding(.horizontal, 5).frame(minWidth: 17, minHeight: 17).background(Capsule().fill(toneInk("rose"))) }
          }.frame(maxWidth: .infinity, minHeight: 32)
            .background(RoundedRectangle(cornerRadius: 10).fill(current == it.0 ? card : .clear).shadow(color: .black.opacity(current == it.0 ? 0.08 : 0), radius: 3, y: 1))
            .contentShape(Rectangle())
        }.buttonStyle(.plain)
      }
    }.padding(4).background(RoundedRectangle(cornerRadius: 14).fill(toneBg("grey"))).padding(.horizontal, 14)
  }
  func go(_ id: String) {
    switch id {
    case "today": store.selected = nil; store.tab = "overview"
    case "projects": store.selected = nil; store.tab = "projects"
    case "accounts": store.tab = "workers"
    case "board": store.tab = "board"
    default: store.tab = "review"
    }
  }
}

struct PaperCollapsed: View {
  let o: OverviewVM
  var body: some View {
    let cooling = o.accounts.first { $0.state == "cooling" }
    pCard {
      Flow(spacing: 8, rowSpacing: 6) {
        Pill(text: "\(o.machine?.running ?? 0) running", tone: "sage")
        Pill(text: "\(o.inbox?.review ?? 0) to review", tone: "lilac")
        Pill(text: "\(o.inbox?.alerts ?? 0) alerts", tone: "rose")
        if let c = cooling { Text("\(c.label) rests until \(hhmm(c.resets5))").font(f(-2)).foregroundColor(faint) }
      }
    }.padding(.horizontal, 14)
  }
}

struct PaperBody: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      switch store.tab {
      case "workers": PaperAccounts(o: o)
      case "board": PaperBoard(o: o)
      case "review", "alerts": PaperInbox(o: o)
      case "projects", "repo", "timeline":
        if store.selected == nil { PaperProjects(o: o) } else if let p = store.project { PaperProject(p: p, o: o) } else { loading }
      default:
        if store.selected == nil { PaperToday(o: o) } else if let p = store.project { PaperProject(p: p, o: o) } else { loading }
      }
    }.padding(.horizontal, 14).padding(.bottom, 24)
  }
  var loading: some View { Text("Loading project…").font(f(-1)).foregroundColor(faint).padding(.top, 12) }
}
struct PH1: View {
  let title: String; var lede: String? = nil
  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      Text(title).font(serif(28)).padding(.top, 10)
      if let l = lede { Text(l).font(serif(16)).fixedSize(horizontal: false, vertical: true) }
    }.padding(.bottom, 6)
  }
}

struct PaperToday: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    let needs = needsList(o)
    let date = Date(timeIntervalSince1970: TimeInterval(store.now))
    let hour = Calendar.current.component(.hour, from: date)
    let greys = o.accounts.filter { greyedWhy($0) != nil }
    VStack(alignment: .leading, spacing: 0) {
      HStack(spacing: 8) { Circle().fill(toneInk("sage")).frame(width: 8, height: 8); Text("\(paperDateFmt.string(from: date)) · \(hhmm(store.now))").font(f(-2)).foregroundColor(faint) }
        .padding(.top, 8)
      Text(hour < 12 ? "Good morning." : hour < 17 ? "Good afternoon." : "Good evening.").font(serif(30)).padding(.top, 4).padding(.bottom, 6)
      ledeText(o).font(serif(16)).lineSpacing(5).fixedSize(horizontal: false, vertical: true)
      PH2(title: "Needs you", small: "\(needs.count) thing\(needs.count == 1 ? "" : "s")")
      if needs.isEmpty { Text("Nothing needs you right now.").font(f(-1)).foregroundColor(faint) }
      VStack(spacing: 10) { ForEach(needs) { PNeedCard(n: $0) } }
      PH2(title: "Your accounts", small: "5-hour quota")
      pCard {
        VStack(alignment: .leading, spacing: 10) {
          LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6, alignment: .top), count: min(5, max(1, o.accounts.count))), spacing: 12) {
            ForEach(o.accounts) { a in
              Button(action: { store.tab = "workers" }) {
                VStack(spacing: 4) {
                  Ring(value: a.state == "cooling" ? 100 : (a.pct5 ?? 0), tone: pAccTone(a), size: 56, inner: pct(a.pct5))
                  Text(workerId(a)).font(f(-2, .semibold)).lineLimit(1)
                  Text(a.state == "cooling" ? "rests \(store.countdown(a.resets5))" : store.countdown(a.resets5)).font(f(-2)).foregroundColor(faint).lineLimit(1)
                }.help("\(a.label) · \(quotaText(a, now: store.now))").opacity(a.dispatch == false ? 0.7 : 1)
              }.buttonStyle(.plain)
            }
          }
          ForEach(greys) { a in Text("\(a.label) is greyed out: \(greyedWhy(a) ?? "")").font(f(-2)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true) }
        }
      }
      WorkersSection(o: o)
      let alerts = o.alerts ?? []
      if !alerts.isEmpty {
        PH2(title: "Worth a look", small: "\(alerts.count) alert\(alerts.count == 1 ? "" : "s")")
        VStack(spacing: 10) { ForEach(alerts.prefix(3)) { PAlertCard(e: $0) } }
        Button(action: { store.tab = "alerts" }) { Text("See all alerts ›").font(f(-1)).foregroundColor(toneInk("sky")).padding(.vertical, 8) }.buttonStyle(.plain)
      }
    }
  }
  /// The lede is a sentence built from the overview's real counts; the review count is highlighted.
  func ledeText(_ o: OverviewVM) -> Text {
    let running = o.machine?.running ?? o.projects.reduce(0) { $0 + ($1.counts?.running ?? 0) }
    let active = o.projects.filter { $0.enabled != false }.count
    let rv = o.inbox?.review ?? 0
    let cooling = o.accounts.first { $0.state == "cooling" }
    let spare = o.accounts.first { $0.capacity?.spare == true && $0.capacity?.why == nil }
    var s = AttributedString("\(words(running, cap: true)) \(running == 1 ? "task is" : "tasks are") moving across \(words(active)) project\(active == 1 ? "" : "s"). ")
    var m = AttributedString("\(words(rv, cap: true)) finished piece\(rv == 1 ? "" : "s") of work")
    m.backgroundColor = toneBg("butter")
    s += m
    s += AttributedString(rv == 1 ? " is waiting for you." : " are waiting for you.")
    if let c = cooling {
      s += AttributedString(" \(c.label) is resting until ")
      var b = AttributedString(hhmm(c.resets5)); b.font = serif(16, .semibold); s += b
      s += AttributedString(spare == nil ? "." : ",")
    }
    if let sp = spare { s += AttributedString("\(cooling == nil ? " " : " and ")\(sp.label) has a lane free with quota to spare.") }
    return Text(s)
  }
}

struct PNeedCard: View {
  @EnvironmentObject var store: DeckStore
  let n: NeedsItem
  var body: some View {
    let t = n.kind == "review" ? ("lilac", "Ready for your review") : n.kind == "question" ? ("peach", "A question for you") : ("rose", "Worth a look")
    pCard(t.0) {
      VStack(alignment: .leading, spacing: 6) {
        PKicker(text: t.1, tone: t.0)
        Text((n.task.map { "#\(n.num.map(String.init) ?? shortId($0)) " } ?? "") + n.title).font(serif(17, .medium)).fixedSize(horizontal: false, vertical: true)
        Text(n.text).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
        pProjPill(store, n.project).padding(.top, 2)
        NeedActions(n: n).padding(.top, 4)
      }
    }
  }
}
struct PAlertCard: View {
  @EnvironmentObject var store: DeckStore
  let e: DeckEvent
  var body: some View {
    let t = pEvTone(e)
    pCard(t.0) {
      VStack(alignment: .leading, spacing: 6) {
        PKicker(text: "\(t.1) \(e.type.replacingOccurrences(of: "_", with: " ")) · \(hhmm(e.ts))", tone: t.0)
        Text(e.text).font(serif(16, .medium)).fixedSize(horizontal: false, vertical: true)
        HStack(spacing: 8) {
          pProjPill(store, e.project)
          if e.task != nil, let p = e.project { Spacer(); Key(title: "Open") { store.selected = p; store.tab = "board" } }
        }
      }
    }
  }
}

struct PaperProjects: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      PH1(title: "Projects", lede: "\(words(o.projects.count, cap: true)) project\(o.projects.count == 1 ? "" : "s"), each with its own board and task store. Click one to open its page.")
      ForEach(o.projects) { p in
        Button(action: { if p.enabled != false { store.selected = p.id; store.tab = "projects" } }) {
          pCard {
            VStack(alignment: .leading, spacing: 10) {
              HStack(spacing: 10) {
                Text(p.icon ?? "◆").font(.system(size: 18)).frame(width: 38, height: 38).background(RoundedRectangle(cornerRadius: 12).fill(toneBg(p.scope == "office" ? "indigo" : "teal")))
                VStack(alignment: .leading, spacing: 4) { Text(p.title).font(serif(17, .medium)); HStack(spacing: 6) { pScope(p.scope); if p.enabled == false { Pill(text: "Paused", tone: "grey") } } }
                Spacer(); Text("›").font(.system(size: 22)).foregroundColor(faint)
              }
              if let h = p.headline { Text(h).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true) }
              Flow(spacing: 6) {
                Pill(text: "\(p.counts?.running ?? 0) running", tone: "sage"); Pill(text: "\(p.counts?.queued ?? 0) ready", tone: "sky")
                Pill(text: "\(p.counts?.review ?? 0) in review", tone: "lilac"); Pill(text: "\(p.counts?.blocked ?? 0) blocked", tone: "peach")
              }
              HStack(spacing: 8) {
                Circle().fill(toneInk((p.alerts ?? 0) > 0 ? "butter" : "sage")).frame(width: 9, height: 9)
                Text((p.alerts ?? 0) > 0 ? "\(p.alerts!) alert\(p.alerts == 1 ? "" : "s") to look at" : "No alerts").font(f(-2)).foregroundColor(faint)
              }
            }
          }.opacity(p.enabled == false ? 0.55 : 1)
        }.buttonStyle(.plain)
      }
      Text("Projects are added or removed in the config file.").font(f(-2)).foregroundColor(faint).frame(maxWidth: .infinity)
    }
  }
}

struct PaperProject: View {
  @EnvironmentObject var store: DeckStore
  let p: ProjectVM; let o: OverviewVM
  var body: some View {
    let tile = store.tile(p.project.id)
    let b = p.board
    let lanes = store.lanes(on: p.project.id)
    let health = p.project.health ?? []
    VStack(alignment: .leading, spacing: 0) {
      Button(action: { store.selected = nil; store.tab = "projects" }) { Text("‹ All projects").font(f(-1)).foregroundColor(toneInk("sky")).padding(.vertical, 8) }.buttonStyle(.plain)
      HStack(spacing: 10) {
        Text(tile?.icon ?? "◆").font(.system(size: 18)).frame(width: 38, height: 38).background(RoundedRectangle(cornerRadius: 12).fill(toneBg(p.project.scope == "office" ? "indigo" : "teal")))
        Text(p.project.title).font(serif(28)).lineLimit(1).minimumScaleFactor(0.7)
      }
      Flow(spacing: 6) { pScope(p.project.scope); Pill(text: p.project.allowPersonalAccounts == false ? "Office accounts only" : "Personal accounts allowed", tone: "grey") }.padding(.top, 8)
      if let h = tile?.headline { Text(h).font(serif(16)).fixedSize(horizontal: false, vertical: true).padding(.top, 12) }
      PColTabs(counts: [(b.upNext ?? []).count + (b.backlog ?? []).count, (b.running ?? []).count, (b.review ?? []).count, (b.needsYou ?? []).count],
               order: [1, 0, 2, 3], selected: nil) { i in store.paperCol = i; store.tab = "board" }.padding(.top, 14)
      PH2(title: "Working on it now")
      if lanes.isEmpty { Text("Nobody is working on it right now.").font(f(-1)).foregroundColor(faint) }
      VStack(spacing: 6) { ForEach(lanes, id: \.l.id) { PLane(a: $0.a, l: $0.l) } }
      PH2(title: "Up next")
      if (b.upNext ?? []).isEmpty { Text("Nothing ready.").font(f(-1)).foregroundColor(faint) }
      VStack(spacing: 9) { ForEach(b.upNext ?? []) { PTaskCard(t: $0) } }
      if !(p.inbox ?? []).isEmpty || !(b.needsYou ?? []).isEmpty {
        PH2(title: "Waiting for you")
        VStack(spacing: 10) {
          ForEach(p.inbox ?? []) { r in
            Button(action: { store.tab = "review" }) {
              pCard("lilac") {
                VStack(alignment: .leading, spacing: 5) {
                  PKicker(text: r.pilot == true ? "Review · pilot" : "Review", tone: "lilac")
                  Text(r.task.title).font(serif(17, .medium))
                  Text("Waiting \(dur(r.waitingSec)) · \((r.doneWhen ?? []).filter { $0.met }.count)/\((r.doneWhen ?? []).count) checks pass").font(f(-1)).foregroundColor(faint)
                }
              }
            }.buttonStyle(.plain)
          }
          ForEach(b.needsYou ?? []) { t in
            pCard("peach") {
              VStack(alignment: .leading, spacing: 5) {
                PKicker(text: t.question != nil ? "A question for you" : "Blocked", tone: "peach")
                Text(t.title).font(serif(17, .medium))
                if let r = t.stateReason { Text(r).font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true) }
                TaskDetail(t: t)
              }
            }
          }
        }
      }
      if let al = p.alerts, !al.isEmpty {
        PH2(title: "Alerts")
        VStack(spacing: 10) { ForEach(al) { PAlertCard(e: $0) } }
      }
      PH2(title: "Repo health")
      pCard {
        VStack(alignment: .leading, spacing: 10) {
          HStack(spacing: 8) {
            Circle().fill(toneInk(health.contains { $0.isUp == false } ? "butter" : "sage")).frame(width: 9, height: 9)
            Text(health.contains { $0.isUp == false } ? health.filter { $0.isUp == false }.map { "\($0.label) is down" }.joined(separator: " · ") : "All services up").font(f(-1, .semibold))
          }
          Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
            kv("Head", p.project.repo?.commit ?? "—")
            kv("Working tree", (p.project.repo?.changes ?? []).isEmpty ? "clean" : (p.project.repo?.changes ?? []).map { "\($0.area): \($0.files) files" }.joined(separator: ", "))
            kv("Services", health.map { "\($0.isUp == true ? "🟢" : $0.isUp == false ? "🔴" : "⚪︎") \($0.label)" }.joined(separator: " · "))
            kv("Merge", p.merge?.active.map { "\(shortId($0.task)) · gates \($0.gatesDone ?? 0)/\($0.gatesTotal ?? 0)" } ?? "idle")
            kv("Locks", (p.domains ?? []).map { "\($0.name) → \($0.holder.map { shortId($0) } ?? ($0.note ?? "free"))\($0.waiting?.isEmpty == false ? " (\($0.waiting!.map { shortId($0) }.joined(separator: ", ")) waiting)" : "")" }.joined(separator: "\n"))
          }
        }
      }
      PH2(title: "Accounts for this project")
      pCard {
        VStack(alignment: .leading, spacing: 0) {
          ForEach(store.workerRows, id: \.a.id) { w in
            HStack(alignment: .top, spacing: 10) {
              Circle().fill(toneInk(w.allowed ? "sage" : "grey")).frame(width: 9, height: 9).padding(.top, 6)
              VStack(alignment: .leading, spacing: 2) {
                Text("\(workerId(w.a)) · \(w.a.label)\(w.allowed ? "" : " 🔒")").font(f(-1, .semibold))
                Text(w.allowed ? "Allowed." : (w.reason ?? "Not allowed.")).font(f(-2)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
              }
              Spacer(minLength: 4)
              Text(pct(w.a.pct5)).font(f(-2)).foregroundColor(faint)
            }.padding(.vertical, 7).opacity(w.allowed ? 1 : 0.7)
          }
        }
      }
    }
  }
  func kv(_ k: String, _ v: String) -> some View {
    GridRow(alignment: .firstTextBaseline) { Text(k).font(f(-2)).foregroundColor(faint); Text(v).font(f(-2)).fixedSize(horizontal: false, vertical: true) }
  }
}

/// Column tabs: big serif count + label in the column's pastel. counts and order use column indices (0 ready, 1 running, 2 review, 3 blocked).
struct PColTabs: View {
  let counts: [Int]; var order = [0, 1, 2, 3]; let selected: Int?; let tap: (Int) -> Void
  static let cols: [(String, String)] = [("sky", "Ready"), ("sage", "Running"), ("lilac", "Review"), ("peach", "Blocked")]
  var body: some View {
    HStack(spacing: 6) {
      ForEach(order, id: \.self) { i in
        let c = PColTabs.cols[i]
        let n = i < counts.count ? counts[i] : 0
        Button(action: { tap(i) }) {
          VStack(spacing: 2) { Text("\(n)").font(serif(20, .medium)); Text(c.1).font(f(-2, .semibold)) }
            .foregroundColor(toneInk(c.0)).frame(maxWidth: .infinity, minHeight: 50)
            .background(RoundedRectangle(cornerRadius: 14).fill(toneBg(c.0)))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(selected == i ? toneInk(c.0) : .clear, lineWidth: 2))
        }.buttonStyle(.plain)
      }
    }
  }
}

/// A lane as a pastel pill row.
struct PLane: View {
  @EnvironmentObject var store: DeckStore
  let a: Account; let l: Lane
  var body: some View {
    let free = freeLaneText(a)
    let quiet = l.state == "running" && (l.lastActiveSec ?? 0) > 600
    let tn: String = l.state == "running" ? (quiet ? "rose" : "sage") : l.state == "cooling" ? "butter" : free.kind == "spare" || free.kind == "cool" ? "butter" : "grey"
    let main: String, sub: String, right: String
    switch l.state {
    case "running":
      main = "#\(shortId(l.task)) \(l.title ?? "")"
      sub = quiet ? "quiet \(dur(l.lastActiveSec)) · looks stuck" : "step \(l.stepIndex ?? 0) of \(l.stepCount ?? 0) · \(store.projectNames[l.project ?? ""] ?? l.project ?? "")"
      right = quiet ? "check" : l.etaMin.map { "~\(Int($0)) min" } ?? ""
    case "cooling":
      main = l.task != nil ? "#\(shortId(l.task)) \(l.title ?? "")" : "Resting"
      sub = "resumes in the same session after the reset"
      right = store.countdown(l.resumeAt ?? a.resets5)
    default:
      main = free.kind == "spare" ? "Free lane, quota to spare" : free.kind == "policy" ? "🔒 Free lane" : free.kind == "cool" ? "Resting" : "Free lane"
      sub = free.text
      right = ""
    }
    return HStack(spacing: 10) {
      HStack(spacing: 4) {
        Circle().fill(store.wLine(worker: l.worker) ?? toneInk(tn)).frame(width: 7, height: 7)
        Text("\(l.worker)·\(l.lane)").font(f(-2, .bold)).foregroundColor(toneInk(tn))
      }.frame(minWidth: 44, alignment: .leading)
      VStack(alignment: .leading, spacing: 1) {
        clip(main, f(-1), txC)
        Text(sub).font(f(-2)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
      }
      Spacer(minLength: 4)
      if !right.isEmpty { Text(right).font(f(-2, .semibold)).foregroundColor(toneInk(tn)).fixedSize() }
    }.padding(.horizontal, 10).padding(.vertical, 7).background(RoundedRectangle(cornerRadius: 11).fill(toneBg(tn)))
      .overlay(RoundedRectangle(cornerRadius: 11).stroke(free.kind == "spare" && l.state == "free" ? toneInk("butter") : .clear, lineWidth: 1.5))
  }
}

/// Task card: "#num title", plain-language line, progress bar, state / dependency pills. Click to open.
struct PTaskCard: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM; var showProject = false
  @State private var open = false
  var body: some View {
    let s = pTone(t)
    VStack(alignment: .leading, spacing: 5) {
      VStack(alignment: .leading, spacing: 5) {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
          (Text("#\(t.numText) ").foregroundColor(faint) ++ Text(t.title).fontWeight(.semibold)).font(f(-0.5)).fixedSize(horizontal: false, vertical: true)
          Spacer(minLength: 0)
          WorkerBadge(worker: t.worker)
        }
        Text(lineText).font(f(-2)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
        if let m = t.metrics?.first, let tot = m.total, tot > 0 {
          Bar(value: m.done / tot, color: toneInk("sage"), height: 6, track: toneBg("grey"))
          Text("\(m.label): \(Int(m.done)) of \(Int(tot))").font(f(-2)).foregroundColor(faint)
        } else if t.state == "running", let p = t.progress { Bar(value: p, color: toneInk("sage"), height: 6, track: toneBg("grey")) }
        let deps = t.deps ?? []
        if isQuiet(t) || t.state == "limit_paused" || !deps.isEmpty || t.kind == "pilot" {
          Flow(spacing: 6, rowSpacing: 6) {
            if isQuiet(t) || t.state == "limit_paused" { Pill(text: s.1, tone: s.0) }
            if t.kind == "pilot" { Pill(text: "Pilot", tone: "butter") }
            ForEach(deps, id: \.id) { d in Pill(text: "after #\(shortId(d.id)) · \(d.state == "done" ? "done ✓" : d.state.replacingOccurrences(of: "_", with: " "))", tone: d.state == "done" ? "sage" : "butter") }
          }.padding(.top, 2)
        }
      }.contentShape(Rectangle()).onTapGesture { open.toggle() }
      if open { TaskDetail(t: t).padding(.top, 6) }
    }.padding(.horizontal, 12).padding(.leading, store.wLine(worker: t.worker) == nil ? 0 : 3).padding(.vertical, 10).frame(maxWidth: .infinity, alignment: .leading)
      .background(RoundedRectangle(cornerRadius: 13).fill(card).shadow(color: .black.opacity(T.dark ? 0.25 : 0.06), radius: 6, y: 2))
      .overlay(stripe(store.wLine(worker: t.worker)).clipShape(RoundedRectangle(cornerRadius: 13)))
  }
  var lineText: String {
    switch t.state {
    case "running" where t.step != nil:
      let st = t.step!
      return "\(t.worker ?? "") · step \(st.index) of \(st.count), \(st.title.lowercased())\(st.etaMin.map { " · about \(Int($0)) min" } ?? "")\(isQuiet(t) ? " · quiet \(dur(t.lastActiveSec))" : "")"
    case "limit_paused": return "\(t.worker ?? "") · resumes by itself after the reset (\(store.countdown(t.resumeAt)))"
    default: return statusText(t, now: store.now)
    }
  }
}

struct PaperAccounts: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    let rows = store.workerRows
    let leased = rows.filter { $0.a.dispatch != false }.count
    VStack(alignment: .leading, spacing: 12) {
      PH1(title: "Accounts", lede: "\(words(rows.count, cap: true)) account\(rows.count == 1 ? "" : "s"). \(words(leased, cap: true)) take\(leased == 1 ? "s" : "") work in parallel lanes\(rows.count > leased ? "; the rest show their usage only" : "").\(store.selected != nil ? " Greyed accounts may not work on \(store.project?.project.title ?? "this project")." : "")")
      ForEach(rows, id: \.a.id) { w in PAccountCard(a: w.a, allowed: w.allowed, reason: w.reason) }
      if let b = o.backlog {
        pCard(fill: toneBg("sky")) {
          VStack(alignment: .leading, spacing: 4) {
            Text("Backlog").font(serif(17, .medium)).foregroundColor(toneInk("sky"))
            Text("\(words(b.eligible ?? 0, cap: true)) task\(b.eligible == 1 ? " is" : "s are") ready against a target of \(b.target ?? 0).\(b.hint.map { " \($0.prefix(1).uppercased() + $0.dropFirst())." } ?? "")")
              .font(f(-1)).fixedSize(horizontal: false, vertical: true)
          }
        }
      }
    }
  }
}

struct PAccountCard: View {
  @EnvironmentObject var store: DeckStore
  let a: Account; let allowed: Bool; let reason: String?
  var body: some View {
    let off = a.dispatch == false || !allowed
    pCard(fill: off ? toneBg("grey").opacity(0.55) : nil) {
      VStack(alignment: .leading, spacing: 10) {
        HStack(alignment: .top, spacing: 14) {
          Ring(value: a.state == "cooling" ? 100 : (a.pct5 ?? 0), tone: off ? "grey" : pAccTone(a), size: 88, inner: pct(a.pct5), sub: "5h used")
          VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) { Circle().fill(store.wLine(account: a.id) ?? faint).frame(width: 10, height: 10); Text(a.label).font(serif(18, .medium)).foregroundColor(off ? faint : txC) }
            Flow(spacing: 6, rowSpacing: 6) {
              pScope(a.scope); Pill(text: workerId(a), tone: "grey")
              if a.dispatch == false { Pill(text: "Usage only", tone: "grey") }
              if a.state == "cooling" { Pill(text: "At limit", tone: "rose") }
              if !allowed { Pill(text: "Not allowed here", tone: "rose") }
            }
            (Text("Resets in ") ++ Text(store.countdown(a.resets5)).fontWeight(.semibold) ++ Text(" at \(hhmm(a.resets5))").foregroundColor(faint)).font(f(-1))
            HStack(spacing: 8) {
              Text("7 days").font(f(-2)).foregroundColor(faint)
              Bar(value: (a.pct7 ?? 0) / 100, color: faint, height: 6, track: toneBg("grey"))
              Text(pct(a.pct7)).font(f(-2)).foregroundColor(faint)
            }
          }
        }
        if let lanes = a.lanes, !lanes.isEmpty { VStack(spacing: 6) { ForEach(lanes) { PLane(a: a, l: $0) } } }
        else if a.dispatch == false { Text("Usage only · never leased work").font(f(-1)).padding(10).frame(maxWidth: .infinity, alignment: .leading).background(RoundedRectangle(cornerRadius: 11).fill(toneBg("lilac"))) }
        if let why = !allowed ? reason : greyedWhy(a), off {
          (Text(!allowed ? "Why it is greyed out here. " : "Why it is greyed out. ").fontWeight(.semibold) ++ Text(why)).font(f(-1)).fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 12).padding(.vertical, 9).frame(maxWidth: .infinity, alignment: .leading).background(RoundedRectangle(cornerRadius: 12).fill(card))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(faint, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
        }
      }
    }
  }
}

struct PaperBoard: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      PH1(title: "Board")
      Flow(spacing: 6, rowSpacing: 6) {
        chip("All", on: store.selected == nil) { store.selected = nil }
        ForEach(o.projects) { p in chip("\(p.icon ?? "") \(p.title)", on: store.selected == p.id, off: p.enabled == false) { store.selected = p.id } }
      }
      if let p = store.project {
        let b = p.board
        let cols: [[TaskVM]] = [(b.upNext ?? []) + (b.backlog ?? []), b.running ?? [], b.review ?? [], b.needsYou ?? []]
        let col = min(3, max(0, store.paperCol))
        let c = PColTabs.cols[col]
        PColTabs(counts: cols.map { $0.count }, selected: col) { store.paperCol = $0 }
        VStack(alignment: .leading, spacing: 9) {
          HStack { Text(c.1).font(serif(18, .medium)); Spacer(); Text("\(cols[col].count)").font(serif(18, .medium)) }.foregroundColor(toneInk(c.0)).padding(.horizontal, 4)
          if cols[col].isEmpty { Text("Nothing here.").font(f(-1)).foregroundColor(faint).padding(.horizontal, 4) }
          ForEach(cols[col]) { PTaskCard(t: $0) }
        }.padding(14).frame(maxWidth: .infinity, alignment: .leading).background(RoundedRectangle(cornerRadius: 20).fill(toneBg(c.0)))
        HStack(spacing: 6) {
          ForEach(0..<4, id: \.self) { i in Capsule().fill(i == col ? txC : line).frame(width: i == col ? 18 : 7, height: 7) }
        }.frame(maxWidth: .infinity)
        if let d = b.doneToday, !d.isEmpty {
          Text("Done today: " + d.map { "#\($0.numText) \($0.title)" }.joined(separator: " · ")).font(f(-2)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
        }
        Text("Click a column above to switch pages. “After #” shows a dependency.").font(f(-2)).foregroundColor(faint).frame(maxWidth: .infinity)
      } else if store.selected != nil {
        Text("Loading project…").font(f(-1)).foregroundColor(faint)
      } else {
        PColTabs(counts: [sumCounts(o) { $0.queued }, sumCounts(o) { $0.running }, sumCounts(o) { $0.review }, sumCounts(o) { $0.blocked }], selected: nil) { _ in }
        Text("Boards are kept per project. Choose one above to see its cards.").font(f(-1)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
      }
    }
  }
  func chip(_ s: String, on: Bool, off: Bool = false, _ tap: @escaping () -> Void) -> some View {
    Button(action: { if !off { tap() } }) {
      Text(s).font(f(-2)).foregroundColor(on ? bg : faint).padding(.horizontal, 11).frame(minHeight: 30)
        .background(Capsule().fill(on ? txC : .clear)).overlay(Capsule().stroke(on ? txC : line, lineWidth: 1))
    }.buttonStyle(.plain).opacity(off ? 0.5 : 1)
  }
}

struct PaperInbox: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    let alerts = store.selected == nil ? (o.alerts ?? []) : (store.project?.alerts ?? [])
    let reviews = (store.project?.inbox ?? []).sorted { ($0.waitingSec ?? 0) > ($1.waitingSec ?? 0) }
    let needs = needsList(o).filter { $0.kind != "alert" }
    let nReview = store.selected == nil ? needs.count : reviews.count
    VStack(alignment: .leading, spacing: 12) {
      PH1(title: "Inbox")
      HStack(spacing: 6) {
        chip("To review · \(nReview)", on: store.tab == "review") { store.tab = "review" }
        chip("Alerts · \(alerts.count)", on: store.tab == "alerts") { store.tab = "alerts" }
      }
      if store.tab == "alerts" {
        if alerts.isEmpty { Text("No alerts.").font(f(-1)).foregroundColor(faint) }
        ForEach(alerts) { PAlertCard(e: $0) }
      } else if store.selected == nil {
        if needs.isEmpty { Text("Nothing is waiting for you.").font(f(-1)).foregroundColor(faint) }
        ForEach(needs) { PNeedCard(n: $0) }
      } else {
        if reviews.isEmpty { Text("Nothing to review in \(store.project?.project.title ?? "this project").").font(f(-1)).foregroundColor(faint) }
        ForEach(reviews) { PReviewCard(r: $0) }
      }
    }
  }
  func chip(_ s: String, on: Bool, _ tap: @escaping () -> Void) -> some View {
    Button(action: tap) {
      Text(s).font(f(-2)).foregroundColor(on ? bg : faint).padding(.horizontal, 11).frame(minHeight: 30)
        .background(Capsule().fill(on ? txC : .clear)).overlay(Capsule().stroke(on ? txC : line, lineWidth: 1))
    }.buttonStyle(.plain)
  }
}

struct PReviewCard: View {
  @EnvironmentObject var store: DeckStore
  let r: ReviewVM
  var body: some View {
    let dw = r.doneWhen ?? []
    pCard {
      VStack(alignment: .leading, spacing: 10) {
        Flow(spacing: 6, rowSpacing: 6) {
          pProjPill(store, store.selected)
          if r.pilot == true { Pill(text: "Pilot", tone: "butter") }
          Pill(text: "waiting \(dur(r.waitingSec))", tone: "grey")
        }
        Text(r.task.title).font(serif(19, .medium))
        Text(r.summary).font(f(-1)).fixedSize(horizontal: false, vertical: true)
        VStack(alignment: .leading, spacing: 8) {
          ForEach(dw, id: \.self) { d in
            HStack(alignment: .top, spacing: 10) {
              Text(d.met ? "✓" : "✕").font(.system(size: 12, weight: .bold)).foregroundColor(toneInk(d.met ? "sage" : "rose"))
                .frame(width: 22, height: 22).background(Circle().fill(toneBg(d.met ? "sage" : "rose")))
              VStack(alignment: .leading, spacing: 1) {
                Text(d.item).font(f(-1)).fixedSize(horizontal: false, vertical: true)
                Text(d.evidence).font(f(-2)).foregroundColor(faint).fixedSize(horizontal: false, vertical: true)
              }
            }
          }
        }
        Flow(spacing: 14, rowSpacing: 4) {
          fact("by ", "\(r.worker ?? "") \(r.account ?? "")", "")
          fact("", "\(r.git?.commits ?? 0)", " commits"); fact("", "\(r.git?.filesChanged ?? 0)", " files")
          fact("", r.git?.conflictsWithMain == true ? "conflicts" : "0", r.git?.conflictsWithMain == true ? " with main" : " conflicts")
          fact("tests ", "\((r.tests ?? []).reduce(0) { $0 + ($1.passed ?? 0) })", " pass")
        }
        if let u = r.git?.uncommitted, u > 0 { Text("\(u) uncommitted change\(u > 1 ? "s" : "") will not be merged.").font(f(-1)).foregroundColor(toneInk("butter")) }
        ForEach(r.risks ?? [], id: \.self) { Text("Worth knowing: \($0).").font(f(-1)).foregroundColor(toneInk("peach")).fixedSize(horizontal: false, vertical: true) }
        if let u = r.unlocks, !u.isEmpty { Text("Approving lets #\(u.map { shortId($0) }.joined(separator: ", #")) start.").font(f(-2)).foregroundColor(faint) }
        Decisions(r: r)
        ReviewActions(r: r).padding(.top, 2)
      }
    }
  }
  func fact(_ a: String, _ b: String, _ c: String) -> Text { (Text(a).foregroundColor(faint) ++ Text(b).fontWeight(.semibold) ++ Text(c).foregroundColor(faint)).font(f(-2)) }
}

// MARK: - Worker colours: Okabe–Ito (colour-blind safe), one per account in overview order; AccountVM.color overrides.
// Each theme gets adjusted variants: line (stripes, graph series) ≥ 3:1 and text ≥ 4.5:1 against card and background.

let OKABE_ITO: [UInt32] = [0x0072B2, 0xE69F00, 0x009E73, 0xCC79A7, 0x56B4E9, 0xD55E00, 0xF0E442]
/// --w0..--w6 per token set, exactly as web/themes.css ("worker colours"), so the panel and the phone deck match.
let WORKER_COLOURS: [String: [UInt32]] = [
  "studio-light": [0x0072b2, 0xb06a00, 0x007a59, 0xa8437f, 0xc04a00, 0x2b7bb0, 0x7a6c00],
  "studio-dark": [0x56b4e9, 0xe69f00, 0x2bc08f, 0xe08fbf, 0xff8a52, 0x9ab8ff, 0xf0e442],
  "mission-dark": [0x56b4e9, 0xe69f00, 0x2bc08f, 0xe08fbf, 0xff7a3d, 0x9ab8ff, 0xf0e442],
  "mission-light": [0x0072b2, 0xa86400, 0x007a59, 0xa8437f, 0xb84400, 0x2b6fa8, 0x6f6300],
  "paper-light": [0x0072b2, 0xa86400, 0x007a59, 0xa8437f, 0xb84400, 0x2b7bb0, 0x776a00],
  "paper-dark": [0x56b4e9, 0xe69f00, 0x2bc08f, 0xe08fbf, 0xff8a52, 0x9ab8ff, 0xf0e442]]
/// Line colour (stripes, graph series) of the i-th account in a token set; a config override is tuned the same way.
func workerHex(_ i: Int, override: UInt32? = nil, _ t: Theme = T) -> UInt32 {
  if let o = override { return workerLine(o, t) }
  return WORKER_COLOURS[t.id]?[i % 7] ?? workerLine(OKABE_ITO[i % 7], t)
}
/// Studio draws its worker graphs on an LCD panel (same in light and dark, as in the PhoneDeck Studio look).
let LCD_BG: UInt32 = 0x0f0f10, LCD_DOT: UInt32 = 0x2a2a2a, LCD_MUTED: UInt32 = 0xa3a3a3
func parseHex(_ s: String?) -> UInt32? {
  guard let s = s, s.hasPrefix("#"), s.count == 7, let v = UInt32(s.dropFirst(), radix: 16) else { return nil }
  return v
}
/// Move c toward white (dark backgrounds) or black (light ones) in small steps until it reaches `min` against every background.
func adjustColor(_ c: UInt32, on bgs: [UInt32], min target: Double, dark: Bool) -> UInt32 {
  var v = c, k = 0.0
  while bgs.contains(where: { contrast(v, $0) < target }) && k < 1 { k += 0.04; v = mix(dark ? 0xffffff : 0x000000, c, Swift.min(1, k)) }
  return v
}
func workerLine(_ c: UInt32, _ t: Theme = T) -> UInt32 { adjustColor(c, on: [t.card, t.bg], min: 3, dark: t.dark) }
func workerText(_ c: UInt32, _ t: Theme = T) -> UInt32 { adjustColor(c, on: [t.card, t.bg, t.bg2], min: 4.5, dark: t.dark) }
func workerLcd(_ c: UInt32) -> UInt32 { adjustColor(c, on: [LCD_BG], min: 3, dark: true) }
/// Pairs checked by --check-fixtures: (name, colour, background, minimum ratio).
func workerColorPairs(_ t: Theme, extra: [UInt32] = []) -> [(String, UInt32, UInt32, Double)] {
  var p: [(String, UInt32, UInt32, Double)] = []
  for (i, c) in ((0..<7).map { workerHex($0, t) } + extra.map { workerHex(0, override: $0, t) }).enumerated() {
    let n = i < 7 ? "--w\(i) " + String(format: "#%06x", c) : "override " + String(format: "#%06x", c)
    p += [("\(n) line/card", c, t.card, 3), ("\(n) line/bg", c, t.bg, 3),
          ("\(n) text/card", workerText(c, t), t.card, 4.5), ("\(n) text/bg", workerText(c, t), t.bg, 4.5), ("\(n) text/bg2", workerText(c, t), t.bg2, 4.5)]
    if t.layout == "studio" { p.append(("\(n) lcd", workerLcd(c), LCD_BG, 3)) }
  }
  return p
}

extension DeckStore {
  /// Line colour of an account in the active token set: config override (tuned), else --wN by position in the overview.
  func accountHex(_ id: String?) -> UInt32? {
    guard let id = id, let i = accounts.firstIndex(where: { $0.id == id }) else { return nil }
    return workerHex(i, override: parseHex(accounts[i].color))
  }
  /// Worker ("W1" or lane "W1·2") → account id, via the overview lanes or the project's workers.
  func accountId(worker w: String?) -> String? {
    guard let w0 = w, !w0.isEmpty else { return nil }
    let w = String(w0.split(separator: "·").first ?? Substring(w0))
    if let a = accounts.first(where: { ($0.lanes ?? []).contains { $0.worker == w } }) { return a.id }
    return project?.workers?.first { $0.worker == w }?.account
  }
  func wLine(account id: String?) -> Color? { accountHex(id).map { rgb($0) } }
  func wText(account id: String?) -> Color? { accountHex(id).map { rgb(workerText($0)) } }
  func wLine(worker w: String?) -> Color? { wLine(account: accountId(worker: w)) }
  func wText(worker w: String?) -> Color? { wText(account: accountId(worker: w)) }
}

/// Small outlined worker badge in the worker's colour.
struct WorkerBadge: View {
  @EnvironmentObject var store: DeckStore
  let worker: String?
  var body: some View {
    if let w = worker, !w.isEmpty {
      let acc = store.accountId(worker: w)
      let label = store.accounts.first { $0.id == acc }?.label
      let c = store.wText(worker: w) ?? faint
      Text(label.map { "\(w) \($0)" } ?? w).font(fl(.bold)).foregroundColor(c).padding(.horizontal, 6).padding(.vertical, 2)
        .overlay(RoundedRectangle(cornerRadius: T.layout == "mission" ? 2 : 999).stroke(store.wLine(worker: w) ?? line, lineWidth: 1)).fixedSize()
    }
  }
}
/// Left colour stripe for a card (worker colour).
func stripe(_ c: Color?, width: CGFloat = 4) -> some View {
  HStack(spacing: 0) { Rectangle().fill(c ?? .clear).frame(width: width); Spacer(minLength: 0) }.allowsHitTesting(false)
}

// MARK: - Usage graph: 5h % solid, 7d % dashed, reset markers. Studio draws it as an LCD dot matrix.

struct UsageGraph: View {
  let u: UsageVM; let c: UInt32; let now: Int; let lcd: Bool
  var body: some View {
    let pts = (u.points ?? []).sorted { $0.ts < $1.ts }
    let t0 = pts.first?.ts ?? now, t1 = max(pts.last?.ts ?? now, now)
    let resets = (u.resets ?? []).filter { $0 >= t0 && $0 <= t1 }
    VStack(alignment: .leading, spacing: 6) {
      Canvas { ctx, size in
        let w = size.width, h = size.height
        func x(_ ts: Int) -> CGFloat { t1 == t0 ? 0 : CGFloat(ts - t0) / CGFloat(t1 - t0) * w }
        func y(_ v: Double) -> CGFloat { h - CGFloat(min(100, max(0, v)) / 100) * (h - 4) - 2 }
        func value(at px: CGFloat, _ k: KeyPath<UsagePoint, Double?>) -> Double? {
          let ts = Double(t0) + Double(px / max(1, w)) * Double(t1 - t0)
          let known = pts.compactMap { p in p[keyPath: k].map { (Double(p.ts), $0) } }
          guard let first = known.first, let last = known.last, ts >= first.0, ts <= last.0 else { return nil }
          for i in 1..<max(2, known.count) where i < known.count {
            let a = known[i - 1], b = known[i]
            if ts <= b.0 { return b.0 == a.0 ? b.1 : a.1 + (b.1 - a.1) * (ts - a.0) / (b.0 - a.0) }
          }
          return last.1
        }
        if lcd {
          ctx.fill(Path(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: 8), with: .color(rgb(LCD_BG)))
          let step: CGFloat = 5
          var gy: CGFloat = 3
          while gy < h { var gx: CGFloat = 3; while gx < w { ctx.fill(Path(ellipseIn: CGRect(x: gx - 0.8, y: gy - 0.8, width: 1.6, height: 1.6)), with: .color(rgb(LCD_DOT))); gx += step }; gy += step }
          for r in resets { var gy2: CGFloat = 3; while gy2 < h { ctx.fill(Path(ellipseIn: CGRect(x: x(r) - 1, y: gy2 - 1, width: 2, height: 2)), with: .color(rgb(LCD_MUTED))); gy2 += step * 2 } }
          let col = rgb(workerLcd(c))
          var px: CGFloat = 3, i = 0
          while px < w {
            if i % 2 == 0, let v = value(at: px, \.pct7) { ctx.fill(Path(ellipseIn: CGRect(x: px - 1.3, y: y(v) - 1.3, width: 2.6, height: 2.6)), with: .color(col.opacity(0.55))) }
            if let v = value(at: px, \.pct5) { ctx.fill(Path(ellipseIn: CGRect(x: px - 1.7, y: y(v) - 1.7, width: 3.4, height: 3.4)), with: .color(col)) }
            px += step / 2.5; i += 1
          }
        } else {
          for g in [50.0, 100.0] { var p = Path(); p.move(to: CGPoint(x: 0, y: y(g))); p.addLine(to: CGPoint(x: w, y: y(g))); ctx.stroke(p, with: .color(line), lineWidth: 1) }
          for r in resets { var p = Path(); p.move(to: CGPoint(x: x(r), y: 0)); p.addLine(to: CGPoint(x: x(r), y: h)); ctx.stroke(p, with: .color(faint), style: StrokeStyle(lineWidth: 1, dash: [2, 3])) }
          let col = rgb(c)
          func series(_ k: KeyPath<UsagePoint, Double?>) -> Path {
            var p = Path(), started = false
            for pt in pts { guard let v = pt[keyPath: k] else { continue }; let q = CGPoint(x: x(pt.ts), y: y(v)); if started { p.addLine(to: q) } else { p.move(to: q); started = true } }
            return p
          }
          ctx.stroke(series(\.pct7), with: .color(col), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
          ctx.stroke(series(\.pct5), with: .color(col), style: StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))
        }
      }.frame(height: 84)
      HStack(spacing: 12) {
        Text("━ 5h %"); Text("┅ 7d %"); if !resets.isEmpty { Text("┆ reset \(resets.map { hhmm($0) }.joined(separator: ", "))") }
        Spacer(); Text("\(hhmm(t0))–\(hhmm(t1))")
      }.font(fl(.regular)).foregroundColor(faint)
    }
  }
}

// MARK: - Workers section (every layout): compact per-worker rows that expand like task cards.

struct WorkersSection: View {
  @EnvironmentObject var store: DeckStore
  let o: OverviewVM
  var body: some View {
    VStack(alignment: .leading, spacing: T.layout == "mission" ? 0 : 8) {
      header
      ForEach(o.accounts) { WorkerItem(a: $0) }
    }
  }
  /// Legend: one swatch per worker in its colour.
  var legend: some View {
    Flow(spacing: 10, rowSpacing: 4) {
      ForEach(o.accounts) { a in
        HStack(spacing: 4) {
          RoundedRectangle(cornerRadius: 2).fill(store.wLine(account: a.id) ?? faint).frame(width: 10, height: 10)
          Text(workerId(a)).font(fl()).foregroundColor(store.wText(account: a.id) ?? faint)
        }
      }
    }
  }
  @ViewBuilder var header: some View {
    switch T.layout {
    case "mission":
      MHead(n: "\(o.accounts.count)", title: "Workers", trailing: "click a row to expand")
      legend.padding(.horizontal, 12).padding(.bottom, 8)
      HRule()
    case "paper":
      PH2(title: "Workers", small: "click one to open its graph")
      legend.padding(.bottom, 2)
    default:
      HStack(alignment: .firstTextBaseline) {
        Text("WORKERS").font(f(-1, .heavy)).foregroundColor(accTx).tracking(1.4); Spacer()
        Text("tap a worker for its graph").font(f(-1, .semibold)).foregroundColor(faint)
      }
      legend
    }
  }
}

struct WorkerItem: View {
  @EnvironmentObject var store: DeckStore
  let a: Account
  var open: Bool { store.expandedWorkers.contains(a.id) }
  var body: some View {
    let c = store.accountHex(a.id) ?? T.accent
    let inner = VStack(alignment: .leading, spacing: 10) {
      Button(action: { if open { store.expandedWorkers.remove(a.id) } else { store.expandedWorkers.insert(a.id) } }) { summary(c) }.buttonStyle(.plain)
      if open { detail(c) }
    }
    switch T.layout {
    case "mission":
      mRow(inner.overlay(alignment: .leading) { Rectangle().fill(rgb(c)).frame(width: 3).padding(.leading, -12).padding(.vertical, -9) })
    case "paper":
      pCard { inner }.overlay(stripe(rgb(c), width: 5).clipShape(RoundedRectangle(cornerRadius: 16)))
    default:
      studioCard { inner }.overlay(stripe(rgb(c)).clipShape(RoundedRectangle(cornerRadius: 12)))
    }
  }
  func summary(_ c: UInt32) -> some View {
    HStack(spacing: 8) {
      Circle().fill(rgb(c)).frame(width: 10, height: 10)
      Text(workerId(a)).font(f(-1, .bold)).foregroundColor(rgb(workerText(c)))
      clip(a.label, f(-1, .semibold))
      Spacer(minLength: 4)
      Text("5h \(a.state == "cooling" ? "LIMIT" : pct(a.pct5))").font(f(-1, .semibold, mono: true)).foregroundColor(a.state == "cooling" ? badC : txC).fixedSize()
      if let m = a.capacity?.lanesMax, m > 0 { Text("lanes \(a.capacity?.lanesUsed ?? 0)/\(m)").font(f(-1, mono: true)).foregroundColor(faint).fixedSize() }
      else if a.dispatch == false { Text("usage only").font(f(-1)).foregroundColor(faint).fixedSize() }
      Image(systemName: open ? "chevron.up" : "chevron.down").font(.system(size: 11, weight: .semibold)).foregroundColor(faint)
    }.contentShape(Rectangle())
  }
  @ViewBuilder func detail(_ c: UInt32) -> some View {
    if let u = a.usage, !(u.points ?? []).isEmpty { UsageGraph(u: u, c: c, now: store.now, lcd: T.layout == "studio") }
    let facts = [a.usage?.burn5PerHour.map { "burn \(String(format: $0 < 10 ? "%.1f" : "%.0f", $0))%/h" },
                 a.usage?.minutesToLimit.map { "limit in ~\(dur(Int($0 * 60)))" },
                 a.doneToday.map { "\($0) job\($0 == 1 ? "" : "s") done today" }].compactMap { $0 }
    if !facts.isEmpty { Text(facts.joined(separator: " · ")).font(f(-1, .semibold)).foregroundColor(dim) }
    let running = (a.lanes ?? []).filter { $0.state == "running" || $0.state == "cooling" }
    if !running.isEmpty {
      VStack(alignment: .leading, spacing: 4) {
        ForEach(running) { l in
          HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(l.worker)·\(l.lane)").font(f(-1, .bold, mono: true)).foregroundColor(rgb(workerText(c))).frame(width: 46, alignment: .leading)
            clip("\(l.state == "cooling" ? "⏸" : "▶") \(shortId(l.task)) \(l.title ?? "")\(l.stepCount.map { " · \(l.stepIndex ?? 0)/\($0)" } ?? "")", f(-1))
          }
        }
      }
    }
    if a.capacity?.why != nil || a.capacity?.fitsSize != nil {
      Text(freeLaneText(a).text).font(f(-1)).foregroundColor(freeLaneText(a).kind == "spare" ? warnC : faint).fixedSize(horizontal: false, vertical: true)
    }
    if let q = a.queued, !q.isEmpty {
      VStack(alignment: .leading, spacing: 3) {
        Text(T.layout == "paper" ? "Queued next" : "QUEUED").font(fl()).tracking(T.layout == "paper" ? 0.5 : 1.4).foregroundColor(faint)
        ForEach(q, id: \.self) { x in
          clip("→ \(shortId(x.task)) \(x.title ?? "")\(x.project.map { " · \(store.projectNames[$0] ?? $0)" } ?? "")", f(-1), dim)
        }
      }
    }
  }
}

// MARK: - Panel: fixed chrome (header / strips / tabs) over a scrolling body, both chosen by layout.

struct PanelChrome: View {
  var body: some View {
    switch T.layout {
    case "mission": MissionChrome()
    case "paper": PaperChrome()
    default: StudioChrome()
    }
  }
}
struct PanelBody: View {
  @EnvironmentObject var store: DeckStore
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      if let r = store.reassign { ReassignSheet(t: r.task, rows: r.rows).padding(.horizontal, 12).padding(.top, 8) }
      if let o = store.overview {
        switch T.layout {
        case "mission": MissionBody(o: o)
        case "paper": PaperBody(o: o)
        default: StudioBody(o: o)
        }
      } else {
        Text(store.offline ? "Can't reach deckd at \(store.base?.absoluteString ?? "?") — retrying every 2 s" : "Loading…").font(f(-1)).foregroundColor(faint).padding(16)
      }
    }
  }
}
/// scroll = false for snapshots (ImageRenderer draws ScrollView content blank).
struct PanelView: View {
  @EnvironmentObject var store: DeckStore
  var scroll = true
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      PanelChrome()
      if !store.collapsed {
        if scroll { ScrollView { PanelBody() } } else { PanelBody() }
      }
    }.foregroundColor(txC).lineSpacing(T.base * 0.3).background(bg)
  }
}
/// Re-identified when the layout or mode changes, so every view re-reads the active tokens.
struct RootView: View {
  @ObservedObject var store: DeckStore
  var body: some View {
    PanelView().environmentObject(store).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top).background(bg)
      .id("\(store.layout)-\(store.mode)-\(store.systemDark)")
  }
}

// MARK: - Entry point

func arg(_ name: String) -> String? {
  let a = CommandLine.arguments
  guard let i = a.firstIndex(of: name), i + 1 < a.count else { return nil }
  return a[i + 1]
}
let isSnapshot = arg("--snapshot") != nil
let stateDirRaw: String = arg("--state-dir") ?? ProcessInfo.processInfo.environment["AGENT_DECK_STATE"] ?? "~/.local/share/agent-deck"
let stateDir = URL(fileURLWithPath: (stateDirRaw as NSString).expandingTildeInPath)

/// Decode every fixture and run the shared helpers; prints a summary. Used by start.sh/CI as the "decode test".
func checkFixtures(_ dir: URL) -> Bool {
  var ok = true
  func check(_ cond: Bool, _ what: String) { print("\(cond ? "ok  " : "FAIL") \(what)"); if !cond { ok = false } }
  func load<V: Decodable>(_ n: String, _ t: V.Type) -> V? {
    do { return try JSONDecoder().decode(V.self, from: Data(contentsOf: dir.appendingPathComponent(n))) } catch { print("FAIL decode \(n): \(error)"); ok = false; return nil }
  }
  if let o = load("overview.json", OverviewVM.self) {
    check(o.projects.count == 3 && o.accounts.count == 5, "overview: \(o.projects.count) projects, \(o.accounts.count) accounts")
    check(o.accounts.contains { $0.scope == "office" && $0.pct5 != nil }, "office account present with usage")
    check(o.accounts.contains { ($0.lanes ?? []).filter { $0.state == "running" }.count >= 2 }, "parallel lanes on one account")
    if let office = o.accounts.first(where: { $0.id == "claude-office" }) { check(freeLaneText(office).kind == "policy", "office free lane explained by policy") }
    if let a = o.accounts.first(where: { $0.id == "codex-a" }) { check(freeLaneText(a).kind == "spare", "codex-a free lane is spare: \(freeLaneText(a).text)") }
    if let w = o.accounts.first(where: { $0.state == "cooling" }) { check(quotaText(w, now: o.time).hasPrefix("LIMIT"), "cooling account shows LIMIT countdown") }
  }
  for n in ((try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []).sorted() where n.hasPrefix("project") {
    if let p = load(n, ProjectVM.self) {
      let tasks = allTasks(p.board)
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
  // layouts × modes
  check(LAYOUTS.map { $0.1 } == ["Studio", "Mission Control", "Paper"], "layout picker: Studio · Mission Control · Paper")
  check(MODES.map { $0.1 } == ["Auto", "Light", "Dark"], "mode picker: Auto · Light · Dark")
  check(THEMES.count == 6 && LAYOUTS.allSatisfy { l in ["light", "dark"].allSatisfy { m in THEMES.contains { $0.layout == l.0 && $0.mode == m } } }, "six token sets: every layout has light and dark")
  var res = true
  for l in LAYOUTS.map({ $0.0 }) {
    for sys in [false, true] {
      res = res && resolveTheme(layout: l, mode: "light", systemDark: sys).id == "\(l)-light" && resolveTheme(layout: l, mode: "dark", systemDark: sys).id == "\(l)-dark"
      res = res && resolveTheme(layout: l, mode: "auto", systemDark: sys).id == "\(l)-\(sys ? "dark" : "light")"
    }
  }
  check(res, "layout × mode resolution: Light/Dark fixed, Auto follows the macOS appearance (all 3 layouts)")
  check(resolveTheme(layout: "nonsense", mode: "weird", systemDark: true).id == "studio-dark", "unknown layout → Studio, unknown mode → Auto")
  // persistence + migration of the old single deck.theme value (in a throwaway defaults suite)
  let suite = "agent-deck.selftest.\(getpid())"
  if let d = UserDefaults(suiteName: suite) {
    var mig = true
    for (old, want) in [("deck", ("studio", "dark")), ("mission", ("mission", "dark")), ("paper-light", ("paper", "light")), ("paper-dark", ("paper", "dark")), ("auto", ("paper", "auto"))] {
      d.removePersistentDomain(forName: suite)
      d.set(old, forKey: "deck.theme")
      let p = loadPrefs(d)
      mig = mig && p.layout == want.0 && p.mode == want.1 && d.string(forKey: "deck.layout") == want.0 && d.string(forKey: "deck.mode") == want.1 && d.string(forKey: "deck.theme") == nil
    }
    check(mig, "deck.theme migrates to deck.layout + deck.mode (deck, mission, paper-light, paper-dark, auto) and is removed")
    d.removePersistentDomain(forName: suite)
    d.set("mission", forKey: "deck.layout"); d.set("light", forKey: "deck.mode"); d.set("paper-dark", forKey: "deck.theme")
    let p = loadPrefs(d)
    check(p.layout == "mission" && p.mode == "light" && d.string(forKey: "deck.theme") == nil, "existing deck.layout/deck.mode win over a stale deck.theme")
    d.removePersistentDomain(forName: suite)
    check(loadPrefs(d) == (layout: "studio", mode: "auto"), "fresh install defaults to Studio + Auto")
    let s = DeckStore(base: nil, fixtures: dir, token: nil, prefs: d)
    s.layout = "paper"; s.mode = "dark"; s.cycleLayout(); s.cycleMode()
    check(d.string(forKey: "deck.layout") == "studio" && d.string(forKey: "deck.mode") == "auto" && T.id == "studio-\(s.systemDark ? "dark" : "light")",
          "store persists layout/mode separately; quick toggles cycle Paper→Studio and Dark→Auto")
    d.removePersistentDomain(forName: suite)
  } else { check(false, "UserDefaults suite for the persistence test") }
  let overrides = (load("overview.json", OverviewVM.self)?.accounts ?? []).compactMap { parseHex($0.color) }
  if let o = load("overview.json", OverviewVM.self) {
    check(o.accounts.contains { ($0.usage?.points ?? []).count > 1 } && o.accounts.contains { $0.doneToday != nil } && o.accounts.contains { !($0.queued ?? []).isEmpty },
          "overview: optional usage / doneToday / queued decode")
    let s = DeckStore(base: nil, fixtures: dir, token: nil, prefs: nil)
    let hexes = o.accounts.compactMap { s.accountHex($0.id) }
    check(Set(hexes).count == o.accounts.count, "one distinct colour per account: \(hexes.map { String(format: "#%06x", $0) }.joined(separator: " "))")
    if let i = o.accounts.firstIndex(where: { $0.color != nil }) { check(hexes[i] == workerHex(i, override: parseHex(o.accounts[i].color)), "AccountVM.color overrides the --wN palette (\(o.accounts[i].id))") }
    check(s.accountId(worker: "W1·2") == o.accounts.first { ($0.lanes ?? []).contains { $0.worker == "W1" } }?.id, "worker/lane id maps to its account colour")
    s.expandedWorkers.insert(o.accounts[0].id); s.refresh(); s.refresh()
    check(s.expandedWorkers.contains(o.accounts[0].id), "expanded Workers rows survive refresh")
    let bare = Account(id: "x", label: "x", tool: nil, scope: nil, pct5: nil, pct7: nil, resets5: nil, readAgeSec: nil, confidence: nil, state: nil, dispatch: nil, capacity: nil, lanes: nil)
    check(bare.usage == nil && bare.queued == nil && bare.doneToday == nil, "missing usage/queued/doneToday stay nil (rows hidden, nothing invented)")
  }
  for t in THEMES {
    let wp = workerColorPairs(t, extra: overrides)
    let wbad = wp.filter { contrast($0.1, $0.2) < $0.3 }.map { "\($0.0)=\(String(format: "%.2f", contrast($0.1, $0.2)))" }
    check(wbad.isEmpty, "worker colours, \(t.name): \(wp.count) pairs (lines ≥3:1, text ≥4.5:1)\(wbad.isEmpty ? "" : " — " + wbad.joined(separator: ", "))")
    let pairs = contrastPairs(t)
    let bad = pairs.filter { contrast($0.1, $0.2) < 4.5 }.map { "\($0.0)=\(String(format: "%.2f", contrast($0.1, $0.2)))" }
    check(bad.isEmpty, "WCAG AA contrast, \(t.name): \(pairs.count) pairs\(bad.isEmpty ? "" : " — " + bad.joined(separator: ", "))")
    T = t
    check([-3, -2, -1, 0, 1].allSatisfy { fontSize($0) >= 13 } && fontSize(-4) == 11, "\(t.name): body text ≥ 13 pt, badges 11 pt")
  }
  for (raw, want) in [("merge waits: main checkout busy · retry 21:44", "merging · retry in 1m · main checkout busy"), ("gates 2/3", "merging · retry in 1m · gates 2/3")] {
    let t = TaskVM(id: "1-x", num: 1, title: "x", summary: nil, tags: nil, priority: "P0", size: nil, state: "merging", stateReason: raw, worker: nil, progress: nil, step: nil,
                   etaFinish: nil, metrics: nil, lastActiveSec: nil, warnings: nil, deps: nil, resumeAt: 1060, kind: nil, rev: 1, question: nil)
    check(mergeText(t, now: 1000) == want, "merging status: \"\(raw)\" → \"\(mergeText(t, now: 1000))\"")
  }
  check(bump("P1", up: true) == "P0" && actionBody("priority", rev: 1, priority: "P0") != nil, "P0 is a valid priority key")
  check(actionBody("retry", rev: 4) != nil, "Retry action always available (incl. failed tasks)")
  T = THEMES[0]
  return ok
}

if let dir = arg("--check-fixtures") { exit(checkFixtures(URL(fileURLWithPath: dir)) ? 0 : 1) }

let fixturesDir = arg("--fixtures").map { URL(fileURLWithPath: $0) }
let token = (try? String(contentsOf: stateDir.appendingPathComponent("token"), encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
// Snapshots never touch the saved layout/mode.
let store = DeckStore(base: fixturesDir == nil ? URL(string: (arg("--url") ?? "http://127.0.0.1:7788") + "/") : nil, fixtures: fixturesDir, token: token,
                      prefs: isSnapshot ? nil : .standard)

let width: CGFloat = 520
/// --snapshot out.png [--layout studio|mission|paper] [--mode light|dark] [--project id] [--tab t] [--expand-workers] [--collapsed]: render offscreen to a PNG.
if let out = arg("--snapshot") {
  if let l = arg("--layout") { store.layout = l }
  store.mode = arg("--mode") ?? "dark"
  if let p = arg("--project") { store.selected = p }
  if let t = arg("--tab") { store.tab = t }
  if arg("--collapsed") != nil { store.collapsed = true }
  if CommandLine.arguments.contains("--expand-workers") { store.expandedWorkers = Set(store.accounts.map { $0.id }) }
  let png: Data? = MainActor.assumeIsolated {
    let r = ImageRenderer(content: PanelView(scroll: false).environmentObject(store).frame(width: width))
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
  let h: CGFloat = c ? 110 : screen.height
  panel.setFrame(NSRect(x: screen.maxX - width, y: screen.maxY - h, width: width, height: h), display: true, animate: true)
}
panel.orderFrontRegardless()
final class Closer: NSObject, NSWindowDelegate { func windowWillClose(_ n: Notification) { NSApp.terminate(nil) } }
let closer = Closer(); panel.delegate = closer
app.run()
