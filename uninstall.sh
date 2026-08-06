#!/usr/bin/env bash

# Automated uninstaller for AI Agent Multi-Monitor Desktop Notifier
set -e

USER_HOME="${HOME:-/home/$USER}"
LOCAL_BIN="$USER_HOME/.local/bin"
CLAUDE_HOOKS="$USER_HOME/.claude/hooks"
CODEX_DIR="$USER_HOME/.codex"
GEMINI_HOOKS="$USER_HOME/.gemini/hooks"

echo "=== 1. Restoring configuration backups ==="

python3 -c '
import json, os, shutil

USER_HOME = os.environ.get("HOME", f"/home/{os.environ.get(\"USER\")}")

paths_to_restore = [
    os.path.join(USER_HOME, ".claude", "settings.json"),
    os.path.join(USER_HOME, ".codex", "config.toml"),
    os.path.join(USER_HOME, ".codex", "hooks.json"),
    os.path.join(USER_HOME, ".gemini", "settings.json"),
    os.path.join(USER_HOME, ".gemini", "antigravity-cli", "settings.json"),
]

for p in paths_to_restore:
    bak = p + ".bak"
    if os.path.exists(bak):
        shutil.copyfile(bak, p)
        print(f"✓ Restored {p} from backup {bak}")
    elif os.path.exists(p):
        print(f"ℹ No backup found for {p}, keeping current file.")
'

echo "=== 2. Removing notification scripts ==="
rm -f "$LOCAL_BIN/multi-desktop-notify.py"
rm -f "$CLAUDE_HOOKS/notify-input.sh"
rm -f "$CODEX_DIR/notify.py"
rm -f "$GEMINI_HOOKS/notify-antigravity.sh"

echo "=== 3. Uninstallation Complete ==="
echo "Restart or reload your VS Code window to apply changes."
