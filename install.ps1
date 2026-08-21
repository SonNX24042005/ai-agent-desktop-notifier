# Automated installer for AI Agent Multi-Monitor Desktop Notifier on Windows
# Requires PowerShell 5.1+ and Python 3.8+

$ErrorActionPreference = "Stop"

Write-Host "=== 1. Kiem tra moi truong va Python ===" -ForegroundColor Cyan

$PythonExe = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
}

if (-not $PythonExe) {
    Write-Host "[ERROR] Khong tim thay Python tren he thong!" -ForegroundColor Red
    Write-Host "Vui long cai dat Python 3.8+ tu: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Hoac chay lenh: winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}

$PythonVer = & $PythonExe --version 2>&1
Write-Host "[OK] Tim thay $PythonVer" -ForegroundColor Green

$RepoUrl = "https://github.com/SonNX24042005/ai-agent-desktop-notifier.git"
$ScriptDir = $PSScriptRoot

if (-not $ScriptDir -or -not (Test-Path (Join-Path $ScriptDir "bin\multi-desktop-notify.py"))) {
    Write-Host "Dang tai ma nguon tu GitHub..." -ForegroundColor Cyan
    $TempZipDir = Join-Path $env:TEMP ("anoti_installer_" + (Get-Random))
    New-Item -ItemType Directory -Force -Path $TempZipDir | Out-Null
    
    $ZipPath = Join-Path $TempZipDir "repo.zip"
    $ZipUrl = "https://github.com/SonNX24042005/ai-agent-desktop-notifier/archive/refs/heads/master.zip"
    
    try {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
        Expand-Archive -Path $ZipPath -DestinationPath $TempZipDir -Force
        $ScriptDir = Join-Path $TempZipDir "ai-agent-desktop-notifier-master"
    } catch {
        Write-Host "[ERROR] Khong the tai bo cai dat tu GitHub: $_" -ForegroundColor Red
        exit 1
    }
}

$UserHome = $env:USERPROFILE
$LocalBin = Join-Path $UserHome ".local\bin"
$ClaudeHooks = Join-Path $UserHome ".claude\hooks"
$CodexDir = Join-Path $UserHome ".codex"
$GeminiHooks = Join-Path $UserHome ".gemini\hooks"
$GeminiConfig = Join-Path $UserHome ".gemini\config"
$ConfigDir = Join-Path $UserHome ".config\ai-agent-notifier"

Write-Host "=== 2. Tao cac thu muc he thong ===" -ForegroundColor Cyan
$DirsToCreate = @($LocalBin, $ClaudeHooks, $CodexDir, $GeminiHooks, $GeminiConfig, $ConfigDir)
foreach ($dir in $DirsToCreate) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}
Write-Host "[OK] Da khoi tao thu muc dich." -ForegroundColor Green

Write-Host "=== 3. Sao chep tep ma nguon va hook ===" -ForegroundColor Cyan
Copy-Item (Join-Path $ScriptDir "bin\multi-desktop-notify.py") (Join-Path $LocalBin "multi-desktop-notify.py") -Force
Copy-Item (Join-Path $ScriptDir "bin\anoti") (Join-Path $LocalBin "anoti") -Force
Copy-Item (Join-Path $ScriptDir "bin\anoti.cmd") (Join-Path $LocalBin "anoti.cmd") -Force
Copy-Item (Join-Path $ScriptDir "bin\anoti.ps1") (Join-Path $LocalBin "anoti.ps1") -Force
Copy-Item (Join-Path $ScriptDir "hooks\claude-notify.py") (Join-Path $ClaudeHooks "notify-claude.py") -Force
Copy-Item (Join-Path $ScriptDir "hooks\codex-notify.py") (Join-Path $CodexDir "notify.py") -Force
Copy-Item (Join-Path $ScriptDir "hooks\antigravity-notify.py") (Join-Path $GeminiHooks "notify-antigravity.py") -Force
Write-Host "[OK] Da sao chep tat ca tep chuong trinh." -ForegroundColor Green

Write-Host "=== 4. Cau hinh bien moi truong PATH ===" -ForegroundColor Cyan
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$LocalBin*") {
    $NewUserPath = "$UserPath;$LocalBin".TrimStart(";")
    [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    $env:Path = "$env:Path;$LocalBin"
    Write-Host "[OK] Da them $LocalBin vao bien moi truong PATH." -ForegroundColor Green
} else {
    Write-Host "[OK] $LocalBin da co san trong PATH." -ForegroundColor Green
}

Write-Host "=== 5. Dong bo cau hinh cac AI Agent ===" -ForegroundColor Cyan

$MergeScript = @"
import json
import os
from pathlib import Path

user_home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))

# 1. Claude Code (~/.claude/settings.json)
claude_path = user_home / ".claude" / "settings.json"
claude_hook = user_home / ".claude" / "hooks" / "notify-claude.py"
try:
    cdata = {}
    if claude_path.exists():
        with open(claude_path, "r", encoding="utf-8") as f:
            cdata = json.load(f)
    hook_cmd = f'python "{claude_hook}"'
    cdata["hooks"] = {
        "SessionStart": [
            {"hooks": [{"type": "command", "command": hook_cmd}]}
        ],
        "PreToolUse": [
            {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": hook_cmd}]}
        ],
        "Notification": [
            {"matcher": "permission_prompt", "hooks": [{"type": "command", "command": hook_cmd}]},
            {"matcher": "agent_completed", "hooks": [{"type": "command", "command": hook_cmd}]}
        ],
        "Stop": [
            {"hooks": [{"type": "command", "command": hook_cmd}]}
        ]
    }
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    with open(claude_path, "w", encoding="utf-8") as f:
        json.dump(cdata, f, indent=2)
    print("- Merged Claude Code settings.json")
except Exception as e:
    print(f"- [WARN] Claude Code config error: {e}")

# 2. OpenAI Codex (~/.codex/config.toml & ~/.codex/hooks.json)
codex_cfg = user_home / ".codex" / "config.toml"
codex_script = user_home / ".codex" / "notify.py"
try:
    content = ""
    if codex_cfg.exists():
        with open(codex_cfg, "r", encoding="utf-8") as f:
            content = f.read()
    lines = [l for l in content.splitlines() if not l.strip().startswith("notify")]
    py_path = str(codex_script).replace("\\", "/")
    notify_line = f'notify = ["python", "{py_path}"]'
    lines.insert(0, notify_line)
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    with open(codex_cfg, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("- Merged Codex config.toml")
except Exception as e:
    print(f"- [WARN] Codex config.toml error: {e}")

codex_hooks = user_home / ".codex" / "hooks.json"
try:
    data_hooks = {"description": "Windows desktop notifications for Codex", "hooks": {}}
    if codex_hooks.exists():
        with open(codex_hooks, "r", encoding="utf-8") as f:
            data_hooks = json.load(f)
    if "hooks" not in data_hooks:
        data_hooks["hooks"] = {}
    data_hooks["hooks"]["PermissionRequest"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f'python "{codex_script}"',
                    "timeout": 5,
                    "statusMessage": "Sending Windows notification"
                }
            ]
        }
    ]
    with open(codex_hooks, "w", encoding="utf-8") as f:
        json.dump(data_hooks, f, indent=2)
    print("- Merged Codex hooks.json")
except Exception as e:
    print(f"- [WARN] Codex hooks.json error: {e}")

# 3. Google Antigravity (~/.gemini/settings.json & ~/.gemini/config/hooks.json)
gemini_script = user_home / ".gemini" / "hooks" / "notify-antigravity.py"
gemini_cmd = f'python "{gemini_script}"'

gemini_settings_file = user_home / ".gemini" / "settings.json"
try:
    sdata = {}
    if gemini_settings_file.exists():
        with open(gemini_settings_file, "r", encoding="utf-8") as f:
            sdata = json.load(f)
    if "hooks" not in sdata:
        sdata["hooks"] = {}
    sdata["hooks"]["PreInvocation"] = [
        {"type": "command", "command": gemini_cmd, "timeout": 5}
    ]
    sdata["hooks"]["PreToolUse"] = [
        {"matcher": "ask_question|AskUserQuestion", "hooks": [{"type": "command", "command": gemini_cmd, "timeout": 10}]}
    ]
    sdata["hooks"]["Stop"] = [
        {"type": "command", "command": gemini_cmd, "timeout": 10}
    ]
    sdata["hooks"]["Notification"] = [
        {"matcher": "permission_prompt|idle_prompt|agent_needs_input|agent_completed", "hooks": [{"type": "command", "command": gemini_cmd, "timeout": 10}]}
    ]
    gemini_settings_file.parent.mkdir(parents=True, exist_ok=True)
    with open(gemini_settings_file, "w", encoding="utf-8") as f:
        json.dump(sdata, f, indent=2)
    print("- Merged Antigravity settings.json")
except Exception as e:
    print(f"- [WARN] Antigravity settings.json error: {e}")

gemini_hooks_file = user_home / ".gemini" / "config" / "hooks.json"
try:
    gdata = {}
    if gemini_hooks_file.exists():
        with open(gemini_hooks_file, "r", encoding="utf-8") as f:
            gdata = json.load(f)
    gdata["desktop-notifier"] = {
        "PreInvocation": [
            {"type": "command", "command": gemini_cmd, "timeout": 5}
        ],
        "PreToolUse": [
            {"matcher": "ask_question|AskUserQuestion", "hooks": [{"type": "command", "command": gemini_cmd, "timeout": 10}]}
        ],
        "Stop": [
            {"type": "command", "command": gemini_cmd, "timeout": 10}
        ]
    }
    gemini_hooks_file.parent.mkdir(parents=True, exist_ok=True)
    with open(gemini_hooks_file, "w", encoding="utf-8") as f:
        json.dump(gdata, f, indent=2)
    print("- Merged Antigravity config/hooks.json")
except Exception as e:
    print(f"- [WARN] Antigravity hooks.json error: {e}")
"@

& $PythonExe -c $MergeScript

Write-Host "=== 6. Kiem tra thong bao thu nghiem ===" -ForegroundColor Cyan
& $PythonExe (Join-Path $LocalBin "multi-desktop-notify.py") `
    --app-name="AI Agent Notifier" `
    --title="Cài đặt thành công!" `
    --message="Hệ thống thông báo đa màn hình và Windows Toast đã sẵn sàng." `
    --timeout=4

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Cai dat anoti thanh cong tren Windows!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Ban co the chay lenh 'anoti' tu bat ky cua so PowerShell / CMD nao:"
Write-Host "   anoti status   # Kiem tra trang thai tich hop" -ForegroundColor Gray
Write-Host "   anoti test     # Ban thong bao thu nghiem len tat ca man hinh" -ForegroundColor Gray
Write-Host "   anoti focus    # Chuyen ngay den cua so AI agent dang cho" -ForegroundColor Gray
Write-Host "   anoti update   # Cap nhat phien ban moi nhat" -ForegroundColor Gray
Write-Host ""
Write-Host "Luu y: Hay tai lai cua so VS Code / IDE cua ban de ap dung hook:" -ForegroundColor Yellow
Write-Host "   Ctrl + Shift + P -> Developer: Reload Window" -ForegroundColor Yellow
Write-Host ""
