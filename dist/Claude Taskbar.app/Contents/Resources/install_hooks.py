#!/usr/bin/env python3
"""Add (or with --remove, take out) the Claude Taskbar hooks in ~/.claude/settings.json.

Keeps every other setting and hook you already have, and writes a timestamped
backup before changing anything. Safe to run more than once.
"""
import json
import os
import shutil
import sys
import time

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK = os.path.expanduser("~/.claude-taskbar/claude_hook.py")
MARKER = "/.claude-taskbar/claude_hook.py"
STATUS = f'"{sys.executable}" "{HOOK}"'
ASK = f'"{sys.executable}" "{HOOK}" --ask'
ASK_TIMEOUT = 900  # seconds; the hook itself hands back to the terminal sooner


def group(command, timeout, matcher=None):
    g = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
    return {"matcher": matcher, **g} if matcher is not None else g


OURS = {
    "SessionStart": [group(STATUS, 5)],
    "UserPromptSubmit": [group(STATUS, 5)],
    "PreToolUse": [group(STATUS, 5, "*"), group(ASK, ASK_TIMEOUT, "AskUserQuestion")],
    "PostToolUse": [group(STATUS, 5, "*")],
    "PermissionRequest": [group(ASK, ASK_TIMEOUT, "*")],
    "Notification": [group(STATUS, 5)],
    "Stop": [group(ASK, ASK_TIMEOUT)],
    "SessionEnd": [group(STATUS, 5)],
}


def strip_ours(groups):
    kept = []
    for g in groups:
        hooks = g.get("hooks", [])
        rest = [h for h in hooks if MARKER not in str(h.get("command", ""))]
        if rest or not hooks:
            g["hooks"] = rest
            kept.append(g)
    return kept


def main():
    remove = "--remove" in sys.argv
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)

    settings = {}
    if os.path.exists(SETTINGS):
        with open(SETTINGS) as f:
            settings = json.load(f)
        backup = f"{SETTINGS}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(SETTINGS, backup)
        print(f"Backed up settings to {backup}")

    hooks = settings.setdefault("hooks", {})
    for event in set(hooks) | set(OURS):
        groups = strip_ours(hooks.get(event, []))
        if not remove:
            groups += OURS.get(event, [])
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)

    with open(SETTINGS, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("Removed Claude Taskbar hooks." if remove else f"Installed hooks in {SETTINGS}")


if __name__ == "__main__":
    main()
