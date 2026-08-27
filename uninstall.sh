#!/usr/bin/env bash

# Automated uninstaller for AI Agent Multi-Monitor Desktop Notifier
set -e

USER_HOME="${HOME:-/home/$USER}"
LOCAL_BIN="$USER_HOME/.local/bin"
CLAUDE_HOOKS="$USER_HOME/.claude/hooks"
CODEX_DIR="$USER_HOME/.codex"
GEMINI_HOOKS="$USER_HOME/.gemini/hooks"
GNOME_EXTENSION_UUID="ai-agent-desktop-notifier@sonnx24042005"
GNOME_EXTENSION_DIR="$USER_HOME/.local/share/gnome-shell/extensions/$GNOME_EXTENSION_UUID"

echo "=== 1. Restoring configuration backups & removing hooks ==="

python3 << 'EOF'
import json, os, shutil, subprocess, ast

USER_HOME = os.environ.get("HOME") or os.path.expanduser("~")

# 1. Clean up Claude hooks
claude_path = os.path.join(USER_HOME, ".claude", "settings.json")
if os.path.exists(claude_path):
    try:
        with open(claude_path, "r") as f:
            cdata = json.load(f)
        if isinstance(cdata, dict) and "hooks" in cdata and isinstance(cdata["hooks"], dict):
            hooks = cdata["hooks"]
            for event in list(hooks.keys()):
                if isinstance(hooks[event], list):
                    filtered = [item for item in hooks[event] if "notify-input.sh" not in json.dumps(item) and "notify-claude.py" not in json.dumps(item) and "ai-agent-desktop-notifier" not in json.dumps(item)]
                    if filtered:
                        hooks[event] = filtered
                    else:
                        del hooks[event]
            if not hooks:
                del cdata["hooks"]
            with open(claude_path, "w") as f:
                json.dump(cdata, f, indent=2)
            print("✓ Cleaned ai-agent notifier hooks from Claude Code settings.json (preserved other hooks)")
    except Exception:
        pass

# 2. Clean up Antigravity hooks (settings.json & hooks.json)
gemini_settings_file = os.path.join(USER_HOME, ".gemini", "settings.json")
if os.path.exists(gemini_settings_file):
    try:
        with open(gemini_settings_file, "r") as f:
            sdata = json.load(f)
        if isinstance(sdata, dict) and "hooks" in sdata and isinstance(sdata["hooks"], dict):
            hooks = sdata["hooks"]
            for evt in list(hooks.keys()):
                if isinstance(hooks[evt], list):
                    filtered = [item for item in hooks[evt] if "notify-antigravity" not in json.dumps(item)]
                    if filtered:
                        hooks[evt] = filtered
                    else:
                        del hooks[evt]
            if not hooks:
                del sdata["hooks"]
            with open(gemini_settings_file, "w") as f:
                json.dump(sdata, f, indent=2)
            print("✓ Cleaned ai-agent notifier hooks from Antigravity settings.json (preserved other hooks)")
    except Exception:
        pass

gemini_hooks_file = os.path.join(USER_HOME, ".gemini", "config", "hooks.json")
if os.path.exists(gemini_hooks_file):
    try:
        with open(gemini_hooks_file, "r") as f:
            gdata = json.load(f)
        if isinstance(gdata, dict) and "desktop-notifier" in gdata:
            del gdata["desktop-notifier"]
            with open(gemini_hooks_file, "w") as f:
                json.dump(gdata, f, indent=2)
            print("✓ Cleaned desktop-notifier from Antigravity hooks.json")
    except Exception:
        pass

# 3. Clean up Codex config.toml
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

# 4. Clean up Codex hooks.json
codex_hooks = os.path.join(USER_HOME, ".codex", "hooks.json")
if os.path.exists(codex_hooks):
    try:
        with open(codex_hooks, "r") as f:
            data_hooks = json.load(f)
        if isinstance(data_hooks, dict) and "hooks" in data_hooks and isinstance(data_hooks["hooks"], dict):
            if "PermissionRequest" in data_hooks["hooks"] and isinstance(data_hooks["hooks"]["PermissionRequest"], list):
                filtered = [item for item in data_hooks["hooks"]["PermissionRequest"] if "notify.py" not in json.dumps(item)]
                if filtered:
                    data_hooks["hooks"]["PermissionRequest"] = filtered
                else:
                    del data_hooks["hooks"]["PermissionRequest"]
            with open(codex_hooks, "w") as f:
                json.dump(data_hooks, f, indent=2)
            print("✓ Cleaned PermissionRequest from Codex hooks.json")
    except Exception:
        pass

# 5. Clean up GNOME global shortcut and restore default window menu
try:
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
EOF

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
gnome-extensions disable "$GNOME_EXTENSION_UUID" &>/dev/null || true
python3 - "$GNOME_EXTENSION_UUID" << 'PY'
import ast
import subprocess
import sys

uuid = sys.argv[1]
try:
    raw = subprocess.check_output(
        ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()
    enabled = ast.literal_eval(raw) if raw and raw != "@as []" else []
    if uuid in enabled:
        enabled.remove(uuid)
        subprocess.run(
            ["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(enabled)],
            check=False,
        )
except Exception:
    pass
PY
if [ -d "$GNOME_EXTENSION_DIR" ]; then
    find "$GNOME_EXTENSION_DIR" -mindepth 1 -maxdepth 1 -delete
    rmdir "$GNOME_EXTENSION_DIR" 2>/dev/null || true
fi
rm -rf "${XDG_RUNTIME_DIR:-/tmp}/ai-agent-notifier"
rm -f /tmp/ai_agent_notifier*

echo "=== 3. Uninstallation Complete ==="
echo "Restart or reload your VS Code window to apply changes."
