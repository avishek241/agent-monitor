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


// MARK: - Fonts (bundled in fonts/, SIL OFL; registered for this process only; system fonts are the fallback)

enum FontKit {
  static func register(_ dir: String) {
    guard let files = try? FileManager.default.contentsOfDirectory(atPath: dir) else { return }
    for f in files where f.hasSuffix(".woff2") || f.hasSuffix(".ttf") || f.hasSuffix(".otf") {
      CTFontManagerRegisterFontsForURL(URL(fileURLWithPath: dir + "/" + f) as CFURL, .process, nil)
    }
  }
  private static let wghtTag = 0x77676874  // 'wght'
  /// A font by PostScript name; `wght` sets the weight axis of a variable font.
  static func make(_ ps: String, _ size: CGFloat, wght: CGFloat? = nil) -> Font? {
    guard let base = NSFont(name: ps, size: size) else { return nil }
    guard let w = wght else { return Font(base) }
    let d = base.fontDescriptor.addingAttributes([NSFontDescriptor.AttributeName(rawValue: kCTFontVariationAttribute as String): [wghtTag: w]])
    return Font(NSFont(descriptor: d, size: size) ?? base)
  }
}
func sysWeight(_ w: CGFloat) -> Font.Weight { w >= 850 ? .heavy : w >= 750 ? .bold : w >= 650 ? .bold : w >= 550 ? .semibold : w >= 450 ? .medium : .regular }

// MARK: - Theme (design tokens)
// Three layouts from docs/v2/mockups, each in light and dark:
//   Studio          panel.html + deck.html (PhoneDeck Studio: warm-grey body, dark LCD blocks, chunky ink-bordered keys)
//   Mission Control alt-a/panel.html (dense, rules, LEDs, monospace numerals, sparklines, ticker, key hints)
//   Paper           alt-b/panel.html (serif headlines, humanist text, pastel pills, soft cards, quota rings)
// All text/background pairs meet WCAG AA (4.5:1). Data and behaviour are identical in every theme.

func hex(_ v: UInt32, _ a: Double = 1) -> Color {
  Color(.sRGB, red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255, opacity: a)
}
func nsHex(_ v: UInt32) -> NSColor {
  NSColor(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255, blue: CGFloat(v & 0xFF) / 255, alpha: 1)
}

/// Spacing scale (4/8 px).
enum S { static let xxs: CGFloat = 2, xs: CGFloat = 4, s: CGFloat = 8, m: CGFloat = 12, l: CGFloat = 16, xl: CGFloat = 24 }

struct Chip { var fill: Color, ink: Color, stroke: Color
  static func solid(_ c: Color, _ ink: Color = .white) -> Chip { Chip(fill: c, ink: ink, stroke: .clear) }
  static func outline(_ c: Color) -> Chip { Chip(fill: c.opacity(0.10), ink: c, stroke: c.opacity(0.55)) }
  static func tint(_ bg: UInt32, _ ink: UInt32) -> Chip { Chip(fill: hex(bg), ink: hex(ink), stroke: .clear) }
}
enum Role { case ok, warn, bad, hot, claude, codex, violet, teal, sky, neutral, number, numberMuted }

enum LayoutKind: String, CaseIterable { case studio, mission, paper
  var label: String { switch self { case .studio: return "Studio"; case .mission: return "Mission Control"; case .paper: return "Paper" } }
}

struct Theme {
  var layout: LayoutKind = .studio
  var dark = false
  var id: String { layout.rawValue + (dark ? "-dark" : "-light") }
  var name: String { layout.label + " · " + (dark ? "Dark" : "Light") }
  // surfaces
  var bg = Color.black, bgTop = Color.black, bg2 = Color.black, surface = Color.black, surfaceAlt = Color.black
  var line = Color.gray, lineStrong = Color.gray, ink = Color.black
  var nsBg = NSColor.black
  // text
  var text = Color.white, dim = Color.gray, faint = Color.gray
  // accents & status
  var accent = Color.orange, accentText = Color.orange, onAccent = Color.black
  var ok = Color.green, warn = Color.yellow, bad = Color.red, info = Color.blue, magenta = Color.pink, violet = Color.purple
  var claude = Color.orange, codex = Color.blue, track = Color.gray, progress = Color.orange
  // Studio LCD + key tints
  var lcd = Color.black, lcdText = Color.white, lcdDim = Color.gray, lcdLine = Color.gray
  var keyAlert = Color.white, keyQueue = Color.white
  var chips: [Role: Chip] = [:]

  func chip(_ r: Role) -> Chip { chips[r] ?? .solid(.gray) }
  func usage(_ p: Double) -> Color { p >= 90 ? bad : p >= 60 ? warn : ok }
  func workerRole(_ s: String) -> Role { s.contains("Claude") ? .claude : .codex }
  func tagRole(_ t: String) -> Role {
    switch t.lowercased() {
    case "bug": return .bad
    case "enhancement", "feature": return .sky
    case "data", "research": return .teal
    case "money": return .warn
    case "p1": return .hot
    default: return .neutral
    }
  }
  /// Extra leading for ~1.4 line height.
  func leading(_ size: CGFloat) -> CGFloat { (size * 0.22).rounded() }

  // Type. Studio: Space Grotesk titles, JetBrains Mono labels, Doto LCD digits.
  // Mission Control: Barlow Semi Condensed titles, IBM Plex Mono everything else. Paper: Fraunces headlines, Source Sans 3 text.
  func title(_ size: CGFloat, _ w: CGFloat = 700) -> Font {
    switch layout {
    case .studio: return FontKit.make("SpaceGrotesk-Light", size, wght: w) ?? .system(size: size, weight: sysWeight(w))
    case .mission:
      let ps = w >= 700 ? "BarlowSemiCondensed-Bold" : w >= 600 ? "BarlowSemiCondensed-SemiBold" : "BarlowSemiCondensed-Medium"
      return FontKit.make(ps, size) ?? .system(size: size, weight: sysWeight(w)).width(.condensed)
    case .paper: return FontKit.make("Fraunces-Regular", size, wght: min(600, max(400, w - 200))) ?? .system(size: size, weight: .medium, design: .serif)
    }
  }
  func text(_ size: CGFloat, _ w: CGFloat = 400) -> Font {
    switch layout {
    case .studio: return FontKit.make("SpaceGrotesk-Light", size, wght: max(400, w)) ?? .system(size: size, weight: sysWeight(w))
    case .mission: return mono(size, w)
    case .paper: return FontKit.make("SourceSans3-Roman", size, wght: w) ?? .system(size: size, weight: sysWeight(w))
    }
  }
  func mono(_ size: CGFloat, _ w: CGFloat = 400) -> Font {
    switch layout {
    case .mission:
      let ps = w >= 700 ? "IBMPlexMono-Bold" : w >= 600 ? "IBMPlexMono-SemiBold" : "IBMPlexMono-Regular"
      return FontKit.make(ps, size) ?? .system(size: size, weight: sysWeight(w), design: .monospaced)
    case .paper: return (FontKit.make("SourceSans3-Roman", size + 0.5, wght: w) ?? .system(size: size, weight: sysWeight(w))).monospacedDigit()
    case .studio: return FontKit.make("JetBrainsMono-Regular", size, wght: w) ?? .system(size: size, weight: sysWeight(w), design: .monospaced)
    }
  }
  /// Commands and commit lines: always a real monospace.
  func code(_ size: CGFloat, _ w: CGFloat = 400) -> Font {
    layout == .mission ? mono(size, w) : (FontKit.make("JetBrainsMono-Regular", size, wght: w) ?? .system(size: size, design: .monospaced))
  }
  func lcdDigits(_ size: CGFloat) -> Font { FontKit.make("DotoBlack-Regular_Thin", size, wght: 900) ?? .system(size: size, weight: .black, design: .monospaced) }

  // MARK: palettes
  static func make(_ l: LayoutKind, dark: Bool) -> Theme {
    var t = Theme(); t.layout = l; t.dark = dark
    switch (l, dark) {
    case (.studio, false):
      t.bgTop = hex(0xF5F4F0); t.bg = hex(0xE6E4DD); t.nsBg = nsHex(0xEDEBE6); t.bg2 = hex(0xEDEBE6)
      t.surface = hex(0xFBFAF7); t.surfaceAlt = hex(0xF2F0EA); t.line = hex(0xCFCCC4); t.lineStrong = hex(0xB9B5AC); t.ink = hex(0x141414)
      t.text = hex(0x141414); t.dim = hex(0x3B3832); t.faint = hex(0x5E5A52)
      t.accent = hex(0xFF5A1F); t.accentText = hex(0xB93A0A); t.onAccent = .black
      t.ok = hex(0x0F7A46); t.warn = hex(0x8A5A00); t.bad = hex(0xB42318); t.info = hex(0x2F5FE0); t.violet = hex(0x7A3FB0)
      t.claude = hex(0xB05A3C); t.codex = hex(0x2F5FE0); t.track = hex(0xD9D6CE); t.progress = hex(0xFF5A1F)
      t.lcd = hex(0x0F0F10); t.lcdText = hex(0xEEEEEE); t.lcdDim = hex(0x9A9A9A); t.lcdLine = hex(0x2A2A2A)
      t.keyAlert = hex(0xFFF0ED); t.keyQueue = hex(0xFFFBEA)
      t.chips = [.ok: .solid(hex(0x0F7A46)), .warn: .solid(hex(0xE3A13A), .black), .bad: .solid(hex(0xB42318)), .hot: .solid(hex(0xFF5A1F), .black),
                 .claude: .solid(hex(0xB05A3C)), .codex: .solid(hex(0x2F5FE0)), .violet: .solid(hex(0x7A3FB0)), .teal: .solid(hex(0x0B6E66)),
                 .sky: .solid(hex(0x2F5FE0)), .neutral: .solid(hex(0x5E5A52)), .number: .solid(hex(0xFF5A1F), .black), .numberMuted: .solid(hex(0x141414))]
    case (.studio, true):
      t.bgTop = hex(0x2A2926); t.bg = hex(0x1C1B19); t.nsBg = nsHex(0x232220); t.bg2 = hex(0x232220)
      t.surface = hex(0x2D2B28); t.surfaceAlt = hex(0x262522); t.line = hex(0x3D3B36); t.lineStrong = hex(0x55524B); t.ink = hex(0x050505)
      t.text = hex(0xF3F1EC); t.dim = hex(0xD6D2CA); t.faint = hex(0xB3ADA3)
      t.accent = hex(0xFF5A1F); t.accentText = hex(0xFF7A45); t.onAccent = .black
      t.ok = hex(0x35E07F); t.warn = hex(0xF0B54A); t.bad = hex(0xFF6B5E); t.info = hex(0x7FA2FF); t.violet = hex(0xC39BFF)
      t.claude = hex(0xB05A3C); t.codex = hex(0x2F5FE0); t.track = hex(0x3A3833); t.progress = hex(0xFF5A1F)
      t.lcd = hex(0x0B0B0C); t.lcdText = hex(0xEEEEEE); t.lcdDim = hex(0x9A9A9A); t.lcdLine = hex(0x333333)
      t.keyAlert = hex(0x3A2420); t.keyQueue = hex(0x37301A)
      t.chips = [.ok: .solid(hex(0x0F7A46)), .warn: .solid(hex(0xE3A13A), .black), .bad: .solid(hex(0xB42318)), .hot: .solid(hex(0xFF5A1F), .black),
                 .claude: .solid(hex(0xB05A3C)), .codex: .solid(hex(0x2F5FE0)), .violet: .solid(hex(0x7A3FB0)), .teal: .solid(hex(0x0B6E66)),
                 .sky: .solid(hex(0x2F5FE0)), .neutral: .solid(hex(0x5E5A52)), .number: .solid(hex(0xFF5A1F), .black), .numberMuted: .solid(hex(0xF3F1EC), .black)]
    case (.mission, true):
      t.bg = hex(0x0A0C0F); t.bgTop = t.bg; t.nsBg = nsHex(0x0A0C0F); t.bg2 = hex(0x0F1318); t.surface = hex(0x0F1318); t.surfaceAlt = hex(0x141A21)
      t.line = hex(0x1F2731); t.lineStrong = hex(0x2C3744); t.ink = t.line
      t.text = hex(0xDBE4EE); t.dim = hex(0xA3B0BF); t.faint = hex(0x8A97A8)
      t.accent = hex(0x2EE6FF); t.accentText = hex(0x2EE6FF); t.onAccent = hex(0x001318)
      t.ok = hex(0x3DFF9A); t.warn = hex(0xFFC53D); t.bad = hex(0xFF5872); t.info = hex(0x2EE6FF); t.magenta = hex(0xFF7AD9); t.violet = hex(0xB9A2FF)
      t.claude = hex(0xFF9A4A); t.codex = hex(0xB9A2FF); t.track = hex(0x1A222B); t.progress = hex(0x2EE6FF)
      t.chips = [.ok: .outline(hex(0x3DFF9A)), .warn: .outline(hex(0xFFC53D)), .bad: .outline(hex(0xFF5872)), .hot: .outline(hex(0xFF9A4A)),
                 .claude: .outline(hex(0xFF9A4A)), .codex: .outline(hex(0xB9A2FF)), .violet: .outline(hex(0xB9A2FF)), .teal: .outline(hex(0x3DFF9A)),
                 .sky: .outline(hex(0x2EE6FF)), .neutral: .outline(hex(0xA3B0BF)), .number: .outline(hex(0x2EE6FF)), .numberMuted: .outline(hex(0xA3B0BF))]
    case (.mission, false):
      t.bg = hex(0xF3F6F9); t.bgTop = t.bg; t.nsBg = nsHex(0xF3F6F9); t.bg2 = hex(0xE8EDF2); t.surface = hex(0xFFFFFF); t.surfaceAlt = hex(0xDFE6ED)
      t.line = hex(0xD3DBE4); t.lineStrong = hex(0xB3BFCC); t.ink = t.line
      t.text = hex(0x0D1620); t.dim = hex(0x3E4C5C); t.faint = hex(0x56657A)
      t.accent = hex(0x006F80); t.accentText = hex(0x006F80); t.onAccent = .white
      t.ok = hex(0x0A7A43); t.warn = hex(0x8A5A00); t.bad = hex(0xC0213F); t.info = hex(0x006F80); t.magenta = hex(0xA3237F); t.violet = hex(0x5B45B0)
      t.claude = hex(0xA84A00); t.codex = hex(0x5B45B0); t.track = hex(0xDCE3EA); t.progress = hex(0x0094AA)
      t.chips = [.ok: .outline(hex(0x0A7A43)), .warn: .outline(hex(0x8A5A00)), .bad: .outline(hex(0xC0213F)), .hot: .outline(hex(0xA84A00)),
                 .claude: .outline(hex(0xA84A00)), .codex: .outline(hex(0x5B45B0)), .violet: .outline(hex(0x5B45B0)), .teal: .outline(hex(0x0A7A43)),
                 .sky: .outline(hex(0x006F80)), .neutral: .outline(hex(0x3E4C5C)), .number: .outline(hex(0x006F80)), .numberMuted: .outline(hex(0x3E4C5C))]
    case (.paper, false):
      t.bg = hex(0xF6F2EA); t.bgTop = t.bg; t.nsBg = nsHex(0xF6F2EA); t.bg2 = hex(0xECE9E3); t.surface = hex(0xFFFDF8); t.surfaceAlt = hex(0xFBF7EF)
      t.line = hex(0xE6DFD2); t.lineStrong = hex(0xD6CCBB); t.ink = t.line
      t.text = hex(0x2A2723); t.dim = hex(0x4A453E); t.faint = hex(0x655F56)
      t.accent = hex(0x58429A); t.accentText = hex(0x58429A); t.onAccent = .white
      t.ok = hex(0x2C6A3D); t.warn = hex(0x735600); t.bad = hex(0xA02D33); t.info = hex(0x2A5A88); t.violet = hex(0x58429A)
      t.claude = hex(0x934514); t.codex = hex(0x2A5A88); t.track = hex(0xECE9E3); t.progress = hex(0x2A5A88)
      t.keyQueue = hex(0xF6EDCB)
      t.chips = [.ok: .tint(0xE2EFE1, 0x2C6A3D), .warn: .tint(0xF6EDCB, 0x735600), .bad: .tint(0xF8DFDF, 0xA02D33), .hot: .tint(0xFBE5D4, 0x934514),
                 .claude: .tint(0xFBE5D4, 0x934514), .codex: .tint(0xE0EBF7, 0x2A5A88), .violet: .tint(0xEBE5F6, 0x58429A), .teal: .tint(0xDBEEEC, 0x1D625C),
                 .sky: .tint(0xE0EBF7, 0x2A5A88), .neutral: .tint(0xECE9E3, 0x6B665E), .number: .tint(0xEBE5F6, 0x58429A), .numberMuted: .tint(0xECE9E3, 0x6B665E)]
    case (.paper, true):
      t.bg = hex(0x1B1A17); t.bgTop = t.bg; t.nsBg = nsHex(0x1B1A17); t.bg2 = hex(0x2F2D29); t.surface = hex(0x25231F); t.surfaceAlt = hex(0x211F1C)
      t.line = hex(0x38352F); t.lineStrong = hex(0x4A463E); t.ink = t.line
      t.text = hex(0xEFE9DF); t.dim = hex(0xD2CBC0); t.faint = hex(0xB0A89C)
      t.accent = hex(0xCBBCF2); t.accentText = hex(0xCBBCF2); t.onAccent = hex(0x1B1A17)
      t.ok = hex(0xA3D9B1); t.warn = hex(0xEAD17C); t.bad = hex(0xF4A6AC); t.info = hex(0xA8CAEF); t.violet = hex(0xCBBCF2)
      t.claude = hex(0xF3BB8F); t.codex = hex(0xA8CAEF); t.track = hex(0x2F2D29); t.progress = hex(0xA8CAEF)
      t.keyQueue = hex(0x3A3219)
      t.chips = [.ok: .tint(0x22362A, 0xA3D9B1), .warn: .tint(0x3A3219, 0xEAD17C), .bad: .tint(0x40232A, 0xF4A6AC), .hot: .tint(0x3E2B1D, 0xF3BB8F),
                 .claude: .tint(0x3E2B1D, 0xF3BB8F), .codex: .tint(0x1E2F42, 0xA8CAEF), .violet: .tint(0x2E2842, 0xCBBCF2), .teal: .tint(0x1C3634, 0x97D8D1),
                 .sky: .tint(0x1E2F42, 0xA8CAEF), .neutral: .tint(0x2F2D29, 0xA39D93), .number: .tint(0x2E2842, 0xCBBCF2), .numberMuted: .tint(0x2F2D29, 0xA39D93)]
    }
    return t
  }
}

struct ThemeKey: EnvironmentKey { static let defaultValue = Theme.make(.studio, dark: false) }
extension EnvironmentValues { var theme: Theme { get { self[ThemeKey.self] } set { self[ThemeKey.self] = newValue } } }

/// Layout + mode (light / dark / auto = follow macOS). Saved in UserDefaults; config "theme" key ("studio-dark", "paper", "mission-auto"…) is the default.
final class ThemeStore: ObservableObject {
  static let layoutKey = "agentMonitor.layout", modeKey = "agentMonitor.mode"
  static let modes = ["light", "dark", "auto"]
  @Published private(set) var layout: LayoutKind
  @Published private(set) var mode: String
  @Published private(set) var theme: Theme
  var onChange: ((Theme) -> Void)?
  private var appearanceObs: NSKeyValueObservation?
  init() {
    let cfg = (Self.configTheme() ?? "").lowercased()
    let cfgLayout: LayoutKind? = cfg.contains("mission") ? .mission : cfg.contains("paper") ? .paper : (cfg.contains("studio") || cfg.contains("deck")) ? .studio : nil
    let cfgMode: String? = cfg.contains("dark") ? "dark" : cfg.contains("light") ? "light" : cfg.contains("auto") ? "auto" : nil
    let l = UserDefaults.standard.string(forKey: Self.layoutKey).flatMap(LayoutKind.init(rawValue:)) ?? cfgLayout ?? .studio
    let m = UserDefaults.standard.string(forKey: Self.modeKey).flatMap { Self.modes.contains($0) ? $0 : nil } ?? cfgMode ?? "auto"
    layout = l; mode = m; theme = Theme.make(l, dark: Self.isDark(m))
    appearanceObs = NSApplication.shared.observe(\.effectiveAppearance, options: [.new]) { [weak self] _, _ in
      DispatchQueue.main.async { self?.refresh() }
    }
  }
  static func systemDark() -> Bool { NSApplication.shared.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua }
  static func isDark(_ m: String) -> Bool { m == "dark" || (m == "auto" && systemDark()) }
  func set(layout l: LayoutKind) { layout = l; UserDefaults.standard.set(l.rawValue, forKey: Self.layoutKey); refresh() }
  func set(mode m: String) { mode = m; UserDefaults.standard.set(m, forKey: Self.modeKey); refresh() }
  /// ⌘T
  func cycleLayout() { let a = LayoutKind.allCases; set(layout: a[((a.firstIndex(of: layout) ?? 0) + 1) % a.count]) }
  /// ⌘⇧T: flip light/dark (leaves Auto for an explicit choice).
  func toggleMode() { set(mode: theme.dark ? "light" : "dark") }
  /// For snapshots: force a theme without saving.
  func preview(_ t: Theme) { theme = t }
  func refresh() {
    let t = Theme.make(layout, dark: Self.isDark(mode))
    if t.id != theme.id { theme = t; onChange?(t) }
  }
  static func configTheme() -> String? {
    let path = ProcessInfo.processInfo.environment["AGENT_MONITOR_CONFIG"] ?? (NSHomeDirectory() + "/.config/agent-monitor/config.json")
    guard let d = FileManager.default.contents(atPath: path),
          let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return nil }
    return j["theme"] as? String
  }
}

/// Recent 5-hour usage per account, kept in memory for the Mission Control sparklines.
final class UsageHistory: ObservableObject {
  @Published private(set) var points: [String: [Double]] = [:]
  private var last = Date.distantPast
  /// Snapshot tooling only.
  func debugSeed(_ name: String, _ v: Double) { points[name] = (0..<24).map { i in max(0, v - Double(24 - i) * v / 40 + Double((i * 7) % 5)) } }
  func record(_ s: Status?) {
    guard let s = s, Date().timeIntervalSince(last) >= 30 || points.isEmpty else { return }
    last = Date()
    for a in s.accounts where a.unknown != true { points[a.name, default: []].append(a.five); if points[a.name]!.count > 60 { points[a.name]!.removeFirst() } }
  }
}

// MARK: - Formatting and derived text

func dur(_ s: Int) -> String { s >= 3600 ? "\(s / 3600)h \(s % 3600 / 60)m" : s >= 60 ? "\(s / 60)m \(s % 60)s" : "\(s)s" }
func secs(_ s: Double) -> String { s < 0 ? "" : s < 60 ? String(format: "%.1fs", s) : dur(Int(s)) }
func tok(_ t: Int) -> String { t >= 1_000_000 ? String(format: "%.1fM", Double(t) / 1e6) : "\(t / 1000)k" }
func num(_ v: Double) -> String { v == v.rounded() ? NumberFormatter.localizedString(from: NSNumber(value: Int(v)), number: .decimal) : String(format: "%.1f", v) }
func words(_ n: Int) -> String {
  let w = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"]
  return n < w.count ? w[n] : "\(n)"
}
func todayLine(_ f: String) -> String { let d = DateFormatter(); d.dateFormat = f; return d.string(from: Date()) }
func progressOf(_ t: RunningTask) -> Double {
  guard !t.steps.isEmpty else { return 0 }
  let done = Double(t.steps.filter { $0.status == "done" }.count), active = Double(t.steps.filter { $0.status == "active" }.count)
  return (done + active * 0.5) / Double(t.steps.count)
}
func firstMetricLine(_ t: RunningTask) -> String? {
  guard let m = t.metrics?.first(where: { $0.total != nil }) ?? t.metrics?.first else { return nil }
  return "\(m.label): \(m.total.map { "\(num(m.done)) of \(num($0))" } ?? num(m.done))\(m.left.isEmpty ? "" : " · " + m.left)"
}
/// "W0 Claude-A (orchestrator)" → ("W0", "Claude-A (orchestrator)")
func splitWorker(_ name: String) -> (String, String) {
  let p = name.split(separator: " ", maxSplits: 1).map(String.init)
  return p.count == 2 && p[0].count <= 3 ? (p[0], p[1]) : (String(name.prefix(2)), name)
}

// MARK: - Shared building blocks

struct Wrap: View {
  @Environment(\.theme) var th
  let text: String; let font: Font; let size: CGFloat; var color: Color? = nil; var lines: Int = 2
  var body: some View {
    Text(text).font(font).foregroundColor(color ?? th.text).lineLimit(lines).lineSpacing(th.leading(size))
      .truncationMode(.tail).fixedSize(horizontal: false, vertical: true).help(text)
  }
}

struct LED: View {
  let color: Color; var size: CGFloat = 8; var glow = true
  var body: some View { Circle().fill(color).frame(width: size, height: size).shadow(color: glow ? color.opacity(0.85) : .clear, radius: glow ? 3 : 0) }
}

struct Bar: View {
  let value: Double?; let color: Color; let track: Color
  var height: CGFloat = 8; var radius: CGFloat? = nil; var segments = 0
  @State private var phase: CGFloat = -0.4
  var body: some View {
    GeometryReader { g in
      if let v = value, segments > 0 {
        let lit = v <= 0 ? 0 : Int((min(1, v) * Double(segments)).rounded(.up))
        HStack(spacing: 2) { ForEach(0..<segments, id: \.self) { i in Rectangle().fill(i < lit ? color : track) } }
      } else {
        let shape = RoundedRectangle(cornerRadius: radius ?? height / 2)
        ZStack(alignment: .leading) {
          shape.fill(track)
          if let v = value {
            shape.fill(color).frame(width: max(height, g.size.width * min(1, max(0, v)))).animation(.easeInOut(duration: 0.6), value: v)
          } else {
            shape.fill(LinearGradient(colors: [.clear, color, .clear], startPoint: .leading, endPoint: .trailing))
              .frame(width: g.size.width * 0.35).offset(x: g.size.width * phase)
              .onAppear { withAnimation(.linear(duration: 1.6).repeatForever(autoreverses: false)) { phase = 1.05 } }
          }
        }.clipShape(shape)
      }
    }.frame(height: height)
  }
}

/// Wraps children onto new lines (tags).
struct Flow: Layout {
  var spacing: CGFloat = 6, lineSpacing: CGFloat = 6   // wrap test allows 1 pt for pixel rounding of the bounds
  private func rows(_ maxW: CGFloat, _ subviews: Subviews) -> (CGSize, [CGPoint]) {
    var pts: [CGPoint] = [], x: CGFloat = 0, y: CGFloat = 0, rowH: CGFloat = 0, w: CGFloat = 0
    for s in subviews {
      let sz = s.sizeThatFits(.unspecified)
      if x > 0 && x + sz.width > maxW + 1 { x = 0; y += rowH + lineSpacing; rowH = 0 }
      pts.append(CGPoint(x: x, y: y)); x += sz.width + spacing; rowH = max(rowH, sz.height); w = max(w, x - spacing)
    }
    return (CGSize(width: min(w, maxW), height: y + rowH), pts)
  }
  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize { rows(proposal.width ?? .infinity, subviews).0 }
  func placeSubviews(in b: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
    let pts = rows(b.width, subviews).1
    for (s, p) in zip(subviews, pts) { s.place(at: CGPoint(x: b.minX + p.x, y: b.minY + p.y), proposal: .unspecified) }
  }
}

struct Pill: View {
  @Environment(\.theme) var th
  let text: String; let role: Role; var size: CGFloat = 11.5
  var body: some View {
    let c = th.chip(role)
    let r: CGFloat = th.layout == .mission ? 2 : 20
    let font: Font = th.layout == .paper ? th.text(size + 0.5, 600) : th.layout == .mission ? th.mono(size, 600) : th.mono(size, 800)
    Text(th.layout == .studio ? text.uppercased() : text).font(font).lineLimit(1)
      .tracking(th.layout == .studio ? 0.6 : 0)
      .padding(.horizontal, th.layout == .mission ? 6 : 8).padding(.vertical, th.layout == .mission ? 2 : 3)
      .foregroundColor(c.ink).background(RoundedRectangle(cornerRadius: r).fill(c.fill))
      .overlay(RoundedRectangle(cornerRadius: r).stroke(c.stroke, lineWidth: 1)).fixedSize()
  }
}

struct NumTag: View {
  @Environment(\.theme) var th
  let text: String; var muted = false
  var body: some View {
    let c = th.chip(muted ? .numberMuted : .number)
    let r: CGFloat = th.layout == .mission ? 2 : th.layout == .paper ? 8 : 5
    Text(text).font(th.mono(th.layout == .paper ? 12.5 : 12, 800)).lineLimit(1)
      .padding(.horizontal, 6).padding(.vertical, 2)
      .foregroundColor(c.ink).background(RoundedRectangle(cornerRadius: r).fill(c.fill))
      .overlay(RoundedRectangle(cornerRadius: r).stroke(c.stroke, lineWidth: 1)).fixedSize()
  }
}

/// Expanded task details, shared by all layouts (styled by the theme).
struct TaskDetails: View {
  @Environment(\.theme) var th
  let t: RunningTask
  @Binding var raw: Bool
  var liveColor: Color { t.lastActive < 60 ? th.ok : t.lastActive < 300 ? th.warn : th.bad }
  func sub(_ s: String) -> some View {
    Group {
      if th.layout == .paper { Text(s.prefix(1) + s.dropFirst().lowercased()).font(th.title(15, 600)).foregroundColor(th.dim) }
      else { Text(s).font(th.mono(11.5, 800)).tracking(1.4).foregroundColor(th.faint) }
    }
  }
  var body: some View {
    VStack(alignment: .leading, spacing: S.m) {
      Rectangle().fill(th.line).frame(height: 1)
      HStack(spacing: S.m) {
        Label("running \(dur(t.elapsed))", systemImage: "clock")
        LED(color: liveColor, glow: th.layout == .mission)
        Text(t.lastActive < 60 ? "active now" : "last activity \(dur(t.lastActive)) ago")
        if t.failovers > 0 { Pill(text: "switched account ×\(t.failovers)", role: .violet) }
      }.font(th.mono(12.5)).foregroundColor(th.dim)
      if let ms = t.metrics, !ms.isEmpty {
        VStack(alignment: .leading, spacing: S.m) {
          sub("PROGRESS NUMBERS")
          ForEach(ms, id: \.self) { m in
            let bad = m.kind == "bad" && m.done > 0
            VStack(alignment: .leading, spacing: S.xs + 2) {
              HStack(alignment: .firstTextBaseline, spacing: S.s) {
                Wrap(text: m.label, font: th.text(13.5), size: 13.5, color: bad ? th.bad : th.text)
                Spacer(minLength: S.s)
                Text(m.total.map { "\(num(m.done)) / \(num($0))" } ?? num(m.done)).font(th.mono(13, 700)).foregroundColor(bad ? th.bad : th.text).fixedSize()
              }
              if let total = m.total {
                Bar(value: m.done / total, color: m.kind == "bad" ? th.bad : th.ok, track: th.track, height: th.layout == .mission ? 4 : 6,
                    radius: th.layout == .mission ? 0 : nil, segments: th.layout == .mission ? 24 : 0)
              }
              if !m.rate.isEmpty || !m.left.isEmpty {
                Text([m.rate, m.left].filter { !$0.isEmpty }.joined(separator: " · ")).font(th.mono(11.5)).foregroundColor(th.faint)
              }
            }
          }
        }
      }
      VStack(alignment: .leading, spacing: S.s) {
        sub("SUBTASKS")
        ForEach(t.steps, id: \.self) { s in
          HStack(alignment: .firstTextBaseline, spacing: S.s) {
            Image(systemName: s.status == "done" ? "checkmark.circle.fill" : s.status == "active" ? "circle.dotted.circle" : "circle")
              .foregroundColor(s.status == "done" ? th.ok : s.status == "active" ? th.accentText : th.faint)
            Wrap(text: s.title, font: th.text(13.5, s.status == "active" ? 600 : 400), size: 13.5, color: s.status == "pending" ? th.faint : th.text)
          }
        }
      }
      if !t.recent.isEmpty || t.commands > 0 {
        VStack(alignment: .leading, spacing: S.s) {
          Button(action: { withAnimation(.easeInOut(duration: 0.2)) { raw.toggle() } }) {
            HStack(spacing: S.xs + 2) {
              Image(systemName: raw ? "chevron.down" : "chevron.right")
              Text("RAW ACTIVITY · \(t.commands) commands · \(t.edits) edits · \(tok(t.tokens)) tokens").lineLimit(1)
            }.font(th.mono(11.5, 700)).foregroundColor(th.faint)
          }.buttonStyle(.plain)
          if raw {
            ForEach(t.recent, id: \.self) { c in
              HStack(alignment: .firstTextBaseline, spacing: S.s) {
                Group {
                  if c.state == "running" { ProgressView().controlSize(.small).frame(width: 16, height: 16) }
                  else { Image(systemName: c.state == "ok" ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundColor(c.state == "ok" ? th.ok : th.bad) }
                }.font(th.mono(12))
                Wrap(text: c.cmd, font: th.code(12), size: 12, color: c.state == "running" ? th.text : th.dim)
                Spacer(minLength: S.xs)
                Text(secs(c.secs)).font(th.mono(11.5)).foregroundColor(c.state == "running" ? th.accentText : th.faint)
              }
            }
          }
        }
      }
      if !t.note.isEmpty {
        VStack(alignment: .leading, spacing: S.xs + 2) {
          sub("LATEST NOTE")
          Wrap(text: t.note, font: th.text(13.5), size: 13.5, color: th.dim, lines: 4)
        }
      }
    }
  }
}

// MARK: - Theme picker (styled per layout). Tooltips name the full theme, e.g. "Studio · Light".

struct ThemePicker: View {
  @Environment(\.theme) var th
  @ObservedObject var ts: ThemeStore
  func modeLabel(_ m: String) -> String { m == "auto" ? "Auto" : m == "dark" ? "Dark" : "Light" }
  func tip(_ l: LayoutKind) -> String { "\(l.label) · \(ts.theme.dark ? "Dark" : "Light")  (⌘T cycles layouts)" }
  func tipMode(_ m: String) -> String { m == "auto" ? "Auto: follow macOS light/dark  (⌘⇧T flips light/dark)" : "\(ts.layout.label) · \(modeLabel(m))  (⌘⇧T flips light/dark)" }
  var body: some View {
    switch th.layout {
    case .studio: studio
    case .mission: mission
    case .paper: paper
    }
  }
  // Chunky keys: 2px ink border, 3px drop.
  func key(_ label: String, symbol: String? = nil, on: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Group { if let sym = symbol { Image(systemName: sym).font(.system(size: 13, weight: .bold)) } else { Text(label).font(th.mono(11.5, 800)).tracking(1.2) } }
        .lineLimit(1).minimumScaleFactor(0.85)
        .frame(maxWidth: .infinity).padding(.vertical, 7)
        .foregroundColor(on ? .black : th.text)
        .background(RoundedRectangle(cornerRadius: 10).fill(on ? th.accent : th.surface))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(th.ink, lineWidth: 2))
        .background(RoundedRectangle(cornerRadius: 10).fill(th.ink).offset(y: 3))
        .contentShape(Rectangle())
    }.buttonStyle(.plain)
  }
  var studio: some View {
    HStack(spacing: S.s) {
      ForEach(LayoutKind.allCases, id: \.self) { l in key(l == .mission ? "MISSION" : l.label.uppercased(), on: ts.layout == l) { ts.set(layout: l) }.help(tip(l)) }
      Rectangle().fill(th.line).frame(width: 1, height: 26)
      ForEach(ThemeStore.modes, id: \.self) { m in
        key(m, symbol: m == "auto" ? "circle.lefthalf.filled" : m == "dark" ? "moon.fill" : "sun.max.fill", on: ts.mode == m) { ts.set(mode: m) }.frame(width: 40).help(tipMode(m))
      }
    }.padding(.bottom, 3)
  }
  // Scope-cell grid: thin rules, active cell gets an accent underline.
  var mission: some View {
    HStack(spacing: 0) {
      ForEach(LayoutKind.allCases, id: \.self) { l in
        let on = ts.layout == l
        Button(action: { ts.set(layout: l) }) {
          VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 5) {
              LED(color: on ? th.accent : th.faint, size: 6, glow: on)
              Text(l == .mission ? "MISSION" : l.label.uppercased()).font(th.title(13.5, 600)).tracking(1.4).lineLimit(1)
            }.foregroundColor(on ? th.accent : th.text)
            Text(l == .studio ? "LCD · keys" : l == .mission ? "dense · rules" : "serif · calm").font(th.mono(11.5)).foregroundColor(th.dim).lineLimit(1)
          }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 9).padding(.vertical, 7)
            .background(on ? th.surfaceAlt : Color.clear)
            .overlay(alignment: .bottom) { if on { Rectangle().fill(th.accent).frame(height: 2).shadow(color: th.accent.opacity(th.dark ? 0.8 : 0), radius: 4) } }
            .contentShape(Rectangle())
        }.buttonStyle(.plain).help(tip(l))
        Rectangle().fill(th.line).frame(width: 1)
      }
      ForEach(ThemeStore.modes, id: \.self) { m in
        let on = ts.mode == m
        Button(action: { ts.set(mode: m) }) {
          Text(m.uppercased()).font(th.mono(11.5, 600)).tracking(1).foregroundColor(on ? th.accent : th.dim)
            .frame(width: 50).frame(maxHeight: .infinity)
            .background(on ? th.surfaceAlt : Color.clear)
            .overlay(alignment: .bottom) { if on { Rectangle().fill(th.accent).frame(height: 2) } }
            .contentShape(Rectangle())
        }.buttonStyle(.plain).help(tipMode(m))
        if m != "auto" { Rectangle().fill(th.line).frame(width: 1) }
      }
    }.fixedSize(horizontal: false, vertical: true)
  }
  // Soft segmented control on a grey track.
  func seg(_ label: String, on: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Text(label).font(th.text(13.5, on ? 600 : 400)).lineLimit(1)
        .frame(maxWidth: .infinity).padding(.vertical, 6)
        .foregroundColor(on ? th.text : th.faint)
        .background(RoundedRectangle(cornerRadius: 10).fill(on ? th.surface : Color.clear).shadow(color: .black.opacity(on ? (th.dark ? 0.35 : 0.08) : 0), radius: 3, y: 1))
        .contentShape(Rectangle())
    }.buttonStyle(.plain)
  }
  var paper: some View {
    HStack(spacing: S.s) {
      HStack(spacing: 4) { ForEach(LayoutKind.allCases, id: \.self) { l in seg(l.label, on: ts.layout == l) { ts.set(layout: l) }.help(tip(l)) } }
        .padding(4).background(RoundedRectangle(cornerRadius: 14).fill(th.bg2))
      HStack(spacing: 4) { ForEach(ThemeStore.modes, id: \.self) { m in seg(modeLabel(m), on: ts.mode == m) { ts.set(mode: m) }.help(tipMode(m)) } }
        .frame(width: 168).padding(4).background(RoundedRectangle(cornerRadius: 14).fill(th.bg2))
    }
  }
}

struct CollapseButton: View {
  @Environment(\.theme) var th
  @ObservedObject var store: Store
  var body: some View {
    Button(action: { store.collapsed.toggle() }) {
      Image(systemName: store.collapsed ? "chevron.down.circle.fill" : "chevron.up.circle.fill").font(.system(size: 19)).foregroundColor(th.layout == .studio ? th.accent : th.accentText)
    }.buttonStyle(.plain).help(store.collapsed ? "Expand" : "Collapse")
  }
}

// MARK: - STUDIO layout (PhoneDeck: warm-grey body, LCD blocks, chunky keys)

struct LCD<Content: View>: View {
  @Environment(\.theme) var th
  let content: Content
  init(@ViewBuilder content: () -> Content) { self.content = content() }
  var body: some View {
    content.padding(.horizontal, S.m + 2).padding(.vertical, S.m).frame(maxWidth: .infinity, alignment: .leading)
      .background(RoundedRectangle(cornerRadius: 14).fill(th.lcd))
      .overlay(Canvas { ctx, size in   // scanlines
        var y: CGFloat = 0
        while y < size.height { ctx.fill(Path(CGRect(x: 0, y: y, width: size.width, height: 1)), with: .color(.white.opacity(0.03))); y += 3 }
      }.allowsHitTesting(false).clipShape(RoundedRectangle(cornerRadius: 14)))
      .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(th.lcdLine, lineWidth: 3))
      .foregroundColor(th.lcdText)
  }
}

/// Key surface: ink border + solid drop shadow.
struct KeyStyle: ViewModifier {
  let th: Theme; var fill: Color? = nil; var highlight = false; var dashed = false
  func body(content: Content) -> some View {
    let shape = RoundedRectangle(cornerRadius: 14)
    content.padding(.horizontal, S.m + 2).padding(.vertical, S.m).frame(maxWidth: .infinity, alignment: .leading)
      .background(shape.fill(fill ?? th.surface))
      .overlay(shape.stroke(highlight ? th.accent : th.ink, style: StrokeStyle(lineWidth: 2, dash: dashed ? [5, 3] : [])))
      .background(dashed ? nil : shape.fill(th.ink).offset(y: 4))
      .padding(.bottom, dashed ? 0 : 4)
  }
}

struct StudioLabel: View {
  @Environment(\.theme) var th
  let title: String; var count: String? = nil
  var body: some View {
    HStack(spacing: S.s) {
      Text(title).font(th.mono(11.5, 800)).tracking(2.2).foregroundColor(th.faint).lineLimit(1)
      Rectangle().fill(th.line).frame(height: 1)
      if let c = count { Text(c.uppercased()).font(th.mono(11.5, 800)).tracking(1.6).foregroundColor(th.accentText) }
    }
  }
}

struct StudioTask: View {
  @Environment(\.theme) var th
  let t: RunningTask
  @State private var expanded = false
  @State private var raw = false
  var body: some View {
    VStack(alignment: .leading, spacing: S.s + 2) {
      HStack(alignment: .top, spacing: S.s) {
        NumTag(text: t.num).padding(.top, 2)
        Wrap(text: t.title, font: th.title(16, 700), size: 16)
        Spacer(minLength: S.xs)
        Pill(text: t.account, role: th.workerRole(t.account))
      }
      if let sm = t.summary, !sm.isEmpty { Wrap(text: sm, font: th.text(13.5, 400), size: 13.5, color: th.dim) }
      if let tg = t.tags, !tg.isEmpty { Flow { ForEach(tg, id: \.self) { Pill(text: $0, role: th.tagRole($0)) } } }
      HStack(alignment: .firstTextBaseline) {
        Text(t.estimated ? "PROGRESS (ESTIMATED)" : "PROGRESS").font(th.mono(11.5, 800)).tracking(1.4).foregroundColor(th.faint)
        Spacer()
        Text("\(Int(progressOf(t) * 100))%").font(th.mono(14, 800))
      }
      Bar(value: progressOf(t), color: th.progress, track: th.track, height: 8)
      if !expanded, let a = t.steps.first(where: { $0.status == "active" }) {
        Text("● \(a.title)").font(th.mono(12.5, 700)).foregroundColor(th.accentText).lineLimit(1).help(a.title)
      }
      if !expanded, let m = firstMetricLine(t) { Text(m).font(th.mono(12.5, 700)).foregroundColor(th.text).lineLimit(1).help(m) }
      if expanded { TaskDetails(t: t, raw: $raw) }
    }
    .modifier(KeyStyle(th: th, highlight: expanded))
    .contentShape(Rectangle()).onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct StudioPanel: View {
  @Environment(\.theme) var th
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  var body: some View {
    let s = store.status
    VStack(alignment: .leading, spacing: S.l) {
      LCD {
        VStack(alignment: .leading, spacing: S.xs + 2) {
          HStack(spacing: S.s) {
            LED(color: hex(0x35E07F), size: 7)
            Text((s?.title ?? "Agent monitor").uppercased()).font(th.mono(11.5, 700)).tracking(1.8).foregroundColor(th.lcdDim).lineLimit(1)
              .help(s?.title ?? "Agent monitor")
            Spacer(minLength: S.s)
            Text(todayLine("EEE d MMM").uppercased()).font(th.mono(11.5, 700)).tracking(1.8).foregroundColor(th.lcdDim)
            CollapseButton(store: store)
          }
          if !store.collapsed {
            Text(s?.time ?? "--:--:--").font(th.lcdDigits(40)).foregroundColor(hex(0xF4F4F4)).shadow(color: .white.opacity(0.28), radius: 7)
          }
          if let s = s {
            let blocked = s.queued?.filter { $0.blocked }.count ?? 0
            HStack(spacing: S.m) {
              let c = store.collapsed
              Text(c ? "▶\(s.running.count)" : "▶ \(s.running.count) RUNNING").foregroundColor(.white)
              Text(c ? "⧗\(s.queued?.count ?? 0)" : "\(s.queued?.count ?? 0) WAITING")
              if blocked > 0 { Text(c ? "✋\(blocked)" : "\(blocked) NEED YOU").foregroundColor(hex(0xFF7A45)) }
              if !s.agents.isEmpty { Text(c ? "◆\(s.agents.count)" : "\(s.agents.count) AGENTS") }
              if store.collapsed {
                ForEach(s.accounts) { a in Text("\(splitWorker(a.name).0) \(a.five >= 100 ? "LIMIT" : "\(Int(a.five))%")").foregroundColor(a.five >= 100 ? hex(0xFF6B5E) : hex(0xBDBDBD)) }
              }
            }.font(th.mono(11.5, 700)).tracking(1).foregroundColor(hex(0xBDBDBD)).lineLimit(1)
          }
        }
      }
      if !store.collapsed {
        ThemePicker(ts: ts)
        if let s = s { sections(s) } else { Text("LOADING…").font(th.mono(12, 800)).foregroundColor(th.faint) }
      }
    }.padding(S.m + 2).foregroundColor(th.text)
  }
  @ViewBuilder func sections(_ s: Status) -> some View {
    VStack(alignment: .leading, spacing: S.s + 2) {
      StudioLabel(title: "ACCOUNTS", count: "\(s.accounts.count)")
      ForEach(s.accounts) { a in
        let stale = (a.age ?? 0) >= 600
        LCD {
          VStack(alignment: .leading, spacing: S.xs + 2) {
            HStack(alignment: .firstTextBaseline, spacing: S.s) {
              Text(a.name).font(th.mono(13, 700)).foregroundColor(.white).lineLimit(1).help(a.name)
              Pill(text: a.tool, role: a.tool == "Claude" ? .claude : .codex, size: 11)
              Spacer(minLength: S.xs)
              if a.unknown == true { Text("not known yet").font(th.mono(12.5)).foregroundColor(th.lcdDim) }
              else if a.five >= 100 { Text("LIMIT → \(dur(a.resetIn))").font(th.mono(12.5, 800)).foregroundColor(hex(0xFF6B5E)) }
              else { Text("5h \(Int(a.five))% · 7d \(Int(a.seven))%").font(th.mono(12.5, 600)).foregroundColor(stale ? th.lcdDim : hex(0xDDDDDD)) }
            }
            Bar(value: a.unknown == true ? 0 : a.five / 100, color: stale ? hex(0x777777) : a.five >= 90 ? hex(0xFF6B5E) : a.five >= 60 ? hex(0xE3A13A) : hex(0x35E07F),
                track: hex(0x2A2A2A), height: 6, radius: 3)
            HStack(spacing: S.s) {
              if a.resetIn > 0 && a.five < 100 && a.unknown != true { Text("↻ 5-hour window resets in \(dur(a.resetIn))") }
              if let g = a.age, g >= 600 { Text("· reading from \(dur(g)) ago").foregroundColor(hex(0xF0B54A)) }
            }.font(th.mono(11.5)).foregroundColor(th.lcdDim)
          }
        }
      }
    }
    VStack(alignment: .leading, spacing: S.s + 2) {
      StudioLabel(title: "IN PROGRESS", count: "\(s.running.count)")
      if s.running.isEmpty { Text("Nothing running").font(th.mono(12.5)).foregroundColor(th.faint) }
      ForEach(s.running) { StudioTask(t: $0) }
    }
    if let q = s.queued, !q.isEmpty {
      VStack(alignment: .leading, spacing: S.s + 2) {
        StudioLabel(title: "UP NEXT", count: "\(q.count) waiting")
        ForEach(q) { q in
          VStack(alignment: .leading, spacing: S.xs + 2) {
            HStack(alignment: .top, spacing: S.s) {
              NumTag(text: q.num, muted: true).padding(.top, 2)
              Wrap(text: q.title, font: th.title(15, 700), size: 15)
              Spacer(minLength: S.xs)
              if !q.worker.isEmpty { Pill(text: q.worker, role: th.workerRole(q.worker)) }
            }
            if !q.summary.isEmpty { Wrap(text: q.summary, font: th.text(13.5), size: 13.5, color: th.dim) }
            HStack(alignment: .firstTextBaseline, spacing: S.s) {
              Text(q.blocked ? "✋" : "⧗").font(th.mono(12.5, 800))
              Wrap(text: q.waitingFor, font: th.mono(12.5, q.blocked ? 700 : 400), size: 12.5, color: q.blocked ? th.accentText : th.faint)
            }
          }.modifier(KeyStyle(th: th, fill: q.blocked ? th.keyAlert : th.keyQueue))
        }
      }
    }
    if !s.agents.isEmpty {
      VStack(alignment: .leading, spacing: S.s + 2) {
        StudioLabel(title: "CLAUDE AGENTS", count: "\(s.agents.count) running")
        ForEach(s.agents) { StudioAgent(a: $0) }
      }
    }
    VStack(alignment: .leading, spacing: S.s + 2) {
      StudioLabel(title: "RECENTLY FINISHED", count: s.finished.isEmpty ? nil : "\(s.finished.count)")
      LCD {
        VStack(alignment: .leading, spacing: S.s) {
          if s.finished.isEmpty { Text("nothing yet").font(th.mono(12.5)).foregroundColor(th.lcdDim) }
          ForEach(s.finished) { d in
            HStack(alignment: .firstTextBaseline, spacing: S.s) {
              Text(d.ok ? "✓" : "✕").font(th.mono(13, 800)).foregroundColor(d.ok ? hex(0x35E07F) : hex(0xFF6B5E)).frame(width: 14)
              Text(d.tid).font(th.mono(12.5, 800)).foregroundColor(hex(0xFF7A45)).frame(minWidth: 30, alignment: .leading)
              Text(d.title).font(th.mono(12.5)).foregroundColor(hex(0xDDDDDD)).lineLimit(1).help(d.title)
              Spacer(minLength: S.s)
              Text(d.time).font(th.mono(12.5)).foregroundColor(th.lcdDim).fixedSize()
            }
          }
        }
      }
    }
    VStack(alignment: .leading, spacing: S.s + 2) {
      StudioLabel(title: "REPO & HEALTH")
      LCD {
        VStack(alignment: .leading, spacing: S.s) {
          Wrap(text: s.commit, font: th.code(12.5), size: 12.5, color: hex(0xDDDDDD))
          if s.changes.isEmpty { Text("working tree clean").font(th.mono(12)).foregroundColor(th.lcdDim) }
          ForEach(s.changes) { c in
            HStack { Text(c.area).font(th.code(12.5)).lineLimit(1).help(c.area); Spacer(); Text("\(c.files) files changed").font(th.mono(12.5, 700)).foregroundColor(hex(0xF0B54A)) }
          }
          if let hs = s.health, !hs.isEmpty {
            Rectangle().fill(th.lcdLine).frame(height: 1)
            HealthRows(hs: hs, labelColor: hex(0xDDDDDD))
          }
        }
      }
    }
  }
}

struct StudioAgent: View {
  @Environment(\.theme) var th
  let a: Agent
  @State private var expanded = false
  var body: some View {
    VStack(alignment: .leading, spacing: S.s) {
      HStack(spacing: S.s) {
        Pill(text: "Rahul · Claude", role: .claude)
        Text("\(a.tools) ACTIONS").font(th.mono(11.5, 800)).tracking(1).foregroundColor(th.faint)
        Spacer()
        LED(color: a.lastActive < 60 ? th.ok : th.warn, glow: false)
      }
      Wrap(text: a.title, font: th.title(15, 700), size: 15, lines: expanded ? 3 : 2)
      Bar(value: nil, color: th.claude, track: th.track, height: 6)
      if !expanded, let r = a.recent.first { Text("▸ \(r)").font(th.mono(12.5)).foregroundColor(th.dim).lineLimit(1).help(r) }
      if expanded {
        ForEach(Array(a.recent.enumerated()), id: \.offset) { i, r in
          Wrap(text: (i == 0 ? "▶ " : "✓ ") + r, font: th.code(12.5), size: 12.5, color: i == 0 ? th.text : th.dim)
        }
      }
    }
    .modifier(KeyStyle(th: th, highlight: expanded))
    .contentShape(Rectangle()).onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct HealthRows: View {
  @Environment(\.theme) var th
  let hs: [Health]; var labelColor: Color? = nil
  var body: some View {
    VStack(alignment: .leading, spacing: S.s) {
      ForEach(Array(Dictionary(grouping: hs, by: { $0.group ?? "" }).keys.sorted()), id: \.self) { g in
        HStack(spacing: S.l + 2) {
          ForEach(hs.filter { ($0.group ?? "") == g }) { h in
            HStack(spacing: S.xs + 2) { LED(color: h.up ? th.ok : th.bad, size: 8, glow: th.layout != .paper); Text(h.label).lineLimit(1) }
          }
        }.font(th.mono(12.5)).foregroundColor(labelColor ?? th.dim)
      }
    }
  }
}

// MARK: - MISSION CONTROL layout (dense rows, thin rules, LEDs, ticker, KPI grid, sparklines, key hints)

struct MCHead: View {
  @Environment(\.theme) var th
  let title: String; var count: String? = nil; var trailing: String? = nil
  var body: some View {
    HStack(spacing: S.s) {
      Text(title).font(th.mono(11.5, 600)).tracking(2.4).foregroundColor(th.dim).lineLimit(1)
      if let c = count { Text(c.uppercased()).font(th.mono(11.5, 600)).tracking(1).foregroundColor(th.accent) }
      Rectangle().fill(th.line).frame(height: 1)
      if let t = trailing { Text(t).font(th.mono(11.5)).foregroundColor(th.dim) }
    }.padding(.horizontal, S.m).padding(.top, S.l).padding(.bottom, S.s)
  }
}

struct Rule: View { @Environment(\.theme) var th; var body: some View { Rectangle().fill(th.line).frame(height: 1) } }

struct Ticker: View {
  @Environment(\.theme) var th
  let text: String
  @State private var w: CGFloat = 0
  @State private var x: CGFloat = 0
  var body: some View {
    HStack(spacing: 0) {
      Text("LIVE").font(th.mono(11, 700)).tracking(2).foregroundColor(th.onAccent).padding(.horizontal, S.s).frame(maxHeight: .infinity).background(th.accent)
      GeometryReader { _ in
        HStack(spacing: 40) { line; line }.fixedSize().offset(x: x)
      }.clipped()
    }
    .frame(height: 26).background(th.bg2)
    .onAppear { start() }
    .onChange(of: text) { _ in x = 0; start() }
  }
  var line: some View {
    Text(text).font(th.mono(12)).foregroundColor(th.dim).fixedSize().padding(.leading, S.s)
      .background(GeometryReader { g in Color.clear.onAppear { w = g.size.width + 40 } })
  }
  func start() {
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
      guard w > 0 else { return }
      withAnimation(.linear(duration: Double(w) / 28).repeatForever(autoreverses: false)) { x = -w }
    }
  }
}

struct Sparkline: View {
  let points: [Double]; let color: Color
  var body: some View {
    GeometryReader { g in
      let pts = points.count >= 2 ? points : [points.first ?? 0, points.first ?? 0]
      let step = g.size.width / CGFloat(max(1, pts.count - 1))
      let y: (Double) -> CGFloat = { g.size.height * (1 - CGFloat(min(100, max(0, $0)) / 100)) }
      let line = Path { p in
        for (i, v) in pts.enumerated() { let pt = CGPoint(x: CGFloat(i) * step, y: y(v)); i == 0 ? p.move(to: pt) : p.addLine(to: pt) }
      }
      ZStack {
        Path { p in
          p.move(to: CGPoint(x: 0, y: g.size.height))
          for (i, v) in pts.enumerated() { p.addLine(to: CGPoint(x: CGFloat(i) * step, y: y(v))) }
          p.addLine(to: CGPoint(x: g.size.width, y: g.size.height)); p.closeSubpath()
        }.fill(color.opacity(0.14))
        line.stroke(color, lineWidth: 1.5)
        Circle().fill(color).frame(width: 4, height: 4).position(x: g.size.width, y: y(pts.last ?? 0))
      }
    }
  }
}

struct MCKpi: View {
  @Environment(\.theme) var th
  let value: String; var unit: String? = nil; let label: String; let color: Color
  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack(alignment: .firstTextBaseline, spacing: 1) {
        Text(value).font(th.mono(21, 600)).foregroundColor(color)
        if let u = unit { Text(u).font(th.mono(12.5)).foregroundColor(th.dim) }
      }
      HStack(spacing: 5) { LED(color: color, size: 6, glow: th.dark); Text(label).font(th.mono(11.5)).tracking(1.2).foregroundColor(th.dim).lineLimit(1).minimumScaleFactor(0.85) }
    }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 10).padding(.vertical, 9)
  }
}

struct MCTaskRow: View {
  @Environment(\.theme) var th
  let t: RunningTask
  @State private var expanded = false
  @State private var raw = false
  var live: Color { t.lastActive < 60 ? th.ok : t.lastActive < 300 ? th.warn : th.bad }
  var body: some View {
    VStack(alignment: .leading, spacing: S.s) {
      HStack(alignment: .center, spacing: 10) {
        LED(color: live, size: 7, glow: th.dark)
        VStack(alignment: .leading, spacing: 3) {
          HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(t.num).font(th.mono(13, 600)).foregroundColor(th.accent)
            Text(t.title).font(th.title(16, 600)).foregroundColor(th.text).lineLimit(1).help(t.title)
          }
          Text(t.steps.first(where: { $0.status == "active" }).map { "▸ " + $0.title } ?? (t.summary ?? ""))
            .font(th.mono(12)).foregroundColor(th.dim).lineLimit(1).help(t.summary ?? "")
        }
        Spacer(minLength: S.xs)
        VStack(alignment: .trailing, spacing: 3) {
          Text("\(Int(progressOf(t) * 100))%").font(th.mono(14, 600)).foregroundColor(th.text)
          Text(dur(t.elapsed)).font(th.mono(11.5)).foregroundColor(th.dim)
        }.fixedSize()
      }
      Bar(value: progressOf(t), color: th.progress, track: th.track, height: 5, radius: 0, segments: 32).padding(.leading, 17)
      Flow(spacing: 6, lineSpacing: 6) {
        Pill(text: t.account, role: th.workerRole(t.account))
        ForEach(t.tags ?? [], id: \.self) { Pill(text: $0, role: th.tagRole($0)) }
      }.padding(.leading, 17)
      if !expanded, let m = firstMetricLine(t) { Text(m).font(th.mono(12, 600)).foregroundColor(th.text).lineLimit(1).padding(.leading, 17).help(m) }
      if expanded {
        if let sm = t.summary, !sm.isEmpty { Wrap(text: sm, font: th.title(15, 500), size: 15, color: th.text, lines: 3).padding(.leading, 17) }
        TaskDetails(t: t, raw: $raw).padding(.leading, 17)
      }
    }
    .padding(.horizontal, S.m).padding(.vertical, 10)
    .background(expanded ? th.bg2 : Color.clear)
    .overlay(alignment: .leading) { if expanded { Rectangle().fill(th.accent).frame(width: 2) } }
    .contentShape(Rectangle()).onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct MissionPanel: View {
  @Environment(\.theme) var th
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  @ObservedObject var history: UsageHistory
  var tickerText: String {
    guard let s = store.status else { return "waiting for status…" }
    var parts = s.finished.map { "\($0.time)  \($0.ok ? "DONE" : "FAIL") \($0.tid) \($0.title)" }
    parts += s.running.map { "RUN \($0.num) \($0.title)\($0.steps.first(where: { $0.status == "active" }).map { " · " + $0.title } ?? "")" }
    return parts.joined(separator: "   ·   ")
  }
  var brand: some View {
    let title = (store.status?.title ?? "Agent monitor").uppercased()
    let parts = title.components(separatedBy: " · ")
    return HStack(spacing: 0) {
      Text(parts[0]).foregroundColor(th.text)
      if parts.count > 1 { Text("/").foregroundColor(th.accent); Text(parts.dropFirst().joined(separator: " · ")).foregroundColor(th.accent) }
    }.font(th.title(17, 600)).tracking(2.2).lineLimit(1).help(store.status?.title ?? "")
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack(spacing: S.s) {
        LED(color: th.ok, size: 8, glow: th.dark)
        brand
        HStack(spacing: 4) { LED(color: th.ok, size: 5, glow: th.dark); Text("LIVE").font(th.mono(11, 600)).tracking(1.6) }.foregroundColor(th.ok)
        Spacer(minLength: S.xs)
        Text(store.status?.time ?? "--:--:--").font(th.mono(17, 600)).foregroundColor(th.text).fixedSize()
        CollapseButton(store: store)
      }.padding(.horizontal, S.m).padding(.vertical, 10)
      Rule()
      if store.collapsed, let s = store.status {
        HStack(spacing: S.m) {
          Text("▶\(s.running.count) run").foregroundColor(th.text)
          Text("⧗\(s.queued?.count ?? 0) wait").foregroundColor(th.accent)
          ForEach(s.accounts) { a in Text("\(splitWorker(a.name).0) \(a.five >= 100 ? "LIMIT" : "\(Int(a.five))%")").foregroundColor(th.usage(a.five)) }
        }.font(th.mono(12.5, 600)).lineLimit(1).padding(.horizontal, S.m).padding(.vertical, 10)
      } else {
        Ticker(text: tickerText)
        Rule()
        ThemePicker(ts: ts)
        Rule()
        if let s = store.status { content(s) } else { Text("LOADING…").font(th.mono(12)).foregroundColor(th.dim).padding(S.m) }
      }
    }
  }
  @ViewBuilder func content(_ s: Status) -> some View {
    let q = s.queued ?? []
    let blocked = q.filter { $0.blocked }.count
    let up = (s.health ?? []).filter { $0.up }.count
    let limited = s.accounts.filter { $0.five >= 100 }.count
    let files = s.changes.reduce(0) { $0 + $1.files }
    let failed = s.finished.filter { !$0.ok }.count
    VStack(spacing: 0) {
      HStack(spacing: 0) {
        MCKpi(value: "\(s.running.count)", label: "RUNNING", color: th.ok); Rectangle().fill(th.line).frame(width: 1)
        MCKpi(value: "\(q.count - blocked)", label: "QUEUED", color: th.info); Rectangle().fill(th.line).frame(width: 1)
        MCKpi(value: "\(blocked)", label: "NEED YOU", color: blocked > 0 ? th.warn : th.dim); Rectangle().fill(th.line).frame(width: 1)
        MCKpi(value: "\(s.agents.count)", label: "AGENTS", color: th.magenta)
      }
      Rule()
      HStack(spacing: 0) {
        MCKpi(value: "\(s.finished.count - failed)", unit: failed > 0 ? "/\(failed)✕" : nil, label: "DONE", color: th.ok); Rectangle().fill(th.line).frame(width: 1)
        MCKpi(value: "\(up)", unit: "/\((s.health ?? []).count)", label: "HEALTH", color: up == (s.health ?? []).count ? th.ok : th.bad); Rectangle().fill(th.line).frame(width: 1)
        MCKpi(value: "\(limited)", label: "AT LIMIT", color: limited > 0 ? th.bad : th.dim); Rectangle().fill(th.line).frame(width: 1)
        MCKpi(value: "\(files)", label: "CHANGED", color: files > 0 ? th.warn : th.dim)
      }
      Rule()
    }.fixedSize(horizontal: false, vertical: true)

    MCHead(title: "ACCOUNTS", count: "\(s.accounts.count)", trailing: "5H BURN")
    ForEach(s.accounts) { a in
      let (wid, rest) = splitWorker(a.name)
      let stale = (a.age ?? 0) >= 600
      let c = stale ? th.faint : th.usage(a.five)
      HStack(spacing: 10) {
        LED(color: c, size: 7, glow: th.dark)
        VStack(alignment: .leading, spacing: 3) {
          Text(wid).font(th.mono(14, 600)).foregroundColor(th.text)
          Text("\(a.tool) · \(rest)").font(th.mono(11.5)).foregroundColor(th.dim).lineLimit(1).help(a.name)
        }.frame(width: 150, alignment: .leading)
        Sparkline(points: history.points[a.name] ?? [a.five], color: c).frame(height: 26)
        VStack(alignment: .trailing, spacing: 3) {
          Text(a.unknown == true ? "—" : "\(Int(a.five))%").font(th.mono(14, 600)).foregroundColor(a.five >= 100 ? th.bad : th.text)
          Text("5h · 7d \(Int(a.seven))%").font(th.mono(11.5)).foregroundColor(th.dim)
        }.frame(width: 92, alignment: .trailing)
        VStack(alignment: .trailing, spacing: 3) {
          Text(a.resetIn > 0 ? dur(a.resetIn) : "—").font(th.mono(14, 600)).foregroundColor(th.text)
          Text(stale ? "read \(dur(a.age ?? 0)) ago" : "reset").font(th.mono(11.5)).foregroundColor(stale ? th.warn : th.dim).lineLimit(1)
        }.frame(width: 84, alignment: .trailing)
      }.padding(.horizontal, S.m).padding(.vertical, 9)
      Rule()
    }

    MCHead(title: "IN PROGRESS", count: "\(s.running.count)")
    if s.running.isEmpty { Text("nothing running").font(th.mono(12)).foregroundColor(th.dim).padding(.horizontal, S.m) }
    ForEach(s.running) { t in MCTaskRow(t: t); Rule() }

    if !q.isEmpty {
      MCHead(title: "UP NEXT", count: "\(q.count) waiting")
      ForEach(q) { item in
        HStack(alignment: .center, spacing: 10) {
          LED(color: item.blocked ? th.warn : th.faint, size: 7, glow: th.dark && item.blocked)
          VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
              Text(item.num).font(th.mono(13, 600)).foregroundColor(th.accent)
              Text(item.title).font(th.title(16, 600)).lineLimit(1).help(item.title)
            }
            if !item.summary.isEmpty { Text(item.summary).font(th.mono(12)).foregroundColor(th.dim).lineLimit(1).help(item.summary) }
            Wrap(text: (item.blocked ? "✋ " : "⧗ ") + item.waitingFor, font: th.mono(12), size: 12, color: item.blocked ? th.warn : th.faint)
          }
          Spacer(minLength: S.xs)
          if !item.worker.isEmpty { Pill(text: item.worker, role: th.workerRole(item.worker)) }
        }.padding(.horizontal, S.m).padding(.vertical, 9)
        Rule()
      }
    }

    if !s.agents.isEmpty {
      MCHead(title: "CLAUDE AGENTS", count: "\(s.agents.count) running")
      ForEach(s.agents) { a in
        HStack(alignment: .center, spacing: 10) {
          LED(color: a.lastActive < 60 ? th.ok : th.warn, size: 7, glow: th.dark)
          VStack(alignment: .leading, spacing: 3) {
            Text(a.title).font(th.title(15, 600)).lineLimit(1).help(a.title)
            Text(a.recent.first.map { "▸ " + $0 } ?? "").font(th.mono(12)).foregroundColor(th.dim).lineLimit(1).help(a.recent.first ?? "")
          }
          Spacer(minLength: S.xs)
          VStack(alignment: .trailing, spacing: 3) {
            Text("\(a.tools)").font(th.mono(14, 600))
            Text("actions").font(th.mono(11.5)).foregroundColor(th.dim)
          }.fixedSize()
        }.padding(.horizontal, S.m).padding(.vertical, 9)
        Rule()
      }
    }

    MCHead(title: "TIMELINE", count: s.finished.isEmpty ? nil : "\(s.finished.count)", trailing: "recently finished")
    ForEach(s.finished) { d in
      HStack(alignment: .firstTextBaseline, spacing: 10) {
        Text(d.time).font(th.mono(12)).foregroundColor(th.dim).frame(width: 46, alignment: .leading)
        Text(d.ok ? "DONE" : "FAIL").font(th.mono(11.5, 600)).tracking(1).foregroundColor(d.ok ? th.ok : th.bad).frame(width: 40, alignment: .leading)
        Text(d.tid).font(th.mono(12, 600)).foregroundColor(th.accent)
        Text(d.title).font(th.mono(12)).foregroundColor(th.text).lineLimit(1).help(d.title)
        Spacer(minLength: 0)
      }.padding(.horizontal, S.m).padding(.vertical, 6)
      Rectangle().fill(th.line.opacity(0.7)).frame(height: 1)
    }

    MCHead(title: "REPO · HEALTH")
    VStack(alignment: .leading, spacing: S.s) {
      let parts = s.commit.components(separatedBy: " · ")
      HStack(spacing: 0) {
        mcCell("COMMIT", parts.first ?? "—"); Rectangle().fill(th.line).frame(width: 1)
        mcCell("AGE", parts.count > 2 ? parts.last! : "—"); Rectangle().fill(th.line).frame(width: 1)
        mcCell("CHANGES", s.changes.isEmpty ? "clean" : "\(files) files", color: s.changes.isEmpty ? th.ok : th.warn)
      }.fixedSize(horizontal: false, vertical: true).overlay(Rectangle().stroke(th.line))
      if parts.count > 1 { Wrap(text: parts.dropFirst().dropLast(parts.count > 2 ? 1 : 0).joined(separator: " · "), font: th.mono(12), size: 12, color: th.dim) }
      ForEach(s.changes) { c in
        HStack { Text(c.area).font(th.mono(12)).lineLimit(1).help(c.area); Spacer(); Text("\(c.files) files changed").font(th.mono(12)).foregroundColor(th.warn) }
      }
      if let hs = s.health, !hs.isEmpty { HealthRows(hs: hs).padding(.top, S.xs) }
    }.padding(.horizontal, S.m)

    // key hints
    HStack(spacing: S.s) {
      keycap("⌘T"); Text("layout").foregroundColor(th.dim)
      keycap("⌘⇧T"); Text("light/dark").foregroundColor(th.dim)
      keycap("CLICK"); Text("expand row").foregroundColor(th.dim)
      Spacer(minLength: 0)
    }.font(th.mono(11.5)).padding(.horizontal, S.m).padding(.vertical, S.s).background(th.bg2).overlay(alignment: .top) { Rule() }.padding(.top, S.l)
  }
  func mcCell(_ k: String, _ v: String, color: Color? = nil) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(k).font(th.mono(11.5)).tracking(1.4).foregroundColor(th.dim)
      Text(v).font(th.mono(13, 600)).foregroundColor(color ?? th.text).lineLimit(1).help(v)
    }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, S.s).padding(.vertical, 6)
  }
  func keycap(_ k: String) -> some View {
    Text(k).font(th.mono(11, 600)).foregroundColor(th.accent).padding(.horizontal, 5).padding(.vertical, 2)
      .background(RoundedRectangle(cornerRadius: 3).fill(th.accent.opacity(0.10))).overlay(RoundedRectangle(cornerRadius: 3).stroke(th.accent.opacity(0.5)))
  }
}

// MARK: - PAPER layout (editorial: greeting, story lede, soft cards, rings)

struct PaperCard: ViewModifier {
  let th: Theme; var edge: Color? = nil; var highlight = false
  func body(content: Content) -> some View {
    content.padding(.horizontal, S.l).padding(.vertical, S.m + 2).padding(.leading, edge == nil ? 0 : 4)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(RoundedRectangle(cornerRadius: 16).fill(th.surface).shadow(color: .black.opacity(th.dark ? 0.3 : 0.07), radius: 8, y: 3))
      .overlay(alignment: .leading) { if let e = edge { Rectangle().fill(e).frame(width: 6) } }
      .overlay(RoundedRectangle(cornerRadius: 16).stroke(highlight ? th.accent.opacity(0.6) : th.line.opacity(th.dark ? 1 : 0.6)))
      .clipShape(RoundedRectangle(cornerRadius: 16))
  }
}

struct PaperH2: View {
  @Environment(\.theme) var th
  let title: String; var count: String? = nil
  var body: some View {
    HStack(alignment: .firstTextBaseline) {
      Text(title).font(th.title(20, 600)).foregroundColor(th.text)
      Spacer()
      if let c = count { Text(c).font(th.text(14)).foregroundColor(th.faint) }
    }.padding(.top, S.s)
  }
}

struct Kicker: View {
  @Environment(\.theme) var th
  let text: String; let color: Color
  var body: some View { Text(text.uppercased()).font(th.text(12, 600)).tracking(1).foregroundColor(color).lineLimit(1) }
}

struct PaperRing: View {
  @Environment(\.theme) var th
  let a: Account
  var body: some View {
    let stale = (a.age ?? 0) >= 600
    let c = stale ? th.faint : th.usage(a.five)
    VStack(spacing: S.xs + 2) {
      ZStack {
        Circle().stroke(th.track, lineWidth: 7)
        if a.unknown != true {
          Circle().trim(from: 0, to: min(1, a.five / 100)).stroke(c, style: StrokeStyle(lineWidth: 7, lineCap: .round)).rotationEffect(.degrees(-90))
        }
        Text(a.unknown == true ? "—" : "\(Int(a.five))%").font(th.mono(14, 600)).foregroundColor(th.text)
      }.frame(width: 64, height: 64)
      Text(splitWorker(a.name).0 + " · " + a.tool).font(th.text(13, 600)).foregroundColor(th.text).lineLimit(1).help(a.name)
      Group {
        if a.five >= 100 { Text("resting · \(dur(a.resetIn))").foregroundColor(th.bad) }
        else if a.unknown == true { Text("not known yet") }
        else { Text("7d \(Int(a.seven))% · ↻ \(dur(a.resetIn))") }
      }.font(th.text(12.5)).foregroundColor(th.faint).lineLimit(1)
      if let g = a.age, g >= 600 { Text("read \(dur(g)) ago").font(th.text(12)).foregroundColor(th.warn).lineLimit(1) }
    }.frame(maxWidth: .infinity)
  }
}

struct PaperTask: View {
  @Environment(\.theme) var th
  let t: RunningTask
  @State private var expanded = false
  @State private var raw = false
  var body: some View {
    let live = t.lastActive < 60 ? th.ok : t.lastActive < 300 ? th.warn : th.bad
    VStack(alignment: .leading, spacing: S.xs + 2) {
      HStack(spacing: S.s) {
        Kicker(text: "In progress · \(t.num)", color: th.ok)
        Spacer()
        Circle().fill(live).frame(width: 9, height: 9)
      }
      Wrap(text: t.title, font: th.title(18, 600), size: 18)
      if let sm = t.summary, !sm.isEmpty { Wrap(text: sm, font: th.text(14.5), size: 14.5, color: th.faint) }
      HStack(spacing: S.s) {
        Bar(value: progressOf(t), color: th.progress, track: th.track, height: 6)
        Text("\(Int(progressOf(t) * 100))%").font(th.mono(13.5, 600)).foregroundColor(th.text).fixedSize()
      }.padding(.top, S.xs)
      if !expanded, let a = t.steps.first(where: { $0.status == "active" }) {
        Text("Now: \(a.title)").font(th.text(13.5)).foregroundColor(th.dim).lineLimit(1).help(a.title)
      }
      if !expanded, let m = firstMetricLine(t) { Text(m).font(th.text(13.5, 600)).foregroundColor(th.text).lineLimit(1).help(m) }
      Flow {
        Pill(text: t.account, role: th.workerRole(t.account))
        ForEach(t.tags ?? [], id: \.self) { Pill(text: $0, role: th.tagRole($0)) }
      }.padding(.top, S.xs)
      if expanded { TaskDetails(t: t, raw: $raw).padding(.top, S.xs) }
    }
    .modifier(PaperCard(th: th, highlight: expanded))
    .contentShape(Rectangle()).onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct PaperPanel: View {
  @Environment(\.theme) var th
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  var greeting: String {
    let h = Calendar.current.component(.hour, from: Date())
    return h < 5 ? "Good night." : h < 12 ? "Good morning." : h < 17 ? "Good afternoon." : "Good evening."
  }
  func lede(_ s: Status) -> Text {
    let q = s.queued ?? []
    let blocked = q.filter { $0.blocked }.count
    var t = Text("\(words(s.running.count)) task\(s.running.count == 1 ? " is" : "s are") moving")
    t = t + Text(s.agents.isEmpty ? ". " : " and \(words(s.agents.count).lowercased()) Claude agent\(s.agents.count == 1 ? " is" : "s are") working. ")
    if !q.isEmpty {
      t = t + Text("\(words(q.count)) more \(q.count == 1 ? "waits" : "wait")")
      if blocked > 0 {
        var mark = AttributedString("\(words(blocked).lowercased()) of them \(blocked == 1 ? "needs" : "need") you")
        mark.backgroundColor = th.chip(.warn).fill
        t = t + Text(", and ") + Text(mark) + Text(". ")
      } else { t = t + Text(". ") }
    }
    if let r = s.accounts.first(where: { $0.five >= 100 }) {
      t = t + Text("\(splitWorker(r.name).0) is resting for ") + Text(dur(r.resetIn)).bold() + Text(". ")
    }
    if let free = s.accounts.filter({ $0.unknown != true && $0.five < 100 }).min(by: { $0.five < $1.five }) {
      t = t + Text("\(splitWorker(free.name).0) has the most quota to spare (\(Int(free.five))% used).")
    }
    return t
  }
  var body: some View {
    VStack(alignment: .leading, spacing: S.m) {
      HStack(spacing: S.s) {
        Circle().fill(th.ok).frame(width: 8, height: 8)
        Text("\(todayLine("EEEE d MMMM")) · \(store.status?.time ?? "")").font(th.text(13.5)).foregroundColor(th.faint)
        Spacer()
        Text(store.status?.title ?? "").font(th.text(13.5)).foregroundColor(th.faint).lineLimit(1).help(store.status?.title ?? "")
        CollapseButton(store: store)
      }
      if store.collapsed, let s = store.status {
        HStack(spacing: S.s) {
          Pill(text: "\(s.running.count) running", role: .ok)
          Pill(text: "\(s.queued?.count ?? 0) waiting", role: .violet)
          ForEach(s.accounts.filter { $0.five >= 100 }) { a in Pill(text: "\(splitWorker(a.name).0) resting", role: .bad) }
        }
      } else {
        Text(greeting).font(th.title(32, 500)).foregroundColor(th.text)
        if let s = store.status {
          lede(s).font(th.title(16.5, 400)).foregroundColor(th.text).lineSpacing(5).fixedSize(horizontal: false, vertical: true)
        }
        ThemePicker(ts: ts).padding(.vertical, S.xs)
        if let s = store.status { sections(s) } else { Text("Loading…").font(th.text(14)).foregroundColor(th.faint) }
      }
    }.padding(.horizontal, S.l + 2).padding(.vertical, S.l)
  }
  @ViewBuilder func sections(_ s: Status) -> some View {
    let q = s.queued ?? []
    if q.contains(where: { $0.blocked }) {
      PaperH2(title: "Needs you", count: "\(q.filter { $0.blocked }.count) things")
      ForEach(q.filter { $0.blocked }) { paperQueued($0) }
    }
    PaperH2(title: "Accounts", count: "\(s.accounts.count)")
    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: S.s), count: min(4, max(1, s.accounts.count))), spacing: S.m) {
      ForEach(s.accounts) { PaperRing(a: $0) }
    }.modifier(PaperCard(th: th))
    PaperH2(title: "In progress", count: "\(s.running.count) moving")
    if s.running.isEmpty { Text("Nothing running").font(th.text(14)).foregroundColor(th.faint) }
    ForEach(s.running) { PaperTask(t: $0) }
    if q.contains(where: { !$0.blocked }) {
      PaperH2(title: "Up next", count: "\(q.filter { !$0.blocked }.count) waiting")
      ForEach(q.filter { !$0.blocked }) { paperQueued($0) }
    }
    if !s.agents.isEmpty {
      PaperH2(title: "Claude agents", count: "\(s.agents.count) running")
      ForEach(s.agents) { a in
        VStack(alignment: .leading, spacing: S.xs + 2) {
          HStack { Kicker(text: "Claude agent · \(a.tools) actions", color: th.claude); Spacer(); Circle().fill(a.lastActive < 60 ? th.ok : th.warn).frame(width: 9, height: 9) }
          Wrap(text: a.title, font: th.title(16.5, 600), size: 16.5)
          if let r = a.recent.first { Text(r).font(th.text(13.5)).foregroundColor(th.faint).lineLimit(1).help(r) }
        }.modifier(PaperCard(th: th))
      }
    }
    PaperH2(title: "Recently finished", count: s.finished.isEmpty ? nil : "\(s.finished.count)")
    VStack(alignment: .leading, spacing: S.s + 2) {
      ForEach(s.finished) { d in
        HStack(alignment: .firstTextBaseline, spacing: S.s) {
          Circle().fill(d.ok ? th.ok : th.bad).frame(width: 9, height: 9)
          Text(d.tid).font(th.mono(13, 600)).foregroundColor(th.faint).frame(minWidth: 30, alignment: .leading)
          Text(d.title).font(th.text(14.5)).foregroundColor(th.text).lineLimit(1).help(d.title)
          Spacer(minLength: S.s)
          Text(d.time).font(th.mono(13)).foregroundColor(th.faint).fixedSize()
        }
      }
    }.modifier(PaperCard(th: th))
    PaperH2(title: "Repository & health")
    VStack(alignment: .leading, spacing: S.s) {
      Wrap(text: s.commit, font: th.code(12.5), size: 12.5, color: th.dim)
      if s.changes.isEmpty { Text("Working tree clean").font(th.text(13.5)).foregroundColor(th.faint) }
      ForEach(s.changes) { c in
        HStack { Text(c.area).font(th.code(12.5)).lineLimit(1).help(c.area); Spacer(); Pill(text: "\(c.files) files changed", role: .warn) }
      }
      if let hs = s.health, !hs.isEmpty { HealthRows(hs: hs).padding(.top, S.xs) }
    }.modifier(PaperCard(th: th))
  }
  func paperQueued(_ q: Queued) -> some View {
    VStack(alignment: .leading, spacing: S.xs + 2) {
      Kicker(text: q.blocked ? "Waiting for you · \(q.num)" : "Up next · \(q.num)", color: q.blocked ? th.chip(.hot).ink : th.accent)
      Wrap(text: q.title, font: th.title(17, 600), size: 17)
      if !q.summary.isEmpty { Wrap(text: q.summary, font: th.text(14), size: 14, color: th.faint) }
      Wrap(text: q.waitingFor, font: th.text(13.5, q.blocked ? 600 : 400), size: 13.5, color: q.blocked ? th.chip(.hot).ink : th.faint)
      if !q.worker.isEmpty { Pill(text: q.worker, role: th.workerRole(q.worker)).padding(.top, S.xxs) }
    }.modifier(PaperCard(th: th, edge: q.blocked ? th.chip(.hot).ink : th.accent))
  }
}

// MARK: - Root

struct PanelView: View {
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  @ObservedObject var history: UsageHistory
  var body: some View {
    Group {
      switch ts.theme.layout {
      case .studio: StudioPanel(store: store, ts: ts)
      case .mission: MissionPanel(store: store, ts: ts, history: history)
      case .paper: PaperPanel(store: store, ts: ts)
      }
    }
    .environment(\.theme, ts.theme)
  }
}

struct PanelBackground: View {
  @Environment(\.theme) var th
  var body: some View {
    th.layout == .studio
      ? AnyView(LinearGradient(colors: [th.bgTop, th.bg], startPoint: .top, endPoint: .bottom))
      : AnyView(th.bg)
  }
}

struct RootView: View {
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  @ObservedObject var history: UsageHistory
  var body: some View {
    ScrollView { PanelView(store: store, ts: ts, history: history) }
      .padding(.top, 28)
      .background(PanelBackground().environment(\.theme, ts.theme).ignoresSafeArea())
      .preferredColorScheme(ts.theme.dark ? .dark : .light)
      .onReceive(store.$status) { history.record($0) }
  }
}

// MARK: - Window: full height on the right edge

let script = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "status.py"
FontKit.register((script as NSString).deletingLastPathComponent + "/fonts")
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let store = Store(script: script)
let themes = ThemeStore()
let history = UsageHistory()
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
func applyWindowTheme(_ t: Theme) {
  panel.appearance = NSAppearance(named: t.dark ? .darkAqua : .aqua)
  panel.backgroundColor = t.nsBg
}
applyWindowTheme(themes.theme)
themes.onChange = { applyWindowTheme($0) }
panel.contentView = NSHostingView(rootView: RootView(store: store, ts: themes, history: history))
panel.setFrame(NSRect(x: screen.maxX - width, y: screen.minY, width: width, height: screen.height), display: true)
store.onCollapse = { c in
  let h: CGFloat = c ? 140 : screen.height
  panel.setFrame(NSRect(x: screen.maxX - width, y: screen.maxY - h, width: width, height: h), display: true, animate: true)
}
// ⌘T cycles the layout (Studio → Mission Control → Paper); ⌘⇧T flips light/dark. Works while the panel is focused.
let keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { e in
  let mods = e.modifierFlags.intersection(.deviceIndependentFlagsMask)
  guard e.charactersIgnoringModifiers?.lowercased() == "t" else { return e }
  if mods == .command { themes.cycleLayout(); return nil }
  if mods == [.command, .shift] { themes.toggleMode(); return nil }
  return e
}
panel.orderFrontRegardless()
final class Closer: NSObject, NSWindowDelegate { func windowWillClose(_ n: Notification) { NSApp.terminate(nil) } }
let closer = Closer(); panel.delegate = closer
app.run()
