# Claude Taskbar

An always-on-top panel for macOS that lists your Claude work in one place and lets you answer Claude's questions without switching windows:

- **Claude Code sessions**, from any terminal or editor, via Claude Code hooks
- **claude.ai chats** open in Chrome, via a small Chrome extension

The panel stays above other windows, appears on every Space, and also shows over full-screen apps.

## Build and share it

Claude Taskbar is a regular Mac app. On a Mac with the Xcode Command Line Tools:

```bash
bash build-app.sh
```

This builds `dist/Claude Taskbar.app` for both Apple silicon and Intel Macs, and packs it into `dist/Claude-Taskbar-1.0.0.dmg`. That disk image is the file you share. It contains the app, a shortcut to Applications, and a "Read Me First" for the person installing it (the same text as `INSTALL.txt`).

To set the version number: `VERSION=1.1.0 bash build-app.sh`.

### Opening without a security warning

By default the app is signed ad hoc, so the first time someone opens it, macOS asks them to confirm (the steps are in `INSTALL.txt`). To remove that step, sign and notarize it with an Apple Developer ID. This needs an Apple Developer account ($99 a year, or your company's):

```bash
xcrun notarytool store-credentials claude-taskbar --apple-id you@example.com --team-id TEAMID   # once
DEVELOPER_ID="Developer ID Application: Your Company (TEAMID)" NOTARY_PROFILE="claude-taskbar" bash build-app.sh
```

## What the app does on first launch

- Shows a welcome window. **Connect to Claude Code** adds the hooks to `~/.claude/settings.json`, after saving a backup. It also offers to open the app at login (macOS 13 and later).
- Copies its hook and bridge scripts to `~/.claude-taskbar/`. It does this on every launch, so updating the app updates them too.
- Runs the local bridge for the Chrome extension itself, and stops it when you quit.
- Removes the launchd agents that older versions created with `install.sh`.

Everything is in the menu bar icon afterwards: **Connect/Disconnect from Claude Code**, **Set Up Chrome Extension…**, **Open at Login**, and **About**.

Python 3 is needed for the hooks. It comes with Apple's Command Line Tools, which most developers already have. If they're missing, the app offers to install them.

## Answering questions in the panel

When Claude needs you, a card appears at the top of the panel with a soft sound. **Claude Code never waits for the panel**: the question is still in the terminal, and whichever side you answer, the other clears.

### Two-way sync: start Claude Code with `claude-tb`

Choose **Two-Way Sync with claude-tb…** in the menu bar icon once (it can add `~/.claude-taskbar/bin` to your PATH). Then run `claude-tb` instead of `claude`; it takes the same arguments. It starts Claude Code with Claude Taskbar's channel (`channel_server.py`), so:

| Claude is asking | Panel | Terminal |
|---|---|---|
| Permission to run a command, edit a file, or use a tool | **Allow** / **Deny** | its usual dialog; first answer wins |
| A question at the end of its reply | type a reply, press Return | type your reply as usual |
| A multiple-choice question | shown read-only | pick in Claude Code's picker |

Channels are a Claude Code research preview: Claude Code shows a warning about development channels on every start (choose "I am using this for local development"), and Team/Enterprise organizations must allow channels.

### Sessions started with plain `claude`

Questions show as read-only cards with **Open Claude Code** (brings the terminal forward) and **Dismiss**.

### claude.ai

When a claude.ai reply ends with a question, type a reply and press Return, or **Open chat** / **Dismiss**.

A "question at the end of its reply" means the last line of Claude's message ends with a question mark.

### Settings

Create `~/.claude-taskbar/config.json` to change what the panel shows:

```json
{
  "permissions": true,
  "choices": true,
  "replies": true,
  "blocking": false,
  "timeout_seconds": 600
}
```

Set any of the first three to `false` to keep that kind of question in the terminal only. `"blocking": true` brings back the old behavior for sessions without `claude-tb`: the panel takes the question and Claude Code waits for it (**Answer in terminal** hands it back; after `timeout_seconds` it goes back by itself).

## The task list

| Dot | Meaning |
|---|---|
| Blue, pulsing | Claude is working |
| Orange | Claude needs you |
| Green | Claude finished its reply |
| Grey | Idle |

- **Subagents:** a Claude Code row shows how many subagents are working on that task (for example "· 3 subagents"); hover over the row to see what each one is doing. A task whose turn has ended stays blue while background subagents are still running.
- **Click a Claude Code row** to bring its terminal or editor app to the front.
- **Click a claude.ai row** to switch to that Chrome tab. macOS asks once for permission to control Chrome.
- **Drag the header** to move the panel; **click the header** to collapse the task list. Question cards stay visible when it's collapsed.
- **Hide the panel** with the round button at the right of the header, or press **Control-Option-C** from any app. Press it again, click the menu bar icon, or open Claude Taskbar from Spotlight to bring it back. A new question from Claude also brings it back.
- The **menu bar icon** shows how many tasks are active, and lets you hide the panel or quit.

## Limits

- **Claude desktop app and Cowork tasks aren't shown.** They don't offer any way for outside tools to read their status or answer for you. Chats you run on claude.ai in Chrome are tracked.
- **claude.ai depends on its page markup.** The extension finds the Stop button, Claude's replies, the message box, and the send button by selectors at the top of `chrome-extension/content.js`. If claude.ai changes its page and something stops working, update those.
- **claude.ai's tappable option buttons aren't shown as options.** Type the option's name as your reply instead, which Claude reads the same way.
- **A reply typed for claude.ai replaces any unsent draft** in that chat's message box.
- **Replies to a claude.ai tab you haven't looked at for a while can take up to a minute,** because Chrome slows down background tabs.
- **Commands are visible on screen.** Permission cards show the full command Claude wants to run. Keep this in mind when sharing your screen.

## Uninstall

In the menu bar icon, choose **Disconnect from Claude Code**, then **Quit**, and delete the app. Or run `bash uninstall.sh`, which also removes `~/.claude-taskbar` and anything older versions installed.

## How it fits together

```
Claude Code ── hooks ──> claude_hook.py ──> sessions/*.json, questions/*.json ─┐
            ── channel ─> channel_server.py ─> questions/*.json <── answers/ ───┤
                                                                               ├── ClaudeTaskbar (panel)
claude.ai tab ── extension ──> web_bridge.py (127.0.0.1:47823) ──> web/*.json ─┤
              <── reply ───── web_bridge.py <── web-out/*.json <───────────────┘
```

All files live in `~/.claude-taskbar/`. The panel touches `panel.alive` twice a second; hooks and the channel only show cards while that file is fresh.
