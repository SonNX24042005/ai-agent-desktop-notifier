#!/usr/bin/env bash

# Safe execution: errors in notification script must not crash Claude Code
set +e

MULTI_NOTIFY="/home/samer/.local/bin/multi-desktop-notify.py"
JQ="/usr/bin/jq"
PYTHON3="/usr/bin/python3"

SOUND_WARNING="/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
SOUND_COMPLETE="/usr/share/sounds/freedesktop/stereo/complete.oga"

payload="$(cat)"
if [ -z "$payload" ]; then
    exit 0
fi

event_name="$(printf '%s' "$payload" | $JQ -r '.hook_event_name // .event // ""' 2>/dev/null)"
notif_type="$(printf '%s' "$payload" | $JQ -r '.notification_type // .type // .matcher // ""' 2>/dev/null)"
tool_name="$(printf '%s' "$payload" | $JQ -r '.tool_name // ""' 2>/dev/null)"

title="Claude Code"
message=""
urgency="normal"
sound=""

questions_json=""
if [ "$event_name" = "PreToolUse" ] || [ "$tool_name" = "AskUserQuestion" ]; then
    urgency="critical"
    sound="$SOUND_WARNING"
    title="Claude Code: Câu hỏi"
    questions_json="$(printf '%s' "$payload" | $JQ -c '.tool_input // {}' 2>/dev/null)"
    question="$(printf '%s' "$payload" | $JQ -r '
        if .tool_input.questions then
            .tool_input.questions | map(.question // .title // "") | join("\n")
        elif .tool_input.question then
            .tool_input.question
        elif .tool_input.prompt then
            .tool_input.prompt
        else
            .message // "Claude đang đặt câu hỏi cho bạn"
        end
    ' 2>/dev/null)"
    message="$question"

elif [ "$notif_type" = "permission_prompt" ] || [ "$event_name" = "PermissionRequest" ]; then
    urgency="critical"
    sound="$SOUND_WARNING"
    if [ -n "$tool_name" ]; then
        title="Claude Code: Cần cấp quyền ($tool_name)"
    else
        title="Claude Code: Cần cấp quyền"
    fi
    detail="$(printf '%s' "$payload" | $JQ -r '
        .tool_input.description //
        .tool_input.command //
        .message //
        "Claude cần bạn cấp quyền thực thi."
    ' 2>/dev/null)"
    message="$detail"

elif [ "$notif_type" = "agent_completed" ] || [ "$event_name" = "Stop" ]; then
    urgency="normal"
    sound="$SOUND_COMPLETE"
    title="Claude Code: Hoàn thành"
    message="Claude đã hoàn thành trả lời."

elif [ "$notif_type" = "agent_needs_input" ] || [ "$notif_type" = "idle_prompt" ]; then
    urgency="critical"
    sound="$SOUND_WARNING"
    title="Claude Code: Chờ phản hồi"
    msg="$(printf '%s' "$payload" | $JQ -r '.message // "Claude đang chờ bạn phản hồi."' 2>/dev/null)"
    message="$msg"

else
    msg="$(printf '%s' "$payload" | $JQ -r '.message // .title // "Claude Code cần chú ý"' 2>/dev/null)"
    message="$msg"
    sound="$SOUND_WARNING"
fi

if [ -z "$message" ] || [ "$message" = "null" ]; then
    message="Claude Code đang chờ bạn."
fi

clean_message="$($PYTHON3 -c '
import sys
txt = " ".join(sys.argv[1].split())
if len(txt) > 400:
    txt = txt[:397] + "..."
print(txt)
' "$message" 2>/dev/null || printf '%s' "$message" | head -c 400)"

caller_window="$(xdotool getactivewindow 2>/dev/null || echo "")"
caller_pid="$$"

if [ -x "$MULTI_NOTIFY" ]; then
    setsid "$PYTHON3" "$MULTI_NOTIFY" \
        --app-name="Claude Code" \
        --title="$title" \
        --message="$clean_message" \
        --questions-json="$questions_json" \
        --urgency="$urgency" \
        --sound="$sound" \
        --window-id="$caller_window" \
        --caller-pid="$caller_pid" \
        --timeout=6 </dev/null >/dev/null 2>&1 &
    disown
fi

exit 0
