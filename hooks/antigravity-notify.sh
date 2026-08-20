#!/usr/bin/env python3
"""
Antigravity lifecycle hook handler for desktop notifications.
Executes in 0ms, logs diagnostic payload, and triggers multi-monitor popups.
"""

import sys
import json
import os
import subprocess

USER_HOME = os.environ.get("HOME") or os.path.expanduser("~")
MULTI_NOTIFY = os.path.join(USER_HOME, ".local", "bin", "multi-desktop-notify.py")
PYTHON3 = sys.executable or "/usr/bin/python3"

# 0. Read stdin
try:
    raw_payload = sys.stdin.read()
except Exception:
    raw_payload = ""

# Debug log to verify Antigravity hook execution
try:
    with open("/tmp/antigravity_hook_debug.log", "a") as f:
        f.write(f"[{os.getpid()}] {raw_payload.strip()}\n")
except Exception:
    pass

if not raw_payload.strip():
    print("{}")
    sys.exit(0)

try:
    data = json.loads(raw_payload)
except Exception:
    data = {}

# 1. Fast-path: Ignore idle_prompt, agent_needs_input, and background initialization immediately (0ms)
if (
    os.environ.get("AGENT2AGENTS_INITIALIZING") == "1"
    or os.environ.get("A2A_SILENT") == "1"
    or data.get("notification_type") in ["idle_prompt", "agent_needs_input"]
    or data.get("hook_event_name") in ["idle_prompt", "agent_needs_input"]
    or data.get("event") in ["idle_prompt", "agent_needs_input"]
):
    print('{"decision": "allow"}' if "toolCall" in data else "{}")
    sys.exit(0)

# 2. Extract metadata
conversation_id = str(data.get("conversationId") or data.get("session_id") or data.get("sessionId") or "")
workspace_paths = data.get("workspacePaths") or []
project_hint = os.path.basename(workspace_paths[0]) if workspace_paths else os.path.basename(os.getcwd())

tool_call = data.get("toolCall") or {}
tool_name = tool_call.get("name") or data.get("tool_name") or ""
tool_args = tool_call.get("args") or data.get("tool_input") or {}

termination_reason = data.get("terminationReason") or ""
event_name = data.get("hook_event_name") or data.get("event") or ""
notif_type = data.get("notification_type") or data.get("type") or ""

def resolve_env():
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        for disp in [":1", ":0"]:
            if os.path.exists(f"/tmp/.X11-unix/X{disp.lstrip(':')}"):
                env["DISPLAY"] = disp
                break
        else:
            env["DISPLAY"] = ":1"

    uid = os.getuid()
    if not env.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

    if not env.get("XAUTHORITY"):
        for xauth_path in [
            f"/run/user/{uid}/gdm/Xauthority",
            os.path.expanduser("~/.Xauthority"),
            f"/run/user/{uid}/.Xauthority",
        ]:
            if os.path.exists(xauth_path):
                env["XAUTHORITY"] = xauth_path
                break
    return env


# 3. PreInvocation - early session capture
if "invocationNum" in data or event_name == "PreInvocation":
    if conversation_id and os.path.exists(MULTI_NOTIFY):
        env = resolve_env()
        try:
            caller_win = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=1, env=env).stdout.strip()
        except Exception:
            caller_win = ""
        subprocess.Popen([
            PYTHON3, MULTI_NOTIFY,
            "--capture-session",
            f"--session-id={conversation_id}",
            f"--window-id={caller_win}",
            f"--project-hint={project_hint}",
        ], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("{}")
    sys.exit(0)

def is_genuine_antigravity_completion(payload):
    """
    Strictly verifies if Antigravity has genuinely completed its turn and is waiting
    for the user's next prompt, preventing premature completion notifications during:
    - Multi-step tool executions
    - Active background tasks / timers (status == RUNNING)
    - Interim status messages
    - Errors / Quota exhaustion
    - ask_question modal wait states
    """
    # 1. If explicitly not fully idle, the agent is still running / has pending tasks
    if payload.get("fullyIdle") is False:
        return False

    # 2. If there is a fatal error, do not notify completion
    if payload.get("error"):
        return False

    term_reason = str(payload.get("terminationReason") or "").upper()
    if term_reason == "ERROR":
        return False

    evt = payload.get("hook_event_name") or payload.get("event") or ""
    n_type = payload.get("notification_type") or payload.get("type") or ""

    is_stop_event = (
        bool(term_reason)
        or evt in ["Stop", "agent_completed"]
        or n_type == "agent_completed"
        or payload.get("fullyIdle") is True
    )
    if not is_stop_event:
        return False

    # 3. Check transcript for active background tasks, pending questions, or interim status
    transcript_path = payload.get("transcriptPath")
    if transcript_path:
        candidate_paths = [
            transcript_path.replace("transcript_full.jsonl", "transcript.jsonl"),
            transcript_path,
        ]
        for path in candidate_paths:
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        f.seek(0, os.SEEK_END)
                        size = f.tell()
                        # Read last 64KB
                        f.seek(max(0, size - 65536), os.SEEK_SET)
                        chunk = f.read().decode("utf-8", errors="ignore")
                    
                    lines = [l.strip() for l in chunk.splitlines() if l.strip()]
                    running_tasks = set()
                    completed_tasks = set()
                    steps = []

                    for line in lines:
                        try:
                            step = json.loads(line)
                            steps.append(step)

                            # Track background tasks launched
                            if step.get("status") == "RUNNING":
                                content = step.get("content", "")
                                if "task id:" in content:
                                    tid = content.split("task id:")[1].split()[0].strip()
                                    running_tasks.add(tid)

                            # Track background tasks finished
                            if step.get("source") == "SYSTEM" and step.get("type") == "SYSTEM_MESSAGE":
                                content = step.get("content", "")
                                if "Task id \"" in content and "finished with result" in content:
                                    tid = content.split("Task id \"")[1].split("\"")[0].strip()
                                    completed_tasks.add(tid)
                        except Exception:
                            continue

                    # If any background task is still running, the agent is NOT finished
                    active_tasks = running_tasks - completed_tasks
                    if len(active_tasks) > 0:
                        return False

                    # Check recent model steps in reverse
                    for step in reversed(steps):
                        if step.get("source") == "MODEL" and step.get("type") == "PLANNER_RESPONSE":
                            tool_calls = step.get("tool_calls") or []
                            # If the model called an ask tool, it is waiting for user response, NOT completed
                            if any("ask" in (tc.get("name") or "").lower() for tc in tool_calls):
                                return False
                            # If tool calls were made in this step, this was an intermediate step
                            if tool_calls and len(tool_calls) > 0:
                                return False
                            break
                except Exception:
                    pass

    return True


# 4. Check Question tool vs Completion vs Other tools
is_question = tool_name in ["ask_question", "AskUserQuestion", "ask_user"] or ("ask" in tool_name.lower())
is_completion = False if is_question else is_genuine_antigravity_completion(data)

# Fast exit if tool call is not a question tool
if "toolCall" in data and not is_question:
    print('{"decision": "allow"}')
    sys.exit(0)

# Fast exit if neither question nor completion
if not is_question and not is_completion:
    print('{"decision": "allow"}' if "toolCall" in data else "{}")
    sys.exit(0)

# 5. Build Notification Details
title = "Antigravity"
message = "Antigravity đang chờ bạn."
urgency = "normal"
sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
questions_json = ""
timeout = 0
is_pre_tool = False

if is_question:
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

elif is_completion:
    title = "Antigravity: Hoàn thành"
    urgency = "normal"
    sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
    message = "Antigravity đã hoàn thành trả lời."

# Print response to agent loop immediately
print('{"decision": "allow"}' if is_pre_tool else "{}")
sys.stdout.flush()

# 6. Trigger popup asynchronously
if os.path.exists(MULTI_NOTIFY) and os.access(MULTI_NOTIFY, os.X_OK):
    env = resolve_env()
    try:
        caller_win = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=1, env=env).stdout.strip()
    except Exception:
        caller_win = ""
    cmd = [
        PYTHON3, MULTI_NOTIFY,
        "--app-name=Antigravity",
        f"--title={title}",
        f"--message={message}",
        f"--questions-json={questions_json}",
        f"--urgency={urgency}",
        f"--sound={sound}",
        f"--window-id={caller_win}",
        f"--caller-pid={os.getpid()}",
        f"--project-hint={project_hint}",
        f"--session-id={conversation_id}",
        f"--timeout={timeout}",
    ]
    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

sys.exit(0)
