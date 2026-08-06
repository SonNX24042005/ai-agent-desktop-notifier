#!/usr/bin/env python3

import json
import os
import subprocess
import sys

MULTI_NOTIFY = "/home/samer/.local/bin/multi-desktop-notify.py"
PYTHON3 = "/usr/bin/python3"
SOUND_WARNING = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
SOUND_COMPLETE = "/usr/share/sounds/freedesktop/stereo/complete.oga"


def clean_text(value, limit=400):
    text = " ".join(str(value or "").split())
    if not text:
        return "Codex cần bạn chú ý."
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def send_notification(title, message, urgency="normal", sound_path=None):
    msg = clean_text(message)

    if os.access(MULTI_NOTIFY, os.X_OK):
        try:
            cmd = [
                PYTHON3,
                MULTI_NOTIFY,
                "--app-name=Codex",
                f"--title={title}",
                f"--message={msg}",
                f"--urgency={urgency}",
                "--timeout=0",
            ]
            if sound_path:
                cmd.append(f"--sound={sound_path}")

            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        except Exception:
            pass


def handle_completion(payload):
    msg_type = payload.get("type") or payload.get("hook_event_name")
    if msg_type != "agent-turn-complete":
        return

    message = (
        payload.get("last-assistant-message")
        or payload.get("message")
        or "Codex đã hoàn thành lượt làm việc."
    )

    send_notification(
        "Codex đã hoàn thành",
        message,
        urgency="normal",
        sound_path=SOUND_COMPLETE,
    )


def handle_hook(payload):
    event = payload.get("hook_event_name") or payload.get("type")

    if event == "PermissionRequest":
        tool_name = payload.get("tool_name") or "công cụ"
        tool_input = payload.get("tool_input") or {}

        if isinstance(tool_input, dict):
            detail = (
                tool_input.get("description")
                or tool_input.get("command")
                or "Codex đang chờ bạn cấp quyền."
            )
        else:
            detail = str(tool_input)

        send_notification(
            f"Codex cần cấp quyền: {tool_name}",
            detail,
            urgency="critical",
            sound_path=SOUND_WARNING,
        )
    elif event == "agent-turn-complete":
        handle_completion(payload)


def main():
    try:
        if len(sys.argv) > 1 and sys.argv[1].strip():
            payload = json.loads(sys.argv[1])
            handle_completion(payload)
            return 0

        if not sys.stdin.isatty():
            input_data = sys.stdin.read().strip()
            if input_data:
                payload = json.loads(input_data)
                handle_hook(payload)
                return 0

    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
