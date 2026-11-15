// agent-monitor — full-height floating panel on the right edge (macOS, SwiftUI).
// Runs status.py every 2 s (local files only: no network, no AI, no tokens) and renders it.
import AppKit
import SwiftUI

// MARK: - Model

struct Step: Decodable, Hashable { let title: String; let status: String }
struct Metric: Decodable, Hashable { let label: String; let done: Double; let total: Double?; let kind: String; let rate: String; let left: String }
struct Cmd: Decodable, Hashable { let cmd: String; let state: String; let secs: Double }
struct RunningTask: Decodable, Identifiable {
  var id: String { key }
  let key: String, title: String, account: String, elapsed: Int, steps: [Step], note: String
  let commands: Int, tokens: Int, edits: Int, recent: [Cmd], lastActive: Int, estimated: Bool, failovers: Int
  let num: String; let summary: String?; let metrics: [Metric]?; let tags: [String]?
  enum CodingKeys: String, CodingKey { case metrics, tags, summary, key, title, account, elapsed, steps, note, commands, tokens, edits, recent, lastActive, estimated, failovers, num = "id" }
}
struct Queued: Decodable, Identifiable { var id: String { num + title }; let num: String, title: String, summary: String, worker: String, waitingFor: String, blocked: Bool
  enum CodingKeys: String, CodingKey { case num = "id", title, summary, worker, waitingFor, blocked } }
struct Agent: Decodable, Identifiable { var id: String { title }; let title: String, tools: Int, recent: [String], lastActive: Int }
struct Done: Decodable, Identifiable { var id: String { tid + time }; let tid: String, title: String, time: String, ok: Bool
  enum CodingKeys: String, CodingKey { case tid = "id", title, time, ok } }
struct Account: Decodable, Identifiable { var id: String { name + tool }; let name: String, tool: String, five: Double, seven: Double, resetIn: Int, lastLimit: String
  let age: Int?; let unknown: Bool? }
struct Change: Decodable, Identifiable { var id: String { area }; let area: String, files: Int }
struct Status: Decodable {
  let time: String, running: [RunningTask], queued: [Queued]?, agents: [Agent], finished: [Done], accounts: [Account], commit: String, changes: [Change]
  let title: String?
  let health: [Health]?
}
struct Health: Decodable, Identifiable { var id: String { label }; let label: String; let up: Bool; let group: String? }

final class Store: ObservableObject {
  @Published var status: Status?
  @Published var collapsed = false {
    didSet { onCollapse?(collapsed) }
  }
  var onCollapse: ((Bool) -> Void)?
  let script: String
  private var proc: Process?
  private var buffer = Data()
  init(script: String) { self.script = script; start() }
  /// One long-lived `status.py --serve` process streams a JSON line every 2 s (no process start per refresh, no overlap).
  /// If it exits, it is restarted after 2 s.
  func start() {
    let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/python3"); p.arguments = [script, "--serve"]
    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
    pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
      let chunk = h.availableData
      guard let self = self, !chunk.isEmpty else { return }
      self.buffer.append(chunk)
      var latest: Data?
      while let nl = self.buffer.firstIndex(of: 0x0A) {
        latest = self.buffer.subdata(in: self.buffer.startIndex..<nl)
        self.buffer.removeSubrange(self.buffer.startIndex...nl)
      }
      if let line = latest, let s = try? JSONDecoder().decode(Status.self, from: line) {
        DispatchQueue.main.async { self.status = s }
      }
    }
    p.terminationHandler = { [weak self] _ in
      pipe.fileHandleForReading.readabilityHandler = nil
      DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self?.buffer = Data(); self?.start() }
    }
    do { try p.run(); proc = p } catch { DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in self?.start() } }
  }
}

// MARK: - Style

let orange = Color(red: 1.0, green: 0.45, blue: 0.05)
let bg = Color(white: 0.09), card = Color(white: 0.155), line = Color(white: 0.27)
let dim = Color(white: 0.80), faint = Color(white: 0.62)
let base: CGFloat = 15
func f(_ d: CGFloat = 0, _ w: Font.Weight = .regular, mono: Bool = false) -> Font { .system(size: base + d, weight: w, design: mono ? .monospaced : .default) }
func dur(_ s: Int) -> String { s >= 3600 ? "\(s / 3600)h \(s % 3600 / 60)m" : s >= 60 ? "\(s / 60)m \(s % 60)s" : "\(s)s" }
func secs(_ s: Double) -> String { s < 0 ? "" : s < 60 ? String(format: "%.1fs", s) : dur(Int(s)) }
func tok(_ t: Int) -> String { t >= 1_000_000 ? String(format: "%.1fM", Double(t) / 1e6) : "\(t / 1000)k" }
func usageColor(_ p: Double) -> Color { p >= 90 ? .red : p >= 60 ? .yellow : .green }
let claudeColor = Color(red: 0.80, green: 0.45, blue: 0.32), codexColor = Color(red: 0.22, green: 0.50, blue: 0.95)

struct Bar: View {
  let value: Double?
  let color: Color
  var height: CGFloat = 8
  @State private var phase: CGFloat = -0.4
  var body: some View {
    GeometryReader { g in
      ZStack(alignment: .leading) {
        Capsule().fill(Color.white.opacity(0.10))
        if let v = value {
          Capsule().fill(LinearGradient(colors: [color.opacity(0.8), color], startPoint: .leading, endPoint: .trailing))
            .frame(width: max(height, g.size.width * min(1, max(0, v)))).animation(.easeInOut(duration: 0.6), value: v)
        } else {
          Capsule().fill(LinearGradient(colors: [.clear, color, .clear], startPoint: .leading, endPoint: .trailing))
            .frame(width: g.size.width * 0.35).offset(x: g.size.width * phase)
            .onAppear { withAnimation(.linear(duration: 1.6).repeatForever(autoreverses: false)) { phase = 1.05 } }
        }
      }.clipShape(Capsule())
    }.frame(height: height)
  }
}

struct Block<Content: View>: View {
  let title: String; let content: Content
  init(_ title: String, @ViewBuilder content: () -> Content) { self.title = title; self.content = content() }
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(title).font(f(-2, .heavy)).foregroundColor(orange).tracking(1.2)
      content
    }
  }
}

struct Badge: View { let text: String; let color: Color
  var body: some View { Text(text).font(f(-3, .bold)).padding(.horizontal, 7).padding(.vertical, 2).background(color).foregroundColor(.white).clipShape(Capsule()) } }

struct CommandRow: View {
  let c: Cmd
  var body: some View {
    HStack(alignment: .top, spacing: 8) {
      Group {
        if c.state == "running" { ProgressView().controlSize(.small).frame(width: 16, height: 16) }
        else { Image(systemName: c.state == "ok" ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundColor(c.state == "ok" ? .green : .red) }
      }.font(f(-1))
      Text(c.cmd).font(f(-2, mono: true)).foregroundColor(c.state == "running" ? .white : dim).lineLimit(2).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 4)
      Text(secs(c.secs)).font(f(-3, mono: true)).foregroundColor(c.state == "running" ? orange : faint)
    }
  }
}

func num(_ v: Double) -> String { v == v.rounded() ? NumberFormatter.localizedString(from: NSNumber(value: Int(v)), number: .decimal) : String(format: "%.1f", v) }
func tagColor(_ t: String) -> Color {
  switch t.lowercased() {
  case "bug": return .red
  case "enhancement", "feature": return codexColor
  case "data", "research": return .teal
  case "money": return .yellow
  case "p1": return orange
  default: return Color(white: 0.4)
  }
}

struct MetricRow: View {
  let m: Metric
  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack {
        Text(m.label).font(f(-1)).foregroundColor(m.kind == "bad" && m.done > 0 ? .red : .white)
        Spacer()
        Text(m.total.map { "\(num(m.done)) / \(num($0))" } ?? num(m.done)).font(f(-1, .bold, mono: true))
          .foregroundColor(m.kind == "bad" && m.done > 0 ? .red : .white)
      }
      if let total = m.total { Bar(value: m.done / total, color: m.kind == "bad" ? .red : .green, height: 6) }
      if !m.rate.isEmpty || !m.left.isEmpty {
        Text([m.rate, m.left].filter { !$0.isEmpty }.joined(separator: " · ")).font(f(-3)).foregroundColor(faint)
      }
    }
  }
}

struct TaskCard: View {
  let t: RunningTask
  @State private var expanded = false
  @State private var raw = false
  var progress: Double {
    guard !t.steps.isEmpty else { return 0 }
    let done = Double(t.steps.filter { $0.status == "done" }.count), active = Double(t.steps.filter { $0.status == "active" }.count)
    return (done + active * 0.5) / Double(t.steps.count)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(spacing: 8) {
        Text(t.num).font(f(-2, .heavy, mono: true)).padding(.horizontal, 7).padding(.vertical, 3).background(orange).foregroundColor(.black).clipShape(RoundedRectangle(cornerRadius: 5))
        Text(t.title).font(f(2, .bold)).lineLimit(2)
        Spacer()
        Badge(text: t.account, color: t.account.contains("Claude") ? claudeColor : codexColor)
        Image(systemName: expanded ? "chevron.up" : "chevron.down").font(f(-1, .bold)).foregroundColor(faint)
      }
      if let sm = t.summary, !sm.isEmpty {
        Text(sm).font(f(-1)).foregroundColor(dim).lineLimit(2).fixedSize(horizontal: false, vertical: true)
      }
      if let tg = t.tags, !tg.isEmpty {
        HStack(spacing: 5) { ForEach(tg, id: \.self) { Badge(text: $0, color: tagColor($0)) } }
      }
      VStack(alignment: .leading, spacing: 6) {
        HStack {
          Text(t.estimated ? "Progress (estimated from activity)" : "Progress").font(f(-2)).foregroundColor(faint)
          Spacer()
          Text("\(Int(progress * 100))%").font(f(0, .bold, mono: true))
        }
        Bar(value: progress, color: orange, height: 10)
        if !expanded, let active = t.steps.first(where: { $0.status == "active" }) {
          Text("▸ \(active.title)").font(f(-2)).foregroundColor(dim).lineLimit(1)
        }
        if !expanded, let m = t.metrics?.first(where: { $0.total != nil }) ?? t.metrics?.first {
          Text("\(m.label): \(m.total.map { "\(num(m.done)) of \(num($0))" } ?? num(m.done))\(m.left.isEmpty ? "" : " · " + m.left)")
            .font(f(-2, .semibold)).foregroundColor(.white).lineLimit(1)
        }
      }
      if expanded {
      HStack(spacing: 14) {
        Label("running \(dur(t.elapsed))", systemImage: "clock")
      }.font(f(-2)).foregroundColor(dim)
      if let ms = t.metrics, !ms.isEmpty {
        VStack(alignment: .leading, spacing: 8) {
          Text("PROGRESS NUMBERS").font(f(-3, .heavy)).foregroundColor(faint)
          ForEach(ms, id: \.self) { MetricRow(m: $0) }
        }
      }
      HStack(spacing: 10) {
        Circle().fill(t.lastActive < 60 ? Color.green : t.lastActive < 300 ? Color.yellow : Color.red).frame(width: 8, height: 8)
        Text(t.lastActive < 60 ? "active now" : "last activity \(dur(t.lastActive)) ago").font(f(-2)).foregroundColor(dim)
        if t.failovers > 0 { Badge(text: "switched account ×\(t.failovers)", color: .purple) }
      }
      VStack(alignment: .leading, spacing: 6) {
        Text("SUBTASKS").font(f(-3, .heavy)).foregroundColor(faint)
        ForEach(t.steps, id: \.self) { s in
          HStack(spacing: 8) {
            Image(systemName: s.status == "done" ? "checkmark.circle.fill" : s.status == "active" ? "circle.dotted.circle" : "circle")
              .foregroundColor(s.status == "done" ? .green : s.status == "active" ? orange : faint)
            Text(s.title).font(f(-1, s.status == "active" ? .semibold : .regular)).foregroundColor(s.status == "pending" ? faint : .white)
          }
        }
      }
      if !t.recent.isEmpty || t.commands > 0 {
        VStack(alignment: .leading, spacing: 7) {
          Button(action: { withAnimation(.easeInOut(duration: 0.2)) { raw.toggle() } }) {
            HStack(spacing: 6) {
              Image(systemName: raw ? "chevron.down" : "chevron.right")
              Text("RAW ACTIVITY · \(t.commands) commands · \(t.edits) edits · \(tok(t.tokens)) tokens")
            }.font(f(-3, .heavy)).foregroundColor(faint)
          }.buttonStyle(.plain)
          if raw { ForEach(t.recent, id: \.self) { CommandRow(c: $0) } }
        }
      }
      if !t.note.isEmpty {
        VStack(alignment: .leading, spacing: 4) {
          Text("LATEST NOTE").font(f(-3, .heavy)).foregroundColor(faint)
          Text(t.note).font(f(-1)).foregroundColor(dim).lineLimit(4).fixedSize(horizontal: false, vertical: true)
        }
      }
      }
    }
    .padding(14).background(RoundedRectangle(cornerRadius: 12).fill(card)).overlay(RoundedRectangle(cornerRadius: 12).stroke(expanded ? orange.opacity(0.6) : line))
    .contentShape(Rectangle())
    .onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct QueuedRow: View {
  let q: Queued
  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack(spacing: 8) {
        Text(q.num).font(f(-2, .heavy, mono: true)).padding(.horizontal, 7).padding(.vertical, 3).background(Color(white: 0.35)).foregroundColor(.white).clipShape(RoundedRectangle(cornerRadius: 5))
        Text(q.title).font(f(0, .semibold)).lineLimit(2)
        Spacer()
        if !q.worker.isEmpty { Badge(text: q.worker, color: q.worker.contains("Claude") ? claudeColor : codexColor) }
      }
      if !q.summary.isEmpty { Text(q.summary).font(f(-2)).foregroundColor(dim).lineLimit(2).fixedSize(horizontal: false, vertical: true) }
      HStack(spacing: 6) {
        Image(systemName: q.blocked ? "person.fill.questionmark" : "hourglass").foregroundColor(q.blocked ? .yellow : faint)
        Text(q.waitingFor).font(f(-2)).foregroundColor(q.blocked ? .yellow : faint).lineLimit(2).fixedSize(horizontal: false, vertical: true)
      }
    }
    .padding(12).background(RoundedRectangle(cornerRadius: 10).fill(card.opacity(0.7))).overlay(RoundedRectangle(cornerRadius: 10).stroke(line, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
  }
}

struct AgentCard: View {
  let a: Agent
  @State private var expanded = false
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(spacing: 8) {
        Badge(text: "Rahul · Claude", color: claudeColor)
        Text("\(a.tools) actions").font(f(-2)).foregroundColor(dim)
        Spacer()
        Circle().fill(a.lastActive < 60 ? Color.green : .yellow).frame(width: 8, height: 8)
        Image(systemName: expanded ? "chevron.up" : "chevron.down").font(f(-1, .bold)).foregroundColor(faint)
      }
      Text(a.title).font(f(0, .semibold)).lineLimit(expanded ? 3 : 1)
      Bar(value: nil, color: claudeColor, height: 6)
      if !expanded, let r = a.recent.first { Text("▸ \(r)").font(f(-2)).foregroundColor(dim).lineLimit(1) }
      if expanded { ForEach(Array(a.recent.enumerated()), id: \.offset) { i, r in
        HStack(alignment: .top, spacing: 8) {
          Image(systemName: i == 0 ? "arrow.right.circle.fill" : "checkmark.circle").foregroundColor(i == 0 ? orange : faint)
          Text(r).font(f(-2, mono: true)).foregroundColor(i == 0 ? .white : dim).lineLimit(2).fixedSize(horizontal: false, vertical: true)
        }
      } }
    }
    .padding(14).background(RoundedRectangle(cornerRadius: 12).fill(card)).overlay(RoundedRectangle(cornerRadius: 12).stroke(expanded ? claudeColor.opacity(0.6) : line))
    .contentShape(Rectangle())
    .onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct AccountRow: View {
  let a: Account
  var stale: Bool { (a.age ?? 0) >= 600 }
  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(spacing: 8) {
        Text(a.name).font(f(0, .semibold))
        Badge(text: a.tool, color: a.tool == "Claude" ? claudeColor : codexColor)
        Spacer()
        if a.unknown == true { Text("not known yet").font(f(-1)).foregroundColor(faint) }
        else if a.five >= 100 { Text("LIMIT · resets in \(dur(a.resetIn))").font(f(-2, .bold)).foregroundColor(.red) }
        else { Text("5h \(Int(a.five))%  ·  7d \(Int(a.seven))%").font(f(-1, mono: true)).foregroundColor(stale ? faint : dim) }
      }
      Bar(value: a.unknown == true ? 0 : a.five / 100, color: stale ? faint : usageColor(a.five))
      HStack(spacing: 8) {
        if a.resetIn > 0 && a.five < 100 && a.unknown != true { Text("5-hour window resets in \(dur(a.resetIn))") }
        if let g = a.age, g >= 600 { Text("· reading from \(dur(g)) ago").foregroundColor(.yellow) }
      }.font(f(-3)).foregroundColor(faint)
    }
  }
}

struct Dot: View { let on: Bool; let label: String
  var body: some View { HStack(spacing: 5) { Circle().fill(on ? Color.green : Color.red).frame(width: 9, height: 9); Text(label) } } }

struct PanelView: View {
  @ObservedObject var store: Store
  var body: some View {
    VStack(alignment: .leading, spacing: 22) {
      HStack {
        Image(systemName: "gearshape.2.fill").foregroundColor(orange).font(f(4))
        Text(store.status?.title ?? "Agent monitor").font(f(4, .heavy))
        Spacer()
        Text(store.status?.time ?? "…").font(f(-2, mono: true)).foregroundColor(faint)
        Button(action: { store.collapsed.toggle() }) {
          Image(systemName: store.collapsed ? "chevron.down.circle.fill" : "chevron.up.circle.fill").font(f(6)).foregroundColor(orange)
        }.buttonStyle(.plain).help(store.collapsed ? "Expand" : "Collapse")
      }
      if store.collapsed, let s = store.status {
        HStack(spacing: 14) {
          Label("\(s.running.count) running · \(s.queued?.count ?? 0) waiting", systemImage: "gearshape.2")
          ForEach(s.accounts) { a in
            Text("\(a.name.prefix(2)) \(a.five >= 100 ? "LIMIT" : "\(Int(a.five))%")").foregroundColor(usageColor(a.five))
          }
        }.font(f(-1, .semibold, mono: true))
      } else if let s = store.status {
        Block("ACCOUNTS") { ForEach(s.accounts) { AccountRow(a: $0) } }
        Block("IN PROGRESS · \(s.running.count)") {
          if s.running.isEmpty { Text("Nothing running").font(f(-1)).foregroundColor(faint) }
          ForEach(s.running) { TaskCard(t: $0) }
        }
        if let q = s.queued, !q.isEmpty {
          Block("UP NEXT · \(q.count) WAITING") { ForEach(q) { QueuedRow(q: $0) } }
        }
        if !s.agents.isEmpty { Block("CLAUDE AGENTS · \(s.agents.count) RUNNING") { ForEach(s.agents) { AgentCard(a: $0) } } }
        Block("RECENTLY FINISHED") {
          ForEach(s.finished) { d in
            HStack(spacing: 8) {
              Image(systemName: d.ok ? "checkmark.circle.fill" : "xmark.octagon.fill").foregroundColor(d.ok ? .green : .red)
              Text(d.tid).font(f(-2, .bold, mono: true)).foregroundColor(faint)
              Text(d.title).font(f(-1)).lineLimit(1)
              Spacer()
              Text(d.time).font(f(-2, mono: true)).foregroundColor(faint)
            }
          }
        }
        Block("REPOSITORY") {
          Text(s.commit).font(f(-2, mono: true)).foregroundColor(dim).lineLimit(2)
          if s.changes.isEmpty { Text("Working tree clean").font(f(-2)).foregroundColor(faint) }
          ForEach(s.changes) { c in
            HStack { Text(c.area).font(f(-2, mono: true)); Spacer(); Text("\(c.files) files changed").font(f(-2)).foregroundColor(.yellow) }
          }
        }
        if let hs = s.health, !hs.isEmpty {
          Block("HEALTH") {
            ForEach(Array(Dictionary(grouping: hs, by: { $0.group ?? "" }).keys.sorted()), id: \.self) { g in
              HStack(spacing: 18) {
                ForEach(hs.filter { ($0.group ?? "") == g }) { h in Dot(on: h.up, label: h.label) }
              }.font(f(-1)).foregroundColor(dim)
            }
          }
        }
      } else {
        Text("Loading…").foregroundColor(faint)
      }
    }
    .padding(18).foregroundColor(.white)
  }
}

// MARK: - Window: full height on the right edge

let script = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "status.py"
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let store = Store(script: script)
let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
let width: CGFloat = 520
let panel = NSPanel(contentRect: NSRect(x: screen.maxX - width, y: screen.minY, width: width, height: screen.height),
                    styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                    backing: .buffered, defer: false)
panel.titleVisibility = .hidden
panel.titlebarAppearsTransparent = true
panel.level = .floating
panel.isFloatingPanel = true
panel.hidesOnDeactivate = false
panel.isMovableByWindowBackground = true
panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
panel.appearance = NSAppearance(named: .darkAqua)
panel.backgroundColor = NSColor(white: 0.09, alpha: 1)
panel.contentView = NSHostingView(rootView: ScrollView { PanelView(store: store) }.background(bg).padding(.top, 14))
panel.setFrame(NSRect(x: screen.maxX - width, y: screen.minY, width: width, height: screen.height), display: true)
store.onCollapse = { c in
  let h: CGFloat = c ? 96 : screen.height
  panel.setFrame(NSRect(x: screen.maxX - width, y: screen.maxY - h, width: width, height: h), display: true, animate: true)
}
panel.orderFrontRegardless()
final class Closer: NSObject, NSWindowDelegate { func windowWillClose(_ n: Notification) { NSApp.terminate(nil) } }
let closer = Closer(); panel.delegate = closer
app.run()
