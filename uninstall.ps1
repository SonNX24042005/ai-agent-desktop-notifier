# Automated uninstaller for AI Agent Multi-Monitor Desktop Notifier on Windows
# Requires PowerShell 5.1+

$ErrorActionPreference = "Continue"

Write-Host "=== 1. Go bo hook va khoi phuc cau hinh cac AI Agent ===" -ForegroundColor Cyan

$PythonExe = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
}

if ($PythonExe) {
    $CleanScript = @"
import json
import os
from pathlib import Path

user_home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))

# 1. Clean Claude Code (~/.claude/settings.json)
claude_path = user_home / ".claude" / "settings.json"
if claude_path.exists():
    try:
        with open(claude_path, "r", encoding="utf-8") as f:
            cdata = json.load(f)
        if isinstance(cdata, dict) and "hooks" in cdata and isinstance(cdata["hooks"], dict):
            hooks = cdata["hooks"]
            for event in list(hooks.keys()):
                if isinstance(hooks[event], list):
                    filtered = [item for item in hooks[event] if "notify-claude.py" not in json.dumps(item) and "notify-input.sh" not in json.dumps(item) and "ai-agent-desktop-notifier" not in json.dumps(item)]
                    if filtered:
                        hooks[event] = filtered
                    else:
                        del hooks[event]
            if not hooks:
                del cdata["hooks"]
            with open(claude_path, "w", encoding="utf-8") as f:
                json.dump(cdata, f, indent=2)
            print("- Cleaned ai-agent notifier hooks from Claude Code settings.json (preserved other hooks)")
    except Exception as e:
        print(f"- [WARN] Claude Code error: {e}")

# 2. Clean Codex (~/.codex/config.toml & ~/.codex/hooks.json)
codex_cfg = user_home / ".codex" / "config.toml"
if codex_cfg.exists():
    try:
        with open(codex_cfg, "r", encoding="utf-8") as f:
            content = f.read()
        lines = [l for l in content.splitlines() if "notify.py" not in l and not l.strip().startswith("notify =")]
        with open(codex_cfg, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("- Cleaned Codex config.toml")
    except Exception as e:
        print(f"- [WARN] Codex config error: {e}")

codex_hooks = user_home / ".codex" / "hooks.json"
if codex_hooks.exists():
    try:
        with open(codex_hooks, "r", encoding="utf-8") as f:
            data_hooks = json.load(f)
        if isinstance(data_hooks, dict) and "hooks" in data_hooks and isinstance(data_hooks["hooks"], dict):
            if "PermissionRequest" in data_hooks["hooks"] and isinstance(data_hooks["hooks"]["PermissionRequest"], list):
                filtered = [item for item in data_hooks["hooks"]["PermissionRequest"] if "notify.py" not in json.dumps(item)]
                if filtered:
                    data_hooks["hooks"]["PermissionRequest"] = filtered
                else:
                    del data_hooks["hooks"]["PermissionRequest"]
            with open(codex_hooks, "w", encoding="utf-8") as f:
                json.dump(data_hooks, f, indent=2)
            print("- Cleaned PermissionRequest from Codex hooks.json")
    except Exception as e:
        print(f"- [WARN] Codex hooks error: {e}")

# 3. Clean Antigravity (~/.gemini/settings.json & ~/.gemini/config/hooks.json)
gemini_settings_file = user_home / ".gemini" / "settings.json"
if gemini_settings_file.exists():
    try:
        with open(gemini_settings_file, "r", encoding="utf-8") as f:
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
            with open(gemini_settings_file, "w", encoding="utf-8") as f:
                json.dump(sdata, f, indent=2)
            print("- Cleaned ai-agent notifier hooks from Antigravity settings.json (preserved other hooks)")
    except Exception as e:
        print(f"- [WARN] Antigravity settings error: {e}")

gemini_hooks_file = user_home / ".gemini" / "config" / "hooks.json"
if gemini_hooks_file.exists():
    try:
        with open(gemini_hooks_file, "r") as f:
            gdata = json.load(f)
        if isinstance(gdata, dict) and "desktop-notifier" in gdata:
            del gdata["desktop-notifier"]
            with open(gemini_hooks_file, "w", encoding="utf-8") as f:
                json.dump(gdata, f, indent=2)
            print("- Cleaned desktop-notifier from Antigravity config/hooks.json")
    except Exception as e:
        print(f"- [WARN] Antigravity hooks error: {e}")
"@
    & $PythonExe -c $CleanScript
}

Write-Host "=== 2. Xoa tep chuong trinh va bo nho dem ===" -ForegroundColor Cyan
$UserHome = $env:USERPROFILE
$FilesToRemove = @(
    (Join-Path $UserHome ".local\bin\multi-desktop-notify.py"),
    (Join-Path $UserHome ".local\bin\anoti"),
    (Join-Path $UserHome ".local\bin\anoti.cmd"),
    (Join-Path $UserHome ".local\bin\anoti.ps1"),
    (Join-Path $UserHome ".claude\hooks\notify-input.sh"),
    (Join-Path $UserHome ".claude\hooks\notify-claude.py"),
    (Join-Path $UserHome ".codex\notify.py"),
    (Join-Path $UserHome ".gemini\hooks\notify-antigravity.sh"),
    (Join-Path $UserHome ".gemini\hooks\notify-antigravity.py")
)

foreach ($f in $FilesToRemove) {
    if (Test-Path $f) {
        Remove-Item -Path $f -Force
        Write-Host "- Da xoa $f" -ForegroundColor Gray
    }
}

# Clean temp cache files and runtime directory
if ($env:LOCALAPPDATA) {
    $RuntimeDir = Join-Path $env:LOCALAPPDATA "ai-agent-notifier"
    if (Test-Path $RuntimeDir) {
        Remove-Item -Path $RuntimeDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
$TempDir = $env:TEMP
Get-ChildItem -Path $TempDir -Filter "ai_agent_notifier*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Da go cai dat AI Agent Desktop Notifier thanh cong!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Hay tai lai cua so VS Code / IDE de hoan tat:" -ForegroundColor Yellow
Write-Host "   Ctrl + Shift + P -> Developer: Reload Window" -ForegroundColor Yellow
Write-Host ""
