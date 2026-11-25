"""Receiving answers from the Claude Taskbar panel, and only from the panel.

The panel never writes answers to files. It sends them over a Unix socket,
~/.claude-taskbar/panel.sock, and every client checks the process on the other end:
the kernel reports the peer's pid (LOCAL_PEERPID), and that pid's executable must be
the installed app, /Applications/Claude Taskbar.app (or ~/Applications).

What this does and doesn't stop: it rejects a forged answer file and a stand-in
program running from anywhere else. It is NOT a code-signature check: the build is
ad-hoc signed, so a same-user program that overwrites the installed app could still
pass. Answers also carry a hash of what the card showed ("shown"), so editing a
pending card's text makes the answer invalid (see shown_hash).

Protocol: newline-delimited JSON.
  client -> panel  {"wait": "<question id>"}      one answer, then the panel closes
                   {"subscribe": "<id prefix>"}   every answer whose id starts with it
  panel -> client  {"id": "<question id>", "answer": {...}}
"""
import ctypes
import hashlib
import json
import os
import socket
import struct
import time

SOCKET_PATH = os.path.expanduser("~/.claude-taskbar/panel.sock")
APP_BINARIES = {
    "/Applications/Claude Taskbar.app/Contents/MacOS/ClaudeTaskbar",
    os.path.expanduser("~/Applications/Claude Taskbar.app/Contents/MacOS/ClaudeTaskbar"),
}
SOL_LOCAL = 0
LOCAL_PEERPID = 0x002

try:
    _libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    _libc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
except OSError:
    _libc = None


def process_path(pid):
    if not _libc or pid <= 0:
        return ""
    buf = ctypes.create_string_buffer(4096)
    n = _libc.proc_pidpath(pid, buf, 4096)
    return buf.value.decode("utf-8", "replace") if n > 0 else ""


def is_panel(sock):
    """True if the peer of this connected Unix socket is the Claude Taskbar app."""
    try:
        pid = struct.unpack("i", sock.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4))[0]
    except OSError:
        return False
    return process_path(pid) in APP_BINARIES


def shown_hash(question):
    """What the panel showed for a card: title, summary line, body, and any multiple-choice
    questions with their option labels. Must match ClaudeTaskbar.swift's shownHash."""
    parts = [str(question.get("title") or ""), str(question.get("description") or ""),
             str(question.get("body") or "")]
    for q in question.get("questions") or []:
        if isinstance(q, dict):
            parts.append(str(q.get("question") or ""))
            for opt in q.get("options") or []:
                if isinstance(opt, dict):
                    parts.append(str(opt.get("label") or ""))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def answer_matches(question, answer):
    """True if the answer was given to exactly this card's content."""
    return isinstance(answer, dict) and answer.get("shown") == shown_hash(question)


def connect(request, timeout=2.0):
    """A verified connection to the panel with `request` sent, or None."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET_PATH)
        if not is_panel(s):
            s.close()
            return None
        s.sendall((json.dumps(request) + "\n").encode())
        return s
    except OSError:
        s.close()
        return None


def read_messages(sock, still_wanted, poll=1.0):
    """Yields messages from the panel until it closes or still_wanted() is False."""
    sock.settimeout(poll)
    buf = b""
    while still_wanted():
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if isinstance(msg, dict) and isinstance(msg.get("answer"), dict):
                yield msg


def wait_for(qid, still_wanted, retry_delay=0.5):
    """Waits for the panel's answer to one question. Reconnects if the panel restarts.
    Returns the answer dict, or None once still_wanted() turns False."""
    while still_wanted():
        sock = connect({"wait": qid})
        if sock is None:
            time.sleep(retry_delay)
            continue
        try:
            for msg in read_messages(sock, still_wanted):
                if msg.get("id") == qid:
                    return msg["answer"]
        finally:
            sock.close()
        time.sleep(retry_delay)
    return None


def subscribe(prefix, on_message, retry_delay=1.0):
    """Calls on_message(id, answer) for every answer whose id starts with prefix. Never returns."""
    while True:
        sock = connect({"subscribe": prefix})
        if sock is None:
            time.sleep(retry_delay)
            continue
        try:
            for msg in read_messages(sock, lambda: True):
                if str(msg.get("id", "")).startswith(prefix):
                    try:
                        on_message(msg["id"], msg["answer"])
                    except Exception:
                        pass
        finally:
            sock.close()
        time.sleep(retry_delay)
