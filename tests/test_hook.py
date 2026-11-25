import json, os, subprocess, sys, tempfile, time, threading
repo = sys.argv[1]
home = tempfile.mkdtemp()
base = os.path.join(home, ".claude-taskbar"); os.makedirs(base)
alive = os.path.join(base, "panel.alive")
env = dict(os.environ, HOME=home)
QD, AD = os.path.join(base, "questions"), os.path.join(base, "answers")
def touch():
    open(alive, "w").close()
def hook(event, ask=False, timeout=5, **kw):
    touch()
    data = {"session_id": "sess-1234-abcd", "cwd": "/tmp/proj", "hook_event_name": event, **kw}
    t = time.time()
    r = subprocess.run([sys.executable, os.path.join(repo, "claude_hook.py")] + (["--ask"] if ask else []),
                       input=json.dumps(data), capture_output=True, text=True, env=env, timeout=timeout)
    return r.stdout, round(time.time() - t, 2)
def cards():
    out = []
    for n in sorted(os.listdir(QD)) if os.path.isdir(QD) else []:
        q = json.load(open(os.path.join(QD, n)))
        out.append((q["kind"], q.get("live", False)))
    return out
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, extra)

bash = {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}
# --- unsynced, default non-blocking
out, dt = hook("PermissionRequest", ask=True, **bash)
check("unsynced permission: view-only card, no wait, no output", cards() == [("permission-info", False)] and out == "" and dt < 2, (cards(), dt))
hook("PostToolUse", **bash)
check("allowed in terminal (PostToolUse) clears card", cards() == [])
hook("PermissionRequest", ask=True, **bash)
other = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_id": "a1", "agent_type": "Explore"}
hook("PostToolUse", **other)
check("a different Bash call finishing (e.g. subagent) keeps the card", cards() == [("permission-info", False)], cards())
hook("Notification", message="Claude is waiting for your input")
check("denied in terminal (idle notification) clears card", cards() == [])
askq = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Which DB?", "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}}
out, dt = hook("PreToolUse", ask=True, **askq)
check("AskUserQuestion: choice-info, no output", cards() == [("choice-info", False)] and out == "", (cards(), out))
hook("PostToolUse", **askq)
check("answered picker clears choice card", cards() == [])
out, dt = hook("Stop", ask=True, last_assistant_message="Done. Should I also add tests?")
check("question at end: reply-info, no block", cards() == [("reply-info", False)] and out == "" and dt < 2, (cards(), out))
hook("Notification", message="Claude is waiting for your input")
check("idle reminder after the turn keeps the reply card", cards() == [("reply-info", False)], cards())
hook("UserPromptSubmit", prompt="yes add tests")
check("typing in terminal clears reply card", cards() == [])

# --- synced: channel server running as a sibling (same Claude pid)
ch = subprocess.Popen([sys.executable, os.path.join(repo, "channel_server.py")], stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, text=True, env=env)
time.sleep(0.4)
out, dt = hook("Stop", ask=True, last_assistant_message="Is this blocked by policy?")
check("unverified channel (maybe blocked): reply stays read-only", cards() == [("reply-info", False)] and out == "", cards())
hook("UserPromptSubmit", prompt="next")
out, dt = hook("PermissionRequest", ask=True, **bash)
check("synced permission: read-only card first (in case channel is blocked)", cards() == [("permission-info", False)] and out == "", cards())
ch.stdin.write(json.dumps({"jsonrpc":"2.0","method":"notifications/claude/channel/permission_request","params":{"request_id":"qwert","tool_name":"Bash","description":"Clean","input_preview":"{}"}}) + "\n"); ch.stdin.flush()
time.sleep(0.4)
check("working channel swaps it for a live Yes/No card", cards() == [("permission", True)], cards())
hook("PostToolUse", **bash)
check("terminal answered first: live card cleared by hook", cards() == [])
out, dt = hook("Stop", ask=True, last_assistant_message="Which port should I use?")
check("synced Stop: live reply card, no block", cards() == [("reply", True)] and out == "", (cards(), out))
qid = os.listdir(QD)[0][:-5]
os.makedirs(AD, exist_ok=True)
json.dump({"action": "reply", "text": "8080"}, open(os.path.join(AD, qid + ".json"), "w"))
import select
r, _, _ = select.select([ch.stdout], [], [], 2)
check("forged reply file is NOT delivered into the session", not r)
ch.stdin.close(); ch.wait(3)

print("ALL PASS" if ok else "SOME FAILED")
