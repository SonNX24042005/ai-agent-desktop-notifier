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


def find_caller_tty(start_pid):
    """Find the pts inherited by the agent, even when the hook stdin is a pipe."""
    pid = int(start_pid or 0)
    visited = set()

    while pid > 1 and pid not in visited:
        visited.add(pid)
        for fd in (0, 1, 2):
            try:
                tty_path = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if tty_path.startswith("/dev/pts/"):
                return tty_path

        try:
            with open(f"/proc/{pid}/stat", "r") as stat_file:
                parent_pid = int(stat_file.read().split()[3])
        except (OSError, ValueError, IndexError):
            break
        pid = parent_pid

    return ""


def send_notification(title, message, urgency="normal", sound_path=None, questions_json=""):
    msg = clean_text(message)

    caller_window = ""
    try:
        caller_window = subprocess.check_output(["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        caller_window = ""

    caller_pid = os.getppid()
    caller_tty = find_caller_tty(os.getpid())
    terminal_screen = os.environ.get("GNOME_TERMINAL_SCREEN", "")
    project_hint = os.path.basename(os.getcwd().rstrip("/")) if os.getcwd() != "/" else ""

    if os.access(MULTI_NOTIFY, os.X_OK):
        try:
            cmd = [
                PYTHON3,
                MULTI_NOTIFY,
                "--app-name=Codex",
                f"--title={title}",
                f"--message={msg}",
                f"--urgency={urgency}",
                f"--window-id={caller_window}",
                f"--caller-pid={caller_pid}",
                f"--project-hint={project_hint}",
                f"--caller-tty={caller_tty}",
                f"--terminal-screen={terminal_screen}",
                "--timeout=6",
            ]
            if sound_path:
                cmd.append(f"--sound={sound_path}")
            if questions_json:
                cmd.append(f"--questions-json={questions_json}")

            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception:
            pass


def handle_completion(payload):
    msg_type = payload.get("type") or payload.get("hook_event_name")
    if msg_type != "agent-turn-complete":
        return

    send_notification(
        "Codex đã hoàn thành",
        "Codex đã hoàn thành lượt làm việc.",
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
            q_json = json.dumps(tool_input)
        else:
            detail = str(tool_input)
            q_json = ""

        send_notification(
            f"Codex cần cấp quyền: {tool_name}",
            detail,
            urgency="critical",
            sound_path=SOUND_WARNING,
            questions_json=q_json,
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
