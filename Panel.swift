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
  let num: String; let summary: String?; let metrics: [Metric]?; let tags: [String]?; let wid: String?
  enum CodingKeys: String, CodingKey { case metrics, tags, summary, key, title, account, elapsed, steps, note, commands, tokens, edits, recent, lastActive, estimated, failovers, wid, num = "id" }
}
struct Queued: Decodable, Identifiable { var id: String { num + title }; let num: String, title: String, summary: String, worker: String, waitingFor: String, blocked: Bool; let wid: String?
  enum CodingKeys: String, CodingKey { case num = "id", title, summary, worker, waitingFor, blocked, wid } }
struct Agent: Decodable, Identifiable { var id: String { title }; let title: String, tools: Int, recent: [String], lastActive: Int }
struct Done: Decodable, Identifiable { var id: String { tid + time }; let tid: String, title: String, time: String, ok: Bool; let wid: String?
  enum CodingKeys: String, CodingKey { case tid = "id", title, time, ok, wid } }
struct Account: Decodable, Identifiable { var id: String { name + tool }; let name: String, tool: String, five: Double, seven: Double, resetIn: Int, lastLimit: String
  let age: Int?; let unknown: Bool?; let wid: String? }
struct Change: Decodable, Identifiable { var id: String { area }; let area: String, files: Int }
struct Status: Decodable {
  let time: String, running: [RunningTask], queued: [Queued]?, agents: [Agent], finished: [Done], accounts: [Account], commit: String, changes: [Change]
  let title: String?
  let health: [Health]?
  let workers: [Worker]?
}
struct Lane: Decodable, Hashable { let id: String; let title: String; let stale: Bool }
struct PipeItem: Decodable, Hashable { let id: String; let title: String; let blocked: Bool; let likely: Bool }
/// One worker (W0, W1…) with what it is doing: from status.py's "workers" block (local files only).
struct Worker: Decodable, Identifiable {
  let id: String, name: String, color: String?, slot: Int, account: String
  let lanes: [Lane], doneToday: Int, failedToday: Int?, doneIds: [String], lastDone: Int, idleSecs: Int, pipeline: [PipeItem]
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
  // Raw surfaces for worker-colour contrast maths: every surface worker-coloured text can sit on, the card surface, the LCD.
  var inkBgs: [UInt32] = [], cardHex: UInt32 = 0xFFFFFF, bgHex: UInt32 = 0xFFFFFF, lcdHex: UInt32 = 0x0F0F10

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
    switch (l, dark) {
    case (.studio, false): t.inkBgs = [0xE6E4DD, 0xFBFAF7, 0xF2F0EA, 0xFFFBEA, 0xFFF0ED]; t.cardHex = 0xFBFAF7; t.bgHex = 0xEDEBE6; t.lcdHex = 0x0F0F10
    case (.studio, true): t.inkBgs = [0x1C1B19, 0x2D2B28, 0x262522, 0x3A2420, 0x37301A]; t.cardHex = 0x2D2B28; t.bgHex = 0x232220; t.lcdHex = 0x0B0B0C
    case (.mission, false): t.inkBgs = [0xF3F6F9, 0xFFFFFF, 0xE8EDF2]; t.cardHex = 0xFFFFFF; t.bgHex = 0xF3F6F9
    case (.mission, true): t.inkBgs = [0x0A0C0F, 0x0F1318, 0x141A21]; t.cardHex = 0x0F1318; t.bgHex = 0x0A0C0F
    case (.paper, false): t.inkBgs = [0xF6F2EA, 0xFFFDF8, 0xECE9E3, 0xFBF7EF]; t.cardHex = 0xFFFDF8; t.bgHex = 0xF6F2EA
    case (.paper, true): t.inkBgs = [0x1B1A17, 0x25231F, 0x2F2D29, 0x211F1C]; t.cardHex = 0x25231F; t.bgHex = 0x1B1A17
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

// MARK: - Worker colours: one colour per worker (W0, W1…) everywhere on the panel
// Default hues follow the Okabe–Ito palette (designed to stay distinct with the common colour-vision deficiencies),
// assigned by the order of the config's "workers" list, then any extra accounts; a "workers" entry may carry its own colour.
// Each theme derives readable variants: text/lines are pushed darker (light themes) or lighter (dark themes) until they
// reach 4.5:1 on every surface they can sit on, and badge text is checked against its own badge fill.

struct RGB: Equatable {
  var r: Double, g: Double, b: Double
  init(_ v: UInt32) { r = Double((v >> 16) & 0xFF) / 255; g = Double((v >> 8) & 0xFF) / 255; b = Double(v & 0xFF) / 255 }
  init(r: Double, g: Double, b: Double) { self.r = r; self.g = g; self.b = b }
  static func parse(_ s: String?) -> RGB? {
    guard var h = s?.trimmingCharacters(in: .whitespaces) else { return nil }
    if h.hasPrefix("#") { h.removeFirst() }
    guard h.count == 6, let v = UInt32(h, radix: 16) else { return nil }
    return RGB(v)
  }
  var lum: Double {
    func f(_ c: Double) -> Double { c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }
  func contrast(_ o: RGB) -> Double { let a = lum, b = o.lum; return (max(a, b) + 0.05) / (min(a, b) + 0.05) }
  func mix(_ o: RGB, _ t: Double) -> RGB { RGB(r: r + (o.r - r) * t, g: g + (o.g - g) * t, b: b + (o.b - b) * t) }
  var color: Color { Color(.sRGB, red: r, green: g, blue: b, opacity: 1) }
  var hexString: String { String(format: "#%02X%02X%02X", Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded())) }
  static let black = RGB(0x000000), white = RGB(0xFFFFFF)
  /// Darken (on light surfaces) or lighten (on dark ones) until the colour reaches `target` contrast on every surface.
  func readable(on bgs: [RGB], _ target: Double = 4.5) -> RGB {
    guard !bgs.isEmpty else { return self }
    let light = bgs.map(\.lum).reduce(0, +) / Double(bgs.count) > 0.25
    var c = self
    for i in 0...40 {
      if bgs.allSatisfy({ c.contrast($0) >= target }) { return c }
      c = self.mix(light ? .black : .white, Double(i + 1) * 0.025)
    }
    return c
  }
  /// Black or white, whichever reads better (always ≥ 4.58:1).
  var bestInk: RGB { contrast(.black) >= contrast(.white) ? .black : .white }
}

struct WorkerColor {
  let wid: String, name: String, hueName: String
  let base: RGB          // the worker's own colour
  let ink: Color         // text, stripes and graph lines on the theme's surfaces (≥ 4.5:1)
  let lcd: Color         // the same on the Studio LCD
  let fill: Color, onFill: Color, stroke: Color   // badge
  let tint: Color        // faint wash for drawer headers
  let inkRGB: RGB, fillRGB: RGB, onFillRGB: RGB, lcdRGB: RGB
}

struct WorkerPalette {
  static let hues: [(UInt32, String)] = [(0x0072B2, "blue"), (0xE69F00, "orange"), (0x009E73, "green"), (0xCC79A7, "pink"),
                                         (0xD55E00, "vermilion"), (0x56B4E9, "sky"), (0xF0E442, "yellow"), (0x8C8C8C, "grey")]
  let th: Theme
  let workers: [Worker]
  private let colors: [String: WorkerColor]
  init(th: Theme, workers: [Worker]) {
    self.th = th; self.workers = workers
    var m: [String: WorkerColor] = [:]
    for w in workers { m[w.id] = Self.make(th, w) }
    colors = m
  }
  static func make(_ th: Theme, _ w: Worker) -> WorkerColor {
    let h = hues[((w.slot % hues.count) + hues.count) % hues.count]
    let custom = RGB.parse(w.color)
    let base = custom ?? RGB(h.0)
    let bgs = th.inkBgs.map(RGB.init)
    let ink = base.readable(on: bgs)
    let lcd = base.readable(on: [RGB(th.lcdHex)])
    let card = RGB(th.cardHex)
    var fill: RGB, on: RGB, stroke: Color
    switch th.layout {
    case .studio: fill = base; on = base.bestInk; stroke = .clear
    case .mission: fill = RGB(th.bgHex).mix(ink, th.dark ? 0.16 : 0.10); on = ink.readable(on: [fill]); stroke = ink.color.opacity(0.6)
    case .paper: fill = card.mix(base, th.dark ? 0.26 : 0.18); on = base.readable(on: [fill]); stroke = .clear
    }
    return WorkerColor(wid: w.id, name: w.name, hueName: custom == nil ? h.1 : "custom", base: base, ink: ink.color, lcd: lcd.color,
                       fill: fill.color, onFill: on.color, stroke: stroke, tint: card.mix(base, th.dark ? 0.12 : 0.07).color,
                       inkRGB: ink, fillRGB: fill, onFillRGB: on, lcdRGB: lcd)
  }
  /// By worker id ("W2"), or from a label that starts with one ("W2 Codex-B (worker)").
  func of(_ wid: String?, label: String? = nil) -> WorkerColor? {
    if let w = wid, !w.isEmpty, let c = colors[w] { return c }
    if let l = label, let first = l.split(separator: " ").first, let c = colors[String(first)] { return c }
    if let l = label, let c = colors.values.first(where: { !$0.name.isEmpty && l.contains($0.name) }) { return c }
    return nil
  }
  /// "W0 blue · W1 orange · …" for tooltips.
  var key: String { workers.compactMap { w in colors[w.id].map { "\(w.id) \($0.hueName)" } }.joined(separator: " · ") }
}

struct WorkersKey: EnvironmentKey { static let defaultValue = WorkerPalette(th: Theme.make(.studio, dark: false), workers: []) }
extension EnvironmentValues { var workers: WorkerPalette { get { self[WorkersKey.self] } set { self[WorkersKey.self] = newValue } } }

/// Small UI preferences that survive restarts (UserDefaults).
final class UIPrefs: ObservableObject {
  static let workersKey = "agentMonitor.workersOpen"
  @Published var workersOpen: Bool { didSet { UserDefaults.standard.set(workersOpen, forKey: Self.workersKey) } }
  init() { workersOpen = UserDefaults.standard.bool(forKey: Self.workersKey) }
}

/// One usage reading. `reset` is the 5-hour window's reset time (unix seconds, 0 = unknown).
struct UsageSample: Codable { let t: Double; let five: Double; let seven: Double; let reset: Double }

/// Usage per worker over the last 24 h: one sample a minute, kept in memory for the sparklines and graphs and saved to
/// ~/Library/Application Support/agent-monitor/usage-history-<config>.json so it survives restarts.
final class UsageHistory: ObservableObject {
  @Published private(set) var samples: [String: [UsageSample]] = [:]
  private var last = Date.distantPast
  static let keep: Double = 24 * 3600, every: Double = 60
  let file: URL? = {
    guard let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("agent-monitor") else { return nil }
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let cfg = ProcessInfo.processInfo.environment["AGENT_MONITOR_CONFIG"].map { (($0 as NSString).lastPathComponent as NSString).deletingPathExtension } ?? "config"
    return dir.appendingPathComponent("usage-history-\(cfg.replacingOccurrences(of: "/", with: "_")).json")
  }()
  init(load: Bool = true) {
    guard load, let f = file, let d = try? Data(contentsOf: f), let s = try? JSONDecoder().decode([String: [UsageSample]].self, from: d) else { return }
    let cut = Date().timeIntervalSince1970 - Self.keep
    samples = s.mapValues { $0.filter { $0.t >= cut } }.filter { !$0.value.isEmpty }
  }
  static func key(_ a: Account) -> String { (a.wid?.isEmpty == false ? a.wid! : a.name) }
  /// Last hour of 5-hour usage for a sparkline.
  func spark(_ key: String) -> [Double] { (samples[key] ?? []).suffix(60).map(\.five) }
  func record(_ s: Status?) {
    guard let s = s, Date().timeIntervalSince(last) >= Self.every else { return }
    last = Date()
    let now = Date().timeIntervalSince1970
    for a in s.accounts where a.unknown != true && ((a.age ?? 0) < 600 || a.five >= 100) {
      let k = Self.key(a)
      var arr = samples[k] ?? []
      arr.append(UsageSample(t: now, five: a.five, seven: a.seven, reset: a.resetIn > 0 ? now + Double(a.resetIn) : 0))
      if let i = arr.firstIndex(where: { $0.t >= now - Self.keep }), i > 0 { arr.removeFirst(i) }
      samples[k] = arr
    }
    save()
  }
  func save() {
    guard let f = file, let d = try? JSONEncoder().encode(samples) else { return }
    try? d.write(to: f, options: .atomic)
  }
  /// Snapshot tooling only: a plausible 24 h of readings ending at the current values (no file is written).
  func debugSeed(_ key: String, five: Double, seven: Double, resetIn: Int, seed: Int) {
    let now = Date().timeIntervalSince1970, nextReset = now + Double(max(resetIn, 600))
    var out: [UsageSample] = []
    var t = now - Self.keep
    var x = UInt64(truncatingIfNeeded: seed &* 2654435761 &+ 7)
    func rnd() -> Double { x = x &* 6364136223846793005 &+ 1442695040888963407; return Double((x >> 33) % 1000) / 1000 }
    let rates = (0..<6).map { _ in 6 + rnd() * 22 }   // % per hour in each 5-hour window
    while t <= now {
      let w = Int(floor((nextReset - t) / (5 * 3600)))           // 0 = current window
      let start = nextReset - Double(w + 1) * 5 * 3600
      let inWin = (t - start) / 3600
      var v: Double
      if w == 0 { let span = (now - start) / 3600; v = span > 0 ? five * min(1, inWin / span) : five }
      else { v = min(100, inWin * rates[min(w, rates.count - 1)]) }
      if !(w == 2 && inWin > 1.5 && inWin < 2.3) {   // the panel was closed for a while: a gap in the line
        out.append(UsageSample(t: t, five: (v * 10).rounded() / 10, seven: max(0, seven - (now - t) / Self.keep * 9), reset: nextReset - Double(w) * 5 * 3600))
      }
      t += 300
    }
    samples[key] = out
  }
}

/// Burn rate, time to the 5-hour limit and the reset markers, from the samples.
struct WorkerStats {
  let a: Account?
  let samples: [UsageSample]
  let now = Date().timeIntervalSince1970
  var limited: Bool { (a?.five ?? 0) >= 100 }
  /// Times where a 5-hour window reset between two samples.
  var resets: [Double] {
    var out: [Double] = []
    for (p, c) in zip(samples, samples.dropFirst()) {
      if (p.reset > 0 && p.reset <= c.t + 30) || p.five - c.five >= 15 { out.append(p.reset > 0 && p.reset <= c.t + 30 ? max(p.t, p.reset) : c.t) }
    }
    return out
  }
  /// % of the 5-hour window used per hour over the last 90 min of the current window (nil until there are ≥ 10 min of readings).
  var burn: Double? {
    let from = max(now - 90 * 60, resets.last ?? 0)
    let s = samples.filter { $0.t >= from }
    guard let f = s.first, let l = s.last, l.t - f.t >= 600 else { return nil }
    return max(0, (l.five - f.five) / ((l.t - f.t) / 3600))
  }
  /// Seconds until 100 % at the current rate; nil when not burning.
  var limitIn: Int? {
    guard let a = a, !limited, let b = burn, b >= 0.2 else { return nil }
    return Int((100 - a.five) / b * 3600)
  }
  var resetsFirst: Bool { if let l = limitIn, let a = a, a.resetIn > 0 { return l > a.resetIn }; return false }
  var limitText: String {
    if limited { return "at limit" }
    guard burn != nil else { return "—" }
    guard let l = limitIn else { return "not at this rate" }
    return resetsFirst ? "resets first" : "~" + approx(l)
  }
  var burnText: String { burn.map { String(format: $0 >= 10 ? "%.0f%%/h" : "%.1f%%/h", $0) } ?? "—" }
}

// MARK: - Formatting and derived text

/// Coarse duration for estimates: "53m", "3h 48m".
func approx(_ s: Int) -> String { s >= 3600 ? "\(s / 3600)h \(s % 3600 / 60)m" : "\(max(1, s / 60))m" }
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

// MARK: - Worker badge, colour key, usage graph and the Workers drawer (styled per layout)

/// The worker's badge in its own colour; the tooltip names the colour and gives the whole colour key.
struct WorkerBadge: View {
  @Environment(\.theme) var th
  @Environment(\.workers) var wp
  let label: String; var wid: String? = nil; var size: CGFloat = 11.5
  var body: some View {
    if let c = wp.of(wid, label: label) {
      let r: CGFloat = th.layout == .mission ? 2 : 20
      let font: Font = th.layout == .paper ? th.text(size + 0.5, 600) : th.layout == .mission ? th.mono(size, 600) : th.mono(size, 800)
      Text(th.layout == .studio ? label.uppercased() : label).font(font).lineLimit(1)
        .tracking(th.layout == .studio ? 0.6 : 0)
        .padding(.horizontal, th.layout == .mission ? 6 : 8).padding(.vertical, th.layout == .mission ? 2 : 3)
        .foregroundColor(c.onFill).background(RoundedRectangle(cornerRadius: r).fill(c.fill))
        .overlay(RoundedRectangle(cornerRadius: r).stroke(c.stroke, lineWidth: 1)).fixedSize()
        .help("\(c.wid) · \(c.name): \(c.hueName) everywhere on this panel.\nColour key: \(wp.key)")
    } else {
      Pill(text: label, role: .neutral, size: size).help(label)
    }
  }
}

/// A small square in the worker's colour (with the id next to it when `id` is set).
struct WorkerDot: View {
  @Environment(\.workers) var wp
  let wid: String?; var label: String? = nil; var lcd = false; var size: CGFloat = 9
  var body: some View {
    if let c = wp.of(wid, label: label) {
      RoundedRectangle(cornerRadius: 2).fill(lcd ? c.lcd : c.ink).frame(width: size, height: size)
        .help("\(c.wid) · \(c.name): \(c.hueName)")
    }
  }
}

/// Colour key: "■ W0 ■ W1 …" in each worker's colour.
struct WorkerKeyRow: View {
  @Environment(\.theme) var th
  @Environment(\.workers) var wp
  var lcd = false
  var body: some View {
    HStack(spacing: S.s) {
      ForEach(wp.workers) { w in
        if let c = wp.of(w.id) {
          HStack(spacing: S.xs) {
            RoundedRectangle(cornerRadius: 2).fill(lcd ? c.lcd : c.ink).frame(width: 9, height: 9)
            Text(w.id).font(th.mono(11.5, 700)).foregroundColor(lcd ? c.lcd : c.ink)
          }.help("\(w.id) · \(w.name): \(c.hueName)")
        }
      }
    }.fixedSize()
  }
}

/// 5-hour (solid) and 7-day (dashed) usage over time with reset markers. Studio draws it as a dot-matrix LCD.
struct UsageGraph: View {
  @Environment(\.theme) var th
  let samples: [UsageSample]; let resets: [Double]; let color: Color
  var height: CGFloat = 64
  static func span(_ s: [UsageSample]) -> Double {
    let now = Date().timeIntervalSince1970
    return min(24 * 3600, max(3 * 3600, now - (s.first?.t ?? now)))
  }
  var body: some View {
    let now = Date().timeIntervalSince1970, span = Self.span(samples), t0 = now - span
    let resetColor = th.layout == .studio ? Color.white.opacity(0.45) : th.faint
    Canvas { ctx, size in
      let W = size.width, H = size.height
      let xOf: (Double) -> CGFloat = { CGFloat(($0 - t0) / span) * W }
      if th.layout == .studio {
        // Dot matrix: 5 px pitch. 5h fills columns from the bottom; 7d is one bright dot per column; resets light a dashed column.
        let pitch: CGFloat = 5, dot: CGFloat = 3
        let cols = Int(W / pitch), rows = Int(H / pitch)
        for ci in 0..<cols {
          let a = t0 + span * Double(ci) / Double(cols), b = t0 + span * Double(ci + 1) / Double(cols)
          let bucket = samples.filter { $0.t >= a && $0.t < b }
          let near = bucket.isEmpty ? samples.last(where: { $0.t < a && a - $0.t < 600 }) : nil
          let v5 = bucket.map(\.five).max() ?? near?.five, v7 = bucket.last?.seven ?? near?.seven
          let isReset = resets.contains { $0 >= a && $0 < b }
          let lit = v5.map { Int(($0 / 100 * Double(rows)).rounded()) } ?? -1
          let r7 = v7.map { max(0, Int(($0 / 100 * Double(rows)).rounded()) - 1) } ?? -1
          for ri in 0..<rows {
            let rect = CGRect(x: CGFloat(ci) * pitch + 1, y: H - CGFloat(ri + 1) * pitch + 1, width: dot, height: dot)
            let c: Color
            if isReset && ri % 2 == 0 { c = resetColor }
            else if ri == r7 { c = .white.opacity(0.85) }
            else if ri < lit { c = color.opacity(ri == lit - 1 ? 1 : 0.78) }
            else { c = .white.opacity(0.07) }
            ctx.fill(Path(roundedRect: rect, cornerRadius: 0.8), with: .color(c))
          }
        }
        return
      }
      // Line chart (Mission Control, Paper)
      for g in [0.25, 0.5, 0.75, 1.0] {
        let y = H * (1 - g)
        ctx.stroke(Path { $0.move(to: CGPoint(x: 0, y: y)); $0.addLine(to: CGPoint(x: W, y: y)) }, with: .color(th.line.opacity(g == 1 ? 1 : 0.8)),
                   style: StrokeStyle(lineWidth: 1, dash: th.layout == .paper && g < 1 ? [2, 4] : []))
      }
      let yOf: (Double) -> CGFloat = { H * (1 - CGFloat(min(100, max(0, $0)) / 100)) }
      // split into runs where the panel was running (a gap > 15 min breaks the line)
      var runs: [[UsageSample]] = []
      for s in samples where s.t >= t0 - 300 {
        if let l = runs.last?.last, s.t - l.t <= 900 { runs[runs.count - 1].append(s) } else { runs.append([s]) }
      }
      for run in runs {
        guard let f = run.first, let l = run.last else { continue }
        var area = Path(); area.move(to: CGPoint(x: xOf(f.t), y: H))
        for s in run { area.addLine(to: CGPoint(x: xOf(s.t), y: yOf(s.five))) }
        area.addLine(to: CGPoint(x: xOf(l.t), y: H)); area.closeSubpath()
        if th.layout == .paper {
          ctx.fill(area, with: .linearGradient(Gradient(colors: [color.opacity(th.dark ? 0.35 : 0.22), color.opacity(0.02)]), startPoint: .zero, endPoint: CGPoint(x: 0, y: H)))
        } else { ctx.fill(area, with: .color(color.opacity(th.dark ? 0.16 : 0.10))) }
        var five = Path(), seven = Path()
        for (i, s) in run.enumerated() {
          let p5 = CGPoint(x: xOf(s.t), y: yOf(s.five)), p7 = CGPoint(x: xOf(s.t), y: yOf(s.seven))
          if i == 0 { five.move(to: p5); seven.move(to: p7) } else { five.addLine(to: p5); seven.addLine(to: p7) }
        }
        ctx.stroke(seven, with: .color(color.opacity(0.75)), style: StrokeStyle(lineWidth: 1.25, lineCap: .round, dash: [3, 3]))
        ctx.stroke(five, with: .color(color), style: StrokeStyle(lineWidth: th.layout == .paper ? 2 : 1.5, lineCap: .round, lineJoin: .round))
      }
      for r in resets where r >= t0 {
        let x = xOf(r)
        ctx.stroke(Path { $0.move(to: CGPoint(x: x, y: 0)); $0.addLine(to: CGPoint(x: x, y: H)) }, with: .color(resetColor), style: StrokeStyle(lineWidth: 1, dash: [2, 3]))
      }
      if let l = samples.last {
        let p = CGPoint(x: xOf(l.t), y: yOf(l.five))
        ctx.fill(Path(ellipseIn: CGRect(x: p.x - 3, y: p.y - 3, width: 6, height: 6)), with: .color(color))
      }
    }
    .frame(height: height)
  }
}

/// Caption under a graph: span on the left, legend in the middle, "now" on the right.
struct GraphCaption: View {
  @Environment(\.theme) var th
  let samples: [UsageSample]; let color: Color; var lcd = false
  var body: some View {
    let hours = Int((UsageGraph.span(samples) / 3600).rounded())
    let dim = lcd ? th.lcdDim : th.dim
    let reset = lcd ? Color.white.opacity(0.6) : th.faint
    HStack(spacing: S.s) {
      Text(th.layout == .studio ? "−\(hours)H" : "−\(hours) h").foregroundColor(dim)
      Spacer(minLength: S.xs)
      HStack(spacing: S.xs) { Rectangle().fill(color).frame(width: 12, height: 3); Text(th.layout == .studio ? "5H" : "5-hour").foregroundColor(dim) }
      HStack(spacing: S.xs) {
        if lcd { Circle().fill(Color.white.opacity(0.85)).frame(width: 4, height: 4) }
        else { HStack(spacing: 2) { ForEach(0..<3, id: \.self) { _ in Rectangle().fill(color.opacity(0.75)).frame(width: 3, height: 1.5) } } }
        Text(th.layout == .studio ? "7D" : "7-day").foregroundColor(dim)
      }
      HStack(spacing: S.xs) { Text("┆").foregroundColor(reset); Text(th.layout == .studio ? "RESET" : "reset").foregroundColor(dim) }
      Spacer(minLength: S.xs)
      Text(th.layout == .studio ? "NOW" : "now").foregroundColor(dim)
    }.font(th.mono(11.5, th.layout == .studio ? 700 : 400)).lineLimit(1)
  }
}

/// The facts the drawer shows for one worker, shared by all layouts.
struct WorkerFacts {
  let w: Worker; let a: Account?; let st: WorkerStats
  var five: String { a.map { $0.unknown == true ? "—" : $0.five >= 100 ? "LIMIT" : "\(Int($0.five))%" } ?? "—" }
  var seven: String { a.map { $0.unknown == true ? "—" : "\(Int($0.seven))%" } ?? "—" }
  var resetIn: String { a.map { $0.resetIn > 0 ? approx($0.resetIn) : "—" } ?? "—" }
  var liveLanes: [Lane] { w.lanes.filter { !$0.stale } }
  var lanesValue: String { w.lanes.isEmpty ? "0" : liveLanes.count == w.lanes.count ? "\(w.lanes.count)" : "\(liveLanes.count) +\(w.lanes.count - liveLanes.count) stale" }
  var doneValue: String { "\(w.doneToday)" + ((w.failedToday ?? 0) > 0 ? " · \(w.failedToday!) failed" : "") }
  var idleValue: String { w.idleSecs == 0 ? "busy" : w.idleSecs < 0 ? "—" : approx(w.idleSecs) }
  /// Idle for 10+ minutes while the 5-hour window still has room: worth giving it work.
  var spare: String? {
    guard let a = a, a.unknown != true, !st.limited, w.idleSecs >= 600, a.five < 85 else { return nil }
    return "Spare capacity: idle \(approx(w.idleSecs)) with \(100 - Int(a.five))% of the 5-hour window left"
  }
  /// (label, value, tooltip)
  var cells: [(String, String, String)] {
    [("Resets in", resetIn, "Time until the 5-hour window resets"),
     ("Burn", st.burnText, "5-hour usage per hour over the last 90 minutes"),
     ("Limit in", st.limitText, "Estimated time until 100% at the current rate"),
     ("Lanes now", lanesValue, "Jobs running in parallel now (stale = no update for 30+ min)"),
     ("Done today", doneValue, "Jobs this worker finished since midnight"),
     ("Idle", idleValue, "Time since its last job ended, while nothing is running")]
  }
}

struct WorkersDrawer: View {
  @Environment(\.theme) var th
  @Environment(\.workers) var wp
  let s: Status
  @ObservedObject var history: UsageHistory
  var body: some View {
    VStack(alignment: .leading, spacing: th.layout == .mission ? 0 : S.m) {
      ForEach(wp.workers) { w in
        let a = s.accounts.first { ($0.wid ?? "") == w.id } ?? s.accounts.first { $0.name == w.account }
        let samples = a.map { history.samples[UsageHistory.key($0)] ?? [] } ?? []
        let f = WorkerFacts(w: w, a: a, st: WorkerStats(a: a, samples: samples))
        if let c = wp.of(w.id) {
          switch th.layout {
          case .studio: StudioWorker(f: f, c: c, samples: samples)
          case .mission: MCWorker(f: f, c: c, samples: samples)
          case .paper: PaperWorker(f: f, c: c, samples: samples)
          }
        }
      }
    }
  }
}

/// Lane and pipeline lines, shared: ▶ running, ◌ stale, ⧗ queued, ✋ blocked, ? likely.
struct WorkerJobs: View {
  @Environment(\.theme) var th
  let f: WorkerFacts; let color: Color; var text: Color? = nil; var dim: Color? = nil
  var body: some View {
    let tx = text ?? th.text, dm = dim ?? th.faint
    VStack(alignment: .leading, spacing: S.xs + 2) {
      if f.w.lanes.isEmpty && f.w.pipeline.isEmpty && f.w.doneIds.isEmpty {
        Text(th.layout == .studio ? "NO JOBS YET TODAY" : "No jobs yet today").font(th.mono(12.5)).foregroundColor(dm)
      }
      ForEach(f.w.lanes, id: \.self) { l in
        HStack(alignment: .firstTextBaseline, spacing: S.s) {
          Text(l.stale ? "◌" : "▶").font(th.mono(12.5, 800)).foregroundColor(l.stale ? dm : color).frame(width: 14)
          Text(l.id).font(th.mono(13, 700)).foregroundColor(l.stale ? dm : color).fixedSize()
          Text(l.title + (l.stale ? " · stale" : "")).font(th.text(13)).foregroundColor(l.stale ? dm : tx).lineLimit(1).help(l.title)
        }
      }
      if !f.w.pipeline.isEmpty {
        Text(th.layout == .paper ? "Up next" : "UP NEXT").font(th.layout == .paper ? th.text(13, 600) : th.mono(11.5, 700)).tracking(th.layout == .paper ? 0 : 1.2).foregroundColor(dm).padding(.top, S.xxs)
        ForEach(f.w.pipeline, id: \.self) { q in
          HStack(alignment: .firstTextBaseline, spacing: S.s) {
            Text(q.blocked ? "✋" : q.likely ? "?" : "⧗").font(th.mono(12.5, 800)).foregroundColor(dm).frame(width: 14)
            Text(q.id).font(th.mono(13, 700)).foregroundColor(tx).fixedSize()
            Text(q.title + (q.likely ? " · likely" : "") + (q.blocked ? " · needs you" : "")).font(th.text(13)).foregroundColor(dm).lineLimit(1).help(q.title)
          }
        }
      }
      if !f.w.doneIds.isEmpty {
        HStack(alignment: .firstTextBaseline, spacing: S.s) {
          Text("✓").font(th.mono(12.5, 800)).foregroundColor(dm).frame(width: 14)
          Text((th.layout == .paper ? "Finished today: " : "DONE: ") + f.w.doneIds.suffix(6).joined(separator: " · ")).font(th.mono(12.5)).foregroundColor(dm).lineLimit(1)
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
  var edge: Color? = nil
  init(edge: Color? = nil, @ViewBuilder content: () -> Content) { self.edge = edge; self.content = content() }
  var body: some View {
    content.padding(.horizontal, S.m + 2).padding(.leading, edge == nil ? 0 : 4).padding(.vertical, S.m).frame(maxWidth: .infinity, alignment: .leading)
      .background(RoundedRectangle(cornerRadius: 14).fill(th.lcd))
      .overlay { if let e = edge { RoundedRectangle(cornerRadius: 14).fill(e).mask(HStack(spacing: 0) { Rectangle().frame(width: 9); Spacer(minLength: 0) }).allowsHitTesting(false) } }
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
  /// Worker colour: a stripe down the left edge and the key-cap's drop edge.
  var edge: Color? = nil
  func body(content: Content) -> some View {
    let shape = RoundedRectangle(cornerRadius: 14)
    content.padding(.horizontal, S.m + 2).padding(.leading, edge == nil ? 0 : 4).padding(.vertical, S.m).frame(maxWidth: .infinity, alignment: .leading)
      .background(shape.fill(fill ?? th.surface))
      .overlay { if let e = edge { shape.fill(e).mask(HStack(spacing: 0) { Rectangle().frame(width: 7); Spacer(minLength: 0) }).allowsHitTesting(false) } }
      .overlay(shape.stroke(highlight ? th.accent : th.ink, style: StrokeStyle(lineWidth: 2, dash: dashed ? [5, 3] : [])))
      .background(dashed ? nil : shape.fill(edge ?? th.ink).overlay(shape.stroke(th.ink, lineWidth: 2)).offset(y: 4))
      .padding(.bottom, dashed ? 0 : 4)
  }
}

struct StudioLabel: View {
  @Environment(\.theme) var th
  let title: String; var count: String? = nil
  /// Set for a section that expands: shows a chevron and the worker colour key.
  var open: Bool? = nil
  var body: some View {
    HStack(spacing: S.s) {
      if let o = open { Image(systemName: o ? "chevron.down" : "chevron.right").font(.system(size: 11, weight: .heavy)).foregroundColor(th.accentText) }
      Text(title).font(th.mono(11.5, 800)).tracking(2.2).foregroundColor(open == nil ? th.faint : th.text).lineLimit(1)
      Rectangle().fill(th.line).frame(height: 1)
      if open != nil { WorkerKeyRow() }
      if let c = count { Text(c.uppercased()).font(th.mono(11.5, 800)).tracking(1.6).foregroundColor(th.accentText) }
    }.contentShape(Rectangle())
  }
}

struct StudioTask: View {
  @Environment(\.theme) var th
  @Environment(\.workers) var wp
  let t: RunningTask
  @State private var expanded = false
  @State private var raw = false
  var body: some View {
    VStack(alignment: .leading, spacing: S.s + 2) {
      HStack(alignment: .top, spacing: S.s) {
        NumTag(text: t.num).padding(.top, 2)
        Wrap(text: t.title, font: th.title(16, 700), size: 16)
        Spacer(minLength: S.xs)
        WorkerBadge(label: t.account, wid: t.wid)
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
    .modifier(KeyStyle(th: th, highlight: expanded, edge: wp.of(t.wid, label: t.account)?.ink))
    .contentShape(Rectangle()).onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct StudioPanel: View {
  @Environment(\.theme) var th
  @Environment(\.workers) var wp
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  @ObservedObject var history: UsageHistory
  @ObservedObject var prefs: UIPrefs
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
                ForEach(s.accounts) { a in Text("\(splitWorker(a.name).0) \(a.five >= 100 ? "LIMIT" : "\(Int(a.five))%")").foregroundColor(a.five >= 100 ? hex(0xFF6B5E) : wp.of(a.wid, label: a.name)?.lcd ?? hex(0xBDBDBD)) }
              }
            }.font(th.mono(11.5, 700)).tracking(1).foregroundColor(hex(0xBDBDBD)).lineLimit(1)
            if !store.collapsed && !wp.workers.isEmpty {
              // Colour key and per-worker 5-hour usage; click to open the Workers drawer.
              Button(action: { withAnimation(.easeInOut(duration: 0.2)) { prefs.workersOpen.toggle() } }) {
                HStack(spacing: S.m) {
                  ForEach(wp.workers) { w in
                    let a = s.accounts.first { ($0.wid ?? "") == w.id }
                    HStack(spacing: S.xs) {
                      WorkerDot(wid: w.id, lcd: true, size: 8)
                      Text("\(w.id) \(a.map { $0.unknown == true ? "—" : $0.five >= 100 ? "LIMIT" : "\(Int($0.five))%" } ?? "—")")
                        .foregroundColor(a.map { $0.five >= 100 } == true ? hex(0xFF6B5E) : wp.of(w.id)?.lcd ?? hex(0xBDBDBD))
                    }
                  }
                  Spacer(minLength: 0)
                  Text(prefs.workersOpen ? "WORKERS ▾" : "WORKERS ▸").foregroundColor(th.lcdDim)
                }.font(th.mono(11.5, 700)).tracking(1).lineLimit(1).contentShape(Rectangle())
              }.buttonStyle(.plain).help("Workers: click to \(prefs.workersOpen ? "close" : "open") the drawer.  Colour key: \(wp.key)")
            }
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
      Button(action: { withAnimation(.easeInOut(duration: 0.2)) { prefs.workersOpen.toggle() } }) {
        StudioLabel(title: "WORKERS", open: prefs.workersOpen)
      }.buttonStyle(.plain).help(prefs.workersOpen ? "Close the Workers drawer" : "Open the Workers drawer: usage graph, burn rate, lanes and pipeline per worker")
      if prefs.workersOpen && !wp.workers.isEmpty { WorkersDrawer(s: s, history: history) } else {
      ForEach(s.accounts) { a in
        let stale = (a.age ?? 0) >= 600
        let wc = wp.of(a.wid, label: a.name)
        LCD(edge: wc?.lcd) {
          VStack(alignment: .leading, spacing: S.xs + 2) {
            HStack(alignment: .firstTextBaseline, spacing: S.s) {
              Text(a.name).font(th.mono(13, 700)).foregroundColor(wc?.lcd ?? .white).lineLimit(1).help(a.name)
              Pill(text: a.tool, role: .neutral, size: 11)
              Spacer(minLength: S.xs)
              if a.unknown == true { Text("not known yet").font(th.mono(12.5)).foregroundColor(th.lcdDim) }
              else if a.five >= 100 { Text("LIMIT → \(dur(a.resetIn))").font(th.mono(12.5, 800)).foregroundColor(hex(0xFF6B5E)) }
              else { Text("5h \(Int(a.five))% · 7d \(Int(a.seven))%").font(th.mono(12.5, 600)).foregroundColor(stale ? th.lcdDim : hex(0xDDDDDD)) }
            }
            Bar(value: a.unknown == true ? 0 : a.five / 100, color: stale ? hex(0x777777) : a.five >= 90 ? hex(0xFF6B5E) : wc?.lcd ?? (a.five >= 60 ? hex(0xE3A13A) : hex(0x35E07F)),
                track: hex(0x2A2A2A), height: 6, radius: 3)
            HStack(spacing: S.s) {
              if a.resetIn > 0 && a.five < 100 && a.unknown != true { Text("↻ 5-hour window resets in \(dur(a.resetIn))") }
              if let g = a.age, g >= 600 { Text("· reading from \(dur(g)) ago").foregroundColor(hex(0xF0B54A)) }
            }.font(th.mono(11.5)).foregroundColor(th.lcdDim)
          }
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
              if !q.worker.isEmpty { WorkerBadge(label: q.worker, wid: q.wid) }
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
              Text(d.tid).font(th.mono(12.5, 800)).foregroundColor(hex(0xF4F4F4)).frame(minWidth: 30, alignment: .leading)
              Text(d.title).font(th.mono(12.5)).foregroundColor(hex(0xDDDDDD)).lineLimit(1).help(d.title)
              Spacer(minLength: S.s)
              if let c = wp.of(d.wid) { Text(c.wid).font(th.mono(12.5, 800)).foregroundColor(c.lcd).fixedSize().help("\(c.wid) · \(c.name)") }
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
  /// Set for a section that expands: shows a chevron and the worker colour key.
  var open: Bool? = nil
  var body: some View {
    HStack(spacing: S.s) {
      if let o = open { Image(systemName: o ? "chevron.down" : "chevron.right").font(.system(size: 10, weight: .bold)).foregroundColor(th.accent) }
      Text(title).font(th.mono(11.5, 600)).tracking(2.4).foregroundColor(open == nil ? th.dim : th.text).lineLimit(1)
      if let c = count { Text(c.uppercased()).font(th.mono(11.5, 600)).tracking(1).foregroundColor(th.accent) }
      Rectangle().fill(th.line).frame(height: 1)
      if open != nil { WorkerKeyRow() }
      if let t = trailing { Text(t).font(th.mono(11.5)).foregroundColor(th.dim) }
    }.padding(.horizontal, S.m).padding(.top, S.l).padding(.bottom, S.s).contentShape(Rectangle())
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
  @Environment(\.workers) var wp
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
        WorkerBadge(label: t.account, wid: t.wid)
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
    .overlay(alignment: .leading) {
      if let c = wp.of(t.wid, label: t.account) { Rectangle().fill(c.ink).frame(width: expanded ? 4 : 3) }
      else if expanded { Rectangle().fill(th.accent).frame(width: 2) }
    }
    .contentShape(Rectangle()).onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct MissionPanel: View {
  @Environment(\.theme) var th
  @Environment(\.workers) var wp
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  @ObservedObject var history: UsageHistory
  @ObservedObject var prefs: UIPrefs
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
          ForEach(s.accounts) { a in
            HStack(spacing: S.xs) {
              Text(splitWorker(a.name).0).foregroundColor(wp.of(a.wid, label: a.name)?.ink ?? th.text)
              Text(a.five >= 100 ? "LIMIT" : "\(Int(a.five))%").foregroundColor(a.five >= 90 ? th.bad : th.text)
            }
          }
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

    Button(action: { withAnimation(.easeInOut(duration: 0.2)) { prefs.workersOpen.toggle() } }) {
      MCHead(title: "WORKERS", count: "\(s.accounts.count)", open: prefs.workersOpen)
    }.buttonStyle(.plain).help(prefs.workersOpen ? "Close the Workers drawer" : "Open the Workers drawer: usage graph, burn rate, lanes and pipeline per worker")
    if prefs.workersOpen && !wp.workers.isEmpty { Rule(); WorkersDrawer(s: s, history: history) } else {
    ForEach(s.accounts) { a in
      let (wid, rest) = splitWorker(a.name)
      let stale = (a.age ?? 0) >= 600
      let c = stale ? th.faint : th.usage(a.five)
      let wc = wp.of(a.wid, label: a.name)
      HStack(spacing: 10) {
        LED(color: c, size: 7, glow: th.dark)
        VStack(alignment: .leading, spacing: 3) {
          Text(wid).font(th.mono(14, 600)).foregroundColor(wc?.ink ?? th.text)
          Text("\(a.tool) · \(rest)").font(th.mono(11.5)).foregroundColor(th.dim).lineLimit(1).help(a.name)
        }.frame(width: 150, alignment: .leading)
        Sparkline(points: { let p = history.spark(UsageHistory.key(a)); return p.isEmpty ? [a.five] : p }(), color: stale ? th.faint : wc?.ink ?? c).frame(height: 26)
        VStack(alignment: .trailing, spacing: 3) {
          Text(a.unknown == true ? "—" : "\(Int(a.five))%").font(th.mono(14, 600)).foregroundColor(a.five >= 100 ? th.bad : th.text)
          Text("5h · 7d \(Int(a.seven))%").font(th.mono(11.5)).foregroundColor(th.dim)
        }.frame(width: 92, alignment: .trailing)
        VStack(alignment: .trailing, spacing: 3) {
          Text(a.resetIn > 0 ? dur(a.resetIn) : "—").font(th.mono(14, 600)).foregroundColor(th.text)
          Text(stale ? "read \(dur(a.age ?? 0)) ago" : "reset").font(th.mono(11.5)).foregroundColor(stale ? th.warn : th.dim).lineLimit(1)
        }.frame(width: 84, alignment: .trailing)
      }.padding(.horizontal, S.m).padding(.vertical, 9)
      .overlay(alignment: .leading) { if let w = wc { Rectangle().fill(w.ink).frame(width: 3) } }
      Rule()
    }
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
          if !item.worker.isEmpty { WorkerBadge(label: item.worker, wid: item.wid) }
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
        if let c = wp.of(d.wid) { Text(c.wid).font(th.mono(12, 600)).foregroundColor(c.ink).fixedSize().help("\(c.wid) · \(c.name)") }
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
  @Environment(\.workers) var wp
  let a: Account
  var body: some View {
    let stale = (a.age ?? 0) >= 600
    // The ring is the worker's own colour; the number turns red near the limit.
    let c = stale ? th.faint : a.five >= 90 ? th.bad : wp.of(a.wid, label: a.name)?.ink ?? th.usage(a.five)
    VStack(spacing: S.xs + 2) {
      ZStack {
        Circle().stroke(th.track, lineWidth: 7)
        if a.unknown != true {
          Circle().trim(from: 0, to: min(1, a.five / 100)).stroke(c, style: StrokeStyle(lineWidth: 7, lineCap: .round)).rotationEffect(.degrees(-90))
        }
        Text(a.unknown == true ? "—" : "\(Int(a.five))%").font(th.mono(14, 600)).foregroundColor(a.five >= 90 && !stale ? th.bad : th.text)
      }.frame(width: 64, height: 64)
      HStack(spacing: S.xs) {
        WorkerDot(wid: a.wid, label: a.name, size: 8)
        Text(splitWorker(a.name).0).font(th.text(13, 700)).foregroundColor(wp.of(a.wid, label: a.name)?.ink ?? th.text)
        Text("· " + a.tool).font(th.text(13, 600)).foregroundColor(th.text)
      }.lineLimit(1).help(a.name)
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
  @Environment(\.workers) var wp
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
        WorkerBadge(label: t.account, wid: t.wid)
        ForEach(t.tags ?? [], id: \.self) { Pill(text: $0, role: th.tagRole($0)) }
      }.padding(.top, S.xs)
      if expanded { TaskDetails(t: t, raw: $raw).padding(.top, S.xs) }
    }
    .modifier(PaperCard(th: th, edge: wp.of(t.wid, label: t.account)?.ink, highlight: expanded))
    .contentShape(Rectangle()).onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
  }
}

struct PaperPanel: View {
  @Environment(\.theme) var th
  @Environment(\.workers) var wp
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  @ObservedObject var history: UsageHistory
  @ObservedObject var prefs: UIPrefs
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
    Button(action: { withAnimation(.easeInOut(duration: 0.2)) { prefs.workersOpen.toggle() } }) {
      HStack(alignment: .firstTextBaseline, spacing: S.s) {
        Image(systemName: prefs.workersOpen ? "chevron.down" : "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundColor(th.accentText)
        Text("Workers").font(th.title(20, 600)).foregroundColor(th.text)
        Spacer()
        WorkerKeyRow()
      }.padding(.top, S.s).contentShape(Rectangle())
    }.buttonStyle(.plain).help(prefs.workersOpen ? "Close the Workers drawer" : "Open the Workers drawer: usage graph, burn rate, lanes and pipeline per worker")
    if prefs.workersOpen && !wp.workers.isEmpty { WorkersDrawer(s: s, history: history) } else {
      LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: S.s), count: min(4, max(1, s.accounts.count))), spacing: S.m) {
        ForEach(s.accounts) { PaperRing(a: $0) }
      }.modifier(PaperCard(th: th))
    }
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
          if let c = wp.of(d.wid) { Text(c.wid).font(th.mono(13, 700)).foregroundColor(c.ink).fixedSize().help("\(c.wid) · \(c.name)") }
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
      if !q.worker.isEmpty { WorkerBadge(label: q.worker, wid: q.wid).padding(.top, S.xxs) }
    }.modifier(PaperCard(th: th, edge: q.blocked ? th.chip(.hot).ink : th.accent))
  }
}

// MARK: - Workers drawer, one view per layout

/// Studio: an LCD panel with a dot-matrix graph.
struct StudioWorker: View {
  @Environment(\.theme) var th
  let f: WorkerFacts; let c: WorkerColor; let samples: [UsageSample]
  var body: some View {
    LCD(edge: c.lcd) {
      VStack(alignment: .leading, spacing: S.s) {
        HStack(alignment: .center, spacing: S.s) {
          WorkerBadge(label: f.w.id, wid: f.w.id, size: 12)
          Text(f.w.name.uppercased()).font(th.mono(13, 700)).foregroundColor(.white).lineLimit(1).help(f.a?.name ?? f.w.name)
          if let a = f.a { Pill(text: a.tool, role: .neutral, size: 11) }
          Spacer(minLength: S.xs)
          Text(f.five).font(th.lcdDigits(30)).foregroundColor(f.st.limited ? hex(0xFF6B5E) : c.lcd).shadow(color: c.lcd.opacity(0.35), radius: 5).fixedSize()
          VStack(alignment: .leading, spacing: 0) { Text("5H"); Text("7D \(f.seven)") }.font(th.mono(11.5, 700)).foregroundColor(th.lcdDim).fixedSize()
        }
        UsageGraph(samples: samples, resets: f.st.resets, color: c.lcd, height: 60)
        GraphCaption(samples: samples, color: c.lcd, lcd: true)
        Rectangle().fill(th.lcdLine).frame(height: 1)
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: S.s, alignment: .leading), count: 3), alignment: .leading, spacing: S.s) {
          ForEach(f.cells, id: \.0) { cell in
            VStack(alignment: .leading, spacing: 2) {
              Text(cell.0.uppercased()).font(th.mono(11.5, 700)).tracking(1).foregroundColor(th.lcdDim).lineLimit(1)
              Text(cell.1.uppercased()).font(th.mono(13.5, 700)).foregroundColor(hex(0xEEEEEE)).lineLimit(1).minimumScaleFactor(0.8)
            }.help(cell.2)
          }
        }
        if let sp = f.spare { Wrap(text: "◆ " + sp, font: th.mono(12.5, 700), size: 12.5, color: hex(0x35E07F)) }
        Rectangle().fill(th.lcdLine).frame(height: 1)
        WorkerJobs(f: f, color: c.lcd, text: hex(0xDDDDDD), dim: th.lcdDim)
      }
    }
  }
}

/// Mission Control: a dense row with a ruled line chart and KPI cells.
struct MCWorker: View {
  @Environment(\.theme) var th
  let f: WorkerFacts; let c: WorkerColor; let samples: [UsageSample]
  var body: some View {
    VStack(alignment: .leading, spacing: S.s) {
      HStack(alignment: .center, spacing: 10) {
        LED(color: f.st.limited ? th.bad : th.usage(f.a?.five ?? 0), size: 7, glow: th.dark)
        Text(f.w.id).font(th.mono(15, 600)).foregroundColor(c.ink)
        Text("\(f.a?.tool ?? "") · \(f.w.name)").font(th.mono(12)).foregroundColor(th.dim).lineLimit(1).help(f.a?.name ?? f.w.name)
        Spacer(minLength: S.xs)
        Text(f.five).font(th.mono(18, 600)).foregroundColor(f.st.limited ? th.bad : th.text).fixedSize()
        VStack(alignment: .leading, spacing: 0) { Text("5H"); Text("7D \(f.seven)") }.font(th.mono(11.5)).foregroundColor(th.dim).fixedSize()
      }
      UsageGraph(samples: samples, resets: f.st.resets, color: c.ink, height: 64).help("Top rule = 100%; dotted rules every 25%")
      GraphCaption(samples: samples, color: c.ink)
      VStack(spacing: 0) {
        ForEach(0..<2, id: \.self) { row in
          HStack(spacing: 0) {
            ForEach(0..<3, id: \.self) { col in
              let cell = f.cells[row * 3 + col]
              VStack(alignment: .leading, spacing: 3) {
                Text(cell.0.uppercased()).font(th.mono(11.5)).tracking(1.2).foregroundColor(th.dim).lineLimit(1)
                Text(cell.1).font(th.mono(13.5, 600)).foregroundColor(th.text).lineLimit(1).minimumScaleFactor(0.8)
              }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, S.s).padding(.vertical, 6).help(cell.2)
              if col < 2 { Rectangle().fill(th.line).frame(width: 1) }
            }
          }.fixedSize(horizontal: false, vertical: true)
          if row == 0 { Rule() }
        }
      }.overlay(Rectangle().stroke(th.line))
      if let sp = f.spare { Wrap(text: "◆ " + sp, font: th.mono(12.5, 600), size: 12.5, color: th.ok) }
      WorkerJobs(f: f, color: c.ink)
    }
    .padding(.horizontal, S.m).padding(.vertical, 10).padding(.leading, 4)
    .overlay(alignment: .leading) { Rectangle().fill(c.ink).frame(width: 3) }
    .overlay(alignment: .bottom) { Rule() }
  }
}

/// Paper: a soft card with a calm area chart and a plain-English line.
struct PaperWorker: View {
  @Environment(\.theme) var th
  let f: WorkerFacts; let c: WorkerColor; let samples: [UsageSample]
  var body: some View {
    VStack(alignment: .leading, spacing: S.s) {
      HStack(spacing: S.s) {
        Kicker(text: "\(f.w.id) · \(f.w.name)", color: c.ink)
        Spacer()
        if let a = f.a { Pill(text: a.tool, role: .neutral) }
      }
      (Text(f.st.limited ? "Resting" : "\(f.five) used").font(th.title(19, 600)).foregroundColor(th.text)
        + Text(f.st.limited ? " until the 5-hour window resets" : " of the 5-hour window").font(th.title(16.5, 400)).foregroundColor(th.dim))
        .fixedSize(horizontal: false, vertical: true)
      Text("7-day \(f.seven) · burning \(f.st.burnText) · resets in \(f.resetIn)").font(th.text(14)).foregroundColor(th.faint).lineLimit(1)
      UsageGraph(samples: samples, resets: f.st.resets, color: c.ink, height: 72).padding(.top, S.xs)
      GraphCaption(samples: samples, color: c.ink)
      LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: S.s, alignment: .leading), count: 3), alignment: .leading, spacing: S.m) {
        ForEach(f.cells, id: \.0) { cell in
          VStack(alignment: .leading, spacing: 2) {
            Text(cell.1).font(th.mono(15, 600)).foregroundColor(th.text).lineLimit(1).minimumScaleFactor(0.8)
            Text(cell.0).font(th.text(13)).foregroundColor(th.faint).lineLimit(1)
          }.help(cell.2)
        }
      }.padding(.top, S.xs)
      if let sp = f.spare {
        Wrap(text: sp, font: th.text(13.5, 600), size: 13.5, color: th.chip(.ok).ink)
          .padding(.horizontal, S.s + 2).padding(.vertical, S.xs + 2).background(RoundedRectangle(cornerRadius: 10).fill(th.chip(.ok).fill))
      }
      WorkerJobs(f: f, color: c.ink).padding(.top, S.xs)
    }.modifier(PaperCard(th: th, edge: c.ink))
  }
}


// MARK: - Root

struct PanelView: View {
  @ObservedObject var store: Store
  @ObservedObject var ts: ThemeStore
  @ObservedObject var history: UsageHistory
  @ObservedObject var prefs: UIPrefs
  var body: some View {
    Group {
      switch ts.theme.layout {
      case .studio: StudioPanel(store: store, ts: ts, history: history, prefs: prefs)
      case .mission: MissionPanel(store: store, ts: ts, history: history, prefs: prefs)
      case .paper: PaperPanel(store: store, ts: ts, history: history, prefs: prefs)
      }
    }
    .environment(\.theme, ts.theme)
    .environment(\.workers, WorkerPalette(th: ts.theme, workers: store.status?.workers ?? []))
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
  @ObservedObject var prefs: UIPrefs
  var body: some View {
    ScrollView { PanelView(store: store, ts: ts, history: history, prefs: prefs) }
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
let prefs = UIPrefs()
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
panel.contentView = NSHostingView(rootView: RootView(store: store, ts: themes, history: history, prefs: prefs))
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
