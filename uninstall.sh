#!/bin/bash
# Removes Claude Taskbar: its Claude Code hooks, the app, older launchd agents, and ~/.claude-taskbar.
set -uo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
UID_NUM="$(id -u)"
pkill -x ClaudeTaskbar 2>/dev/null
for label in com.claudetaskbar.widget com.claudetaskbar.bridge; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
/usr/bin/python3 "$SRC/install_hooks.py" --remove
[ -f "$HOME/.zshrc" ] && sed -i "" "/# Claude Taskbar (claude-tb)$/d" "$HOME/.zshrc"
for b in "Google/Chrome" "Google/Chrome Beta" "Chromium" "BraveSoftware/Brave-Browser"; do
  rm -f "$HOME/Library/Application Support/$b/NativeMessagingHosts/com.claudetaskbar.bridge.json"
done
rm -rf "$HOME/.claude-taskbar" "$HOME/Applications/Claude Taskbar.app" "/Applications/Claude Taskbar.app"
echo "Claude Taskbar removed. Remove the Chrome extension from chrome://extensions, and"
echo "Claude Taskbar from System Settings > General > Login Items if it's still listed."
