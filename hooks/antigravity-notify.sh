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
OUTPUT_JSON="{}"

if [ -n "$payload" ]; then
    eval "$("$PYTHON3" -c '
import sys, json, shlex

try:
    data = json.loads(sys.argv[1])
except Exception:
    data = {}

title = "Antigravity"
message = "Antigravity đang chờ bạn."
urgency = "normal"
sound = ""
questions_json = ""
is_pre_tool = False

tool_call = data.get("toolCall") or {}
tool_name = tool_call.get("name") or data.get("tool_name") or ""
tool_args = tool_call.get("args") or data.get("tool_input") or {}

termination_reason = data.get("terminationReason") or ""
event_name = data.get("hook_event_name") or data.get("event") or ""
notif_type = data.get("notification_type") or data.get("type") or ""

if tool_name in ["ask_question", "AskUserQuestion"] or (event_name == "PreToolUse" and "ask" in tool_name.lower()):
    is_pre_tool = True
    title = "Antigravity: Câu hỏi"
    urgency = "critical"
    sound = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
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

elif tool_name:
    is_pre_tool = True
    title = f"Antigravity: Thao tác ({tool_name})"
    urgency = "normal"
    sound = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
    desc = ""
    if isinstance(tool_args, dict):
        desc = tool_args.get("Description") or tool_args.get("CommandLine") or tool_args.get("TargetFile") or ""
    message = desc or f"Antigravity đang thực hiện {tool_name}."

elif termination_reason or event_name in ["Stop", "agent_completed"] or notif_type == "agent_completed":
    title = "Antigravity: Hoàn thành"
    urgency = "normal"
    sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
    message = "Antigravity đã hoàn thành trả lời."

elif data.get("message") or data.get("title"):
    title = data.get("title") or "Antigravity"
    message = data.get("message") or "Antigravity cần chú ý."
    sound = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"

print(f"NOTIF_TITLE={shlex.quote(title)}")
print(f"NOTIF_MSG={shlex.quote(message)}")
print(f"NOTIF_URGENCY={shlex.quote(urgency)}")
print(f"NOTIF_SOUND={shlex.quote(sound)}")
print(f"NOTIF_QUESTIONS={shlex.quote(questions_json)}")
if is_pre_tool:
    print("OUTPUT_JSON=\"{\\\"decision\\\": \\\"allow\\\"}\"")
else:
    print("OUTPUT_JSON=\"{}\"")
' "$payload" 2>/dev/null)"

    caller_window="$(xdotool getactivewindow 2>/dev/null || echo "")"
    caller_pid="$$"

    if [ -x "$MULTI_NOTIFY" ]; then
        setsid "$PYTHON3" "$MULTI_NOTIFY" \
            --app-name="Antigravity" \
            --title="${NOTIF_TITLE:-Antigravity}" \
            --message="${NOTIF_MSG:-Antigravity đang chờ bạn.}" \
            --questions-json="${NOTIF_QUESTIONS:-}" \
            --urgency="${NOTIF_URGENCY:-normal}" \
            --sound="${NOTIF_SOUND:-$SOUND_COMPLETE}" \
            --window-id="$caller_window" \
            --caller-pid="$caller_pid" \
            --timeout=6 </dev/null >/dev/null 2>&1 &
        disown
    fi
fi

# Antigravity hook protocol requires JSON on stdout
echo "${OUTPUT_JSON:-{\}}"
exit 0
