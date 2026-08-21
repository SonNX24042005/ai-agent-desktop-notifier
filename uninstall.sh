#!/usr/bin/env bash

# Automated uninstaller for AI Agent Multi-Monitor Desktop Notifier
set -e

USER_HOME="${HOME:-/home/$USER}"
LOCAL_BIN="$USER_HOME/.local/bin"
CLAUDE_HOOKS="$USER_HOME/.claude/hooks"
CODEX_DIR="$USER_HOME/.codex"
GEMINI_HOOKS="$USER_HOME/.gemini/hooks"

echo "=== 1. Restoring configuration backups & removing hooks ==="

python3 -c '
import json, os, shutil

USER_HOME = os.environ.get("HOME") or os.path.expanduser("~")

# 1. Restore .bak files if they exist
paths_to_restore = [
    os.path.join(USER_HOME, ".claude", "settings.json"),
    os.path.join(USER_HOME, ".codex", "config.toml"),
    os.path.join(USER_HOME, ".codex", "hooks.json"),
    os.path.join(USER_HOME, ".gemini", "config", "hooks.json"),
]

for p in paths_to_restore:
    bak = p + ".bak"
    if os.path.exists(bak):
        shutil.copyfile(bak, p)
        print(f"✓ Restored {p} from backup {bak}")

# 2. Clean up Claude hooks if still present
claude_path = os.path.join(USER_HOME, ".claude", "settings.json")
if os.path.exists(claude_path):
    try:
        with open(claude_path, "r") as f:
            cdata = json.load(f)
        if "hooks" in cdata:
            del cdata["hooks"]
            with open(claude_path, "w") as f:
                json.dump(cdata, f, indent=2)
            print("✓ Cleaned hooks from Claude Code settings.json")
    except Exception:
        pass

# 3. Clean up Antigravity hooks (settings.json & hooks.json)
gemini_settings_file = os.path.join(USER_HOME, ".gemini", "settings.json")
if os.path.exists(gemini_settings_file):
    try:
        with open(gemini_settings_file, "r") as f:
            sdata = json.load(f)
        if "hooks" in sdata:
            del sdata["hooks"]
            with open(gemini_settings_file, "w") as f:
                json.dump(sdata, f, indent=2)
            print("✓ Cleaned hooks from Antigravity settings.json")
    except Exception:
        pass

gemini_hooks_file = os.path.join(USER_HOME, ".gemini", "config", "hooks.json")
if os.path.exists(gemini_hooks_file):
    try:
        with open(gemini_hooks_file, "r") as f:
            gdata = json.load(f)
        if "desktop-notifier" in gdata:
            del gdata["desktop-notifier"]
            with open(gemini_hooks_file, "w") as f:
                json.dump(gdata, f, indent=2)
            print("✓ Cleaned desktop-notifier from Antigravity hooks.json")
    except Exception:
        pass

# 4. Clean up Codex config.toml
codex_cfg = os.path.join(USER_HOME, ".codex", "config.toml")
if os.path.exists(codex_cfg):
    try:
        with open(codex_cfg, "r") as f:
            content = f.read()
        lines = [l for l in content.splitlines() if "notify.py" not in l and not l.strip().startswith("notify =")]
        with open(codex_cfg, "w") as f:
            f.write("\n".join(lines) + "\n")
        print("✓ Cleaned notify from Codex config.toml")
    except Exception:
        pass

# 5. Clean up Codex hooks.json
codex_hooks = os.path.join(USER_HOME, ".codex", "hooks.json")
if os.path.exists(codex_hooks):
    try:
        with open(codex_hooks, "r") as f:
            data_hooks = json.load(f)
        if "hooks" in data_hooks and "PermissionRequest" in data_hooks["hooks"]:
            del data_hooks["hooks"]["PermissionRequest"]
            with open(codex_hooks, "w") as f:
                json.dump(data_hooks, f, indent=2)
            print("✓ Cleaned PermissionRequest from Codex hooks.json")
    except Exception:
        pass

# 6. Clean up GNOME global shortcut and restore default window menu
try:
    import subprocess, ast
    target_path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/anoti-focus/"
    subprocess.run(["gsettings", "set", "org.gnome.desktop.wm.keybindings", "activate-window-menu", "['<Alt>space']"], check=False)
    out = subprocess.check_output(["gsettings", "get", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings"], stderr=subprocess.DEVNULL).decode().strip()
    bindings = ast.literal_eval(out) if out and out != "@as []" else []
    if target_path in bindings:
        bindings.remove(target_path)
        subprocess.run(["gsettings", "set", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings", str(bindings)], check=False)
        subprocess.run(["gsettings", "reset-recursively", f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{target_path}"], check=False)
        print("✓ Removed Alt+Q global shortcut")
except Exception:
    pass
'

echo "=== 2. Removing notification scripts & caches ==="
rm -f "$LOCAL_BIN/multi-desktop-notify.py"
rm -f "$LOCAL_BIN/anoti"
rm -f "$LOCAL_BIN/anoti.cmd"
rm -f "$LOCAL_BIN/anoti.ps1"
rm -f "$CLAUDE_HOOKS/notify-input.sh"
rm -f "$CLAUDE_HOOKS/notify-claude.py"
rm -f "$CODEX_DIR/notify.py"
rm -f "$GEMINI_HOOKS/notify-antigravity.sh"
rm -f "$GEMINI_HOOKS/notify-antigravity.py"
rm -f /tmp/ai_agent_notifier*

echo "=== 3. Uninstallation Complete ==="
echo "Restart or reload your VS Code window to apply changes."
