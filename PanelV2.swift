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
struct MetricVM: Decodable, Hashable { let label: String, done: Double, total: Double?, kind: String?, rate: String?, left: String? }
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
  let worker: String, account: String, label: String, scope: String?, pct5: Double?, pct7: Double?, resets5: Int?, state: String?, dispatch: Bool?
  let capacity: Capacity?, lanes: [Lane]?, allowed: Bool?, reason: String?
  var asAccount: Account {
    Account(id: account, label: label, tool: nil, scope: scope, pct5: pct5, pct7: pct7, resets5: resets5, readAgeSec: nil, confidence: nil,
            state: state, dispatch: dispatch, capacity: capacity, lanes: lanes)
  }
}
struct Health: Decodable, Identifiable, Hashable { var id: String { label }; let label: String, up: Bool, group: String? }
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
  guard actionNames.contains(action), let rev = rev else { return nil }
  var b: [String: Any] = ["action": action, "rev": rev]
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
  var onCollapse: ((Bool) -> Void)?
  var fetchedAt = Date()
  let base: URL?, fixtures: URL?, token: String?

  init(base: URL?, fixtures: URL?, token: String?) {
    self.base = base; self.fixtures = fixtures; self.token = token
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

// MARK: - Style

let orange = Color(red: 1.0, green: 0.45, blue: 0.05)
let bg = Color(white: 0.09), card = Color(white: 0.155), line = Color(white: 0.27)
let dim = Color(white: 0.80), faint = Color(white: 0.62)
let okC = Color(red: 0.20, green: 0.78, blue: 0.35), warnC = Color(red: 1, green: 0.8, blue: 0), badC = Color(red: 1, green: 0.27, blue: 0.23)
let officeC = Color(red: 0.31, green: 0.49, blue: 1.0), personalC = Color(red: 1.0, green: 0.35, blue: 0.12)
let base: CGFloat = 14
func f(_ d: CGFloat = 0, _ w: Font.Weight = .regular, mono: Bool = false) -> Font { .system(size: base + d, weight: w, design: mono ? .monospaced : .default) }
func usageColor(_ p: Double?) -> Color { guard let p = p else { return faint }; return p >= 90 ? badC : p >= 60 ? warnC : okC }
func hex(_ s: String?) -> Color {
  guard let s = s, s.hasPrefix("#"), s.count == 7, let v = Int(s.dropFirst(), radix: 16) else { return orange }
  return Color(red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255)
}

struct Bar: View {
  let value: Double; let color: Color; var height: CGFloat = 7
  var body: some View {
    GeometryReader { g in
      ZStack(alignment: .leading) {
        Capsule().fill(Color.white.opacity(0.10))
        Capsule().fill(color).frame(width: max(height, g.size.width * min(1, max(0, value))))
      }
    }.frame(height: height)
  }
}
struct Badge: View {
  let text: String; let color: Color
  var body: some View { Text(text).font(f(-4, .heavy)).padding(.horizontal, 6).padding(.vertical, 2).background(color).foregroundColor(.white).clipShape(Capsule()) }
}
struct ScopeBadge: View {
  let scope: String?; var short = false
  var body: some View {
    let o = scope == "office"
    Badge(text: short ? (o ? "O" : scope == "personal" ? "P" : "?") : (o ? "OFFICE" : scope == "personal" ? "PERSONAL" : "UNTAGGED"),
          color: o ? officeC : scope == "personal" ? personalC : badC)
  }
}
struct NumChip: View {
  let text: String
  var body: some View {
    Text(text).font(f(-2, .heavy, mono: true)).padding(.horizontal, 6).padding(.vertical, 2).background(orange).foregroundColor(.black)
      .clipShape(RoundedRectangle(cornerRadius: 5))
  }
}
struct Section<Content: View>: View {
  let title: String; let trailing: String; let content: Content
  init(_ title: String, _ trailing: String = "", @ViewBuilder content: () -> Content) { self.title = title; self.trailing = trailing; self.content = content() }
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack { Text(title).font(f(-2, .heavy)).foregroundColor(orange).tracking(1.2); Spacer(); Text(trailing).font(f(-2, mono: true)).foregroundColor(faint) }
      content
    }
  }
}
struct Key: View {
  let title: String; var tint: Color = Color(white: 0.25); var disabled = false; let action: () -> Void
  var body: some View {
    Button(action: action) {
      Text(title).font(f(-2, .bold)).padding(.horizontal, 10).padding(.vertical, 5).background(disabled ? Color(white: 0.18) : tint)
        .foregroundColor(disabled ? faint : .white).clipShape(RoundedRectangle(cornerRadius: 7))
    }.buttonStyle(.plain).disabled(disabled)
  }
}

// MARK: - Accounts and lanes

struct LaneRow: View {
  let l: Lane; let a: Account; let projects: [String: String]
  var body: some View {
    HStack(alignment: .top, spacing: 8) {
      Text("\(l.worker)·\(l.lane)").font(f(-2, .heavy, mono: true)).frame(width: 44, alignment: .leading)
      Text(text).font(f(-2, mono: true)).foregroundColor(color).lineLimit(2).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 0)
    }.padding(.vertical, 3).padding(.horizontal, 8)
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
    case "running": return (l.lastActiveSec ?? 0) > 600 ? warnC : .white
    case "cooling": return badC
    default:
      switch freeLaneText(a).kind { case "spare": return orange; case "cool": return badC; default: return faint }
    }
  }
}

struct AccountBlock: View {
  let a: Account; let now: Int; let projects: [String: String]; var allowed = true; var reason: String? = nil
  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack(spacing: 6) {
        if !allowed { Text("🔒") }
        Text(a.label).font(f(0, .bold))
        ScopeBadge(scope: a.scope)
        Spacer()
        Text(quotaText(a, now: now)).font(f(-2, .semibold, mono: true)).foregroundColor(a.state == "cooling" ? badC : dim).lineLimit(1)
      }
      Bar(value: (a.state == "cooling" ? 100 : (a.pct5 ?? 0)) / 100, color: a.state == "cooling" ? badC : usageColor(a.pct5))
      if let r = reason, !allowed { Text(r).font(f(-2, .bold)).foregroundColor(badC) }
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
    VStack(alignment: .leading, spacing: 6) {
      HStack(spacing: 8) {
        Text(icon).font(f(0)).frame(width: 16)
        NumChip(text: t.numText)
        Text(t.title).font(f(0, .bold)).lineLimit(1)
        Spacer()
        if let e = t.etaFinish, t.state == "running" { Text("ETA \(hhmm(e))").font(f(-3, mono: true)).foregroundColor(faint) }
      }.contentShape(Rectangle()).onTapGesture { open.toggle() }
      Text(line).font(f(-3)).foregroundColor(faint).lineLimit(open ? 3 : 1)
      if let p = t.progress, t.state == "running" { Bar(value: p, color: orange, height: 5) }
      if open { detail }
    }.padding(9).background(RoundedRectangle(cornerRadius: 10).fill(card))
  }
  @ViewBuilder var detail: some View {
    if let s = t.summary, !s.isEmpty { Text(s).font(f(-2)).foregroundColor(dim).fixedSize(horizontal: false, vertical: true) }
    if let st = t.step { Text("◉ \(st.index)/\(st.count) \(st.title)\(st.etaMin.map { " · ~\(Int($0))m" } ?? "")").font(f(-2)).foregroundColor(orange) }
    ForEach(t.metrics ?? [], id: \.self) { m in
      VStack(alignment: .leading, spacing: 3) {
        HStack {
          Text(m.label).font(f(-2)).foregroundColor(m.kind == "bad" && m.done > 0 ? badC : .white)
          Spacer()
          Text("\(Int(m.done))\(m.total.map { " / \(Int($0))" } ?? "")\(m.rate.map { $0.isEmpty ? "" : " · \($0)" } ?? "")").font(f(-2, .bold, mono: true))
        }
        if let tot = m.total, tot > 0 { Bar(value: m.done / tot, color: okC, height: 5) }
      }
    }
    ForEach(t.warnings ?? [], id: \.self) { Text("⚠ \($0)").font(f(-2, .bold)).foregroundColor(warnC) }
    if let q = t.question {
      Text("? \(q.text)").font(f(-2, .bold)).foregroundColor(warnC)
      HStack { ForEach(q.options ?? [], id: \.self) { o in Key(title: o, tint: o == q.default ? orange : Color(white: 0.25)) { store.act(nil, t.id, actionBody("answer", rev: t.rev, note: o)) } } }
    }
    TaskActions(t: t)
  }
}

struct TaskActions: View {
  @EnvironmentObject var store: DeckStore
  let t: TaskVM
  @State private var armCancel = false
  var body: some View {
    HStack(spacing: 6) {
      if ["running", "starting", "queued", "ready"].contains(t.state) { Key(title: "Pause") { store.act(nil, t.id, actionBody("pause", rev: t.rev)) } }
      if ["blocked", "limit_paused"].contains(t.state) && t.question == nil { Key(title: "Resume", tint: okC) { store.act(nil, t.id, actionBody("resume", rev: t.rev)) } }
      if ["hung", "failed", "changes_requested"].contains(t.state) { Key(title: "Retry", tint: orange) { store.act(nil, t.id, actionBody("retry", rev: t.rev)) } }
      if !["done", "cancelled", "merging", "review"].contains(t.state) {
        Key(title: "Reassign…") { store.openReassign(t) }
        Key(title: "▲", disabled: t.priority == "P0") { store.act(nil, t.id, actionBody("priority", rev: t.rev, priority: bump(t.priority, up: true))) }
        Key(title: "▼", disabled: t.priority == "P3") { store.act(nil, t.id, actionBody("priority", rev: t.rev, priority: bump(t.priority, up: false))) }
        Spacer()
        // Two-step confirm in place of the phone's hold-to-cancel.
        Key(title: armCancel ? "Confirm cancel" : "Cancel", tint: armCancel ? badC : Color(white: 0.25)) {
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
    VStack(alignment: .leading, spacing: 6) {
      HStack(spacing: 8) {
        Text(r.task.state == "merging" ? "⇄" : "▣"); NumChip(text: r.task.numText)
        if r.pilot == true { Badge(text: "PILOT", color: orange) }
        Text(r.task.title).font(f(0, .bold)).lineLimit(1)
        Spacer()
        Text("\(r.worker ?? "") \(r.account ?? "")").font(f(-3)).foregroundColor(faint)
      }
      Text(r.summary).font(f(-2)).foregroundColor(dim).lineLimit(4).fixedSize(horizontal: false, vertical: true)
      let dw = r.doneWhen ?? []
      Text("\(dw.filter { $0.met }.count)/\(dw.count) done-when ✓ · tests \((r.tests ?? []).reduce(0) { $0 + ($1.passed ?? 0) }) · waiting \(dur(r.waitingSec))\(r.unlocks?.isEmpty == false ? " · approving starts \(r.unlocks!.map { String($0.split(separator: "-").first ?? "") }.joined(separator: ", "))" : "")")
        .font(f(-3)).foregroundColor(faint)
      ForEach(dw, id: \.self) { d in Text("\(d.met ? "✓" : "✗") \(d.item) — \(d.evidence)").font(f(-3)).foregroundColor(d.met ? okC : badC).lineLimit(2) }
      if let u = r.git?.uncommitted, u > 0 { Text("⚠ \(u) uncommitted change\(u > 1 ? "s" : "") will not be merged").font(f(-2, .bold)).foregroundColor(warnC) }
      ForEach(r.risks ?? [], id: \.self) { Text("• \($0)").font(f(-3)).foregroundColor(dim) }
      ForEach(Array((r.decisionsNeeded ?? []).enumerated()), id: \.offset) { i, d in
        Text(d.question).font(f(-2, .semibold))
        HStack { ForEach(d.options ?? [], id: \.self) { o in
          Key(title: o + (o == d.default ? " (default)" : ""), tint: o == d.default ? orange : Color(white: 0.25)) {
            store.act(nil, r.task.id, actionBody("answer", rev: r.task.rev, decision: i, option: o))
          }
        } }
      }
      if writing {
        TextField("What should change?", text: $note).textFieldStyle(.roundedBorder).font(f(-1))
      }
      if r.task.state != "merging" {
        HStack {
          Key(title: "Approve ✓", tint: okC) { store.act(nil, r.task.id, actionBody("approve", rev: r.task.rev)) }
          Key(title: writing ? "Send changes" : "Changes…", tint: orange) {
            if writing { store.act(nil, r.task.id, actionBody("changes", rev: r.task.rev, note: note)); writing = false } else { writing = true }
          }
        }
      }
    }.padding(9).background(RoundedRectangle(cornerRadius: 10).fill(Color(red: 0.2, green: 0.15, blue: 0.1)))
  }
}

struct EventRow: View {
  let e: DeckEvent
  var body: some View {
    HStack(alignment: .top, spacing: 6) {
      Text(hhmm(e.ts)).font(f(-3, mono: true)).foregroundColor(faint)
      Text(e.severity == "alert" ? "!" : e.severity == "warn" ? "⚠" : e.severity == "action" ? "▣" : "·")
      Text(e.text).font(f(-2)).foregroundColor(e.severity == "alert" || e.severity == "warn" ? badC : dim).lineLimit(2).fixedSize(horizontal: false, vertical: true)
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
                .font(f(-3, w.allowed ? .regular : .bold)).foregroundColor(w.allowed ? faint : badC)
            }
            Spacer()
          }.padding(8).background(RoundedRectangle(cornerRadius: 8).fill(w.allowed ? card : Color(white: 0.12)))
        }.buttonStyle(.plain).disabled(!w.allowed).opacity(w.allowed ? 1 : 0.55)
      }
      Text("Greyed rows can't be clicked. The CLI and API refuse them too and log an alert.").font(f(-3)).foregroundColor(faint)
    }.padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color(white: 0.13))).overlay(RoundedRectangle(cornerRadius: 12).stroke(orange, lineWidth: 1))
  }
}

// MARK: - Panel

struct PanelView: View {
  @EnvironmentObject var store: DeckStore
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
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
      if let t = store.toast { Text(t).font(f(-1, .semibold)).padding(8).frame(maxWidth: .infinity, alignment: .leading).background(Color(white: 0.2)).clipShape(RoundedRectangle(cornerRadius: 8)) }
    }.padding(16).foregroundColor(.white)
  }

  var header: some View {
    let ib = store.overview?.inbox
    return HStack(spacing: 8) {
      Image(systemName: "gearshape.2.fill").foregroundColor(orange).font(f(4))
      Text(store.project?.project.short ?? "Agent Deck").font(f(4, .heavy))
      if let s = store.project?.project.scope { ScopeBadge(scope: s) }
      if (ib?.review ?? 0) > 0 { Badge(text: "\(ib!.review!) review", color: orange) }
      if (ib?.questions ?? 0) > 0 { Badge(text: "\(ib!.questions!) ?", color: Color(white: 0.35)) }
      if (ib?.alerts ?? 0) > 0 { Badge(text: "\(ib!.alerts!) !", color: badC) }
      Spacer()
      if store.offline { Badge(text: "OFFLINE · last update \(hhmm(store.lastUpdate))", color: badC) }
      else if store.fixtures != nil { Badge(text: "FIXTURES", color: Color(white: 0.3)) }
      Text(clockSecFmt.string(from: Date(timeIntervalSince1970: TimeInterval(store.now)))).font(f(-2, mono: true)).foregroundColor(faint)
      Button(action: { store.collapsed.toggle() }) {
        Image(systemName: store.collapsed ? "chevron.down.circle.fill" : "chevron.up.circle.fill").font(f(6)).foregroundColor(orange)
      }.buttonStyle(.plain)
    }
  }

  func chips(_ o: OverviewVM) -> some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 6) {
        chip("All", on: store.selected == nil, color: orange) { store.selected = nil }
        ForEach(o.projects) { p in
          chip("\(p.icon ?? "") \(p.name) \(p.scope == "office" ? "O" : "P")\(p.enabled == false ? " · paused" : "")", on: store.selected == p.id, color: hex(p.color)) {
            if p.enabled != false { store.selected = p.id }
          }.opacity(p.enabled == false ? 0.45 : 1)
        }
      }
    }
  }
  func chip(_ s: String, on: Bool, color: Color, _ tap: @escaping () -> Void) -> some View {
    Button(action: tap) {
      Text(s).font(f(-1, .semibold)).padding(.horizontal, 10).padding(.vertical, 5)
        .background(on ? color : Color(white: 0.18)).foregroundColor(.white).clipShape(Capsule())
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
    }.font(f(-2, .semibold, mono: true)).lineLimit(1)
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
      HStack { Text("\(b.low == true ? "▲ " : "")BACKLOG").font(f(-2, .heavy)); Text("\(b.eligible ?? 0) ready / target \(b.target ?? 0)").font(f(-2, .bold)).foregroundColor(orange)
        Text(b.hint ?? "").font(f(-3)).foregroundColor(faint).lineLimit(2) }
    }
    Section("PROJECTS") {
      ForEach(o.projects) { p in
        Button(action: { if p.enabled != false { store.selected = p.id } }) {
          HStack(spacing: 8) {
            Text(p.icon ?? "◆"); Text(p.name).font(f(0, .bold)); ScopeBadge(scope: p.scope, short: true)
            Text(p.headline ?? "").font(f(-2)).foregroundColor(faint).lineLimit(1)
            Spacer()
            Text(counts(p.counts)).font(f(-2, .bold, mono: true))
          }.padding(8).background(RoundedRectangle(cornerRadius: 8).fill(card)).opacity(p.enabled == false ? 0.45 : 1)
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
        ForEach(p.workers ?? []) { w in AccountBlock(a: w.asAccount, now: store.now, projects: projectNames, allowed: w.allowed != false, reason: w.reason) }
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
      Section("DONE TODAY · \(d.count)") { Text(d.map { "✓ \($0.numText) \($0.title)" }.joined(separator: "   ")).font(f(-2)).foregroundColor(faint) }
    }
  }

  @ViewBuilder func repo(_ p: ProjectVM) -> some View {
    Section("DOMAINS & MERGE") {
      ForEach(p.domains ?? []) { d in
        HStack {
          Text(d.name).font(f(-2, mono: true)); Spacer()
          Text(d.holder.map { "\($0.split(separator: "-").first ?? "") since \(hhmm(d.since))" } ?? (d.note ?? "free")).font(f(-2)).foregroundColor(dim)
          if let w = d.waiting, !w.isEmpty { Text("· \(w.map { String($0.split(separator: "-").first ?? "") }.joined(separator: ", ")) waiting").font(f(-2)).foregroundColor(warnC) }
        }
      }
      if let m = p.merge?.active { Text("merge · \(m.task.split(separator: "-").first ?? "") (gates \(m.gatesDone ?? 0)/\(m.gatesTotal ?? 0))").font(f(-2, mono: true)) }
    }
    Section("REPO & HEALTH") {
      Text(p.project.repo?.commit ?? "—").font(f(-2, mono: true)).foregroundColor(dim)
      if (p.project.repo?.changes ?? []).isEmpty { Text("working tree clean").font(f(-2)).foregroundColor(faint) }
      ForEach(p.project.repo?.changes ?? []) { c in HStack { Text(c.area).font(f(-2, mono: true)); Spacer(); Text("\(c.files) files changed").font(f(-2)).foregroundColor(warnC) } }
      HStack(spacing: 14) {
        ForEach(p.project.health ?? []) { h in Text("\(h.up ? "●" : "✗") \(h.label)").font(f(-2)).foregroundColor(h.up ? okC : badC) }
      }
    }
  }
}

struct NeedsRow: View {
  @EnvironmentObject var store: DeckStore
  let n: NeedsItem
  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack(spacing: 8) {
        Text(n.kind == "review" ? "▣" : n.kind == "question" ? "?" : "!").foregroundColor(n.kind == "alert" ? badC : orange)
        if let t = n.task { NumChip(text: n.num.map(String.init) ?? String(t.split(separator: "-").first ?? "")) }
        Text(n.title).font(f(0, .bold)).lineLimit(1)
        Spacer()
      }
      Text(n.text).font(f(-3)).foregroundColor(faint).lineLimit(2)
      HStack(spacing: 6) {
        if n.kind == "review", let t = n.task { Key(title: "Approve", tint: okC) { store.act(n.project, t, actionBody("approve", rev: n.rev)) } }
        if n.kind == "question", let t = n.task {
          ForEach(n.options ?? [], id: \.self) { o in Key(title: o) { store.act(n.project, t, actionBody("answer", rev: n.rev, note: o)) } }
        }
        Key(title: "Open") { store.selected = n.project; store.tab = n.kind == "review" ? "review" : n.kind == "alert" ? "alerts" : "board" }
      }
    }.padding(9).background(RoundedRectangle(cornerRadius: 10).fill(n.kind == "alert" ? Color(red: 0.25, green: 0.1, blue: 0.1) : card))
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
  check(actionBody("approve", rev: nil) == nil, "actionBody needs rev")
  check(bump("P2", up: true) == "P1" && bump("P0", up: true) == "P0" && bump("P3", up: false) == "P3", "priority bump clamps")
  check(dur(540) == "9m" && dur(12000) == "3h20" && dur(45) == "45s", "dur formatting")
  return ok
}

if let dir = arg("--check-fixtures") { exit(checkFixtures(URL(fileURLWithPath: dir)) ? 0 : 1) }

let fixturesDir = arg("--fixtures").map { URL(fileURLWithPath: $0) }
let token = (try? String(contentsOf: stateDir.appendingPathComponent("token"), encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
let store = DeckStore(base: fixturesDir == nil ? URL(string: (arg("--url") ?? "http://127.0.0.1:7788") + "/") : nil, fixtures: fixturesDir, token: token)

let width: CGFloat = 520
/// --snapshot out.png [--project id] [--tab t]: render the panel offscreen to a PNG (fixture review without a screen).
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
panel.appearance = NSAppearance(named: .darkAqua)
panel.backgroundColor = NSColor(white: 0.09, alpha: 1)
panel.contentView = NSHostingView(rootView: ScrollView { PanelView().environmentObject(store) }.background(bg).padding(.top, 14))
panel.setFrame(NSRect(x: screen.maxX - width, y: screen.minY, width: width, height: screen.height), display: true)
store.onCollapse = { c in
  let h: CGFloat = c ? 96 : screen.height
  panel.setFrame(NSRect(x: screen.maxX - width, y: screen.maxY - h, width: width, height: h), display: true, animate: true)
}
panel.orderFrontRegardless()
final class Closer: NSObject, NSWindowDelegate { func windowWillClose(_ n: Notification) { NSApp.terminate(nil) } }
let closer = Closer(); panel.delegate = closer
app.run()
