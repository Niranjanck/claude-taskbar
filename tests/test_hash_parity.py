"""The panel (Swift) and panel_link.py (Python) must hash a card identically, or every
answer would be rejected. Compiles the same hashing steps as ClaudeTaskbar.swift's
shownHash and compares on cards with Unicode, newlines and multiple-choice options."""
import json, os, subprocess, sys, tempfile
repo = sys.argv[1]
sys.path.insert(0, repo)
import panel_link

cards = [
    {"title": "proj wants to run a command", "description": "Commit & push", "body": "git commit -m 'x'\nEOF\n"},
    {"title": "Tracker is asking you", "description": "", "body": "Ready? ✅ «quotes» 日本語\n\n- item"},
    {"title": "q", "description": "", "body": "", "questions": [
        {"question": "Which DB?", "options": [{"label": "Postgres"}, {"label": "SQLite ⚡"}]},
        {"question": "Keep?", "options": [{"label": "Yes"}]}]},
]
src = r'''
import Foundation
import CryptoKit
let data = FileManager.default.contents(atPath: CommandLine.arguments[1])!
let cards = try! JSONSerialization.jsonObject(with: data) as! [[String: Any]]
for c in cards {
    var parts = [(c["title"] as? String) ?? "", (c["description"] as? String) ?? "", (c["body"] as? String) ?? ""]
    for item in (c["questions"] as? [[String: Any]]) ?? [] {
        parts.append((item["question"] as? String) ?? "")
        for opt in (item["options"] as? [[String: Any]]) ?? [] { parts.append((opt["label"] as? String) ?? "") }
    }
    print(SHA256.hash(data: Data(parts.joined(separator: "\n").utf8)).map { String(format: "%02x", $0) }.joined())
}
'''
d = tempfile.mkdtemp()
open(os.path.join(d, "h.swift"), "w").write(src)
json.dump(cards, open(os.path.join(d, "cards.json"), "w"))
subprocess.run(["swiftc", "-O", os.path.join(d, "h.swift"), "-o", os.path.join(d, "h")], check=True, capture_output=True)
swift = subprocess.run([os.path.join(d, "h"), os.path.join(d, "cards.json")], capture_output=True, text=True).stdout.split()
py = [panel_link.shown_hash(c) for c in cards]
ok = swift == py
print(("PASS " if ok else "FAIL ") + "Swift and Python card hashes match", "" if ok else (swift, py))
# Guard against drift: the app's source must still build the hash the same way.
app = open(os.path.join(repo, "ClaudeTaskbar.swift")).read()
same = 'var parts = [q.title, q.summary, q.body]' in app and 'parts.joined(separator: "\\n").utf8' in app
print(("PASS " if same else "FAIL ") + "ClaudeTaskbar.swift still hashes title, summary, body, questions")
print("ALL PASS" if ok and same else "SOME FAILED")
