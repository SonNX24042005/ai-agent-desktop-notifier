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
timeout="5"
should_notify=0

questions_json=""
if [ "$event_name" = "PreToolUse" ] || [ "$tool_name" = "AskUserQuestion" ]; then
    if [ "$tool_name" = "AskUserQuestion" ] || [[ "$tool_name" =~ [Aa]sk ]]; then
        should_notify=1
        urgency="critical"
        sound="$SOUND_WARNING"
        title="Claude Code: Câu hỏi"
        timeout="0"
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
    fi

elif [ "$notif_type" = "permission_prompt" ] || [ "$event_name" = "PermissionRequest" ]; then
    should_notify=1
    urgency="critical"
    sound="$SOUND_WARNING"
    timeout="0"
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
    should_notify=1
    urgency="normal"
    sound="$SOUND_COMPLETE"
    title="Claude Code: Hoàn thành"
    message="Claude đã hoàn thành trả lời."
    timeout="5"
fi

if [ "$should_notify" -eq 0 ]; then
    exit 0
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

caller_window="$(xdotool getactivewindow 2>/dev/null || echo "")"
caller_pid="$$"
caller_tty="$(find_caller_tty "$caller_pid")"
terminal_screen="${GNOME_TERMINAL_SCREEN:-}"
cwd_path="$(printf '%s' "$payload" | $JQ -r '.cwd // ""' 2>/dev/null)"
project_hint=""
[ -n "$cwd_path" ] && project_hint="$(basename "$cwd_path" 2>/dev/null)"

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
        --project-hint="$project_hint" \
        --caller-tty="$caller_tty" \
        --terminal-screen="$terminal_screen" \
        --timeout="${timeout:-5}" </dev/null >/dev/null 2>&1 &
    disown
fi

exit 0
