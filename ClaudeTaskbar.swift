// ClaudeTaskbar.swift
// An always-on-top panel that lists every Claude Code session and open claude.ai
// chat, and lets you answer Claude's questions right in the panel: permission
// prompts, multiple-choice questions, and follow-up questions.
//
// Build: swiftc -O ClaudeTaskbar.swift -o ClaudeTaskbar   (needs Xcode Command Line Tools)

import Cocoa
import ServiceManagement
import Carbon.HIToolbox

let baseDir = NSHomeDirectory() + "/.claude-taskbar"
let codeDir = baseDir + "/sessions"
let webDir = baseDir + "/web"
let questionsDir = baseDir + "/questions"
let answersDir = baseDir + "/answers"
let webOutDir = baseDir + "/web-out"
let aliveFile = baseDir + "/panel.alive"
let panelWidth: CGFloat = 360
let maxRows = 12
let maxCards = 3

// Fallback when a session didn't record __CFBundleIdentifier.
let termBundles: [String: String] = [
    "Apple_Terminal": "com.apple.Terminal",
    "iTerm.app": "com.googlecode.iterm2",
    "vscode": "com.microsoft.VSCode",
    "WarpTerminal": "dev.warp.Warp-Stable",
    "ghostty": "com.mitchellh.ghostty",
    "WezTerm": "com.github.wez.wezterm",
]

// MARK: - Data

struct TaskItem {
    let id: String
    let source: String      // "code" or "web"
    var state: String       // "running", "waiting", "done", "idle"
    let title: String       // the task title
    let prompt: String      // your latest prompt
    let detail: String
    let project: String
    let appBundle: String
    let term: String
    let url: String
    let last: String
    let promptCount: Int    // goes up with every prompt; brings back a removed task
    let updated: Double
}

struct Question {
    let id: String
    let kind: String        // "permission", "choice", "reply"
    let source: String      // "code" or "web"
    let title: String
    let body: String
    let questions: [[String: Any]]
    let sessionId: String
    let tabId: String
    let tabURL: String
}

func readJSON(_ path: String) -> [String: Any]? {
    guard let data = FileManager.default.contents(atPath: path) else { return nil }
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
}

func writeJSON(_ obj: [String: Any], to path: String) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
    try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
}

func processGone(_ obj: [String: Any], key: String) -> Bool {
    guard let pid = (obj[key] as? NSNumber)?.int32Value, pid > 0 else { return false }
    return kill(pid, 0) != 0 && errno == ESRCH
}

func loadItems(dir: String, source: String, maxAge: Double) -> [TaskItem] {
    let fm = FileManager.default
    guard let names = try? fm.contentsOfDirectory(atPath: dir) else { return [] }
    let now = Date().timeIntervalSince1970
    var out: [TaskItem] = []
    for name in names where name.hasSuffix(".json") {
        let path = dir + "/" + name
        guard let obj = readJSON(path) else { continue }
        let updated = (obj["updated"] as? NSNumber)?.doubleValue ?? 0
        // Drop entries that are too old, or whose Claude Code process exited
        // without sending SessionEnd (closed terminal window, crash, etc.).
        if now - updated > maxAge || processGone(obj, key: "pid") {
            try? fm.removeItem(atPath: path)
            continue
        }
        let str: (String) -> String = { (obj[$0] as? String) ?? "" }
        let state = str("state")
        out.append(TaskItem(
            id: str("id"), source: source, state: state.isEmpty ? "idle" : state,
            title: str("title"), prompt: str("prompt"), detail: str("detail"), project: str("project"),
            appBundle: str("app_bundle"), term: str("term"), url: str("url"),
            last: str("last"), promptCount: (obj["prompt_count"] as? NSNumber)?.intValue ?? 0,
            updated: updated))
    }
    return out
}

func loadCodeQuestions() -> [Question] {
    let fm = FileManager.default
    guard let names = try? fm.contentsOfDirectory(atPath: questionsDir) else { return [] }
    var found: [(Double, Question)] = []
    for name in names where name.hasSuffix(".json") {
        let path = questionsDir + "/" + name
        guard let obj = readJSON(path) else { continue }
        if processGone(obj, key: "hook_pid") {   // Claude Code cancelled the hook
            try? fm.removeItem(atPath: path)
            continue
        }
        let str: (String) -> String = { (obj[$0] as? String) ?? "" }
        let q = Question(id: str("id"), kind: str("kind"), source: "code", title: str("title"),
                         body: str("body"), questions: (obj["questions"] as? [[String: Any]]) ?? [],
                         sessionId: str("session_id"), tabId: "", tabURL: "")
        found.append(((obj["created"] as? NSNumber)?.doubleValue ?? 0, q))
    }
    return found.sorted { $0.0 < $1.0 }.map { $0.1 }
}

/// True when the last non-empty line of Claude's message ends with a question mark.
func endsWithQuestion(_ text: String) -> Bool {
    let lines = text.split(whereSeparator: \.isNewline)
        .map { $0.trimmingCharacters(in: .whitespaces) }
        .filter { !$0.isEmpty }
    guard let last = lines.last else { return false }
    return last.trimmingCharacters(in: CharacterSet(charactersIn: "*_`) ")).hasSuffix("?")
}

func rank(_ state: String) -> Int {
    switch state {
    case "waiting": return 0
    case "running": return 1
    case "done": return 2
    default: return 3
    }
}

func stateColor(_ state: String) -> NSColor {
    switch state {
    case "running": return .systemBlue
    case "waiting": return .systemOrange
    case "done": return .systemGreen
    default: return .tertiaryLabelColor
    }
}

func stateText(_ state: String) -> String {
    switch state {
    case "running": return "Working"
    case "waiting": return "Needs you"
    case "done": return "Finished"
    default: return "Idle"
    }
}

func ageText(_ t: Double) -> String {
    let d = max(0, Int(Date().timeIntervalSince1970 - t))
    if d < 60 { return "now" }
    if d < 3600 { return "\(d / 60)m" }
    return "\(d / 3600)h"
}

func makeLabel(_ text: String, size: CGFloat, weight: NSFont.Weight = .regular,
               color: NSColor = .labelColor) -> NSTextField {
    let l = NSTextField(labelWithString: text)
    l.font = .systemFont(ofSize: size, weight: weight)
    l.textColor = color
    l.lineBreakMode = .byTruncatingTail
    l.maximumNumberOfLines = 1
    l.translatesAutoresizingMaskIntoConstraints = false
    l.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    return l
}

func makeWrappingLabel(_ text: String, size: CGFloat, weight: NSFont.Weight = .regular,
                       color: NSColor = .labelColor, width: CGFloat, maxLines: Int = 0,
                       monospaced: Bool = false) -> NSTextField {
    let l = NSTextField(wrappingLabelWithString: text)
    l.font = monospaced ? .monospacedSystemFont(ofSize: size, weight: weight)
                        : .systemFont(ofSize: size, weight: weight)
    l.textColor = color
    l.preferredMaxLayoutWidth = width
    l.maximumNumberOfLines = maxLines
    l.lineBreakMode = .byWordWrapping
    l.cell?.truncatesLastVisibleLine = true
    l.isSelectable = false
    l.translatesAutoresizingMaskIntoConstraints = false
    l.widthAnchor.constraint(equalToConstant: width).isActive = true
    return l
}

// MARK: - Logo

/// The diamond from the logo, as a template image so macOS tints it for light and dark menu bars.
func diamondGlyph(size: CGFloat = 16) -> NSImage {
    let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { _ in
        let side = size * 0.5
        let path = NSBezierPath(roundedRect: NSRect(x: (size - side) / 2, y: (size - side) / 2,
                                                    width: side, height: side),
                                xRadius: size * 0.09, yRadius: size * 0.09)
        var turn = AffineTransform()
        turn.translate(x: size / 2, y: size / 2)
        turn.rotate(byDegrees: 45)
        turn.translate(x: -size / 2, y: -size / 2)
        path.transform(using: turn)
        NSColor.black.setFill()
        path.fill()
        return true
    }
    image.isTemplate = true
    return image
}

/// The full app icon when running from Claude Taskbar.app, otherwise the diamond.
func logoImage() -> NSImage {
    if Bundle.main.bundlePath.hasSuffix(".app"), let icon = NSApp.applicationIconImage {
        return icon
    }
    return diamondGlyph()
}

// MARK: - Views

/// A panel that can take typing without pulling focus away from the app you're in.
final class KeyablePanel: NSPanel {
    override var canBecomeKey: Bool { true }
}

final class FirstMouseButton: NSButton {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

final class FirstMouseField: NSTextField {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// Takes all clicks inside its frame, including the first click while another app is active.
class ClickableView: NSView {
    var onClick: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? { frame.contains(point) ? self : nil }
}

final class RowView: ClickableView {
    var onRemove: (() -> Void)?
    private let age: NSTextField
    private let removeMark = makeLabel("✕", size: 11, weight: .semibold, color: .secondaryLabelColor)
    private var hovering = false

    /// Three lines: the task title, your latest prompt, and where it runs plus what Claude is doing.
    init(item: TaskItem) {
        age = makeLabel(ageText(item.updated), size: 11, color: .tertiaryLabelColor)
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        toolTip = item.prompt.isEmpty ? item.title : item.prompt

        let dot = NSView()
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 4
        dot.layer?.backgroundColor = stateColor(item.state).cgColor
        dot.translatesAutoresizingMaskIntoConstraints = false
        if item.state == "running" {
            let pulse = CABasicAnimation(keyPath: "opacity")
            pulse.fromValue = 1.0
            pulse.toValue = 0.35
            pulse.duration = 0.9
            pulse.autoreverses = true
            pulse.repeatCount = .infinity
            dot.layer?.add(pulse, forKey: "pulse")
        }

        let fallbackTitle = item.source == "code" ? "New Claude Code session" : "New chat"
        let title = makeLabel(item.title.isEmpty ? fallbackTitle : item.title, size: 13, weight: .semibold)

        age.setContentCompressionResistancePriority(.required, for: .horizontal)
        age.setContentHuggingPriority(.required, for: .horizontal)

        // Your latest prompt, unless it's the same text the title was made from.
        let prompt = item.prompt.trimmingCharacters(in: .whitespaces)
        let titleStem = item.title.hasSuffix("…") ? String(item.title.dropLast()) : item.title
        let showPrompt = !prompt.isEmpty && (titleStem.isEmpty || !prompt.hasPrefix(titleStem))
        let subtitle = makeLabel(prompt, size: 11.5, color: .secondaryLabelColor)

        let place = item.source == "code" ? (item.project.isEmpty ? "Claude Code" : item.project) : "claude.ai"
        let what = item.detail.isEmpty ? stateText(item.state) : item.detail
        let para = makeLabel("\(place): \(what)", size: 11,
                             color: item.state == "waiting" ? .systemOrange : .tertiaryLabelColor)

        let lines = NSStackView()
        lines.orientation = .vertical
        lines.alignment = .leading
        lines.spacing = 2
        lines.translatesAutoresizingMaskIntoConstraints = false
        lines.addArrangedSubview(title)
        if showPrompt { lines.addArrangedSubview(subtitle) }
        lines.addArrangedSubview(para)
        lines.setCustomSpacing(3, after: title)

        removeMark.isHidden = true
        removeMark.toolTip = "Remove from list"
        for v in [dot, lines, age, removeMark] { addSubview(v) }
        NSLayoutConstraint.activate([
            dot.widthAnchor.constraint(equalToConstant: 8),
            dot.heightAnchor.constraint(equalToConstant: 8),
            dot.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            dot.centerYAnchor.constraint(equalTo: title.centerYAnchor),

            lines.leadingAnchor.constraint(equalTo: dot.trailingAnchor, constant: 9),
            lines.topAnchor.constraint(equalTo: topAnchor, constant: 8),
            lines.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -8),
            lines.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),

            title.trailingAnchor.constraint(lessThanOrEqualTo: age.leadingAnchor, constant: -8),
            para.trailingAnchor.constraint(lessThanOrEqualTo: lines.trailingAnchor),

            age.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            age.firstBaselineAnchor.constraint(equalTo: title.firstBaselineAnchor),
            removeMark.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            removeMark.firstBaselineAnchor.constraint(equalTo: title.firstBaselineAnchor),
        ])
        if showPrompt {
            subtitle.trailingAnchor.constraint(lessThanOrEqualTo: lines.trailingAnchor).isActive = true
        }
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func mouseDown(with event: NSEvent) {}
    override func mouseUp(with event: NSEvent) {
        let p = convert(event.locationInWindow, from: nil)
        guard bounds.contains(p) else { return }
        if hovering && removeMark.frame.insetBy(dx: -8, dy: -6).contains(p) {
            onRemove?()
        } else {
            onClick?()
        }
    }

    override func rightMouseDown(with event: NSEvent) {
        let menu = NSMenu()
        let open = NSMenuItem(title: "Open", action: #selector(menuOpen), keyEquivalent: "")
        open.target = self
        let remove = NSMenuItem(title: "Remove from List", action: #selector(menuRemove), keyEquivalent: "")
        remove.target = self
        menu.addItem(open)
        menu.addItem(remove)
        NSMenu.popUpContextMenu(menu, with: event, for: self)
    }
    @objc private func menuOpen() { onClick?() }
    @objc private func menuRemove() { onRemove?() }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds,
                                       options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }
    override func mouseEntered(with event: NSEvent) {
        hovering = true
        layer?.backgroundColor = NSColor.labelColor.withAlphaComponent(0.07).cgColor
        age.isHidden = true
        removeMark.isHidden = false
    }
    override func mouseExited(with event: NSEvent) {
        hovering = false
        layer?.backgroundColor = nil
        age.isHidden = false
        removeMark.isHidden = true
    }
}

/// Drag it to move the panel; click it to collapse or expand the task list.
final class HeaderView: ClickableView {
    let label = makeLabel("", size: 11.5, weight: .semibold, color: .secondaryLabelColor)
    let chevron = makeLabel("", size: 10, weight: .semibold, color: .tertiaryLabelColor)
    var onMinimize: (() -> Void)?
    private let logo = NSImageView()
    private let minimize = NSView()
    private var startMouse = NSPoint.zero
    private var startOrigin = NSPoint.zero
    private var dragged = false

    override init(frame: NSRect) {
        super.init(frame: frame)
        translatesAutoresizingMaskIntoConstraints = false
        logo.image = logoImage()
        logo.imageScaling = .scaleProportionallyUpOrDown
        logo.contentTintColor = .secondaryLabelColor   // only affects the diamond fallback
        logo.translatesAutoresizingMaskIntoConstraints = false
        chevron.setContentCompressionResistancePriority(.required, for: .horizontal)

        // A small round button that hides the whole panel.
        minimize.wantsLayer = true
        minimize.layer?.cornerRadius = 9
        minimize.layer?.backgroundColor = NSColor.labelColor.withAlphaComponent(0.08).cgColor
        minimize.translatesAutoresizingMaskIntoConstraints = false
        let dash = NSView()
        dash.wantsLayer = true
        dash.layer?.cornerRadius = 1
        dash.layer?.backgroundColor = NSColor.secondaryLabelColor.cgColor
        dash.translatesAutoresizingMaskIntoConstraints = false
        minimize.addSubview(dash)
        NSLayoutConstraint.activate([
            dash.widthAnchor.constraint(equalToConstant: 8),
            dash.heightAnchor.constraint(equalToConstant: 2),
            dash.centerXAnchor.constraint(equalTo: minimize.centerXAnchor),
            dash.centerYAnchor.constraint(equalTo: minimize.centerYAnchor),
        ])
        toolTip = "Click to show or hide the task list. The round button hides the panel (Control-Option-C)."

        for v in [logo, label, chevron, minimize] as [NSView] { addSubview(v) }
        NSLayoutConstraint.activate([
            logo.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 11),
            logo.widthAnchor.constraint(equalToConstant: 18),
            logo.heightAnchor.constraint(equalToConstant: 18),
            logo.centerYAnchor.constraint(equalTo: label.centerYAnchor),
            label.leadingAnchor.constraint(equalTo: logo.trailingAnchor, constant: 7),
            label.trailingAnchor.constraint(lessThanOrEqualTo: chevron.leadingAnchor, constant: -8),
            label.topAnchor.constraint(equalTo: topAnchor, constant: 8),
            label.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -6),
            chevron.trailingAnchor.constraint(equalTo: minimize.leadingAnchor, constant: -10),
            chevron.centerYAnchor.constraint(equalTo: label.centerYAnchor),
            minimize.widthAnchor.constraint(equalToConstant: 18),
            minimize.heightAnchor.constraint(equalToConstant: 18),
            minimize.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -10),
            minimize.centerYAnchor.constraint(equalTo: label.centerYAnchor),
        ])
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func mouseDown(with event: NSEvent) {
        startMouse = NSEvent.mouseLocation
        startOrigin = window?.frame.origin ?? .zero
        dragged = false
    }
    override func mouseDragged(with event: NSEvent) {
        let p = NSEvent.mouseLocation
        let dx = p.x - startMouse.x, dy = p.y - startMouse.y
        if abs(dx) + abs(dy) > 3 { dragged = true }
        if dragged { window?.setFrameOrigin(NSPoint(x: startOrigin.x + dx, y: startOrigin.y + dy)) }
    }
    override func mouseUp(with event: NSEvent) {
        guard !dragged else { return }
        let p = convert(event.locationInWindow, from: nil)
        if minimize.frame.insetBy(dx: -6, dy: -6).contains(p) {
            onMinimize?()
        } else {
            onClick?()
        }
    }
}

/// One of Claude's questions, answerable in place.
final class QuestionCard: NSView {
    let question: Question
    var onAction: ((Question, String, [String: Any]) -> Void)?

    private let content = NSStackView()
    private var optionButtons: [[NSButton]] = []
    private var otherFields: [NSTextField] = []
    private var replyField: NSTextField?
    private let innerWidth = panelWidth - 16 - 24

    init(question: Question) {
        self.question = question
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        let box = NSView()
        box.wantsLayer = true
        box.layer?.cornerRadius = 9
        box.layer?.backgroundColor = NSColor.systemOrange.withAlphaComponent(0.12).cgColor
        box.layer?.borderWidth = 1
        box.layer?.borderColor = NSColor.systemOrange.withAlphaComponent(0.4).cgColor
        box.translatesAutoresizingMaskIntoConstraints = false
        addSubview(box)

        content.orientation = .vertical
        content.alignment = .leading
        content.spacing = 7
        content.translatesAutoresizingMaskIntoConstraints = false
        box.addSubview(content)

        NSLayoutConstraint.activate([
            box.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 8),
            box.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -8),
            box.topAnchor.constraint(equalTo: topAnchor, constant: 3),
            box.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -5),
            content.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 12),
            content.trailingAnchor.constraint(equalTo: box.trailingAnchor, constant: -12),
            content.topAnchor.constraint(equalTo: box.topAnchor, constant: 10),
            content.bottomAnchor.constraint(equalTo: box.bottomAnchor, constant: -10),
        ])

        content.addArrangedSubview(makeWrappingLabel(question.title, size: 12.5, weight: .semibold,
                                                     width: innerWidth, maxLines: 2))
        switch question.kind {
        case "permission": buildPermission()
        case "choice": buildChoice()
        default: buildReply()
        }
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    private func button(_ title: String, _ action: Selector, primary: Bool = false) -> NSButton {
        let b = FirstMouseButton(title: title, target: self, action: action)
        b.bezelStyle = .rounded
        b.controlSize = .small
        b.font = .systemFont(ofSize: 11.5, weight: primary ? .semibold : .regular)
        if primary { b.bezelColor = .controlAccentColor }
        return b
    }

    private func buttonRow(_ buttons: [NSButton]) -> NSStackView {
        let row = NSStackView(views: buttons)
        row.orientation = .horizontal
        row.spacing = 6
        return row
    }

    private func field(_ placeholder: String) -> NSTextField {
        let f = FirstMouseField(string: "")
        f.placeholderString = placeholder
        f.font = .systemFont(ofSize: 12)
        f.controlSize = .small
        f.translatesAutoresizingMaskIntoConstraints = false
        f.widthAnchor.constraint(equalToConstant: innerWidth).isActive = true
        return f
    }

    // Permission prompt: Allow / Deny / hand back to the terminal.
    private func buildPermission() {
        if !question.body.isEmpty {
            content.addArrangedSubview(makeWrappingLabel(question.body, size: 11, width: innerWidth,
                                                         maxLines: 8, monospaced: true))
        }
        content.addArrangedSubview(buttonRow([
            button("Allow", #selector(allow), primary: true),
            button("Deny", #selector(deny)),
            button("Answer in terminal", #selector(terminal)),
        ]))
    }

    // AskUserQuestion: options for each question, plus a typed answer.
    private func buildChoice() {
        for q in question.questions {
            let text = (q["question"] as? String) ?? ""
            let multi = (q["multiSelect"] as? Bool) ?? false
            content.addArrangedSubview(makeWrappingLabel(text, size: 12, weight: .medium, width: innerWidth))

            let group = NSStackView()
            group.orientation = .vertical
            group.alignment = .leading
            group.spacing = 3
            var buttons: [NSButton] = []
            for opt in (q["options"] as? [[String: Any]]) ?? [] {
                let label = (opt["label"] as? String) ?? ""
                let b = multi
                    ? FirstMouseButton(checkboxWithTitle: label, target: nil, action: nil)
                    : FirstMouseButton(radioButtonWithTitle: label, target: self, action: #selector(radioPicked(_:)))
                b.font = .systemFont(ofSize: 12)
                group.addArrangedSubview(b)
                buttons.append(b)
                if let desc = opt["description"] as? String, !desc.isEmpty {
                    let d = makeWrappingLabel(desc, size: 10.5, color: .secondaryLabelColor,
                                              width: innerWidth - 20, maxLines: 3)
                    let indent = NSStackView(views: [d])
                    indent.edgeInsets = NSEdgeInsets(top: 0, left: 20, bottom: 2, right: 0)
                    group.addArrangedSubview(indent)
                }
            }
            optionButtons.append(buttons)
            content.addArrangedSubview(group)

            let other = field("Or type your own answer")
            otherFields.append(other)
            content.addArrangedSubview(other)
        }
        content.addArrangedSubview(buttonRow([
            button("Send answer", #selector(submitChoice), primary: true),
            button("Answer in terminal", #selector(terminal)),
        ]))
    }

    // Claude ended its turn with a question: reply in a text field.
    private func buildReply() {
        if !question.body.isEmpty {
            content.addArrangedSubview(makeWrappingLabel(question.body, size: 11.5, color: .secondaryLabelColor,
                                                         width: innerWidth, maxLines: 10))
        }
        let f = field("Type your reply, then press Return")
        f.target = self
        f.action = #selector(sendReply)          // Return sends
        f.cell?.sendsActionOnEndEditing = false  // clicking away doesn't
        replyField = f
        content.addArrangedSubview(f)
        var buttons = [button("Send reply", #selector(sendReply), primary: true)]
        if question.source == "web" {
            buttons.append(button("Open chat", #selector(openChat)))
            buttons.append(button("Dismiss", #selector(dismiss)))
        } else {
            buttons.append(button("Answer in terminal", #selector(terminal)))
        }
        content.addArrangedSubview(buttonRow(buttons))
    }

    // AppKit groups radio buttons that share a superview and an action,
    // so each question's options need this shared (empty) action.
    @objc private func radioPicked(_ sender: NSButton) {}

    @objc private func allow() { onAction?(question, "allow", [:]) }
    @objc private func deny() { onAction?(question, "deny", [:]) }
    @objc private func terminal() { onAction?(question, "terminal", [:]) }
    @objc private func openChat() { onAction?(question, "open", [:]) }
    @objc private func dismiss() { onAction?(question, "dismiss", [:]) }

    @objc private func sendReply() {
        let text = (replyField?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { NSSound.beep(); return }
        onAction?(question, "reply", ["text": text])
    }

    @objc private func submitChoice() {
        var answers: [String: Any] = [:]
        for (i, q) in question.questions.enumerated() {
            let key = (q["question"] as? String) ?? ""
            let typed = otherFields[i].stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            let picked = optionButtons[i].filter { $0.state == .on }.map { $0.title }
            if !typed.isEmpty {
                answers[key] = typed
            } else if !picked.isEmpty {
                answers[key] = picked.joined(separator: ", ")
            } else {
                NSSound.beep()   // every question needs an answer
                return
            }
        }
        onAction?(question, "answer", ["answers": answers])
    }
}

// MARK: - Setup: first launch, Claude Code hooks, the Chrome bridge, login item

let claudeSettings = NSHomeDirectory() + "/.claude/settings.json"
let hookMarker = "/.claude-taskbar/claude_hook.py"
let python = "/usr/bin/python3"

enum Setup {
    static var resources: String { Bundle.main.resourcePath ?? "" }

    @discardableResult
    static func run(_ tool: String, _ args: [String]) -> (status: Int32, output: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (-1, "\(error)") }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }

    /// Python 3 comes with Apple's Command Line Tools, which most Claude Code users already have.
    static func hasCommandLineTools() -> Bool {
        run("/usr/bin/xcode-select", ["-p"]).status == 0
    }

    /// Copies the hook and bridge scripts out of the app, so updating the app updates them too.
    static func installScripts() {
        let fm = FileManager.default
        try? fm.createDirectory(atPath: baseDir, withIntermediateDirectories: true)
        for name in ["claude_hook.py", "web_bridge.py"] {
            let src = resources + "/" + name, dst = baseDir + "/" + name
            guard fm.fileExists(atPath: src) else { continue }
            try? fm.removeItem(atPath: dst)
            try? fm.copyItem(atPath: src, toPath: dst)
        }
    }

    /// Older versions ran from launchd agents made by install.sh; the app now does that itself.
    static func removeOldLaunchAgents() {
        for label in ["com.claudetaskbar.bridge", "com.claudetaskbar.widget"] {
            let plist = NSHomeDirectory() + "/Library/LaunchAgents/\(label).plist"
            guard FileManager.default.fileExists(atPath: plist) else { continue }
            let text = (try? String(contentsOfFile: plist, encoding: .utf8)) ?? ""
            try? FileManager.default.removeItem(atPath: plist)
            // Stop the old copy, unless that old agent is what started this one.
            if let me = Bundle.main.executablePath, text.contains(me) { continue }
            run("/bin/launchctl", ["bootout", "gui/\(getuid())/\(label)"])
        }
    }

    static var hooksConnected: Bool {
        ((try? String(contentsOfFile: claudeSettings, encoding: .utf8)) ?? "").contains(hookMarker)
    }

    static var opensAtLogin: Bool {
        if #available(macOS 13.0, *) { return SMAppService.mainApp.status == .enabled }
        return false
    }

    static func setOpensAtLogin(_ on: Bool) {
        guard #available(macOS 13.0, *) else { return }
        if on { try? SMAppService.mainApp.register() } else { try? SMAppService.mainApp.unregister() }
    }

    // MARK: Dialogs

    @discardableResult
    static func alert(_ title: String, _ text: String, buttons: [String] = ["OK"],
                      accessory: NSView? = nil) -> NSApplication.ModalResponse {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.icon = NSApp.applicationIconImage
        a.accessoryView = accessory
        for b in buttons { a.addButton(withTitle: b) }
        return a.runModal()
    }

    static func firstRunIfNeeded() {
        let d = UserDefaults.standard
        guard !d.bool(forKey: "didFirstRun") else { return }
        d.set(true, forKey: "didFirstRun")
        if hooksConnected { return }   // already set up by an earlier version

        var login: NSButton?
        if #available(macOS 13.0, *) {
            let box = NSButton(checkboxWithTitle: "Open Claude Taskbar at login", target: nil, action: nil)
            box.state = .on
            login = box
        }
        let answer = alert(
            "Welcome to Claude Taskbar",
            "To list your Claude Code sessions and let you answer Claude's questions here, Claude Taskbar adds hooks to ~/.claude/settings.json. Your current settings are backed up first, and you can disconnect at any time from the menu bar icon.",
            buttons: ["Connect to Claude Code", "Not Now"], accessory: login)
        if login?.state == .on { setOpensAtLogin(true) }
        if answer == .alertFirstButtonReturn { connect() }
    }

    static func askForCommandLineTools() {
        let answer = alert(
            "Claude Taskbar needs Python 3",
            "Python 3 comes with Apple's Command Line Tools. Install them, then open Claude Taskbar again.",
            buttons: ["Install", "Cancel"])
        if answer == .alertFirstButtonReturn { run("/usr/bin/xcode-select", ["--install"]) }
    }

    static func connect() {
        guard hasCommandLineTools() else { askForCommandLineTools(); return }
        installScripts()
        let r = run(python, [resources + "/install_hooks.py"])
        guard r.status == 0 else {
            alert("Couldn't connect to Claude Code", r.output.trimmingCharacters(in: .whitespacesAndNewlines))
            return
        }
        alert("Connected to Claude Code",
              "Restart any Claude Code sessions that are already open.\n\nTo follow claude.ai chats as well, choose Set Up Chrome Extension from the Claude Taskbar menu.")
    }

    static func disconnect() {
        let r = run(python, [resources + "/install_hooks.py", "--remove"])
        if r.status == 0 {
            alert("Disconnected from Claude Code",
                  "Claude Code sessions you start from now on won't report to Claude Taskbar. Your other hooks and settings are unchanged.")
        } else {
            alert("Couldn't disconnect", r.output.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    /// Chrome loads unpacked extensions from a folder, so keep one at a fixed path.
    static func setUpChromeExtension() {
        let fm = FileManager.default
        let src = resources + "/chrome-extension", dst = baseDir + "/chrome-extension"
        try? fm.createDirectory(atPath: baseDir, withIntermediateDirectories: true)
        try? fm.removeItem(atPath: dst)
        try? fm.copyItem(atPath: src, toPath: dst)
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: dst)])
        if let chrome = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.google.Chrome"),
           let page = URL(string: "chrome://extensions") {
            NSWorkspace.shared.open([page], withApplicationAt: chrome,
                                    configuration: NSWorkspace.OpenConfiguration(), completionHandler: nil)
        }
        alert("Set up the Chrome extension",
              "Chrome's extensions page and the extension's folder are now open.\n\n1. In Chrome, turn on Developer mode (top right).\n2. Click Load unpacked.\n3. Choose the chrome-extension folder shown in Finder.\n\nAlready set it up from this folder? Click the extension's reload button instead.")
    }
}

/// Runs the local bridge for the Chrome extension, restarting it if it stops.
final class BridgeRunner {
    private var process: Process?
    private var stopping = false
    private var startedAt = Date.distantPast

    func start() {
        let script = baseDir + "/web_bridge.py"
        guard !stopping, process == nil, FileManager.default.fileExists(atPath: script) else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [script]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.process = nil
                guard !self.stopping else { return }
                // Failed right away (port busy, for example)? Wait longer before trying again.
                let delay: Double = Date().timeIntervalSince(self.startedAt) < 5 ? 30 : 2
                DispatchQueue.main.asyncAfter(deadline: .now() + delay) { self.start() }
            }
        }
        startedAt = Date()
        do { try p.run(); process = p } catch {}
    }

    func stop() {
        stopping = true
        process?.terminate()
    }
}

// MARK: - Controller

final class Controller: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let bridge = BridgeRunner()
    private var panelItem: NSMenuItem!
    private var hotKey: EventHotKeyRef?
    private var connectItem: NSMenuItem!
    private var loginItem: NSMenuItem!
    private var panel: KeyablePanel!
    private let stack = NSStackView()
    private let header = HeaderView(frame: .zero)
    private let cardsStack = NSStackView()
    private let rowsStack = NSStackView()
    private var statusItem: NSStatusItem!
    private var collapsed = UserDefaults.standard.bool(forKey: "collapsed")
    private var timer: Timer?

    private var cards: [String: QuestionCard] = [:]
    private var cardOrder: [String] = []
    private var handled: Set<String> = []     // answered or dismissed, hidden right away
    private var seen: Set<String> = []        // for the arrival sound
    private var rowsSignature = ""
    private var sessions: [String: TaskItem] = [:]
    /// Removed tasks: id -> what they looked like when removed. A Claude Code task comes
    /// back when you send it a new prompt; a claude.ai chat when you send a new message.
    private var removed = UserDefaults.standard.dictionary(forKey: "removedTasks") as? [String: String] ?? [:]
    private var restoreItem: NSMenuItem!
    private var followMouse = UserDefaults.standard.object(forKey: "followMouse") as? Bool ?? true
    private var pendingScreen: String?
    private var pendingSince = 0.0

    func applicationDidFinishLaunching(_ notification: Notification) {
        for d in [codeDir, webDir, questionsDir, answersDir, webOutDir] {
            try? FileManager.default.createDirectory(atPath: d, withIntermediateDirectories: true)
        }
        buildPanel()
        buildStatusItem()
        saveRemoved()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.refresh()
        }

        registerHotKey()
        Setup.removeOldLaunchAgents()
        if Setup.hasCommandLineTools() {
            Setup.installScripts()
            // A moment's wait lets an old bridge from launchd release its port.
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.bridge.start() }
        }
        DispatchQueue.main.async { Setup.firstRunIfNeeded() }
    }

    func applicationWillTerminate(_ notification: Notification) {
        try? FileManager.default.removeItem(atPath: aliveFile)   // hooks stop waiting on us
        bridge.stop()
    }

    private func buildPanel() {
        let p = KeyablePanel(contentRect: NSRect(x: 0, y: 0, width: panelWidth, height: 40),
                             styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: false)
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        p.isFloatingPanel = true
        p.hidesOnDeactivate = false
        p.becomesKeyOnlyIfNeeded = true   // only takes the keyboard when you click a text field
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true

        let fx = NSVisualEffectView(frame: p.contentRect(forFrameRect: p.frame))
        fx.material = .hudWindow
        fx.blendingMode = .behindWindow
        fx.state = .active
        fx.wantsLayer = true
        fx.layer?.cornerRadius = 12
        fx.layer?.masksToBounds = true
        p.contentView = fx

        for s in [stack, cardsStack, rowsStack] {
            s.orientation = .vertical
            s.alignment = .leading
            s.spacing = 0
            s.translatesAutoresizingMaskIntoConstraints = false
        }
        fx.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: fx.leadingAnchor),
            stack.topAnchor.constraint(equalTo: fx.topAnchor, constant: 2),
            stack.widthAnchor.constraint(equalToConstant: panelWidth),
        ])
        for v in [header, cardsStack, rowsStack] as [NSView] { add(v, to: stack) }

        header.onClick = { [weak self] in self?.toggleCollapsed() }
        header.onMinimize = { [weak self] in self?.hidePanel() }
        rowsStack.isHidden = collapsed

        panel = p
        // Start on the screen your mouse is on, at the corner you last left it in.
        place(on: screenUnderMouse() ?? NSScreen.main ?? NSScreen.screens[0])
        NotificationCenter.default.addObserver(self, selector: #selector(panelMoved),
                                               name: NSWindow.didMoveNotification, object: p)
        p.orderFrontRegardless()
    }

    private func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = diamondGlyph()
        statusItem.button?.imagePosition = .imageLeft
        statusItem.button?.title = ""
        let menu = NSMenu()
        let toggle = NSMenuItem(title: "Hide Panel", action: #selector(togglePanel), keyEquivalent: "c")
        toggle.keyEquivalentModifierMask = [.control, .option]   // shown as a hint; the real shortcut is global
        toggle.target = self
        panelItem = toggle
        let follow = NSMenuItem(title: "Follow My Mouse Across Screens", action: #selector(toggleFollow(_:)),
                                keyEquivalent: "")
        follow.target = self
        follow.state = followMouse ? .on : .off
        let quit = NSMenuItem(title: "Quit Claude Taskbar", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        restoreItem = NSMenuItem(title: "Show Removed Tasks", action: #selector(restoreRemoved),
                                 keyEquivalent: "")
        restoreItem.target = self
        connectItem = NSMenuItem(title: "Connect to Claude Code", action: #selector(toggleConnect),
                                 keyEquivalent: "")
        connectItem.target = self
        let chrome = NSMenuItem(title: "Set Up Chrome Extension…", action: #selector(setUpChrome),
                                keyEquivalent: "")
        chrome.target = self
        loginItem = NSMenuItem(title: "Open at Login", action: #selector(toggleLogin), keyEquivalent: "")
        loginItem.target = self
        let about = NSMenuItem(title: "About Claude Taskbar", action: #selector(showAbout), keyEquivalent: "")
        about.target = self

        menu.addItem(toggle)
        menu.addItem(follow)
        menu.addItem(restoreItem)
        menu.addItem(.separator())
        menu.addItem(connectItem)
        menu.addItem(chrome)
        if #available(macOS 13.0, *) { menu.addItem(loginItem) }
        menu.addItem(.separator())
        menu.addItem(about)
        menu.addItem(quit)
        menu.delegate = self
        statusItem.menu = menu
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        panelItem.title = panel.isVisible ? "Hide Panel" : "Show Panel"
        connectItem.title = Setup.hooksConnected ? "Disconnect from Claude Code" : "Connect to Claude Code"
        loginItem.state = Setup.opensAtLogin ? .on : .off
    }

    @objc private func toggleConnect() {
        if Setup.hooksConnected { Setup.disconnect() } else { Setup.connect() }
        if Setup.hasCommandLineTools() { bridge.start() }
    }

    @objc private func setUpChrome() { Setup.setUpChromeExtension() }

    @objc private func toggleLogin() { Setup.setOpensAtLogin(!Setup.opensAtLogin) }

    @objc private func showAbout() {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.orderFrontStandardAboutPanel(nil)
    }

    private func add(_ v: NSView, to s: NSStackView) {
        s.addArrangedSubview(v)
        v.widthAnchor.constraint(equalTo: s.widthAnchor).isActive = true
    }

    // MARK: Refresh

    private func refresh() {
        // Tell the hooks the panel is up, so they wait for answers here.
        FileManager.default.createFile(atPath: aliveFile, contents: nil)
        followMouseIfNeeded()

        var items = loadItems(dir: codeDir, source: "code", maxAge: 12 * 3600)
        items += loadItems(dir: webDir, source: "web", maxAge: 150)   // tabs heartbeat every 20–60s

        // Questions: from Claude Code hooks, plus claude.ai chats that ended with a question.
        var questions = loadCodeQuestions()
        for item in items where item.source == "web" && item.state == "done" && endsWithQuestion(item.last) {
            questions.append(Question(
                id: "web-\(item.id)-\(item.last.hashValue)", kind: "reply", source: "web",
                title: item.title.isEmpty ? "Claude asked you something" : item.title,
                body: String(item.last.suffix(700)), questions: [], sessionId: "",
                tabId: item.id, tabURL: item.url))
        }
        questions = questions.filter { !handled.contains($0.id) }

        // Sessions with an open question show as needing you.
        let askingSessions = Set(questions.map { $0.sessionId }.filter { !$0.isEmpty })
        let askingTabs = Set(questions.map { $0.tabId }.filter { !$0.isEmpty })
        for i in items.indices where askingSessions.contains(items[i].id) || askingTabs.contains(items[i].id) {
            items[i].state = "waiting"
        }
        items.sort { a, b in
            rank(a.state) != rank(b.state) ? rank(a.state) < rank(b.state) : a.updated > b.updated
        }
        sessions = Dictionary(items.filter { $0.source == "code" }.map { ($0.id, $0) },
                              uniquingKeysWith: { a, _ in a })
        items = applyRemoved(items)

        let running = items.filter { $0.state == "running" }.count
        let waiting = items.filter { $0.state == "waiting" }.count
        let needYou = max(waiting, questions.count)
        let active = running + needYou
        let barTitle = active == 0 ? "" : " \(active)"
        if statusItem.button?.title != barTitle { statusItem.button?.title = barTitle }

        var counts: [String] = []
        if needYou > 0 { counts.append("\(needYou) need\(needYou == 1 ? "s" : "") you") }
        if running > 0 { counts.append("\(running) working") }
        let summary = counts.isEmpty ? "Claude" : "Claude, " + counts.joined(separator: ", ")
        if header.label.stringValue != summary { header.label.stringValue = summary }
        let chevron = collapsed ? "Show list" : "Hide list"
        if header.chevron.stringValue != chevron { header.chevron.stringValue = chevron }

        let cardsChanged = updateCards(questions)
        let rowsChanged = updateRows(items)
        if cardsChanged || rowsChanged { resizePanel() }
    }

    /// Adds new question cards and removes answered ones, keeping the rest as they are
    /// so a half-typed answer or a picked option survives refreshes.
    private func updateCards(_ questions: [Question]) -> Bool {
        let shown = Array(questions.prefix(maxCards))
        let ids = shown.map { $0.id } + (questions.count > maxCards ? ["more-\(questions.count)"] : [])
        guard ids != cardOrder else { return false }

        var newArrival = false
        for q in shown where cards[q.id] == nil {
            let card = QuestionCard(question: q)
            card.onAction = { [weak self] q, action, payload in self?.answer(q, action, payload) }
            cards[q.id] = card
            if !seen.contains(q.id) { seen.insert(q.id); newArrival = true }
        }
        let keep = Set(shown.map { $0.id })
        for id in cards.keys where !keep.contains(id) { cards[id] = nil }

        cardsStack.arrangedSubviews.forEach { cardsStack.removeArrangedSubview($0); $0.removeFromSuperview() }
        for q in shown { if let card = cards[q.id] { add(card, to: cardsStack) } }
        if questions.count > maxCards {
            add(note("\(questions.count - maxCards) more questions waiting"), to: cardsStack)
        }
        cardOrder = ids

        if newArrival {
            NSSound(named: "Glass")?.play()
            if !panel.isVisible { panel.orderFrontRegardless() }
        }
        return true
    }

    private func updateRows(_ items: [TaskItem]) -> Bool {
        let sig = "\(collapsed)\n" + items.map {
            "\($0.id)|\($0.state)|\($0.title)|\($0.prompt)|\($0.detail)|\(ageText($0.updated))"
        }.joined(separator: "\n")
        guard sig != rowsSignature else { return false }
        rowsSignature = sig

        rowsStack.arrangedSubviews.forEach { rowsStack.removeArrangedSubview($0); $0.removeFromSuperview() }
        if items.isEmpty {
            add(note("No Claude tasks right now. Start Claude Code or open claude.ai."), to: rowsStack)
        }
        for item in items.prefix(maxRows) {
            let row = RowView(item: item)
            row.onClick = { [weak self] in self?.open(item) }
            row.onRemove = { [weak self] in self?.remove(item) }
            add(row, to: rowsStack)
        }
        if items.count > maxRows { add(note("\(items.count - maxRows) more not shown"), to: rowsStack) }
        return true
    }

    private func resizePanel() {
        stack.layoutSubtreeIfNeeded()
        let h = ceil(stack.fittingSize.height) + 6
        let top = panel.frame.maxY
        panel.setFrame(NSRect(x: panel.frame.minX, y: top - h, width: panelWidth, height: h), display: true)
    }

    private func note(_ text: String) -> NSView {
        let box = NSView()
        box.translatesAutoresizingMaskIntoConstraints = false
        let l = makeLabel(text, size: 11, color: .secondaryLabelColor)
        box.addSubview(l)
        NSLayoutConstraint.activate([
            l.leadingAnchor.constraint(equalTo: box.leadingAnchor, constant: 12),
            l.trailingAnchor.constraint(lessThanOrEqualTo: box.trailingAnchor, constant: -12),
            l.topAnchor.constraint(equalTo: box.topAnchor, constant: 5),
            l.bottomAnchor.constraint(equalTo: box.bottomAnchor, constant: -7),
        ])
        return box
    }

    // MARK: Removing tasks

    private func removalMarker(_ item: TaskItem) -> String {
        if item.source == "code" { return "prompt-\(item.promptCount)" }
        // A chat removed mid-reply only returns after that reply ends and you send another.
        return item.state == "running" ? "web-running" : "web"
    }

    private func remove(_ item: TaskItem) {
        removed[item.id] = removalMarker(item)
        saveRemoved()
        rowsSignature = ""
        refresh()
    }

    /// Hides removed tasks, and brings one back once you've asked it something new.
    private func applyRemoved(_ items: [TaskItem]) -> [TaskItem] {
        guard !removed.isEmpty else { return items }
        var changed = false
        let present = Set(items.map { $0.id })
        for id in removed.keys where !present.contains(id) {   // session or tab is gone for good
            removed[id] = nil
            changed = true
        }
        let visible = items.filter { item in
            guard let marker = removed[item.id] else { return true }
            if marker == "web-running" && item.state != "running" {
                removed[item.id] = "web"
                changed = true
                return false
            }
            let askedAgain = item.source == "web" ? (marker == "web" && item.state == "running")
                                                  : marker != removalMarker(item)
            if askedAgain {
                removed[item.id] = nil
                changed = true
                return true
            }
            return false
        }
        if changed { saveRemoved() }
        return visible
    }

    private func saveRemoved() {
        UserDefaults.standard.set(removed, forKey: "removedTasks")
        restoreItem?.title = removed.isEmpty ? "Show Removed Tasks" : "Show Removed Tasks (\(removed.count))"
        restoreItem?.isEnabled = !removed.isEmpty
    }

    @objc private func restoreRemoved() {
        removed.removeAll()
        saveRemoved()
        rowsSignature = ""
        refresh()
    }

    // MARK: Answers

    private func answer(_ q: Question, _ action: String, _ payload: [String: Any]) {
        if q.source == "web" {
            switch action {
            case "reply":
                var msg = payload
                msg["id"] = q.tabId
                let stamp = Int(Date().timeIntervalSince1970 * 1000)
                writeJSON(msg, to: "\(webOutDir)/\(q.tabId)-\(stamp).json")
                handled.insert(q.id)
            case "open":
                focusClaudeTab(q.tabURL)
                return
            default:
                handled.insert(q.id)
            }
        } else {
            var ans = payload
            ans["action"] = action
            writeJSON(ans, to: "\(answersDir)/\(q.id).json")
            handled.insert(q.id)
            if action == "terminal", let session = sessions[q.sessionId] { open(session) }
        }
        panel.makeFirstResponder(nil)
        if action != "terminal" { returnFocus() }
        refresh()
    }

    /// Typing an answer gives the panel the keyboard; hand it back to the app you were in.
    private func returnFocus() {
        guard let front = NSWorkspace.shared.frontmostApplication,
              front != NSRunningApplication.current,
              let url = front.bundleURL else { return }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = true
        NSWorkspace.shared.openApplication(at: url, configuration: cfg, completionHandler: nil)
    }

    // MARK: Opening things

    private func open(_ item: TaskItem) {
        if item.source == "web" {
            focusClaudeTab(item.url)
            return
        }
        let bundle = !item.appBundle.isEmpty ? item.appBundle : (termBundles[item.term] ?? "")
        guard !bundle.isEmpty,
              let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else { return }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = true
        NSWorkspace.shared.openApplication(at: appURL, configuration: cfg, completionHandler: nil)
    }

    /// Brings the existing Chrome tab forward; opens the chat in the default browser if it isn't found.
    private func focusClaudeTab(_ urlString: String) {
        guard let url = URL(string: urlString), url.scheme == "https", url.host == "claude.ai" else { return }
        let safe = urlString.replacingOccurrences(of: "\\", with: "").replacingOccurrences(of: "\"", with: "")
        let source = """
        if application "Google Chrome" is running then
          tell application "Google Chrome"
            repeat with w in windows
              set i to 0
              repeat with t in tabs of w
                set i to i + 1
                if (URL of t) is "\(safe)" then
                  set active tab index of w to i
                  set index of w to 1
                  activate
                  return "ok"
                end if
              end repeat
            end repeat
          end tell
        end if
        return "miss"
        """
        var error: NSDictionary?
        let result = NSAppleScript(source: source)?.executeAndReturnError(&error)
        if result?.stringValue != "ok" { NSWorkspace.shared.open(url) }
    }

    private func toggleCollapsed() {
        collapsed.toggle()
        UserDefaults.standard.set(collapsed, forKey: "collapsed")
        rowsStack.isHidden = collapsed
        rowsSignature = ""
        refresh()
        resizePanel()
    }

    // MARK: Screens

    private func screenID(_ screen: NSScreen?) -> String {
        guard let screen = screen else { return "" }
        let n = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
        return n.map { "\($0)" } ?? "\(screen.frame)"
    }

    private func screenUnderMouse() -> NSScreen? {
        let m = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(m, $0.frame, false) }
    }

    /// Puts the panel on a screen at the same distance from its top-right corner
    /// as you last left it, so it lands in the same spot on every screen.
    private func place(on screen: NSScreen) {
        let d = UserDefaults.standard
        let right = d.object(forKey: "offsetRight") == nil ? 16 : d.double(forKey: "offsetRight")
        let top = d.object(forKey: "offsetTop") == nil ? 16 : d.double(forKey: "offsetTop")
        let vf = screen.visibleFrame
        let h = panel.frame.height
        let x = min(max(vf.maxX - right - panelWidth, vf.minX), vf.maxX - panelWidth)
        let maxY = min(max(vf.maxY - top, vf.minY + h), vf.maxY)
        panel.setFrame(NSRect(x: x, y: maxY - h, width: panelWidth, height: h), display: true)
    }

    /// Moves the panel to the screen your mouse has rested on for a moment.
    private func followMouseIfNeeded() {
        guard followMouse, panel.isVisible, NSScreen.screens.count > 1 else { return }
        if let editor = panel.firstResponder as? NSTextView, editor.isFieldEditor { return }  // you're typing
        if NSEvent.pressedMouseButtons != 0 { return }                                       // mid-drag
        guard let target = screenUnderMouse() else { return }
        let id = screenID(target)
        if id == screenID(panel.screen) { pendingScreen = nil; return }
        let now = Date().timeIntervalSince1970
        if pendingScreen != id { pendingScreen = id; pendingSince = now; return }
        if now - pendingSince >= 0.8 {
            place(on: target)
            pendingScreen = nil
        }
    }

    @objc private func panelMoved() {
        guard let vf = (panel.screen ?? NSScreen.main)?.visibleFrame else { return }
        UserDefaults.standard.set(vf.maxX - panel.frame.maxX, forKey: "offsetRight")
        UserDefaults.standard.set(vf.maxY - panel.frame.maxY, forKey: "offsetTop")
    }

    @objc private func toggleFollow(_ sender: NSMenuItem) {
        followMouse.toggle()
        UserDefaults.standard.set(followMouse, forKey: "followMouse")
        sender.state = followMouse ? .on : .off
    }

    /// Opening the app again (Spotlight, Finder, Launchpad) brings the panel back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showPanel()
        return true
    }

    private func showPanel() {
        guard !panel.isVisible else { return }
        if followMouse, let screen = screenUnderMouse() { place(on: screen) }
        panel.orderFrontRegardless()
    }

    private func hidePanel() {
        panel.makeFirstResponder(nil)
        panel.orderOut(nil)
        let d = UserDefaults.standard
        if !d.bool(forKey: "toldHowToShow") {
            d.set(true, forKey: "toldHowToShow")
            Setup.alert("Panel hidden",
                        "To show it again, press Control-Option-C, click the Claude Taskbar icon in the menu bar, or open Claude Taskbar from Spotlight.\n\nIt also comes back by itself when Claude asks you a question.")
        }
    }

    /// Control-Option-C shows or hides the panel from any app. Carbon hot keys
    /// need no Accessibility permission.
    private func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let userData = userData else { return noErr }
            let me = Unmanaged<Controller>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { me.togglePanelFromKey() }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), nil)
        let id = EventHotKeyID(signature: OSType(0x43544B42), id: 1)   // "CTKB"
        RegisterEventHotKey(UInt32(kVK_ANSI_C), UInt32(controlKey | optionKey), id,
                            GetApplicationEventTarget(), 0, &hotKey)
    }

    fileprivate func togglePanelFromKey() { togglePanel() }

    @objc private func togglePanel() {
        if panel.isVisible { hidePanel() } else { showPanel() }
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }
}

// MARK: - Entry point

let app = NSApplication.shared
let controller = Controller()
app.delegate = controller
app.setActivationPolicy(.accessory)   // no Dock icon
app.run()
