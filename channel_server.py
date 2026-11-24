#!/usr/bin/env python3
"""Claude Code channel for Claude Taskbar: two-way sync between the terminal and the panel.

Claude Code starts this as an MCP server (stdio JSON-RPC) when you run `claude-tb`.
  - Permission prompts: Claude Code shows its own dialog AND relays the request here.
    We put a card in the panel; if you answer there, we send the verdict back.
    Claude Code applies whichever answer arrives first.
  - Replies: when Claude ends its turn with a question, the hook writes a reply card
    whose id starts with our prefix. A reply typed in the panel is delivered into the
    session as a channel message.

Claude Code doesn't tell a channel when the terminal answered first; the hooks clear
those cards (see clear_questions in claude_hook.py).
Standard library only. Never writes anything to stdout except JSON-RPC.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

BASE = os.path.expanduser("~/.claude-taskbar")
try:  # holds prompts, replies and commands: only this user may read it
    os.makedirs(BASE, mode=0o700, exist_ok=True)
except OSError:
    pass
QUESTIONS_DIR = os.path.join(BASE, "questions")
ANSWERS_DIR = os.path.join(BASE, "answers")
CHANNELS_DIR = os.path.join(BASE, "channels")
ALIVE_FILE = os.path.join(BASE, "panel.alive")
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "env", "python3", "python"}
# Claude Code won't register a channel that negotiates 2026-07-28, so stay on this one.
PROTOCOL = "2025-06-18"
INSTRUCTIONS = (
    'Messages inside <channel source="claude-taskbar"> tags were typed by the user in the '
    "Claude Taskbar panel, usually in answer to a question you asked at the end of your last "
    "reply. Treat them exactly as if the user had typed them in this terminal."
)

out_lock = threading.Lock()


def send(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def notify(method, params):
    send({"jsonrpc": "2.0", "method": method, "params": params})


def write_atomic(directory, name, rec):
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, os.path.join(directory, name))


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def panel_alive():
    try:
        return time.time() - os.path.getmtime(ALIVE_FILE) < 5
    except OSError:
        return False


def claude_pid():
    """Walk up past wrapper shells to the Claude Code process (same as the hook does)."""
    pid = os.getppid()
    for _ in range(4):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=1).stdout.strip()
        except Exception:
            break
        parts = out.split(None, 1)
        if len(parts) < 2:
            break
        if os.path.basename(parts[1].strip()) in SHELLS:
            pid = int(parts[0])
            continue
        break
    return pid


CLAUDE_PID = claude_pid()
PREFIX = f"ch-{CLAUDE_PID}-"          # ids of cards this channel answers
MARKER = os.path.join(CHANNELS_DIR, f"{CLAUDE_PID}.json")
PROJECT = os.path.basename(os.getcwd().rstrip("/")) or "Claude Code"


def permission_title(tool):
    if tool == "Bash":
        return f"{PROJECT} wants to run a command"
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return f"{PROJECT} wants to edit a file"
    if tool == "WebFetch":
        return f"{PROJECT} wants to fetch a web page"
    if tool.startswith("mcp__") and len(tool.split("__")) >= 3:
        return f"{PROJECT} wants to use {tool.split('__')[1]}"
    return f"{PROJECT} wants to use {tool}"


def mark_verified():
    """Claude Code relayed a prompt, so this session's channel works (not blocked by policy)."""
    marker = read_json(MARKER) or {}
    if not marker.get("verified"):
        marker.update({"claude_pid": CLAUDE_PID, "server_pid": os.getpid(), "verified": True})
        write_atomic(CHANNELS_DIR, f"{CLAUDE_PID}.json", marker)


def replace_info_card(tool):
    """The hook already showed this prompt read-only; the live card takes its place."""
    try:
        names = os.listdir(QUESTIONS_DIR)
    except OSError:
        return
    oldest = None
    for name in names:
        q = read_json(os.path.join(QUESTIONS_DIR, name))
        if (isinstance(q, dict) and q.get("kind") == "permission-info" and q.get("tool") == tool
                and q.get("claude_pid") == CLAUDE_PID):
            if oldest is None or float(q.get("created") or 0) < oldest[0]:
                oldest = (float(q.get("created") or 0), name)
    if oldest:
        remove(os.path.join(QUESTIONS_DIR, oldest[1]))


def on_permission_request(params):
    request_id = str(params.get("request_id") or "")
    tool = str(params.get("tool_name") or "")
    if not request_id.isalpha() or not request_id.islower() or len(request_id) > 16:
        return
    mark_verified()
    if tool == "AskUserQuestion" or not panel_alive():
        return
    desc = " ".join(str(params.get("description") or "").split())[:300]
    body = str(params.get("input_preview") or "").strip()
    # The preview is JSON-shaped text; show a command as written when it parses.
    try:
        fields = json.loads(body)
        if isinstance(fields, dict) and isinstance(fields.get("command"), str):
            body = fields["command"]
        elif isinstance(fields, dict) and isinstance(fields.get("file_path"), str) and len(fields) == 1:
            body = fields["file_path"]
    except Exception:
        pass
    body = body[:20_000]
    qid = f"{PREFIX}p-{request_id}"
    write_atomic(QUESTIONS_DIR, qid + ".json", {
        "id": qid, "kind": "permission", "live": True, "request_id": request_id,
        "tool": tool, "project": PROJECT, "title": permission_title(tool), "body": body,
        "description": desc,
        "created": time.time(), "claude_pid": CLAUDE_PID, "session_id": "",
    })
    replace_info_card(tool)


def deliver(qid, question, answer):
    action = answer.get("action")
    if question.get("kind") == "permission" and action in ("allow", "deny"):
        notify("notifications/claude/channel/permission",
               {"request_id": question.get("request_id", ""), "behavior": action})
    elif action == "reply":
        text = str(answer.get("text") or "").strip()
        if text:
            notify("notifications/claude/channel", {"content": text, "meta": {"via": "panel"}})


def watch_answers():
    """Picks up the panel's answers to this session's cards."""
    while True:
        try:
            names = os.listdir(ANSWERS_DIR)
        except OSError:
            names = []
        for name in names:
            if not (name.startswith(PREFIX) and name.endswith(".json")):
                continue
            qid = name[:-5]
            apath = os.path.join(ANSWERS_DIR, name)
            qpath = os.path.join(QUESTIONS_DIR, name)
            answer = read_json(apath)
            question = read_json(qpath)
            remove(apath)
            remove(qpath)
            if isinstance(answer, dict) and isinstance(question, dict):
                try:
                    deliver(qid, question, answer)
                except Exception:
                    pass
        time.sleep(0.25)


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

    if method == "initialize":
        asked = str(params.get("protocolVersion") or PROTOCOL)
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": asked if asked <= PROTOCOL else PROTOCOL,
            "capabilities": {"experimental": {"claude/channel": {}, "claude/channel/permission": {}}},
            "serverInfo": {"name": "claude-taskbar", "version": "1.1.0"},
            "instructions": INSTRUCTIONS,
        }})
    elif method == "notifications/claude/channel/permission_request":
        on_permission_request(params)
    elif method == "ping" and mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method in ("tools/list", "resources/list", "prompts/list") and mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {method.split("/")[0]: []}})
    elif mid is not None and method:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": f"Method not found: {method}"}})


def cleanup():
    remove(MARKER)
    try:
        for name in os.listdir(QUESTIONS_DIR):
            if name.startswith(PREFIX):
                remove(os.path.join(QUESTIONS_DIR, name))
    except OSError:
        pass


def main():
    for d in (QUESTIONS_DIR, ANSWERS_DIR, CHANNELS_DIR):
        os.makedirs(d, exist_ok=True)
    # Tells the hooks this session is synced, so they leave questions to the channel.
    write_atomic(CHANNELS_DIR, f"{CLAUDE_PID}.json",
                 {"claude_pid": CLAUDE_PID, "server_pid": os.getpid(), "started": time.time()})
    threading.Thread(target=watch_answers, daemon=True).start()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if isinstance(msg, dict):
                try:
                    handle(msg)
                except Exception:
                    pass
    finally:
        cleanup()


if __name__ == "__main__":
    main()
