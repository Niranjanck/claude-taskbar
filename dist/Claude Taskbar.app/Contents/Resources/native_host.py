#!/usr/bin/python3
"""Chrome native messaging host for the Claude Taskbar extension.

Replaces the old localhost web server: there's no network port. Chrome starts this
program itself, only for the extension listed in the host manifest's
allowed_origins, and talks to it over stdin/stdout (4-byte length + JSON).

  extension -> host  {"type": "tab", "id": "tab12", "state", "title", "url", "last"}
                     {"type": "close", "id": "tab12"}
  host -> extension  {"type": "reply", "id": "tab12", "text": "..."}

Tab state goes to ~/.claude-taskbar/web/<id>.json for the panel. Replies typed in
the panel arrive only over the panel's socket, verified to be the Claude Taskbar
app (panel_link.py), never from files.
"""
import json
import os
import re
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_link  # noqa: E402

BASE = os.path.expanduser("~/.claude-taskbar")
WEB_DIR = os.path.join(BASE, "web")
ID_RE = re.compile(r"^tab\d{1,12}$")
STATES = {"running", "done", "idle"}
MAX_MESSAGE = 1024 * 1024
out_lock = threading.Lock()


def send(msg):
    data = json.dumps(msg).encode()
    with out_lock:
        sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
        sys.stdout.buffer.flush()


def read_message():
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    (n,) = struct.unpack("<I", header)
    if n > MAX_MESSAGE:
        return None
    data = sys.stdin.buffer.read(n)
    try:
        return json.loads(data)
    except Exception:
        return {}


def write_tab(msg):
    tab_id = str(msg.get("id", ""))
    url = str(msg.get("url", ""))
    if not ID_RE.match(tab_id) or not url.startswith("https://claude.ai/"):
        return
    state = msg.get("state")
    rec = {
        "id": tab_id,
        "source": "web",
        "state": state if state in STATES else "idle",
        "title": str(msg.get("title", ""))[:160],
        "url": url[:500],
        "last": str(msg.get("last", ""))[-12_000:],
        "updated": time.time(),
    }
    fd, tmp = tempfile.mkstemp(dir=WEB_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, os.path.join(WEB_DIR, tab_id + ".json"))


def close_tab(msg):
    tab_id = str(msg.get("id", ""))
    if ID_RE.match(tab_id):
        try:
            os.remove(os.path.join(WEB_DIR, tab_id + ".json"))
        except FileNotFoundError:
            pass


def on_panel_reply(qid, answer):
    tab_id = qid[len("web:"):]
    text = str(answer.get("text") or "").strip()
    if ID_RE.match(tab_id) and text:
        send({"type": "reply", "id": tab_id, "text": text[:8000]})


def main():
    os.makedirs(WEB_DIR, mode=0o700, exist_ok=True)
    threading.Thread(target=panel_link.subscribe, args=("web:", on_panel_reply), daemon=True).start()
    while True:
        msg = read_message()
        if msg is None:   # Chrome closed the connection
            return
        if not isinstance(msg, dict):
            continue
        try:
            if msg.get("type") == "tab":
                write_tab(msg)
            elif msg.get("type") == "close":
                close_tab(msg)
        except Exception:
            pass


if __name__ == "__main__":
    main()
