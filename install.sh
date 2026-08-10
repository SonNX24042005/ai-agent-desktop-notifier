#!/usr/bin/env bash

# Automated installer for AI Agent Multi-Monitor Desktop Notifier
set -e

USER_HOME="${HOME:-/home/$USER}"
LOCAL_BIN="$USER_HOME/.local/bin"
CLAUDE_HOOKS="$USER_HOME/.claude/hooks"
CODEX_DIR="$USER_HOME/.codex"
GEMINI_HOOKS="$USER_HOME/.gemini/hooks"

REPO_URL="https://github.com/SonNX24042005/ai-agent-desktop-notifier.git"

# Determine source directory (support remote curl | bash execution)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"

if [ ! -f "$SCRIPT_DIR/bin/multi-desktop-notify.py" ]; then
    echo "Downloading installer from GitHub..."
    TEMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TEMP_DIR"' EXIT
    git clone --depth 1 "$REPO_URL" "$TEMP_DIR/repo" &>/dev/null || {
        echo "Error: Failed to clone repository $REPO_URL"
        exit 1
    }
    SCRIPT_DIR="$TEMP_DIR/repo"
fi

echo "=== 1. Checking dependencies ==="
MISSING_PKGS=()

for cmd in python3 jq paplay; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING_PKGS+=("$cmd")
    fi
done

if ! python3 -c "import gi; gi.require_version('Gtk', '3.0')" &>/dev/null; then
    MISSING_PKGS+=("python3-gi")
fi

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "WARNING: Missing dependencies: ${MISSING_PKGS[*]}"
    echo "Please install them via: sudo apt update && sudo apt install -y libnotify-bin jq pulseaudio-utils sound-theme-freedesktop python3-gi"
fi

echo "=== 2. Creating target directories ==="
mkdir -p "$LOCAL_BIN" "$CLAUDE_HOOKS" "$CODEX_DIR" "$GEMINI_HOOKS"

echo "=== 3. Copying notification scripts ==="
cp "$SCRIPT_DIR/bin/multi-desktop-notify.py" "$LOCAL_BIN/multi-desktop-notify.py"
chmod +x "$LOCAL_BIN/multi-desktop-notify.py"

cp "$SCRIPT_DIR/hooks/claude-notify.sh" "$CLAUDE_HOOKS/notify-input.sh"
chmod +x "$CLAUDE_HOOKS/notify-input.sh"

cp "$SCRIPT_DIR/hooks/codex-notify.py" "$CODEX_DIR/notify.py"
chmod +x "$CODEX_DIR/notify.py"

cp "$SCRIPT_DIR/hooks/antigravity-notify.sh" "$GEMINI_HOOKS/notify-antigravity.sh"
chmod +x "$GEMINI_HOOKS/notify-antigravity.sh"

echo "=== 4. Merging configuration files safely ==="
python3 -c '
import json, os

USER_HOME = os.environ.get("HOME") or os.path.expanduser("~")

# 1. Claude Code (~/.claude/settings.json)
claude_path = os.path.join(USER_HOME, ".claude", "settings.json")
if os.path.exists(claude_path):
    with open(claude_path, "r") as f:
        cdata = json.load(f)
    cdata["hooks"] = {
        "PreToolUse": [
            {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": f"{USER_HOME}/.claude/hooks/notify-input.sh"}]}
        ],
        "Notification": [
            {"matcher": "permission_prompt", "hooks": [{"type": "command", "command": f"{USER_HOME}/.claude/hooks/notify-input.sh"}]},
            {"matcher": "idle_prompt", "hooks": [{"type": "command", "command": f"{USER_HOME}/.claude/hooks/notify-input.sh"}]},
            {"matcher": "agent_needs_input", "hooks": [{"type": "command", "command": f"{USER_HOME}/.claude/hooks/notify-input.sh"}]},
            {"matcher": "agent_completed", "hooks": [{"type": "command", "command": f"{USER_HOME}/.claude/hooks/notify-input.sh"}]}
        ]
    }
    with open(claude_path, "w") as f:
        json.dump(cdata, f, indent=2)
    print("✓ Merged Claude Code settings.json")

# 2. Codex (~/.codex/config.toml & ~/.codex/hooks.json)
codex_cfg = os.path.join(USER_HOME, ".codex", "config.toml")
if os.path.exists(codex_cfg):
    with open(codex_cfg, "r") as f:
        content = f.read()
    lines = [l for l in content.splitlines() if not l.strip().startswith("notify")]
    notify_line = f"notify = [\"/usr/bin/python3\", \"{USER_HOME}/.codex/notify.py\"]"
    lines.insert(0, notify_line)
    with open(codex_cfg, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("✓ Merged Codex config.toml")

codex_hooks = os.path.join(USER_HOME, ".codex", "hooks.json")
data_hooks = {"description": "Ubuntu desktop notifications for Codex", "hooks": {}}
if os.path.exists(codex_hooks):
    try:
        with open(codex_hooks, "r") as f:
            data_hooks = json.load(f)
    except Exception:
        pass
if "hooks" not in data_hooks:
    data_hooks["hooks"] = {}

perm_h = {
    "hooks": [
        {
            "type": "command",
            "command": f"/usr/bin/python3 {USER_HOME}/.codex/notify.py",
            "timeout": 5,
            "statusMessage": "Sending Ubuntu notification"
        }
    ]
}
data_hooks["hooks"]["PermissionRequest"] = [perm_h]
with open(codex_hooks, "w") as f:
    json.dump(data_hooks, f, indent=2)
print("✓ Merged Codex hooks.json")

# 3. Antigravity (~/.gemini/settings.json & ~/.gemini/antigravity-cli/settings.json)
for path in [
    os.path.join(USER_HOME, ".gemini", "settings.json"),
    os.path.join(USER_HOME, ".gemini", "antigravity-cli", "settings.json")
]:
    if os.path.exists(path):
        with open(path, "r") as f:
            gdata = json.load(f)
        gdata["hooks"] = {
            "PreToolUse": [
                {"matcher": "ask_question", "hooks": [{"type": "command", "command": f"{USER_HOME}/.gemini/hooks/notify-antigravity.sh"}]},
                {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": f"{USER_HOME}/.gemini/hooks/notify-antigravity.sh"}]}
            ],
            "Notification": [
                {"matcher": "permission_prompt", "hooks": [{"type": "command", "command": f"{USER_HOME}/.gemini/hooks/notify-antigravity.sh"}]},
                {"matcher": "idle_prompt", "hooks": [{"type": "command", "command": f"{USER_HOME}/.gemini/hooks/notify-antigravity.sh"}]},
                {"matcher": "agent_needs_input", "hooks": [{"type": "command", "command": f"{USER_HOME}/.gemini/hooks/notify-antigravity.sh"}]},
                {"matcher": "agent_completed", "hooks": [{"type": "command", "command": f"{USER_HOME}/.gemini/hooks/notify-antigravity.sh"}]}
            ]
        }
        with open(path, "w") as f:
            json.dump(gdata, f, indent=2)
        print(f"✓ Merged Antigravity {path}")
'

echo "=== 5. Installation Complete! ==="
echo "Testing desktop notification on all screens..."
"$LOCAL_BIN/multi-desktop-notify.py" \
    --app-name="AI Agent Notifier" \
    --title="Cài đặt thành công!" \
    --message="Hệ thống thông báo đa màn hình kèm âm thanh đã sẵn sàng." \
    --sound="/usr/share/sounds/freedesktop/stereo/complete.oga" \
    --timeout=4
