# Multi-Agent Desktop Notifier for Ubuntu 🔔

A lightweight, non-blocking multi-monitor audio-visual desktop notification system for AI Coding Assistants on Ubuntu (supports **Claude Code**, **Codex**, and **Google Antigravity**).

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-Ubuntu%20Linux-orange.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-green.svg)

---

## ✨ Features

- **📺 Dual & Multi-Monitor Support**: Automatically detects all connected monitors (X11 / GNOME) and renders floating popup banners at the top-center of **every monitor** simultaneously.
- **🎨 Sleek & Unified Design**: Single modern dark glassmorphism theme (`#18181b` dark slate background with `#3b82f6` blue accent border). No window titlebar, no taskbar icon, no `X` close button clutter.
- **🔊 Sound Alerts**: Plays subtle audio cues (`dialog-warning.oga` for questions/permission requests and `complete.oga` for task completions) asynchronously without blocking the AI agent execution loop.
- **⚡ Safe & Non-Blocking**: Runs GTK popups and sound playback asynchronously. Any error in notification scripts will never crash or interrupt your AI CLI or IDE session.
- **🔄 Non-Destructive Config Merger**: Preserves all your pre-existing permissions, models, MCP servers, plugins, and trusted workspace settings.

---

## 🛠 Supported AI Coding Agents

1. **Claude Code** (via `~/.claude/settings.json` hooks)
2. **Codex** (via `~/.codex/config.toml` `notify` & `~/.codex/hooks.json`)
3. **Google Antigravity** (via `~/.gemini/settings.json` & `~/.gemini/antigravity-cli/settings.json` hooks)

---

## 🚀 Quick Start & Installation

### Prerequisites

Ensure required system packages are installed on Ubuntu:

```bash
sudo apt update
sudo apt install -y libnotify-bin jq pulseaudio-utils sound-theme-freedesktop python3-gi
```

### Installation

Clone this repository and run the automated installer:

```bash
git clone https://github.com/SonNX24042005/ai-agent-desktop-notifier.git
cd ai-agent-desktop-notifier
chmod +x install.sh
./install.sh
```

After running `install.sh`, reload your VS Code window:
> **`Ctrl + Shift + P`** $\rightarrow$ **`Developer: Reload Window`**

---

## 📁 Repository Structure

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

## 🧪 Testing Notifications Manually

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

## 📜 License

This project is licensed under the [MIT License](LICENSE).
