#!/usr/bin/env python3
"""Localhost bridge between the Chrome extension and the Claude Taskbar panel.

Listens on 127.0.0.1:47823 only.
  POST /tab    the extension reports a claude.ai tab's state -> web/<tab>.json
  POST /close  a tab closed -> removes web/<tab>.json
  GET  /wait   long-poll: the extension collects replies typed in the panel
               (the panel drops them in web-out/)

Every request must carry the X-Claude-Taskbar header. Ordinary web pages can't
send a custom header cross-origin without a CORS preflight, which this server
never approves, so only the extension can talk to it.
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.expanduser("~/.claude-taskbar")
WEB_DIR = os.path.join(BASE, "web")
OUT_DIR = os.path.join(BASE, "web-out")
PORT = 47823
ID_RE = re.compile(r"^tab\d{1,12}$")
STATES = {"running", "done", "idle"}
WAIT_SECONDS = 25


def take_outgoing():
    msgs = []
    for name in sorted(os.listdir(OUT_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(OUT_DIR, name)
        try:
            with open(path) as f:
                msg = json.load(f)
            os.remove(path)
        except Exception:
            continue
        if ID_RE.match(str(msg.get("id", ""))) and str(msg.get("text", "")).strip():
            msgs.append({"id": msg["id"], "text": str(msg["text"])[:8000]})
    return msgs


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body=b""):
        self.send_response(code)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("X-Claude-Taskbar") != "1":
            return self.reply(403)
        if self.path != "/wait":
            return self.reply(404)
        deadline = time.time() + WAIT_SECONDS
        while time.time() < deadline:
            msgs = take_outgoing()
            if msgs:
                return self.reply(200, json.dumps(msgs).encode())
            time.sleep(0.3)
        self.reply(204)

    def do_POST(self):
        if self.headers.get("X-Claude-Taskbar") != "1":
            return self.reply(403)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 16384:
            return self.reply(400)
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            return self.reply(400)

        tab_id = str(data.get("id", ""))
        if not ID_RE.match(tab_id):
            return self.reply(400)
        path = os.path.join(WEB_DIR, tab_id + ".json")

        if self.path == "/close":
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return self.reply(204)
        if self.path != "/tab":
            return self.reply(404)

        url = str(data.get("url", ""))
        if not url.startswith("https://claude.ai/"):
            return self.reply(400)
        state = data.get("state")
        rec = {
            "id": tab_id,
            "source": "web",
            "state": state if state in STATES else "idle",
            "title": str(data.get("title", ""))[:160],
            "url": url[:500],
            "last": str(data.get("last", ""))[-1000:],
            "updated": time.time(),
        }
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, path)
        self.reply(204)


def main():
    for d in (WEB_DIR, OUT_DIR):
        os.makedirs(d, exist_ok=True)
        for name in os.listdir(d):  # tab ids don't survive a restart
            if name.endswith(".json"):
                os.remove(os.path.join(d, name))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
