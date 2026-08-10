# Multi-Agent Desktop Notifier for Ubuntu

A lightweight, non-blocking multi-monitor audio-visual desktop notification system for AI Coding Assistants on Ubuntu (supports Claude Code, Codex, and Google Antigravity).

---

## Features

- **Multi-Monitor Support**: Automatically detects all connected monitors (X11 / GNOME) and renders floating popup banners at the top-center of every monitor simultaneously.
- **Interactive Question Viewing & Direct Answer**: View questions directly inside the desktop notification and respond instantly without switching windows!
  - **Single Choice**: Radio buttons for selecting one answer + custom write-in field.
  - **Multiple Choice**: Checkboxes for selecting multiple options + custom write-in field.
  - **Free-Text Answer**: Text entry field for custom open-ended answers.
  - **Multi-Question Support**: Scrollable panel for answering multiple questions simultaneously.
  - **Auto-Clipboard Sync**: Clicking "Gửi & Copy (Ctrl+Enter)" copies formatted responses directly to system clipboard for instant `Ctrl + V` pasting into your AI CLI or IDE. Saves last answer to `/tmp/ai_agent_last_answer.txt`.
- **Unified Design**: Single modern dark theme (`#18181b` dark slate background with `#3b82f6` blue accent border). No window titlebar, no taskbar icon, no close button clutter.
- **Sound Alerts**: Plays subtle audio cues (`dialog-warning.oga` for questions/permission requests and `complete.oga` for task completions) asynchronously without blocking the AI agent execution loop.
- **Safe & Non-Blocking**: Runs GTK popups and sound playback asynchronously. Any error in notification scripts will never crash or interrupt your AI CLI or IDE session.
- **Non-Destructive Config Merger**: Preserves all your pre-existing permissions, models, MCP servers, plugins, and trusted workspace settings.

---

## Supported AI Coding Agents

1. **Claude Code** (via `~/.claude/settings.json` hooks)
2. **Codex** (via `~/.codex/config.toml` `notify` & `~/.codex/hooks.json`)
3. **Google Antigravity** (via `~/.gemini/settings.json` & `~/.gemini/antigravity-cli/settings.json` hooks)

---

## Quick Start & Installation

### 1-Line Quick Installation (Recommended)

Run this single command in your terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash
```

### Manual Installation

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

## Repository Structure

```
ai-agent-desktop-notifier/
├── bin/
│   └── multi-desktop-notify.py   # Multi-monitor PyGObject GTK popup engine
├── hooks/
│   ├── claude-notify.sh          # Hook handler for Claude Code
│   ├── codex-notify.py           # Notification handler for Codex
│   └── antigravity-notify.sh     # Hook handler for Antigravity
├── install.sh                    # One-command installer & config merger
├── README.md                     # Project documentation
├── .gitignore
└── LICENSE
```

---

## Testing Notifications Manually

You can test notifications on all your screens anytime by running:

```bash
# Test Claude Code question notification
echo '{"hook_event_name":"PreToolUse","tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"Test question on all screens?"}]}}' | ~/.claude/hooks/notify-input.sh

# Test Codex completion notification
~/.codex/notify.py '{"type":"agent-turn-complete","last-assistant-message":"Codex completed task!"}'

# Test Antigravity notification
echo '{"hook_event_name":"PreToolUse","tool_name":"ask_question","tool_input":{"questions":[{"question":"Antigravity test on all screens!"}]}}' | ~/.gemini/hooks/notify-antigravity.sh
```

---

## License

This project is licensed under the MIT License.
