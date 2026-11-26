"""statusline.py must keep the saved usage when a session sends none (new session,
first response not in yet) or only one window."""
import json, os, subprocess, sys, tempfile
repo = sys.argv[1]
home = tempfile.mkdtemp(); env = dict(os.environ, HOME=home)
usage = os.path.join(home, ".claude-taskbar", "usage.json")
ok = True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name, extra)
def render(data):
    return subprocess.run([sys.executable, os.path.join(repo, "statusline.py")], input=json.dumps(data),
                          capture_output=True, text=True, env=env).stdout
both = {"five_hour": {"used_percentage": 30, "resets_at": 9999999999}, "seven_day": {"used_percentage": 12, "resets_at": 9999999999}}
out = render({"model": {"display_name": "Opus"}, "rate_limits": both})
check("saves usage and prints it", json.load(open(usage))["rate_limits"] == both and "session 30%" in out, out)
render({"model": {"display_name": "Opus"}})   # a new session: no rate_limits yet
check("a session without usage doesn't wipe it", json.load(open(usage))["rate_limits"] == both)
render({"rate_limits": {"seven_day": {"used_percentage": 13, "resets_at": 9999999999}}})
saved = json.load(open(usage))["rate_limits"]
check("one window updates, the other is kept", saved["seven_day"]["used_percentage"] == 13 and saved["five_hour"]["used_percentage"] == 30, saved)
print("ALL PASS" if ok else "SOME FAILED")
