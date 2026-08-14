# Multi-agent desktop notifier for Ubuntu

A lightweight, non-blocking multi-monitor audio-visual desktop notification and window-focusing system for AI coding assistants on Ubuntu Linux (supports Claude Code, OpenAI Codex, and Google Antigravity).

---

## Features

- **Multi-monitor display**: Automatically detects all connected monitors (X11 / GNOME) and renders floating popup banners at the top-center of every monitor simultaneously.
- **Session-based early window capture**: Records and caches the exact workspace window at session start (`SessionStart`) with PID ancestry verification, ensuring 100% focus precision even when you switch to other applications or workspaces during long-running tasks.
- **Hierarchical window resolution (6 tiers)**: Accurately identifies the target workspace among multiple open instances:
  - Tier 0: Session cache (`session_id` lookup)
  - Tier 1: Process ancestry tree (`/proc/{pid}/stat`) + project directory hint
  - Tier 2: Workspace title matching across open IDE / terminal windows
  - Tier 3: Terminal pts device control (VTE title stack push & pop marker) + GNOME Terminal D-Bus tab switching
  - Tier 4: Explicit caller window ID
  - Tier 5: Fallback active window
- **Direct app window focus**: Clicking anywhere on the notification banner or clicking the **"Đến cửa sổ ứng dụng"** button instantly focuses and brings to front the exact workspace window via native GDK and X11/EWMH (`_NET_ACTIVE_WINDOW`).
- **Smart anti-spam deduplication**: Deduplicates identical notifications within a configurable cooldown period (`--dedupe-seconds`, default 2s) to prevent UI flicker.
- **Multi-channel webhooks (optional)**: Forwards notifications to mobile channels (Feishu/Lark, DingTalk, Slack, Discord, Bark iOS, ntfy) via `~/.config/ai-agent-notifier/config.json`.
- **Audio alerts**: Plays subtle audio cues (`dialog-warning.oga` for questions/permission requests and `complete.oga` for task completions) asynchronously without blocking the AI agent loop.
- **Non-destructive config merger**: Automatically updates hook configurations while preserving all pre-existing permissions, models, MCP servers, plugins, and trusted workspace settings.

---

## Supported AI coding agents

1. **Claude Code** (via `~/.claude/settings.json` lifecycle hooks)
2. **OpenAI Codex** (via `~/.codex/config.toml` `notify` & `~/.codex/hooks.json`)
3. **Google Antigravity** (via `~/.gemini/config/hooks.json` lifecycle hooks)

---

## Quick start & installation

### 1-line quick installation (recommended)

Run this command in your terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash
```

### Manual installation

Alternatively, clone the repository and run `install.sh`:

```bash
git clone https://github.com/SonNX24042005/ai-agent-desktop-notifier.git
cd ai-agent-desktop-notifier
chmod +x install.sh
./install.sh
```

After installation, reload your VS Code window:
> `Ctrl + Shift + P` -> `Developer: Reload Window`

---

## Updating the notification system

You can update the notification system anytime using any of the following methods:

### Method 1: Run update script from repository

```bash
./update.sh
```

### Method 2: 1-line update via curl

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/update.sh | bash
```

### Method 3: Using the `--update` flag

```bash
~/.local/bin/multi-desktop-notify.py --update
```

---

## Uninstallation

To remove all notification scripts and restore your previous configurations:

### Method 1: Run uninstallation script

```bash
./uninstall.sh
```

### Method 2: 1-line uninstallation via curl

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/uninstall.sh | bash
```

### Method 3: Using the `--uninstall` flag

```bash
~/.local/bin/multi-desktop-notify.py --uninstall
```

---

## Optional webhook configuration

To forward notifications to mobile apps or team chat channels when you are away from your desk, create `~/.config/ai-agent-notifier/config.json`:

```json
{
  "webhooks": {
    "slack": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
    "discord": "https://discord.com/api/webhooks/YOUR/WEBHOOK/URL",
    "bark": "https://api.day.app/YOUR_KEY",
    "ntfy": "https://ntfy.sh/your_topic",
    "feishu": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_KEY",
    "dingtalk": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
  }
}
```

---

## Repository structure

```
ai-agent-desktop-notifier/
├── bin/
│   └── multi-desktop-notify.py   # Multi-monitor PyGObject GTK popup & window focus engine
├── hooks/
│   ├── claude-notify.sh          # Lifecycle hook handler for Claude Code
│   ├── codex-notify.py           # Notification handler for OpenAI Codex
│   └── antigravity-notify.sh     # Lifecycle hook handler for Google Antigravity
├── install.sh                    # One-command installer & config merger
├── update.sh                     # Automated updater & config syncer
├── uninstall.sh                  # Uninstaller & backup restorer
├── README.md                     # Documentation
├── .gitignore
└── LICENSE
```

---

## Testing notifications manually

You can test notifications on all your screens anytime by running:

```bash
# Test Claude Code question notification
echo '{"hook_event_name":"PreToolUse","tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"Test question on all screens?"}]}}' | ~/.claude/hooks/notify-input.sh

# Test Claude Code session start capture
echo '{"hook_event_name":"SessionStart","session_id":"test-session-001"}' | ~/.claude/hooks/notify-input.sh

# Test Codex completion notification
~/.codex/notify.py '{"type":"agent-turn-complete","last-assistant-message":"Codex completed task!"}'

# Test Antigravity question notification
echo '{"toolCall":{"name":"ask_question","args":{"questions":[{"question":"Antigravity test on all screens!"}]}}}' | ~/.gemini/hooks/notify-antigravity.sh

# Test Antigravity completion notification
echo '{"terminationReason":"model_stop"}' | ~/.gemini/hooks/notify-antigravity.sh
```

---

## License

This project is licensed under the MIT License.
