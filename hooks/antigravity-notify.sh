#!/usr/bin/env bash

# Safe execution: errors in notification script must not crash Antigravity
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

title="Antigravity"
message=""
urgency="normal"
sound=""

questions_json=""
if [ "$event_name" = "PreToolUse" ] || [ "$tool_name" = "ask_question" ] || [ "$tool_name" = "AskUserQuestion" ]; then
    urgency="critical"
    sound="$SOUND_WARNING"
    title="Antigravity: Câu hỏi"
    questions_json="$(printf '%s' "$payload" | $JQ -c '.tool_input // {}' 2>/dev/null)"
    question="$(printf '%s' "$payload" | $JQ -r '
        if .tool_input.questions then
            .tool_input.questions | map(.question // .title // "") | join("\n")
        elif .tool_input.question then
            .tool_input.question
        elif .tool_input.prompt then
            .tool_input.prompt
        else
            .message // "Antigravity đang đặt câu hỏi cho bạn"
        end
    ' 2>/dev/null)"
    message="$question"

elif [ "$notif_type" = "permission_prompt" ] || [ "$event_name" = "PermissionRequest" ]; then
    urgency="critical"
    sound="$SOUND_WARNING"
    if [ -n "$tool_name" ]; then
        title="Antigravity: Cần cấp quyền ($tool_name)"
    else
        title="Antigravity: Cần cấp quyền"
    fi
    detail="$(printf '%s' "$payload" | $JQ -r '
        .tool_input.description //
        .tool_input.command //
        .message //
        "Antigravity cần bạn cấp quyền thực thi."
    ' 2>/dev/null)"
    message="$detail"

elif [ "$notif_type" = "agent_completed" ]; then
    urgency="normal"
    sound="$SOUND_COMPLETE"
    title="Antigravity: Hoàn thành"
    msg="$(printf '%s' "$payload" | $JQ -r '.message // .last_assistant_message // "Antigravity đã hoàn thành công việc."' 2>/dev/null)"
    message="$msg"

else
    msg="$(printf '%s' "$payload" | $JQ -r '.message // .title // "Antigravity cần chú ý"' 2>/dev/null)"
    message="$msg"
    sound="$SOUND_WARNING"
fi

if [ -z "$message" ] || [ "$message" = "null" ]; then
    message="Antigravity đang chờ bạn."
fi

clean_message="$($PYTHON3 -c '
import sys
txt = " ".join(sys.argv[1].split())
if len(txt) > 400:
    txt = txt[:397] + "..."
print(txt)
' "$message" 2>/dev/null || printf '%s' "$message" | head -c 400)"

if [ -x "$MULTI_NOTIFY" ]; then
    "$PYTHON3" "$MULTI_NOTIFY" \
        --app-name="Antigravity" \
        --title="$title" \
        --message="$clean_message" \
        --questions-json="$questions_json" \
        --urgency="$urgency" \
        --sound="$sound" \
        --timeout=0 >/dev/null 2>&1
fi

exit 0
