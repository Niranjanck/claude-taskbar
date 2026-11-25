#!/usr/bin/env python3
"""Claude Code status line for Claude Taskbar.

Claude Code gives plan usage (rate_limits: five_hour, seven_day, spend_limit) only to
the status line command, on stdin. This saves those numbers for the panel's usage bar
in ~/.claude-taskbar/usage.json, and prints a short status line for the terminal.

Only rate_limits, the session's cost estimate and a timestamp are kept; nothing else
from the input is written.
"""
import json
import os
import sys
import tempfile
import time

BASE = os.path.expanduser("~/.claude-taskbar")
USAGE_FILE = os.path.join(BASE, "usage.json")


def pct(window):
    try:
        return float(window.get("used_percentage"))
    except (AttributeError, TypeError, ValueError):
        return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    limits = data.get("rate_limits") if isinstance(data.get("rate_limits"), dict) else {}
    cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}

    # Written even without limits, so the panel can tell "not on a plan that reports
    # usage" from "no session has drawn a status line yet".
    if True:
        rec = {"rate_limits": limits, "updated": time.time(),
               "session_cost_usd": cost.get("total_cost_usd")}
        try:
            os.makedirs(BASE, mode=0o700, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=BASE, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(rec, f)
            os.replace(tmp, USAGE_FILE)
        except OSError:
            pass

    parts = []
    model = (data.get("model") or {}).get("display_name") if isinstance(data.get("model"), dict) else None
    if model:
        parts.append(str(model))
    five, week = pct(limits.get("five_hour") or {}), pct(limits.get("seven_day") or {})
    if five is not None:
        parts.append(f"session {five:.0f}%")
    if week is not None:
        parts.append(f"week {week:.0f}%")
    if five is None and week is None and isinstance(cost.get("total_cost_usd"), (int, float)):
        parts.append(f"${cost['total_cost_usd']:.2f}")
    sys.stdout.write(" · ".join(parts))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
