# Claude Taskbar

An always-on-top macOS panel that lists every Claude Code session and claude.ai chat, and shows Claude's questions (permission prompts, multiple-choice questions, questions at the end of a reply) so they can be answered without hunting for the right terminal. Tagline: "Every Claude task, always on top." It's an independent tool, not made by or affiliated with Anthropic; keep that note in user-facing copy.

## How it fits together

```
Claude Code ── hooks ──> claude_hook.py ──> sessions/*.json, questions/*.json ──┐
            <── answer ── claude_hook.py <══ panel.sock (verified peer) ═══════┤
            ── channel ─> channel_server.py ─> questions/*.json               ├── ClaudeTaskbar.app (panel)
            <── verdict ─ channel_server.py <══ panel.sock ════════════════════┤
claude.ai tab ── extension ── native messaging ─> native_host.py ─> web/*.json ┤
              <── reply ───────────────────────── native_host.py <══ panel.sock┘
```

State goes through files in `~/.claude-taskbar/` (owner-only, 0700). **Answers never do**: the panel sends them only over the Unix socket `panel.sock` (0600), and every client (`panel_link.py`) checks the peer with `LOCAL_PEERPID` + `proc_pidpath` that it is `…/Claude Taskbar.app/Contents/MacOS/ClaudeTaskbar` before trusting it. A forged file or a stand-in socket can't approve a permission prompt or inject a reply. (A process running as the user can still do harm other ways, e.g. by editing the hook scripts or Claude Code settings; this closes the one-line-file-write path.)

| Path | Written by | Read by |
|---|---|---|
| `sessions/<session_id>.json` | hooks | panel |
| `questions/<id>.json` | hooks, channel server | panel |
| `panel.sock` | panel (Unix socket, answers only) | blocking hook, channel server, native host |
| `web/<tab>.json` | native host | panel |
| `channels/<claude_pid>.json` | channel server (marker: this session is synced) | hooks |
| `panel.alive` | panel, touched every 0.5 s | hooks (only wait when fresh) |
| `config.json` | user | hooks |
| `mcp.json`, `bin/claude-tb` | app, on every launch | the `claude-tb` launcher |

Writes are atomic (temp file + rename). Ids are validated before use as file names.

## Files

- `ClaudeTaskbar.swift`: the whole app, one file, AppKit, no dependencies. Non-activating `NSPanel` (floating, all Spaces, full-screen auxiliary), menu bar status item, Carbon global hot key Control-Option-C, first-launch setup (`enum Setup`), `AnswerServer` (panel.sock), follow-the-mouse across screens, single-instance enforcement. Built with `swiftc`, not Xcode.
- `claude_hook.py`: Claude Code hook. Plain mode records session state on SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Notification, Stop, SessionEnd. `--ask` mode handles PermissionRequest, PreToolUse(AskUserQuestion) and Stop. Never prints anything except decision JSON (stdout on UserPromptSubmit becomes model context).
- `install_hooks.py`: adds/removes hooks in `~/.claude/settings.json`, keeps other hooks, backs up first, idempotent. Hook commands are identified by the marker `/.claude-taskbar/claude_hook.py`.
- `channel_server.py`: MCP channel server (stdio JSON-RPC, stdlib only) for two-way sync. Declares `claude/channel` and `claude/channel/permission`; receives `notifications/claude/channel/permission_request`, writes a card, sends `notifications/claude/channel/permission` with the panel's verdict; delivers panel replies as `notifications/claude/channel`. Answers `protocolVersion` 2025-06-18 or older (Claude Code won't register a channel on 2026-07-28).
- `panel_link.py`: client side of `panel.sock` with the peer check; used by the hook, channel server and native host.
- `native_host.py`, `chrome-extension/`: claude.ai tab status and replies over Chrome native messaging (`com.claudetaskbar.bridge`, manifest in `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`, `allowed_origins` = the extension only). The manifest's `key` fixes the extension ID to `hdlholpbfnbghdhgbdaafdjfnnickejb`; don't change it. Only `https://claude.ai/` URLs are accepted. Replaced the localhost HTTP bridge (`web_bridge.py`, port 47823) in 1.2.0, which any local process or other account could reach.
- `build-app.sh`: universal (arm64 + x86_64) app bundle in `dist/`, ad-hoc signed unless `DEVELOPER_ID` is set, plus a `.dmg` and a `.pkg` (non-relocatable, installs to /Applications). `VERSION`, `INSTALLER_ID`, `NOTARY_PROFILE` env vars.
- `site/`: static landing page for Cloudflare Pages (project `claude-task-tracker`, https://claude-task-tracker.pages.dev). Styled after Claude's design (ivory, warm grays, serif headings, dark buttons). Download buttons point to `Claude-Taskbar-1.2.0.dmg` in the same folder.
- `logo/`, `AppIcon.icns`: icon is a dark rounded square with a floating card, three status rows (blue working, orange needs you, green finished) and a white diamond pinned top-right. The menu bar glyph is that diamond as a template image.

## How questions work (important design decisions)

Claude Code runs the PermissionRequest hook *before* showing its own prompt and waits for it. The first design made the hook wait for the panel, which left the terminal unusable while a card was open. Current design: **Claude Code never waits on the panel**, and whichever side answers, the other clears.

- Sessions started with `claude-tb` (two-way sync): `claude-tb` runs `claude --mcp-config ~/.claude-taskbar/mcp.json --dangerously-load-development-channels server:claude-taskbar`. Permission prompts are relayed by the channel, so both the terminal dialog and the panel card are live; Claude Code applies the first answer. Replies to end-of-turn questions typed in the panel go in through the channel.
- Other sessions (plain `claude`, the Claude app's Code tab): view-only cards (`permission-info`, `choice-info`, `reply-info`) with an "Open Claude Code" button.
- Multiple-choice (AskUserQuestion) is always view-only: Claude Code only accepts that answer in its picker.
- Clearing cards answered in Claude Code (`clear_questions` in the hook): PostToolUse with the same tool name (allowed), UserPromptSubmit / Stop / SessionEnd (anything pending), Notification containing "waiting for your input" (a denied permission). Claude Code doesn't tell a channel when the terminal answered first, so the hooks do it.
- Non-blocking cards carry `claude_pid` instead of `hook_pid`; the panel removes cards whose process is gone.
- `config.json` `"blocking": true` restores the old behavior (panel takes the question, Claude Code waits, "Answer in terminal" hands it back, 600 s timeout).
- Tested for real on 2026-11-24 in a Claude Team org: Claude Code printed "--dangerously-load-development-channels blocked by org policy" (channelsEnabled not set), plus "server:claude-taskbar · no MCP server configured with that name" (a server from `--mcp-config` may not count for `server:`; untested whether `.mcp.json` or user-scope config fixes it). Because a blocked channel drops messages silently, the hook always writes the read-only card and the channel only swaps it for a live card after Claude Code has relayed a prompt to it (`verified` in the channel marker); replies go through the channel only once verified.
- Hooks run without a controlling terminal (checked in Warp and VS Code), so typing answers into the terminal with TIOCSTI is not possible.
- Channels are a research preview. Custom channels need the development flag, which shows a warning screen on every start. Team/Enterprise orgs must enable channels (`channelsEnabled`). Docs: https://code.claude.com/docs/en/channels-reference

## Other behavior to preserve

- Task title: Claude Code's own session title from the transcript (`custom-title` from /rename > `ai-title` > `agent-name` > `summary`), falling back to the first prompt. Subtitle is the latest prompt (markup like `<agent-message>` stripped), third line is "project: what Claude is doing".
- Removing a task hides it until that session gets a new prompt (`prompt_count` changes); a removed claude.ai chat returns when a new reply starts.
- The panel only takes keyboard focus when a text field is clicked, and hands focus back after answering.
- Only one copy runs; launching another quits the older one. Old launchd agents from the former `install.sh` are removed on launch (exact path comparison).
- Distribution: unsigned builds trigger Gatekeeper; managed Macs may not allow "Open Anyway". The plan for colleagues is the `.pkg` deployed through MDM. No Developer ID certificate is available yet.

## Known limits

- Cowork can't be tracked: it runs in a VM where user hooks don't run.
- Regular chats in the Claude desktop app have no API; only claude.ai in Chrome is tracked (via the extension, which depends on claude.ai's markup; selectors are at the top of `content.js`).
- Requires Python 3 from Apple's Command Line Tools (`/usr/bin/python3`).
- A Windows/Linux version (Tauri rewrite) was started and deliberately abandoned; macOS only for now.

## Testing

- Python: `python3 -m py_compile claude_hook.py channel_server.py install_hooks.py panel_link.py native_host.py`. Hooks can be exercised by piping hook JSON into `claude_hook.py` with `HOME` pointed at a temp folder. The channel server can be driven by writing JSON-RPC lines to its stdin.
- Swift: `bash build-app.sh`, then `pkill -x ClaudeTaskbar; open "dist/Claude Taskbar.app"`. Not unit tested.
- Two-way sync, manual: start `claude-tb` in a project, ask Claude to create a file; the panel and the terminal both show the prompt. Answer in one, check the other clears. Repeat answering on the other side.
- The two-way sync and the view-only cards were tested with simulated messages but not yet in a real `claude-tb` session; verify that first.

## Open work

- Verify two-way sync end to end with real Claude Code (above).
- Repo: https://github.com/Niranjanck/claude-taskbar (public); local `main` tracks `origin/main`. The pre-GitHub local history is kept on branch `old-local`.
- MDM: IT has the .pkg (320 KB for 1.0.0; `build-app.sh` now makes `Claude-Taskbar-1.2.0.pkg`, about 400 KB); waiting to hear whether it must be signed with a Developer ID Installer certificate.
- Website: update the download file name and version text when releasing a new version.
