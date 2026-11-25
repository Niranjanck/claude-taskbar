import json, os, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
repo = sys.argv[1]
home = tempfile.mkdtemp(); env = dict(os.environ, HOME=home)
SF = os.path.join(home, ".claude-taskbar/sessions/s1.json")
def hook(event, ask=False, **kw):
    d = {"session_id": "s1", "cwd": "/tmp/proj", "hook_event_name": event, **kw}
    subprocess.run([sys.executable, os.path.join(repo, "claude_hook.py")] + (["--ask"] if ask else []),
                   input=json.dumps(d), capture_output=True, text=True, env=env)
def rec(): return json.load(open(SF))
ok = True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name, extra)

hook("UserPromptSubmit", prompt="audit the repo")
for desc in ["Search auth code", "Review tests", "Check docs"]:
    hook("PreToolUse", tool_name="Agent", tool_input={"description": desc, "prompt": "x"})
# three subagents start at the same moment (parallel hooks)
with ThreadPoolExecutor(3) as ex:
    list(ex.map(lambda i: hook("SubagentStart", agent_id=f"a{i}", agent_type="Explore"), range(3)))
r = rec()
check("3 parallel starts counted", len(r["agents"]) == 3, sorted(v["desc"] for v in r["agents"].values()))
hook("SubagentStart", agent_id="internal", agent_type="")
check("typeless internal agent ignored", len(rec()["agents"]) == 3)
hook("PreToolUse", tool_name="Grep", tool_input={"pattern": "x"}, agent_id="a9", agent_type="Plan")
check("missed start picked up from subagent tool call", "a9" in rec()["agents"] and len(rec()["agents"]) == 4)
hook("SubagentStop", agent_id="a0", agent_type="Explore"); hook("SubagentStop", agent_id="a9", agent_type="Plan")
check("stops decrement", len(rec()["agents"]) == 2)
# turn ends; a1 still running in background
hook("Stop", ask=True, last_assistant_message="Started the review.", background_tasks=[{"type": "subagent", "id": "a1"}])
r = rec()
check("Stop keeps background agent only", list(r["agents"]) == ["a1"], list(r["agents"]))
check("row stays running while subagent works", r["state"] == "running" and "1 subagent still working" in r["detail"], (r["state"], r["detail"]))
hook("SubagentStop", agent_id="a1", agent_type="Explore")
r = rec()
check("last stop finishes the task", r["agents"] == {} and r["state"] == "done", (r["state"], r["detail"]))
hook("SubagentStart", agent_id="b1", agent_type="Explore"); hook("Stop", ask=True, last_assistant_message="ok")
check("Stop without background_tasks clears foreground agents", rec()["agents"] == {})
print("ALL PASS" if ok else "SOME FAILED")
