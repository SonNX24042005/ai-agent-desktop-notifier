#!/usr/bin/env bash

# Safe execution: errors in notification script must never crash Antigravity
set +e

USER_HOME="${HOME:-/home/$USER}"
MULTI_NOTIFY="$USER_HOME/.local/bin/multi-desktop-notify.py"
PYTHON3="$(command -v python3 || echo /usr/bin/python3)"

SOUND_WARNING="/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
SOUND_COMPLETE="/usr/share/sounds/freedesktop/stereo/complete.oga"

payload="$(cat)"

# Fallback values
NOTIF_TITLE="Antigravity"
NOTIF_MSG="Antigravity đang chờ bạn."
NOTIF_URGENCY="normal"
NOTIF_SOUND="$SOUND_COMPLETE"
NOTIF_QUESTIONS=""
NOTIF_TIMEOUT="5"
SHOULD_NOTIFY="0"
OUTPUT_JSON="{}"

if [ -n "$payload" ]; then
    eval "$("$PYTHON3" -c '
import sys, json, shlex, os

raw_payload = sys.argv[1]
try:
    data = json.loads(raw_payload)
except Exception:
    data = {}

title = "Antigravity"
message = "Antigravity đang chờ bạn."
urgency = "normal"
sound = ""
questions_json = ""
timeout = 5
should_notify = False
is_pre_tool = False
project_hint = os.path.basename((data.get("cwd") or os.getcwd()).rstrip("/"))

tool_call = data.get("toolCall") or {}
tool_name = tool_call.get("name") or data.get("tool_name") or ""
tool_args = tool_call.get("args") or data.get("tool_input") or {}

termination_reason = data.get("terminationReason") or ""
event_name = data.get("hook_event_name") or data.get("event") or ""
notif_type = data.get("notification_type") or data.get("type") or ""

# 1. Check for genuine question / user input
if tool_name in ["ask_question", "AskUserQuestion", "ask_user"] or (event_name == "PreToolUse" and "ask" in tool_name.lower()):
    is_pre_tool = True
    should_notify = True
    title = "Antigravity: Câu hỏi"
    urgency = "critical"
    sound = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
    timeout = 0
    questions_json = json.dumps(tool_args) if tool_args else ""
    
    q_text = ""
    if isinstance(tool_args, dict):
        if "questions" in tool_args and isinstance(tool_args["questions"], list):
            q_text = " | ".join(q.get("question", "") for q in tool_args["questions"] if isinstance(q, dict) and q.get("question"))
        elif "question" in tool_args:
            q_text = str(tool_args["question"])
        elif "prompt" in tool_args:
            q_text = str(tool_args["prompt"])
    message = q_text or "Antigravity đang đặt câu hỏi cho bạn."

# 2. Check for tool executions - allow silently without popups
elif tool_name or event_name == "PreToolUse":
    is_pre_tool = True
    should_notify = False

# 3. Check for task completion (Stop / agent_completed)
elif termination_reason or event_name in ["Stop", "agent_completed"] or notif_type == "agent_completed":
    # Ignore background initialization prompts (e.g. from agent2agents)
    is_init_seed = (
        os.environ.get("AGENT2AGENTS_INITIALIZING") == "1"
        or os.environ.get("A2A_SILENT") == "1"
        or "Initializing imported session history" in raw_payload
    )
    if is_init_seed:
        should_notify = False
    else:
        should_notify = True
        title = "Antigravity: Hoàn thành"
        urgency = "normal"
        sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
        message = "Antigravity đã hoàn thành trả lời."
        timeout = 5

print(f"SHOULD_NOTIFY={\x271\x27 if should_notify else \x270\x27}")
print(f"NOTIF_TITLE={shlex.quote(title)}")
print(f"NOTIF_MSG={shlex.quote(message)}")
print(f"NOTIF_URGENCY={shlex.quote(urgency)}")
print(f"NOTIF_SOUND={shlex.quote(sound)}")
print(f"NOTIF_QUESTIONS={shlex.quote(questions_json)}")
print(f"NOTIF_PROJECT_HINT={shlex.quote(project_hint)}")
print(f"NOTIF_TIMEOUT={timeout}")
if is_pre_tool:
    print("OUTPUT_JSON=\"{\\\"decision\\\": \\\"allow\\\"}\"")
else:
    print("OUTPUT_JSON=\"{}\"")
' "$payload" 2>/dev/null)"

    caller_window="$(xdotool getactivewindow 2>/dev/null || echo "")"
    caller_pid="$$"

    find_caller_tty() {
        local pid="$1"
        local tty_path=""
        local parent_pid=""
        local fd=""

        while [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null; do
            for fd in 0 1 2; do
                tty_path="$(readlink "/proc/$pid/fd/$fd" 2>/dev/null || echo "")"
                case "$tty_path" in
                    /dev/pts/*) printf '%s' "$tty_path"; return 0 ;;
                esac
            done
            parent_pid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null || echo "")"
            [ "$parent_pid" = "$pid" ] && break
            pid="$parent_pid"
        done
    }

    caller_tty="$(find_caller_tty "$caller_pid")"
    terminal_screen="${GNOME_TERMINAL_SCREEN:-}"

    if [ "$SHOULD_NOTIFY" = "1" ] && [ -x "$MULTI_NOTIFY" ]; then
        setsid "$PYTHON3" "$MULTI_NOTIFY" \
            --app-name="Antigravity" \
            --title="${NOTIF_TITLE:-Antigravity}" \
            --message="${NOTIF_MSG:-Antigravity đang chờ bạn.}" \
            --questions-json="${NOTIF_QUESTIONS:-}" \
            --urgency="${NOTIF_URGENCY:-normal}" \
            --sound="${NOTIF_SOUND:-$SOUND_COMPLETE}" \
            --window-id="$caller_window" \
            --caller-pid="$caller_pid" \
            --project-hint="${NOTIF_PROJECT_HINT:-}" \
            --caller-tty="$caller_tty" \
            --terminal-screen="$terminal_screen" \
            --timeout="${NOTIF_TIMEOUT:-5}" </dev/null >/dev/null 2>&1 &
        disown
    fi
fi

# Antigravity hook protocol requires JSON on stdout
echo "${OUTPUT_JSON:-{\}}"
exit 0
