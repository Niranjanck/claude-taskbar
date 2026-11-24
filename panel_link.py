"""Receiving answers from the Claude Taskbar panel, and only from the panel.

The panel never writes answers to files (anything running as you could forge
those). It sends them over a Unix socket, ~/.claude-taskbar/panel.sock, and every
client checks that the process on the other end is the Claude Taskbar app: the
kernel reports the peer's pid (LOCAL_PEERPID), and that pid's executable must be
Claude Taskbar.app's binary. A stand-in socket from any other program is refused.

Protocol: newline-delimited JSON.
  client -> panel  {"wait": "<question id>"}      one answer, then the panel closes
                   {"subscribe": "<id prefix>"}   every answer whose id starts with it
  panel -> client  {"id": "<question id>", "answer": {...}}
"""
import ctypes
import json
import os
import socket
import struct
import time

SOCKET_PATH = os.path.expanduser("~/.claude-taskbar/panel.sock")
APP_BINARY_SUFFIX = "/Claude Taskbar.app/Contents/MacOS/ClaudeTaskbar"
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
    return process_path(pid).endswith(APP_BINARY_SUFFIX)


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
