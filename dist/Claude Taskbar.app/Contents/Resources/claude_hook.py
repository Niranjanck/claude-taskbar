#!/usr/bin/env python3
"""Claude Code hook for Claude Taskbar.

Two modes:
  claude_hook.py          Records the session's live state (fast, never blocks), and
                          clears question cards once they're answered in Claude Code.
  claude_hook.py --ask    Shows Claude's questions in the panel:
                          - PermissionRequest
                          - PreToolUse(AskUserQuestion)
                          - Stop, when Claude's last line is a question

Claude Code never waits on the panel (unless config.json has "blocking": true):
  - Sessions started with `claude-tb` are synced through channel_server.py: permission
    prompts and replies can be answered in the panel or the terminal, first answer wins.
  - Other sessions get view-only cards with an "Open Claude Code" button.
Nothing is printed except the decision JSON Claude Code reads.
"""
import fcntl
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
CHANNELS_DIR = os.path.join(BASE, "channels")
ALIVE_FILE = os.path.join(BASE, "panel.alive")
CONFIG_FILE = os.path.join(BASE, "config.json")
MAX_BODY = 20_000   # the panel shows the full text in a scrolling box
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "env", "python3", "python"}
DEFAULT_CONFIG = {
    "permissions": True,      # answer permission prompts in the panel
    "choices": True,          # answer AskUserQuestion in the panel
    "replies": True,          # reply when Claude ends its turn with a question
    "timeout_seconds": 600,   # blocking mode: then hand the question back to the terminal
    "blocking": False,        # old behavior: Claude Code waits for the panel's answer
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


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def synced_pid():
    """The Claude Code pid if this session's channel is known to work, else None.

    Starting with claude-tb isn't proof: an organization can block channels, and then
    Claude Code drops channel messages silently. The channel server marks itself
    verified once Claude Code has relayed a permission prompt to it."""
    pid = claude_pid()
    marker = read_json(os.path.join(CHANNELS_DIR, f"{pid}.json"))
    if isinstance(marker, dict) and marker.get("verified") and pid_alive(marker.get("server_pid")):
        return pid
    return None


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


# ---------- subagents ----------
#
# SubagentStart / SubagentStop carry agent_id and agent_type, and tool events fired
# inside a subagent carry its agent_id too, so a missed start is picked up from the
# subagent's next tool call and a missed stop expires after a quiet spell.

AGENT_TOOLS = ("Agent", "Task")
AGENT_QUIET_LIMIT = 20 * 60


def track_agents(data, rec):
    event = data.get("hook_event_name", "")
    now = time.time()
    agents = rec.get("agents") if isinstance(rec.get("agents"), dict) else {}
    queue = rec.get("agent_queue") if isinstance(rec.get("agent_queue"), list) else []
    aid = str(data.get("agent_id") or "")
    atype = str(data.get("agent_type") or "")

    # The Agent tool's description names the subagent's job; SubagentStart doesn't.
    if event == "PreToolUse" and data.get("tool_name") in AGENT_TOOLS:
        inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        queue = (queue + [" ".join(str(inp.get("description") or "").split())[:60]])[-20:]

    if event == "SubagentStart":
        if aid and atype:   # internal helper agents have no type; don't count them
            desc = (agents.get(aid) or {}).get("desc") or (queue.pop(0) if queue else "") or atype
            agents[aid] = {"type": atype, "desc": desc, "seen": now}
    elif event == "SubagentStop":
        agents.pop(aid, None)
    elif aid in agents:
        agents[aid]["seen"] = now
    elif aid and atype and event in ("PreToolUse", "PostToolUse"):
        agents[aid] = {"type": atype, "desc": atype, "seen": now}

    if event == "Stop" and not aid:
        # Foreground subagents are done when the turn ends; background ones are listed.
        bg = data.get("background_tasks")
        listed = json.dumps(bg) if bg else ""
        agents = {k: v for k, v in agents.items() if k in listed}

    agents = {k: v for k, v in agents.items() if now - float(v.get("seen") or 0) < AGENT_QUIET_LIMIT}
    rec["agents"] = agents
    rec["agent_queue"] = queue

    waiting_note = "subagent" in str(rec.get("detail") or "") and "still working" in str(rec.get("detail") or "")
    if agents and event == "Stop":
        n = len(agents)
        rec["state"] = "running"
        rec["detail"] = f"{n} subagent{'s' if n != 1 else ''} still working"
    elif not agents and event == "SubagentStop" and waiting_note:
        rec["state"] = "done"
        rec["detail"] = "Finished"


# ---------- session status ----------

def update_status(data, **fields):
    sid = str(data.get("session_id") or "")
    if not sid or "/" in sid:
        return
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    # Hooks for one session run in parallel (several subagents at once), so the
    # read-modify-write of its record is serialized.
    with open(os.path.join(SESSIONS_DIR, sid + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _update_status(data, sid, fields)


def _update_status(data, sid, fields):
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
    track_agents(data, rec)
    write_atomic(SESSIONS_DIR, sid + ".json", rec)


# ---------- clearing cards answered in Claude Code ----------
#
# Claude Code doesn't tell the panel (or a channel) when you answer in the terminal,
# so the hooks that follow an answer remove the matching cards.

def clear_questions(data, tool=None, before=None):
    """tool: remove the oldest permission card for that tool (it was just allowed).
    Otherwise remove every pending card of this session created before `before`."""
    try:
        names = [n for n in os.listdir(QUESTIONS_DIR) if n.endswith(".json")]
    except OSError:
        return
    if not names:
        return
    sid = str(data.get("session_id") or "")
    pid = None
    matches = []
    for name in names:
        path = os.path.join(QUESTIONS_DIR, name)
        q = read_json(path)
        if not isinstance(q, dict) or q.get("hook_pid"):   # blocking cards clean up after themselves
            continue
        mine = sid and q.get("session_id") == sid
        if not mine and q.get("claude_pid"):
            if pid is None:
                pid = claude_pid()
            mine = q.get("claude_pid") == pid
        if not mine:
            continue
        created = float(q.get("created") or 0)
        if tool is not None:
            if q.get("tool") == tool and q.get("kind") in ("permission", "permission-info"):
                matches.append((created, path))
            elif tool == "AskUserQuestion" and q.get("kind") == "choice-info":
                matches.append((created, path))
        elif before is None or created < before:
            matches.append((created, path))
    if tool is not None and tool != "AskUserQuestion":
        matches = sorted(matches)[:1]
    for _, path in matches:
        try:
            os.remove(path)
        except OSError:
            pass


def record(data):
    sid = str(data.get("session_id") or "")
    if not sid or "/" in sid:
        return
    event = data.get("hook_event_name", "")
    path = os.path.join(SESSIONS_DIR, sid + ".json")

    if event in ("SessionEnd", "UserPromptSubmit"):
        clear_questions(data)
    elif event == "PostToolUse":
        clear_questions(data, tool=str(data.get("tool_name") or ""))
    elif event == "Notification" and "waiting for your input" in str(data.get("message") or "").lower():
        clear_questions(data)   # e.g. a permission you denied in the terminal

    if event == "SessionEnd":
        for p in (path, os.path.join(SESSIONS_DIR, sid + ".lock")):
            try:
                os.remove(p)
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
    elif event in ("SubagentStart", "SubagentStop"):
        update_status(data)


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


def show_card(data, kind, title, body, **extra):
    """A card the panel shows without Claude Code waiting for it."""
    q = new_question(data, kind, title, body, **extra)
    q.pop("hook_pid", None)
    q.setdefault("claude_pid", claude_pid())
    write_atomic(QUESTIONS_DIR, q["id"] + ".json", q)


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


def permission_description(tool, inp):
    """The one-line summary Claude Code shows above the command."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Bash":
        return " ".join(str(inp.get("description") or "").split())[:300]
    if tool in ("Edit", "MultiEdit", "Write", "Read", "NotebookEdit"):
        return str(inp.get("file_path") or inp.get("notebook_path") or "")[:300]
    return ""


def diff_lines(old, new):
    return "\n".join(["- " + l for l in str(old).splitlines()] + ["+ " + l for l in str(new).splitlines()])


def permission_body(tool, inp):
    """What Claude Code shows in the box: the command exactly as written, or the change."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Bash":
        body = str(inp.get("command", ""))
    elif tool == "Edit":
        body = diff_lines(inp.get("old_string", ""), inp.get("new_string", ""))
    elif tool == "MultiEdit":
        edits = inp.get("edits") if isinstance(inp.get("edits"), list) else []
        body = "\n\n".join(diff_lines(e.get("old_string", ""), e.get("new_string", ""))
                            for e in edits if isinstance(e, dict))
    elif tool == "Write":
        body = str(inp.get("content", ""))
    elif tool == "Read":
        body = str(inp.get("file_path", ""))
    elif tool == "NotebookEdit":
        body = str(inp.get("notebook_path", ""))
    elif tool == "WebFetch":
        body = str(inp.get("url", ""))
    else:
        body = json.dumps(inp, ensure_ascii=False)
    return body[:MAX_BODY]


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
    blocking = bool(cfg.get("blocking"))

    if event == "PermissionRequest":
        # AskUserQuestion is shown from PreToolUse; here it falls through to the terminal.
        if tool == "AskUserQuestion" or not cfg["permissions"] or not panel_alive():
            return
        update_status(data, state="waiting", detail="Needs your permission")
        title, body = permission_title(project, tool), permission_body(tool, inp)
        desc = permission_description(tool, inp)
        if not blocking:
            # If this session has a working channel, it swaps this for a live Yes/No card.
            show_card(data, "permission-info", title, body, tool=tool, description=desc)
            return
        q = new_question(data, "permission", title, body, tool=tool, description=desc)
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
        title = f"{project} has a question"
        if not blocking:
            # Claude Code only takes this answer in its own picker, so the card is view-only.
            show_card(data, "choice-info", title, "", questions=questions, tool=tool)
            return
        q = new_question(data, "choice", title, "", questions=questions)
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
        clear_questions(data, before=time.time())   # the turn is over; nothing is pending
        record(data)
        last = data.get("last_assistant_message") or last_message_from_transcript(
            data.get("transcript_path") or "")
        if not cfg["replies"] or not panel_alive() or not last_line_is_question(last):
            return
        update_status(data, state="waiting", detail="Asked you a question")
        body = last.strip()
        if len(body) > MAX_BODY:
            body = "…" + body[-MAX_BODY:]
        title = f"{project} is asking you"
        pid = synced_pid()
        if pid:
            # The channel delivers a reply typed in the panel; typing in the terminal works too.
            qid = f"ch-{pid}-r-{uuid.uuid4().hex[:10]}"
            show_card(data, "reply", title, body, id=qid, live=True, claude_pid=pid)
            return
        if not blocking:
            show_card(data, "reply-info", title, body)
            return
        q = new_question(data, "reply", title, body)
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
