#!/usr/bin/env python3
"""Claude Code hook for Claude Taskbar.

Two modes:
  claude_hook.py          Records the session's live state (fast, never blocks).
  claude_hook.py --ask    Sends Claude's questions to the panel and waits for your answer:
                          - PermissionRequest: Allow / Deny
                          - PreToolUse(AskUserQuestion): pick options or type an answer
                          - Stop: when Claude's last line is a question, reply in the panel

If the panel isn't running, or you choose "Answer in terminal", or the wait times out,
the hook steps aside and Claude Code shows its normal prompt in the terminal.
Nothing is printed except the decision JSON Claude Code reads.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

BASE = os.path.expanduser("~/.claude-taskbar")
SESSIONS_DIR = os.path.join(BASE, "sessions")
QUESTIONS_DIR = os.path.join(BASE, "questions")
ANSWERS_DIR = os.path.join(BASE, "answers")
ALIVE_FILE = os.path.join(BASE, "panel.alive")
CONFIG_FILE = os.path.join(BASE, "config.json")
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "env", "python3", "python"}
DEFAULT_CONFIG = {
    "permissions": True,      # answer permission prompts in the panel
    "choices": True,          # answer AskUserQuestion in the panel
    "replies": True,          # reply when Claude ends its turn with a question
    "timeout_seconds": 600,   # then hand the question back to the terminal
}


# ---------- helpers ----------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE) as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: user[k] for k in DEFAULT_CONFIG if k in user})
    except Exception:
        pass
    return cfg


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


def panel_alive():
    try:
        return time.time() - os.path.getmtime(ALIVE_FILE) < 5
    except OSError:
        return False


def claude_pid():
    """Walk up past any wrapper shells to the Claude Code process itself."""
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


def project_of(data, rec=None):
    cwd = data.get("cwd") or (rec or {}).get("cwd") or ""
    return os.path.basename(cwd.rstrip("/")) or cwd or "Claude Code"


def describe_tool(name, inp):
    inp = inp if isinstance(inp, dict) else {}

    def base(p):
        return os.path.basename(str(p)) if p else "file"

    if name == "Bash":
        cmd = " ".join(str(inp.get("command", "")).split())
        return "Running " + (cmd[:60] + "…" if len(cmd) > 60 else cmd)
    if name in ("Edit", "MultiEdit", "Write"):
        return "Editing " + base(inp.get("file_path"))
    if name == "NotebookEdit":
        return "Editing " + base(inp.get("notebook_path"))
    if name == "Read":
        return "Reading " + base(inp.get("file_path"))
    if name in ("Grep", "Glob"):
        return "Searching for " + str(inp.get("pattern", ""))[:40]
    if name == "WebFetch":
        return "Fetching a web page"
    if name == "WebSearch":
        return "Searching the web"
    if name in ("Task", "Agent"):
        return "Subagent: " + str(inp.get("description", "working"))[:40]
    if name == "TodoWrite":
        return "Updating its to-do list"
    if name == "AskUserQuestion":
        return "Asking you a question"
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            return f"Using {parts[1]}: {parts[-1]}"
    return "Using " + name


# ---------- task titles ----------
#
# Claude Code stores a short title for each session in its transcript, the same
# one /resume shows. Priority: your /rename, then Claude's own title, then an
# agent name or older summary line, and finally your first prompt.

TITLE_TYPES = ["custom-title", "ai-title", "agent-name", "summary"]
TITLE_FIELDS = {"custom-title": ("customTitle",), "ai-title": ("aiTitle",),
                "agent-name": ("agentName", "name"), "summary": ("summary",)}
TAG_RE = re.compile(r"<[^>]+>")


def clean_prompt(text):
    """Strip markup like <agent-message ...> and collapse whitespace."""
    text = TAG_RE.sub(" ", str(text or ""))
    return " ".join(text.split())


def shorten(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",.;:") + "…"


def scan_titles(path, tail_bytes=None):
    """Returns {title type: latest value} found in the transcript."""
    found = {}
    if not path:
        return found
    try:
        with open(path, "rb") as f:
            if tail_bytes:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - tail_bytes))
                if size > tail_bytes:
                    f.readline()  # skip the partial first line
            for raw in f:
                if b'"type":"' not in raw or not any(t.encode() in raw for t in TITLE_TYPES):
                    continue
                try:
                    entry = json.loads(raw)
                except Exception:
                    continue
                kind = entry.get("type")
                if kind not in TITLE_FIELDS:
                    continue
                for field in TITLE_FIELDS[kind]:
                    value = entry.get(field)
                    if isinstance(value, str) and value.strip():
                        found[kind] = " ".join(value.split())
                        break
    except OSError:
        pass
    return found


def refresh_title(data, rec, full=False):
    """Updates rec['title'] from the transcript without downgrading a better title."""
    path = data.get("transcript_path") or rec.get("transcript") or ""
    if path:
        rec["transcript"] = path
    found = scan_titles(path, None if full else 256_000)
    current = rec.get("title_src")
    rank_of = lambda kind: TITLE_TYPES.index(kind) if kind in TITLE_TYPES else len(TITLE_TYPES)
    for kind in TITLE_TYPES:
        if kind in found:
            if rank_of(kind) <= rank_of(current):
                rec["title"] = shorten(found[kind], 80)
                rec["title_src"] = kind
            break
    if not rec.get("title") and rec.get("first_prompt"):
        rec["title"] = shorten(rec["first_prompt"], 70)
        rec["title_src"] = "prompt"


# ---------- session status ----------

def update_status(data, **fields):
    sid = str(data.get("session_id") or "")
    if not sid or "/" in sid:
        return
    path = os.path.join(SESSIONS_DIR, sid + ".json")
    rec = read_json(path) or {}
    now = time.time()
    cwd = data.get("cwd") or rec.get("cwd") or ""
    rec.update({"id": sid, "source": "code", "cwd": cwd,
                "project": project_of(data, rec), "updated": now})
    rec.setdefault("started", now)
    # macOS sets __CFBundleIdentifier for processes launched from an app
    # (Terminal, iTerm, VS Code, Cursor...), so the panel knows what to focus.
    rec.setdefault("app_bundle", os.environ.get("__CFBundleIdentifier", ""))
    rec.setdefault("term", os.environ.get("TERM_PROGRAM", ""))
    if not rec.get("pid"):
        rec["pid"] = claude_pid()
    prompt = fields.pop("_prompt", None)
    if prompt:
        rec["prompt"] = prompt[:300]
        rec.setdefault("first_prompt", prompt[:300])
        # The panel uses this to bring back a task you removed once you ask it something new.
        rec["prompt_count"] = int(rec.get("prompt_count") or 0) + 1
    scan = fields.pop("_scan", None)
    rec.update(fields)
    if scan:
        refresh_title(data, rec, full=(scan == "full" and rec.get("title_src") in (None, "prompt")))
    write_atomic(SESSIONS_DIR, sid + ".json", rec)


def record(data):
    sid = str(data.get("session_id") or "")
    if not sid or "/" in sid:
        return
    event = data.get("hook_event_name", "")
    path = os.path.join(SESSIONS_DIR, sid + ".json")

    if event == "SessionEnd":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    current = read_json(path) or {}

    if event == "SessionStart":
        if data.get("source") == "compact" and "state" in current:
            update_status(data, _scan="full")
        else:
            update_status(data, state="idle", detail="Waiting for a prompt", _scan="full")
    elif event == "UserPromptSubmit":
        prompt = clean_prompt(data.get("prompt")) or "Message from another agent"
        update_status(data, _prompt=prompt, state="running", detail="Thinking", _scan="tail")
    elif event == "PreToolUse":
        update_status(data, state="running",
                      detail=describe_tool(data.get("tool_name", ""), data.get("tool_input")))
    elif event == "PostToolUse":
        update_status(data, state="running")
    elif event == "Notification":
        if current.get("state") != "done":  # finished sessions also get idle reminders
            update_status(data, state="waiting",
                          detail=str(data.get("message") or "Needs your input")[:80], _scan="tail")
        else:
            update_status(data, _scan="tail")
    elif event == "Stop":
        update_status(data, state="done", detail="Finished", _scan="full")


# ---------- asking the panel ----------

def ask_panel(question, timeout):
    """Show a question in the panel and wait. Returns the answer dict, or None."""
    qid = question["id"]
    qpath = os.path.join(QUESTIONS_DIR, qid + ".json")
    apath = os.path.join(ANSWERS_DIR, qid + ".json")
    os.makedirs(ANSWERS_DIR, exist_ok=True)
    write_atomic(QUESTIONS_DIR, qid + ".json", question)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if os.path.exists(apath):
                answer = read_json(apath)
                if isinstance(answer, dict):
                    return answer
            if not panel_alive():
                return None
            time.sleep(0.25)
        return None
    finally:
        for p in (qpath, apath):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass


def new_question(data, kind, title, body, **extra):
    sid = str(data.get("session_id") or "")
    q = {"id": f"{sid[:8]}-{uuid.uuid4().hex[:10]}", "kind": kind, "session_id": sid,
         "project": project_of(data), "title": title, "body": body,
         "created": time.time(), "hook_pid": os.getpid()}
    q.update(extra)
    return q


def permission_title(project, tool):
    if tool == "Bash":
        return f"{project} wants to run a command"
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return f"{project} wants to edit a file"
    if tool == "WebFetch":
        return f"{project} wants to fetch a web page"
    if tool.startswith("mcp__") and len(tool.split("__")) >= 3:
        return f"{project} wants to use {tool.split('__')[1]}"
    return f"{project} wants to use {tool}"


def permission_body(tool, inp):
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Bash":
        body = str(inp.get("command", ""))
        if inp.get("description"):
            body = f"{inp['description']}\n\n{body}"
    elif tool in ("Edit", "MultiEdit", "Write", "Read"):
        body = str(inp.get("file_path", ""))
    elif tool == "NotebookEdit":
        body = str(inp.get("notebook_path", ""))
    elif tool == "WebFetch":
        body = str(inp.get("url", ""))
    else:
        body = json.dumps(inp, ensure_ascii=False)
    return body[:700]


def last_line_is_question(text):
    lines = [l.strip() for l in str(text).strip().splitlines() if l.strip()]
    if not lines:
        return False
    return lines[-1].rstrip("*_`) ").endswith("?")


def last_message_from_transcript(path):
    """Fallback for older Claude Code versions without last_assistant_message."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return ""


def emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def handle_ask(data):
    cfg = load_config()
    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    project = project_of(data)
    timeout = max(10, min(int(cfg["timeout_seconds"]), 870))

    if event == "PermissionRequest":
        # AskUserQuestion is answered in PreToolUse; here it falls through to the terminal.
        if tool == "AskUserQuestion" or not cfg["permissions"] or not panel_alive():
            return
        update_status(data, state="waiting", detail="Needs your permission")
        q = new_question(data, "permission", permission_title(project, tool),
                         permission_body(tool, inp), tool=tool)
        answer = ask_panel(q, timeout)
        action = (answer or {}).get("action")
        if action == "allow":
            update_status(data, state="running", detail=describe_tool(tool, inp))
            emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                         "decision": {"behavior": "allow"}}})
        elif action == "deny":
            update_status(data, state="running", detail="Permission denied")
            emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                         "decision": {"behavior": "deny",
                                                      "message": "The user denied this from Claude Taskbar."}}})
        # no answer: Claude Code shows its own prompt in the terminal

    elif event == "PreToolUse" and tool == "AskUserQuestion":
        questions = inp.get("questions")
        if not cfg["choices"] or not panel_alive() or not isinstance(questions, list) or not questions:
            return
        update_status(data, state="waiting", detail="Asking you a question")
        q = new_question(data, "choice", f"{project} has a question", "", questions=questions)
        answer = ask_panel(q, timeout)
        if answer and answer.get("action") == "answer" and isinstance(answer.get("answers"), dict):
            update_status(data, state="running", detail="Thinking")
            updated = dict(inp)
            updated["answers"] = answer["answers"]
            emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                         "permissionDecision": "allow",
                                         "updatedInput": updated}})
        else:
            emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                         "permissionDecision": "ask"}})

    elif event == "Stop":
        record(data)
        last = data.get("last_assistant_message") or last_message_from_transcript(
            data.get("transcript_path") or "")
        if not cfg["replies"] or not panel_alive() or not last_line_is_question(last):
            return
        update_status(data, state="waiting", detail="Asked you a question")
        body = last.strip()
        if len(body) > 900:
            body = "…" + body[-900:]
        q = new_question(data, "reply", f"{project} is asking you", body)
        answer = ask_panel(q, timeout)
        text = str((answer or {}).get("text") or "").strip()
        if answer and answer.get("action") == "reply" and text:
            update_status(data, state="running", detail="Working on your reply")
            emit({"decision": "block", "reason": f"The user replied (from Claude Taskbar): {text}"})
        else:
            update_status(data, state="done", detail="Finished")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    if "--ask" in sys.argv:
        handle_ask(data)
    else:
        record(data)


if __name__ == "__main__":
    # Claude Code may cancel a waiting hook (Esc, timeout); exit cleanly so the
    # question disappears from the panel.
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        pass  # never break the user's Claude session
    sys.exit(0)
