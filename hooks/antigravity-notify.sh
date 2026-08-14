#!/usr/bin/env bash

# Safe execution: errors in notification script must never crash or delay Antigravity
set +e

USER_HOME="${HOME:-/home/$USER}"
MULTI_NOTIFY="$USER_HOME/.local/bin/multi-desktop-notify.py"
PYTHON3="$(command -v python3 || echo /usr/bin/python3)"

SOUND_WARNING="/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
SOUND_COMPLETE="/usr/share/sounds/freedesktop/stereo/complete.oga"

payload="$(cat)"

# 0. Empty payload fast exit
if [ -z "$payload" ]; then
    echo "{}"
    exit 0
fi

# 1. Ignore background initialization from agent2agents immediately (0ms)
if [ "${AGENT2AGENTS_INITIALIZING:-0}" = "1" ] || [ "${A2A_SILENT:-0}" = "1" ]; then
    case "$payload" in
        *toolCall*) echo '{"decision": "allow"}' ;;
        *) echo "{}" ;;
    esac
    exit 0
fi

if [[ "$payload" == *"Initializing imported session history"* ]]; then
    case "$payload" in
        *toolCall*) echo '{"decision": "allow"}' ;;
        *) echo "{}" ;;
    esac
    exit 0
fi

# 1.5 Early session capture on PreInvocation (0ms execution in background)
if [[ "$payload" == *"invocationNum"* ]] || [[ "$payload" == *"PreInvocation"* ]]; then
    session_id="$(echo "$payload" | grep -o '"conversationId": *"[^"]*"' | head -n1 | cut -d'"' -f4)"
    if [ -n "$session_id" ] && [ -x "$MULTI_NOTIFY" ]; then
        caller_window="$(xdotool getactivewindow 2>/dev/null || echo "")"
        "$PYTHON3" "$MULTI_NOTIFY" --capture-session --session-id="$session_id" --window-id="$caller_window" &>/dev/null &
        disown
    fi
    echo "{}"
    exit 0
fi

# 2. Fast-path: If it is a toolCall but NOT a question tool, immediately allow and exit (0ms)
if [[ "$payload" == *"toolCall"* ]]; then
    if [[ "$payload" != *"ask_question"* ]] && [[ "$payload" != *"AskUserQuestion"* ]] && [[ "$payload" != *"ask_user"* ]]; then
        echo '{"decision": "allow"}'
        exit 0
    fi
fi

# 3. Check for genuine question vs completion
is_question=0
is_completion=0

if [[ "$payload" == *"ask_question"* ]] || [[ "$payload" == *"AskUserQuestion"* ]] || [[ "$payload" == *"ask_user"* ]]; then
    is_question=1
elif [[ "$payload" == *"terminationReason"* ]] || [[ "$payload" == *"\"Stop\""* ]] || [[ "$payload" == *"agent_completed"* ]]; then
    is_completion=1
fi

# If neither, exit immediately without doing any work
if [ "$is_question" -eq 0 ] && [ "$is_completion" -eq 0 ]; then
    case "$payload" in
        *toolCall*) echo '{"decision": "allow"}' ;;
        *) echo "{}" ;;
    esac
    exit 0
fi

# Fallback values
NOTIF_TITLE="Antigravity"
NOTIF_MSG="Antigravity đang chờ bạn."
NOTIF_URGENCY="normal"
NOTIF_SOUND="$SOUND_COMPLETE"
NOTIF_QUESTIONS=""
NOTIF_TIMEOUT="0"
SHOULD_NOTIFY="0"
OUTPUT_JSON="{}"

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
timeout = 0
should_notify = False
is_pre_tool = False
project_hint = os.path.basename((data.get("cwd") or os.getcwd()).rstrip("/"))
session_id = str(data.get("session_id") or data.get("sessionId") or data.get("conversationId") or data.get("conversation_id") or "")

tool_call = data.get("toolCall") or {}
tool_name = tool_call.get("name") or data.get("tool_name") or ""
tool_args = tool_call.get("args") or data.get("tool_input") or {}

termination_reason = data.get("terminationReason") or ""
event_name = data.get("hook_event_name") or data.get("event") or ""
notif_type = data.get("notification_type") or data.get("type") or ""

# Genuine question check
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

# Genuine task completion check
elif termination_reason or event_name in ["Stop", "agent_completed"] or notif_type == "agent_completed":
    should_notify = True
    title = "Antigravity: Hoàn thành"
    urgency = "normal"
    sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
    message = "Antigravity đã hoàn thành trả lời."
    timeout = 0

sn = "1" if should_notify else "0"
out_json = "{\"decision\": \"allow\"}" if is_pre_tool else "{}"

print(f"SHOULD_NOTIFY={sn}")
print(f"NOTIF_TITLE={shlex.quote(title)}")
print(f"NOTIF_MSG={shlex.quote(message)}")
print(f"NOTIF_URGENCY={shlex.quote(urgency)}")
print(f"NOTIF_SOUND={shlex.quote(sound)}")
print(f"NOTIF_QUESTIONS={shlex.quote(questions_json)}")
print(f"NOTIF_PROJECT_HINT={shlex.quote(project_hint)}")
print(f"NOTIF_SESSION_ID={shlex.quote(session_id)}")
print(f"NOTIF_TIMEOUT={timeout}")
print(f"OUTPUT_JSON={shlex.quote(out_json)}")
' "$payload")"

# Respond to Antigravity immediately so the agent engine never waits
echo "${OUTPUT_JSON:-{\}}"

if [ "$SHOULD_NOTIFY" = "1" ] && [ -x "$MULTI_NOTIFY" ]; then
    caller_window="$(xdotool getactivewindow 2>/dev/null || echo "")"
    caller_pid="$$"
    terminal_screen="${GNOME_TERMINAL_SCREEN:-}"

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
        --terminal-screen="$terminal_screen" \
        --session-id="${NOTIF_SESSION_ID:-}" \
        --timeout="${NOTIF_TIMEOUT:-5}" </dev/null >/dev/null 2>&1 &
    disown
fi

exit 0
