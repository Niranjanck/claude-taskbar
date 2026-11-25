import json, os, subprocess, sys, tempfile, time, glob
repo = sys.argv[1]
home = tempfile.mkdtemp()
base = os.path.join(home, ".claude-taskbar"); os.makedirs(base)
open(os.path.join(base, "panel.alive"), "w").close()
env = dict(os.environ, HOME=home)
p = subprocess.Popen([sys.executable, os.path.join(repo, "channel_server.py")], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, text=True, env=env, cwd=repo)
def send(o): p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
def recv(): return json.loads(p.stdout.readline())
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{}}})
init = recv()["result"]
print("protocol:", init["protocolVersion"], "caps:", init["capabilities"])
time.sleep(0.3)
marker = glob.glob(os.path.join(base, "channels", "*.json"))
print("marker:", [os.path.basename(m) for m in marker])
pid = json.load(open(marker[0]))["claude_pid"]
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","method":"notifications/claude/channel/permission_request",
      "params":{"request_id":"abcde","tool_name":"Bash","description":"List files","input_preview":'{"command": "ls"}'}})
time.sleep(0.3)
qs = os.listdir(os.path.join(base, "questions"))
print("cards:", qs)
q = json.load(open(os.path.join(base, "questions", qs[0])))
print("card:", q["kind"], q["live"], q["title"], "|", q["body"].replace("\n", " / "))
os.makedirs(os.path.join(base, "answers"), exist_ok=True)
json.dump({"action": "allow"}, open(os.path.join(base, "answers", qs[0]), "w"))
import select
r, _, _ = select.select([p.stdout], [], [], 2)
print("forged allow file ignored:", not r)
# AskUserQuestion is ignored, unknown request errors
send({"jsonrpc":"2.0","method":"notifications/claude/channel/permission_request","params":{"request_id":"fghij","tool_name":"AskUserQuestion","description":"","input_preview":""}})
send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
print("tools/list:", recv())
print("AskUserQuestion card:", os.listdir(os.path.join(base, "questions")))
p.stdin.close(); p.wait(timeout=3)
print("marker after exit:", glob.glob(os.path.join(base, "channels", "*.json")))
