#!/usr/bin/env python3
"""
Multi-Monitor Desktop Notifier & Window Focuser for AI Coding Agents
(Claude Code, Codex, Google Antigravity, etc.)

Renders lightweight dark-themed desktop notification banners across connected monitors.
When clicked (or when clicking "Đến cửa sổ ứng dụng"), it automatically focuses and
brings to front the exact application window (VS Code or Terminal) that triggered the notification.
"""

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time

PID_FILE = "/tmp/ai_agent_notifier.pid"
SESSION_CACHE_FILE = "/tmp/ai_agent_notifier_sessions.json"
DEDUPE_CACHE_FILE = "/tmp/ai_agent_notifier_dedupe.json"
QUEUE_CACHE_FILE = "/tmp/ai_agent_notifier_queue.json"
CONFIG_FILE = os.path.expanduser("~/.config/ai-agent-notifier/config.json")

# Ensure DISPLAY and XAUTHORITY are available in background hook processes
if not os.environ.get("DISPLAY"):
    for disp in [":1", ":0"]:
        if os.path.exists(f"/tmp/.X11-unix/X{disp.lstrip(':')}"):
            os.environ["DISPLAY"] = disp
            break
    else:
        os.environ["DISPLAY"] = ":1"

if not os.environ.get("XDG_RUNTIME_DIR"):
    os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

if not os.environ.get("XAUTHORITY"):
    uid = os.getuid()
    for xauth_path in [
        f"/run/user/{uid}/gdm/Xauthority",
        os.path.expanduser("~/.Xauthority"),
        f"/run/user/{uid}/.Xauthority",
    ]:
        if os.path.exists(xauth_path):
            os.environ["XAUTHORITY"] = xauth_path
            break


def save_session_window(session_id, window_id, project_hint="", pid=0):
    """Caches target window ID for a session ID to enable 100% precision focus."""
    if not session_id or not window_id:
        return
    if not is_developer_window(window_id):
        return
    sessions = {}
    if os.path.exists(SESSION_CACHE_FILE):
        try:
            with open(SESSION_CACHE_FILE, "r") as f:
                sessions = json.load(f)
        except Exception:
            sessions = {}
    sessions[str(session_id)] = {
        "window_id": str(window_id).strip(),
        "project_hint": str(project_hint or "").strip(),
        "pid": int(pid or 0),
        "updated_at": time.time(),
    }
    now = time.time()
    # Prune old sessions older than 24 hours
    sessions = {k: v for k, v in sessions.items() if now - v.get("updated_at", 0) < 86400}
    try:
        with open(SESSION_CACHE_FILE, "w") as f:
            json.dump(sessions, f)
    except Exception:
        pass


def get_session_window(session_id):
    """Retrieves cached window ID for a session."""
    if not session_id or not os.path.exists(SESSION_CACHE_FILE):
        return ""
    try:
        with open(SESSION_CACHE_FILE, "r") as f:
            sessions = json.load(f)
        s_info = sessions.get(str(session_id))
        wid = ""
        if s_info and isinstance(s_info, dict):
            wid = s_info.get("window_id", "")
        elif isinstance(s_info, str):
            wid = s_info
        if wid and is_developer_window(wid):
            return wid
    except Exception:
        pass
    return ""


def is_duplicate_notification(app_name, title, message, dedupe_seconds=2):
    """Checks and sets deduplication state to prevent notification spam."""
    if dedupe_seconds <= 0:
        return False
    import hashlib
    key_raw = f"{app_name}|{title}|{message}"
    key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    now = time.time()
    dedupe_data = {}
    if os.path.exists(DEDUPE_CACHE_FILE):
        try:
            with open(DEDUPE_CACHE_FILE, "r") as f:
                dedupe_data = json.load(f)
        except Exception:
            dedupe_data = {}
    # Prune expired keys (older than 60s)
    dedupe_data = {k: v for k, v in dedupe_data.items() if now - v < 60}
    last_time = dedupe_data.get(key, 0)
    if now - last_time < dedupe_seconds:
        return True
    dedupe_data[key] = now
    try:
        with open(DEDUPE_CACHE_FILE, "w") as f:
            json.dump(dedupe_data, f)
    except Exception:
        pass
    return False


def dispatch_webhooks_async(app_name, title, message):
    """Dispatches webhooks asynchronously to external channels if configured."""
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        webhooks = cfg.get("webhooks", {})
        if not webhooks or not isinstance(webhooks, dict):
            return

        import threading
        import urllib.parse
        import urllib.request

        def send_all():
            payload_text = f"[{app_name}] {title}\n{message}"
            for name, url in webhooks.items():
                if not url or not str(url).startswith("http"):
                    continue
                try:
                    headers = {"Content-Type": "application/json"}
                    if "feishu" in name.lower() or "lark" in name.lower():
                        data = json.dumps({"msg_type": "text", "content": {"text": payload_text}}).encode()
                    elif "dingtalk" in name.lower():
                        data = json.dumps({"msgtype": "text", "text": {"content": payload_text}}).encode()
                    elif "slack" in name.lower() or "discord" in name.lower():
                        data = json.dumps({"text": payload_text}).encode()
                    elif "bark" in name.lower():
                        req = urllib.request.Request(f"{url.rstrip('/')}/{urllib.parse.quote(title)}/{urllib.parse.quote(message)}")
                        urllib.request.urlopen(req, timeout=3)
                        continue
                    elif "ntfy" in name.lower():
                        req = urllib.request.Request(url, data=message.encode("utf-8"), headers={"Title": title, "Priority": "urgent"})
                        urllib.request.urlopen(req, timeout=3)
                        continue
                    else:
                        data = json.dumps({"title": title, "message": message, "app": app_name}).encode()
                    req = urllib.request.Request(url, data=data, headers=headers)
                    urllib.request.urlopen(req, timeout=3)
                except Exception:
                    pass

        threading.Thread(target=send_all, daemon=True).start()
    except Exception:
        pass


def kill_previous_instance():
    """Ensure new notification replaces any active popup to prevent stacking."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and old_pid > 1:
                try:
                    with open(f"/proc/{old_pid}/cmdline", "rb") as cf:
                        cmdline = cf.read().decode(errors="ignore")
                    if "multi-desktop-notify" in cmdline:
                        os.kill(old_pid, signal.SIGTERM)
                except OSError:
                    pass
        except Exception:
            pass
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def send_fallback_notify(app_name, title, message, urgency="normal", timeout=0):
    """Fallback standard desktop notification using notify-send if GUI popup fails."""
    try:
        cmd = ["notify-send", f"[{app_name}] {title}", message, "-u", urgency]
        if timeout > 0:
            cmd.extend(["-t", str(timeout * 1000)])
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass


def clean_text(value, limit=300):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def play_sound_async(sound_path):
    if not sound_path or not os.path.isfile(sound_path):
        return

    players = ["/usr/bin/paplay", "/usr/bin/pw-play", "/usr/bin/canberra-gtk-play", "/usr/bin/aplay"]
    for player in players:
        if os.access(player, os.X_OK):
            try:
                cmd = [player, sound_path] if player != "/usr/bin/canberra-gtk-play" else [player, "-f", sound_path]
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                pass


def is_valid_toplevel_window(wid):
    """
    Checks if WID is a valid managed toplevel window (has _NET_WM_STATE property).
    Filters out internal non-toplevel container windows.
    """
    if not wid or not str(wid).strip().isdigit():
        return False
    try:
        out = subprocess.check_output(["xprop", "-id", str(wid).strip(), "_NET_WM_STATE"], stderr=subprocess.DEVNULL).decode()
        return "_NET_WM_STATE" in out and "not found" not in out
    except Exception:
        return False


def find_window_title(wid):
    try:
        return subprocess.check_output(["xdotool", "getwindowname", str(wid)], stderr=subprocess.DEVNULL).decode().strip().lower()
    except Exception:
        return ""


def write_terminal_control(tty_path, sequence):
    """Write a terminal control sequence only to a real pts device."""
    if not tty_path or not str(tty_path).startswith("/dev/pts/"):
        return False

    try:
        tty_stat = os.stat(tty_path)
        if not stat.S_ISCHR(tty_stat.st_mode):
            return False
        fd = os.open(tty_path, os.O_WRONLY | os.O_NOCTTY)
        try:
            os.write(fd, sequence.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except Exception:
        return False


def find_marker_window(marker):
    try:
        result = subprocess.check_output(
            ["xdotool", "search", "--name", marker],
            stderr=subprocess.DEVNULL,
        )
        for wid in result.decode().splitlines():
            wid = wid.strip()
            if is_valid_toplevel_window(wid):
                return wid
    except Exception:
        pass
    return ""


def gnome_terminal_window_states():
    """Return (D-Bus window path, active tab index) pairs for GNOME Terminal."""
    try:
        output = subprocess.check_output(
            [
                "gdbus",
                "introspect",
                "--session",
                "--dest",
                "org.gnome.Terminal",
                "--object-path",
                "/org/gnome/Terminal/window",
            ],
            stderr=subprocess.DEVNULL,
            timeout=0.5,
        ).decode()
    except Exception:
        return []

    paths = []
    for line in output.splitlines():
        match = re.match(r"\s*node ([0-9]+) \{", line)
        if match:
            paths.append(f"/org/gnome/Terminal/window/{match.group(1)}")

    states = []
    for path in paths:
        try:
            describe = subprocess.check_output(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.gnome.Terminal",
                    "--object-path",
                    path,
                    "--method",
                    "org.gtk.Actions.Describe",
                    "active-tab",
                ],
                stderr=subprocess.DEVNULL,
                timeout=0.5,
            ).decode()
            match = re.search(r"\(\((true|false), signature 'i', \[<([0-9]+)>\]", describe)
            if match and match.group(1) == "true":
                states.append((path, int(match.group(2))))
        except Exception:
            continue
    return states


def activate_gnome_terminal_tab(window_path, tab_index):
    try:
        subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Terminal",
                "--object-path",
                window_path,
                "--method",
                "org.gtk.Actions.Activate",
                "active-tab",
                f"[<{tab_index}>]",
                "{}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
            check=False,
        )
    except Exception:
        pass


def find_gnome_terminal_tab_window(marker):
    """Select the GNOME Terminal tab carrying marker and return its X11 window."""
    states = gnome_terminal_window_states()
    if not states:
        return ""

    target_path = ""
    target_window = ""
    try:
        for window_path, original_index in states:
            # GNOME's built-in tab shortcuts support at least ten tabs. Trying
            # a bounded range also works for windows with more tabs: invalid
            # indices are harmless no-ops.
            for tab_index in range(10):
                activate_gnome_terminal_tab(window_path, tab_index)
                time.sleep(0.06)
                target_window = find_marker_window(marker)
                if target_window:
                    target_path = window_path
                    return target_window
    finally:
        # Keep the source tab selected when found; restore every tab touched
        # during the scan so unrelated terminal windows are not changed.
        for window_path, original_index in states:
            if window_path != target_path:
                activate_gnome_terminal_tab(window_path, original_index)

    return ""


def find_window_by_tty(tty_path, terminal_screen=""):
    """
    Resolves a GNOME Terminal window from the agent's controlling pts.

    GNOME Terminal puts all tabs/windows under one server PID, so
    ``xdotool search --pid`` cannot distinguish them. VTE supports a title
    stack; use a unique temporary title to identify the X11 window, then pop
    the original title back immediately.
    """
    if not tty_path or not str(tty_path).startswith("/dev/pts/"):
        return ""

    marker = f"AI_NOTIFY_{os.getpid()}_{time.monotonic_ns()}"
    pushed_title = write_terminal_control(tty_path, f"\x1b[22;0t\x1b]0;{marker}\x07")
    if not pushed_title:
        return ""

    try:
        deadline = time.monotonic() + 0.75
        while time.monotonic() < deadline:
            marker_window = find_marker_window(marker)
            if marker_window:
                return marker_window
            time.sleep(0.05)

        # A marker in a hidden tab is not reflected in the parent window title.
        # When the hook inherited GNOME_TERMINAL_SCREEN, briefly select tabs via
        # the GNOME Terminal action API to locate that hidden tab as well.
        if terminal_screen.startswith("/org/gnome/Terminal/screen/"):
            marker_window = find_gnome_terminal_tab_window(marker)
            if marker_window:
                return marker_window
    finally:
        # Restore the title saved by VTE's title stack. This keeps the lookup
        # invisible to the user even when the terminal is not focused.
        write_terminal_control(tty_path, "\x1b[23;0t")

    return ""


def get_process_ancestors(pid):
    ancestors = set()
    curr = int(pid or 0)
    visited = set()
    while curr > 1 and curr not in visited:
        visited.add(curr)
        ancestors.add(curr)
        try:
            with open(f"/proc/{curr}/stat", "r") as f:
                curr = int(f.read().split()[3])
        except Exception:
            break
    return ancestors


def get_window_wm_class(wid):
    """Returns the WM_CLASS tuple (instance, class_name) in lowercase."""
    if not wid or not str(wid).strip().isdigit():
        return ("", "")
    try:
        out = subprocess.check_output(["xprop", "-id", str(wid).strip(), "WM_CLASS"], stderr=subprocess.DEVNULL).decode()
        matches = re.findall(r'"([^"]*)"', out)
        if len(matches) >= 2:
            return (matches[0].lower(), matches[1].lower())
        elif len(matches) == 1:
            return (matches[0].lower(), matches[0].lower())
    except Exception:
        pass
    return ("", "")


DEVELOPER_CLASSES = {
    # Terminals
    "gnome-terminal", "gnome-terminal-server", "tilix", "alacritty", "kitty",
    "wezterm", "xfce4-terminal", "konsole", "terminator", "xterm", "uxterm",
    "urxvt", "rxvt", "foot", "contour", "ptyxis", "hyper", "tabby", "rio",
    # IDEs & Editors
    "code", "vscodium", "cursor", "windsurf", "antigravity", "zed",
    "pycharm", "pycharm-community", "idea", "idea-ce", "clion", "webstorm",
    "goland", "phpstorm", "rider", "rubymine", "datagrip", "fleet",
    "sublime_text", "subl", "gedit", "kate", "emacs", "neovim", "gvim",
}

EXCLUDED_CLASSES = {
    # File managers
    "nemo", "nautilus", "dolphin", "thunar", "pcmanfm", "caja", "krusader", "doublecmd",
    # System / Window frames / Desktop
    "mutter-x11-frames", "desktop_window", "desktop", "gala-other", "cinnamon",
    # PDF & Document viewers
    "okular", "evince", "atril", "xreader", "zathura", "acroread", "libreoffice",
    # Media & Browsers & Chat
    "spotify", "vlc", "mpv", "discord", "slack", "telegram-desktop",
}


def is_developer_window(wid):
    """
    Checks whether a window belongs to a known developer host (IDE, code editor, or terminal).
    Guarantees that file managers (Nemo/Nautilus), PDF viewers (Okular), and system window frames
    are never targeted or cached.
    """
    if not is_valid_toplevel_window(wid):
        return False
    inst, cls = get_window_wm_class(wid)
    if not inst and not cls:
        return False
    if any(ex in inst or ex in cls for ex in EXCLUDED_CLASSES):
        return False
    if any(dev in inst or dev in cls for dev in DEVELOPER_CLASSES):
        return True
    # If title contains strong terminal / IDE indicators
    title = find_window_title(wid)
    if any(app in title for app in ["visual studio code", "code", "terminal", "alacritty", "kitty", "tmux", "bash", "zsh"]):
        return True
    return False


def get_all_managed_windows():
    results = []
    try:
        out = subprocess.check_output(["xdotool", "search", "--onlyvisible", ""], stderr=subprocess.DEVNULL).decode()
        for wid in out.splitlines():
            wid = wid.strip()
            if not wid or not is_valid_toplevel_window(wid) or not is_developer_window(wid):
                continue
            try:
                name = subprocess.check_output(["xdotool", "getwindowname", wid], stderr=subprocess.DEVNULL).decode().strip()
                if not name:
                    continue
                wpid_str = subprocess.check_output(["xdotool", "getwindowpid", wid], stderr=subprocess.DEVNULL).decode().strip()
                wpid = int(wpid_str) if wpid_str.isdigit() else 0
                results.append((wid, name, wpid))
            except Exception:
                pass
    except Exception:
        pass
    return results


def find_target_window(window_id_arg="", caller_pid=None, project_hint="", caller_tty="", terminal_screen="", session_id=""):
    """
    Finds the exact X11 window ID for the application (VS Code, GNOME Terminal, Alacritty, Kitty)
    that triggered the notification with 100% precision.
    """
    # 0. Tier 0: Check session cache if session_id is provided
    if session_id:
        cached_wid = get_session_window(session_id)
        if cached_wid and is_developer_window(cached_wid):
            return cached_wid

    project_hint = (project_hint or "").strip().lower()
    managed_windows = get_all_managed_windows()

    # 1. Tier 1: Match by PID tree + project_hint (Exact process owner)
    if caller_pid:
        pid_tree = get_process_ancestors(caller_pid)
        tree_windows = [(wid, name) for wid, name, wpid in managed_windows if wpid in pid_tree and is_developer_window(wid)]
        if tree_windows:
            if project_hint:
                for wid, name in tree_windows:
                    if project_hint in name.lower():
                        if session_id:
                            save_session_window(session_id, wid, project_hint, caller_pid)
                        return wid
            wid = tree_windows[0][0]
            if session_id:
                save_session_window(session_id, wid, project_hint, caller_pid)
            return wid

    # 2. Tier 2: Match project_hint in window title across open developer windows
    # (Matches the specific VS Code workspace or Terminal project folder even if user switched away to browser)
    if project_hint:
        for wid, name, wpid in managed_windows:
            if project_hint in name.lower():
                if session_id:
                    save_session_window(session_id, wid, project_hint, wpid)
                return wid

    # 3. Tier 3: Match window from TTY / pts if provided
    if caller_tty:
        tty_window = find_window_by_tty(caller_tty, terminal_screen=terminal_screen)
        if tty_window and is_developer_window(tty_window):
            if session_id:
                save_session_window(session_id, tty_window, project_hint, caller_pid)
            return tty_window

    # 4. Tier 4: Explicit window_id_arg if valid
    if window_id_arg and str(window_id_arg).strip().isdigit():
        wid = str(window_id_arg).strip()
        if is_developer_window(wid):
            if session_id:
                save_session_window(session_id, wid, project_hint, caller_pid)
            return wid

    # 5. Tier 5: Fallback to active window
    try:
        res = subprocess.check_output(["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL)
        active_wid = res.decode().strip()
        if active_wid and is_developer_window(active_wid):
            if session_id:
                save_session_window(session_id, active_wid, project_hint, caller_pid)
            return active_wid
    except Exception:
        pass

    return ""


def get_current_active_window():
    """Returns the currently active X11 window ID in decimal format, or empty string."""
    try:
        res = subprocess.check_output(["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL)
        wid = res.decode().strip()
        if wid.isdigit():
            return wid
    except Exception:
        pass
    return ""


def is_target_window_active(active_wid, target_wid="", caller_pid=0, project_hint="", session_id=""):
    """
    Checks if active_wid corresponds to the target application window that triggered the notification.
    """
    if not active_wid or not str(active_wid).strip().isdigit():
        return False
    active_wid_str = str(active_wid).strip()

    # 1. Direct match with target_wid
    if target_wid and str(target_wid).strip().isdigit():
        if active_wid_str == str(target_wid).strip():
            return True

    # 2. Match with cached session window
    if session_id:
        cached_wid = get_session_window(session_id)
        if cached_wid and str(cached_wid).strip() == active_wid_str:
            return True

    # 3. Match PID tree
    if caller_pid and int(caller_pid) > 0:
        try:
            wpid_str = subprocess.check_output(["xdotool", "getwindowpid", active_wid_str], stderr=subprocess.DEVNULL).decode().strip()
            if wpid_str.isdigit():
                wpid = int(wpid_str)
                pid_tree = get_process_ancestors(caller_pid)
                if wpid in pid_tree:
                    return True
        except Exception:
            pass

    # 4. Match project hint in active window title (developer windows only)
    if project_hint and str(project_hint).strip():
        try:
            if is_developer_window(active_wid_str):
                active_title = find_window_title(active_wid_str)
                hint = str(project_hint).strip().lower()
                if hint and hint in active_title:
                    return True
        except Exception:
            pass

    return False


def get_queue_key(session_id="", window_id="", caller_pid=0, project_hint=""):
    """Generates a stable key for a window/session to track pending notifications."""
    if session_id and str(session_id).strip():
        s = str(session_id).strip()
        return s if s.startswith("sess_") else f"sess_{s}"
    if window_id and str(window_id).strip().isdigit():
        w = str(window_id).strip()
        return w if w.startswith("win_") else f"win_{w}"
    if caller_pid and int(caller_pid) > 0:
        return f"pid_{int(caller_pid)}"
    if project_hint and str(project_hint).strip():
        p = str(project_hint).strip().lower()
        return p if p.startswith("proj_") else f"proj_{p}"
    return "default_item"


def load_notification_queue():
    """Loads all pending notifications from disk, pruning expired entries (> 24h)."""
    if not os.path.exists(QUEUE_CACHE_FILE):
        return {}
    now = time.time()
    try:
        with open(QUEUE_CACHE_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            valid_queue = {}
            for k, v in data.items():
                if isinstance(v, dict) and (now - v.get("created_at", 0) < 86400):
                    valid_queue[k] = v
            return valid_queue
    except Exception:
        pass
    return {}


def save_to_queue(key, notif_dict):
    """Saves or updates a pending notification in the queue."""
    if not key or not notif_dict:
        return
    queue = load_notification_queue()
    queue[key] = notif_dict
    try:
        with open(QUEUE_CACHE_FILE, "w") as f:
            json.dump(queue, f)
    except Exception:
        pass


def remove_from_queue(key):
    """Removes a notification from the pending queue when resolved or dismissed."""
    if not key:
        return
    queue = load_notification_queue()
    if key in queue:
        del queue[key]
        try:
            with open(QUEUE_CACHE_FILE, "w") as f:
                json.dump(queue, f)
        except Exception:
            pass


def get_next_pending_notification(exclude_key=""):
    """
    Returns the next pending notification from another window/session in the queue.
    Prefers the oldest pending notification (FIFO) to ensure fairness across multiple agent tasks.
    """
    queue = load_notification_queue()
    pending = []
    for k, v in queue.items():
        if k != exclude_key and isinstance(v, dict):
            wid = v.get("target_window_id")
            if wid and not is_valid_toplevel_window(wid):
                refound_wid = find_target_window(
                    window_id_arg="",
                    caller_pid=v.get("caller_pid", 0),
                    project_hint=v.get("project_hint", ""),
                    session_id=v.get("session_id", ""),
                )
                if refound_wid:
                    v["target_window_id"] = refound_wid
                    pending.append((v.get("created_at", 0), k, v))
                else:
                    remove_from_queue(k)
                    continue
            else:
                pending.append((v.get("created_at", 0), k, v))

    if not pending:
        return None, None

    pending.sort(key=lambda x: x[0])
    _, next_key, next_item = pending[0]
    return next_key, next_item


def pop_next_notification_async(exclude_key=""):
    """Asynchronously triggers the next pending notification from another window using a detached subshell."""
    next_key, next_item = get_next_pending_notification(exclude_key=exclude_key)
    if not next_item:
        return

    import shlex

    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        f"--app-name={next_item.get('app_name', 'AI Agent')}",
        f"--title={next_item.get('title', 'Notification')}",
        f"--message={next_item.get('message', '')}",
        f"--questions-json={next_item.get('questions_json', '')}",
        f"--urgency={next_item.get('urgency', 'normal')}",
        f"--window-id={next_item.get('target_window_id', '')}",
        f"--caller-pid={next_item.get('caller_pid', 0)}",
        f"--project-hint={next_item.get('project_hint', '')}",
        f"--session-id={next_item.get('session_id', '')}",
        f"--timeout={next_item.get('timeout', 0)}",
        f"--sound={next_item.get('sound', '')}",
        "--from-queue",
    ]
    try:
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        subprocess.Popen(
            ["bash", "-c", f"(sleep 0.25 && exec {cmd_str}) >/dev/null 2>&1 &"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        pass


def get_window_workspace(wid):
    """
    Returns the workspace (desktop index) where the window currently resides.
    Returns:
        int: workspace index >= 0
        -1: window is sticky (present on all workspaces)
        None: workspace could not be determined
    """
    if not wid:
        return None
    wid_str = str(wid).strip()
    if not wid_str.isdigit():
        return None

    # 1. Try xdotool get_desktop_for_window
    try:
        out = subprocess.check_output(
            ["xdotool", "get_desktop_for_window", wid_str],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if out.lstrip("-").isdigit():
            desk = int(out)
            return desk
    except Exception:
        pass

    # 2. Try xprop _NET_WM_DESKTOP
    try:
        out = subprocess.check_output(
            ["xprop", "-id", wid_str, "_NET_WM_DESKTOP"],
            stderr=subprocess.DEVNULL,
        ).decode()
        if "=" in out:
            val = out.split("=")[1].strip()
            if val.isdigit():
                desk = int(val)
                if desk == 4294967295 or desk == 0xFFFFFFFF:
                    return -1
                return desk
    except Exception:
        pass

    return None


def switch_to_window_workspace(wid):
    """
    Switches current workspace/virtual desktop to the workspace containing the window.
    """
    if not wid:
        return False
    wid_str = str(wid).strip()
    if not wid_str.isdigit():
        return False

    target_desk = get_window_workspace(wid_str)
    # If target workspace is sticky (-1), no need to switch workspace
    if target_desk == -1:
        return True

    # 1. Try xdotool set_desktop_to_window
    try:
        subprocess.run(
            ["xdotool", "set_desktop_to_window", wid_str],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    # 2. Fallback to explicit workspace switch if target_desk is a non-negative number
    if target_desk is not None and target_desk >= 0:
        try:
            subprocess.run(
                ["xdotool", "set_desktop", str(target_desk)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        try:
            subprocess.run(
                ["wmctrl", "-s", str(target_desk)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    return True


def focus_target_window(window_id):
    """
    Activates and brings to front the specified window ID using workspace switching,
    GDK native and xdotool / EWMH.
    """
    if not window_id:
        return False

    wid_str = str(window_id).strip()
    if not wid_str.isdigit():
        return False

    wid_int = int(wid_str)

    # 0. Switch to window's workspace first so the window is visible
    switch_to_window_workspace(wid_str)
    time.sleep(0.05)

    # 1. Native GDK / GdkX11 focus
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("GdkX11", "3.0")
        from gi.repository import Gdk, GdkX11
        display = Gdk.Display.get_default()
        if display and isinstance(display, GdkX11.X11Display):
            gdk_win = GdkX11.X11Window.foreign_new_for_display(display, wid_int)
            if gdk_win:
                gdk_win.focus(Gdk.CURRENT_TIME)
                gdk_win.show()
    except Exception:
        pass

    # 2. Try wmctrl -i -a (EWMH standard activation with workspace switch support)
    try:
        subprocess.run(
            ["wmctrl", "-i", "-a", wid_str],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    # 3. Xdotool windowactivate, windowraise, and windowfocus
    try:
        subprocess.run(["xdotool", "windowactivate", "--sync", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["xdotool", "windowraise", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["xdotool", "windowfocus", "--sync", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def focus_active_or_queued_notification():
    """
    Directly focuses the application window of the currently active or oldest pending notification.
    Pops the next notification from queue if available.
    """
    queue = load_notification_queue()
    if queue:
        pending = []
        for k, v in queue.items():
            if isinstance(v, dict):
                pending.append((v.get("created_at", 0), k, v))
        pending.sort(key=lambda x: x[0])

        for _, k, item in pending:
            wid = item.get("target_window_id", "")
            if not wid or not is_valid_toplevel_window(wid):
                wid = find_target_window(
                    window_id_arg="",
                    caller_pid=item.get("caller_pid", 0),
                    project_hint=item.get("project_hint", ""),
                    session_id=item.get("session_id", ""),
                )
            if wid and is_valid_toplevel_window(wid):
                remove_from_queue(k)
                kill_previous_instance()
                focus_target_window(wid)
                pop_next_notification_async(exclude_key=k)
                return 0
            else:
                remove_from_queue(k)

    # Fallback if queue is empty: check session cache
    if os.path.exists(SESSION_CACHE_FILE):
        try:
            with open(SESSION_CACHE_FILE, "r") as f:
                sessions = json.load(f)
            valid_sessions = []
            for sid, sinfo in sessions.items():
                if isinstance(sinfo, dict) and sinfo.get("window_id"):
                    valid_sessions.append((sinfo.get("updated_at", 0), sinfo.get("window_id")))
            valid_sessions.sort(key=lambda x: x[0], reverse=True)
            for _, wid in valid_sessions:
                if is_valid_toplevel_window(wid):
                    kill_previous_instance()
                    focus_target_window(wid)
                    return 0
        except Exception:
            pass

    # Final fallback: try to find any active developer window
    managed = get_all_managed_windows()
    for wid, name, _ in managed:
        if any(dev in name.lower() for dev in ["visual studio code", "code", "terminal", "alacritty", "kitty"]):
            focus_target_window(wid)
            return 0

    return 1


def extract_summary_from_payload(questions_json_raw, fallback_message):
    """Extracts clean text summary from payload if questions JSON provided."""
    data = None
    if isinstance(questions_json_raw, str) and questions_json_raw.strip():
        try:
            data = json.loads(questions_json_raw)
        except Exception:
            data = None
    elif isinstance(questions_json_raw, (dict, list)):
        data = questions_json_raw

    if not data:
        return fallback_message

    q_list = []
    if isinstance(data, list):
        q_list = data
    elif isinstance(data, dict):
        if "questions" in data and isinstance(data["questions"], list):
            q_list = data["questions"]
        elif "tool_input" in data and isinstance(data["tool_input"], dict):
            ti = data["tool_input"]
            if "questions" in ti and isinstance(ti["questions"], list):
                q_list = ti["questions"]
            else:
                q_list = [ti]
        else:
            q_list = [data]

    extracted = []
    for item in q_list:
        if isinstance(item, str) and item.strip():
            extracted.append(item.strip())
        elif isinstance(item, dict):
            txt = item.get("question") or item.get("title") or item.get("prompt") or item.get("message")
            if txt:
                extracted.append(str(txt).strip())

    if extracted:
        return " | ".join(extracted)

    return fallback_message


def is_boilerplate_message(text, tag_class):
    """Checks if message is redundant boilerplate that should be hidden in compact mode."""
    if not text or not str(text).strip():
        return True
    cleaned = str(text).strip().lower()

    if tag_class == "tag-complete":
        if any(k in cleaned for k in ["hoàn thành", "complete", "finish", "done", "thành công"]):
            return True

    boilerplate_phrases = [
        "đã hoàn thành",
        "hoàn thành trả lời",
        "hoàn thành công việc",
        "hoàn thành nhiệm vụ",
        "hoàn thành lượt làm việc",
        "completed",
        "finished",
        "đang chờ bạn",
        "đang chờ bạn tương tác",
        "đang đặt câu hỏi cho bạn",
        "cần bạn chú ý",
        "ai agent đang chờ",
    ]

    for phrase in boilerplate_phrases:
        if cleaned == phrase:
            return True
        if cleaned.startswith(phrase) or cleaned.endswith(phrase):
            words = [w for w in cleaned.replace(".", "").replace("!", "").split() if w not in ["antigravity", "claude", "codex", "gemini", "agent", "ai"]]
            if not words or " ".join(words) in boilerplate_phrases or any(" ".join(words).startswith(p) for p in boilerplate_phrases):
                return True

    return False


def show_multi_monitor_popup(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id="", queue_key="", auto_dismiss_delay=1.5):
    display_text = extract_summary_from_payload(questions_json, message)

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        send_fallback_notify(app_name, title, display_text or "AI Agent đang chờ bạn.", urgency="normal", timeout=timeout)
        return

    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        gi.require_version("Pango", "1.0")
        from gi.repository import Gdk, GLib, Gtk, Pango
    except Exception:
        send_fallback_notify(app_name, title, display_text or "AI Agent đang chờ bạn.", urgency="normal", timeout=timeout)
        return

    try:
        display = Gdk.Display.get_default()
        if not display:
            send_fallback_notify(app_name, title, display_text or "AI Agent đang chờ bạn.", urgency="normal", timeout=timeout)
            return
        n_monitors = display.get_n_monitors()

        # Dark theme palette
        bg_color = "#18181b"        # Slate dark
        border_color = "#3b82f6"    # Primary blue border
        title_color = "#ffffff"     # White title
        msg_color = "#e4e4e7"       # Zinc 200 message

        css = f"""
        window.notification-window {{
            background-color: transparent;
            border: none;
        }}
        .notification-card {{
            background-color: {bg_color};
            border: 1.5px solid {border_color};
            border-radius: 14px;
        }}
        .banner-box {{
            padding: 10px 16px;
        }}
        .agent-badge {{
            color: #60a5fa;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 0.5px;
        }}
        .category-tag {{
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 0.5px;
        }}
        .queue-badge {{
            color: #a1a1aa;
            font-size: 11px;
            font-weight: bold;
            background-color: #27272a;
            border-radius: 4px;
            padding: 1px 6px;
        }}
        .tag-question {{
            color: #fbbf24;
        }}
        .tag-permission {{
            color: #f43f5e;
        }}
        .tag-complete {{
            color: #34d399;
        }}
        .tag-info {{
            color: #38bdf8;
        }}
        .topic-title {{
            color: {title_color};
            font-size: 14px;
            font-weight: bold;
        }}
        .msg-text {{
            color: {msg_color};
            font-size: 13px;
            margin-top: 2px;
            margin-bottom: 2px;
        }}
        button.focus-btn {{
            background-color: #2563eb;
            color: #ffffff;
            font-weight: bold;
            border-radius: 6px;
            padding: 6px 14px;
            border: none;
            font-size: 12px;
        }}
        button.focus-btn:hover {{
            background-color: #1d4ed8;
        }}
        button.close-btn {{
            background-color: #3f3f46;
            color: #e4e4e7;
            border-radius: 6px;
            padding: 6px 12px;
            border: none;
            font-size: 12px;
        }}
        button.close-btn:hover {{
            background-color: #52525b;
        }}
        """.encode("utf-8")

        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        windows = []
        closing = [False]

        def handle_focus_and_close():
            if closing[0]:
                return
            closing[0] = True

            wid_to_focus = target_window_id
            if not wid_to_focus or not is_valid_toplevel_window(wid_to_focus):
                wid_to_focus = find_target_window(
                    window_id_arg="",
                    caller_pid=caller_pid,
                    project_hint=project_hint,
                    session_id=session_id,
                )
            if wid_to_focus:
                focus_target_window(wid_to_focus)

            if queue_key:
                remove_from_queue(queue_key)

            pop_next_notification_async(exclude_key=queue_key)

            for w in windows:
                try:
                    w.hide()
                except Exception:
                    pass

            GLib.timeout_add(60, Gtk.main_quit)

        def handle_close_only():
            if closing[0]:
                return
            closing[0] = True

            if queue_key:
                remove_from_queue(queue_key)

            pop_next_notification_async(exclude_key=queue_key)

            for w in windows:
                try:
                    w.hide()
                except Exception:
                    pass

            GLib.timeout_add(60, Gtk.main_quit)

        for i in range(n_monitors):
            monitor = display.get_monitor(i)
            geom = monitor.get_geometry()

            win_width = int(min(560, max(460, geom.width * 0.30)))

            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_decorated(False)
            win.get_style_context().add_class("notification-window")
            win.set_app_paintable(True)
            screen = win.get_screen()
            if screen:
                visual = screen.get_rgba_visual()
                if visual:
                    win.set_visual(visual)
            win.set_keep_above(True)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            win.set_role("notification-popup")
            win.stick()

            def make_sticky(w, data=None):
                try:
                    w.stick()
                    gw = w.get_window()
                    if gw and hasattr(gw, "stick"):
                        gw.stick()
                except Exception:
                    pass

            win.connect("realize", make_sticky)
            win.connect("map", make_sticky)

            # EventBox to capture clicks on card background (close without focus)
            event_box = Gtk.EventBox()
            event_box.set_visible_window(True)
            event_box.get_style_context().add_class("notification-card")
            event_box.connect("button-press-event", lambda w, e: handle_close_only())

            vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            vbox_main.get_style_context().add_class("banner-box")

            # Header Box (Agent Badge + Category Tag)
            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            raw_agent = re.split(r'[:\-_]', app_name)[0].strip()
            agent_name_text = raw_agent.upper() if raw_agent else "AI AGENT"

            lbl_agent = Gtk.Label(label=agent_name_text)
            lbl_agent.get_style_context().add_class("agent-badge")

            category_text = "THÔNG BÁO"
            if ":" in title:
                category_text = title.split(":", 1)[1].strip().upper()

            tag_class = "tag-info"
            cat_lower = category_text.lower()
            if any(k in cat_lower for k in ["hỏi", "question", "ask", "input"]):
                tag_class = "tag-question"
            elif any(k in cat_lower for k in ["quyền", "permission", "grant", "exec", "run", "critical"]):
                tag_class = "tag-permission"
            elif any(k in cat_lower for k in ["thành", "complete", "finish", "done", "success"]):
                tag_class = "tag-complete"

            lbl_cat = Gtk.Label(label=f"•  {category_text}")
            lbl_cat.get_style_context().add_class("category-tag")
            lbl_cat.get_style_context().add_class(tag_class)

            header_box.pack_start(lbl_agent, False, False, 0)
            header_box.pack_start(lbl_cat, False, False, 0)

            # Queue counter badge if multiple windows have pending notifications
            queue = load_notification_queue()
            total_in_queue = len(queue)
            if total_in_queue > 1:
                queue_keys = list(queue.keys())
                current_idx = (queue_keys.index(queue_key) + 1) if queue_key in queue_keys else 1
                lbl_queue = Gtk.Label(label=f"[{current_idx}/{total_in_queue}]")
                lbl_queue.get_style_context().add_class("queue-badge")
                header_box.pack_end(lbl_queue, False, False, 0)

            vbox_main.pack_start(header_box, False, False, 0)

            # Show message text only when it contains meaningful custom content (questions, permissions, etc.)
            if not is_boilerplate_message(display_text, tag_class):
                escaped_msg = GLib.markup_escape_text(clean_text(display_text, limit=260))
                lbl_msg = Gtk.Label(xalign=0)
                lbl_msg.get_style_context().add_class("msg-text")
                lbl_msg.set_markup(escaped_msg)
                lbl_msg.set_line_wrap(True)
                lbl_msg.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                vbox_main.pack_start(lbl_msg, False, False, 0)

            # Action Buttons Row
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_box.set_margin_top(4)

            btn_focus = Gtk.Button(label="Đến cửa sổ [Alt+Q]")
            btn_focus.get_style_context().add_class("focus-btn")
            btn_focus.connect("clicked", lambda b: handle_focus_and_close())

            btn_close = Gtk.Button(label="✕ Đóng [Esc]")
            btn_close.get_style_context().add_class("close-btn")
            btn_close.connect("clicked", lambda b: handle_close_only())

            btn_box.pack_start(btn_focus, False, False, 0)
            btn_box.pack_end(btn_close, False, False, 0)

            vbox_main.pack_start(btn_box, False, False, 0)

            event_box.add(vbox_main)
            win.add(event_box)

            win.set_size_request(win_width, -1)
            win.set_default_size(win_width, -1)

            # Center window at top of screen
            def on_size_allocate(w, alloc, gx, gw, gy):
                win_x = gx + (gw - alloc.width) // 2
                win_y = gy + 30
                w.move(win_x, win_y)

            win.connect("size-allocate", lambda w, alloc, gx=geom.x, gw=geom.width, gy=geom.y: on_size_allocate(w, alloc, gx, gw, gy))

            def on_key_press(w, event):
                if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space, Gdk.KEY_f, Gdk.KEY_F, Gdk.KEY_y, Gdk.KEY_Y):
                    handle_focus_and_close()
                    return True
                if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_q, Gdk.KEY_Q, Gdk.KEY_n, Gdk.KEY_N):
                    handle_close_only()
                    return True
                return False

            win.connect("key-press-event", on_key_press)
            win.show_all()
            win.stick()
            windows.append(win)

        # Auto-dismiss when target window is active/focused by the user
        if auto_dismiss_delay > 0:
            active_since = [None]

            def check_target_window_active_timer():
                if closing[0]:
                    return False

                nonlocal target_window_id
                if not target_window_id or not is_valid_toplevel_window(target_window_id):
                    target_window_id = find_target_window(
                        window_id_arg="",
                        caller_pid=caller_pid,
                        project_hint=project_hint,
                        session_id=session_id,
                    )

                active_wid = get_current_active_window()
                if active_wid and is_target_window_active(
                    active_wid,
                    target_wid=target_window_id,
                    caller_pid=caller_pid,
                    project_hint=project_hint,
                    session_id=session_id,
                ):
                    now = time.time()
                    if active_since[0] is None:
                        active_since[0] = now
                    elif (now - active_since[0]) >= auto_dismiss_delay:
                        handle_close_only()
                        return False
                else:
                    active_since[0] = None

                return True

            GLib.timeout_add(250, check_target_window_active_timer)

        if timeout > 0:
            GLib.timeout_add_seconds(timeout, handle_close_only)

        Gtk.main()
    except Exception:
        send_fallback_notify(app_name, title, display_text, urgency="normal", timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description="Multi-monitor desktop notification")
    parser.add_argument("--app-name", default="System Notification")
    parser.add_argument("--title", default="Notification")
    parser.add_argument("--message", default="")
    parser.add_argument("--questions-json", default="")
    parser.add_argument("--urgency", choices=["low", "normal", "critical"], default="normal")
    parser.add_argument("--sound", default="")
    parser.add_argument("--timeout", type=int, default=0)
    parser.add_argument("--window-id", default="")
    parser.add_argument("--caller-pid", type=int, default=0)
    parser.add_argument("--project-hint", default="")
    parser.add_argument("--caller-tty", default="")
    parser.add_argument("--terminal-screen", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--capture-session", action="store_true", default=False)
    parser.add_argument("--focus", "-f", action="store_true", default=False, help="Focus the target application window waiting for input.")
    parser.add_argument("--from-queue", action="store_true", default=False, help="Indicates notification was popped from pending queue.")
    parser.add_argument("--auto-dismiss-delay", type=float, default=1.5, help="Seconds to wait before automatically dismissing notification when target window is active (default: 1.5s, 0 to disable).")
    parser.add_argument("--dedupe-seconds", type=int, default=2)
    parser.add_argument("--update", "-u", "--upgrade", action="store_true", default=False, help="Update notification system to latest version.")
    parser.add_argument("--uninstall", action="store_true", default=False, help="Uninstall notification system and restore backups.")
    parser.add_argument("--install", action="store_true", default=False, help="Install notification system into current user profile.")

    args, _ = parser.parse_known_args()

    # 0. Global focus command
    if args.focus:
        sys.exit(focus_active_or_queued_notification())

    # 0. Lifecycle management flags (update / uninstall / install)
    if args.update:
        print("🔄 Updating AI Agent Desktop Notifier...")
        user_home = os.path.expanduser("~")
        update_cmd = os.path.join(user_home, ".local", "bin", "ai-agent-notifier-update")
        if os.path.exists(update_cmd) and os.access(update_cmd, os.X_OK):
            subprocess.run([update_cmd], check=False)
        else:
            subprocess.run(["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/update.sh | bash"], check=False)
        return

    if args.uninstall:
        print("🗑️  Uninstalling AI Agent Desktop Notifier...")
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_uninstall = os.path.join(script_dir, "uninstall.sh")
        if os.path.exists(local_uninstall) and os.access(local_uninstall, os.X_OK):
            subprocess.run([local_uninstall], check=False)
        else:
            subprocess.run(["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/uninstall.sh | bash"], check=False)
        return

    if args.install:
        print("📦 Installing AI Agent Desktop Notifier...")
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_install = os.path.join(script_dir, "install.sh")
        if os.path.exists(local_install) and os.access(local_install, os.X_OK):
            subprocess.run([local_install], check=False)
        else:
            subprocess.run(["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash"], check=False)
        return

    # 1. Session capture mode (Pure side-effect, 0ms execution without rendering UI)
    if args.capture_session:
        target_wid = find_target_window(
            window_id_arg=args.window_id,
            caller_pid=args.caller_pid,
            project_hint=args.project_hint,
            caller_tty=args.caller_tty,
            terminal_screen=getattr(args, "terminal_screen", ""),
            session_id=args.session_id,
        )
        if target_wid and args.session_id:
            save_session_window(args.session_id, target_wid, args.project_hint, args.caller_pid)
        return

    message = clean_text(args.message)

    # 2. Deduplication check (Skip duplicate notification spam unless popped from queue)
    if not args.from_queue and is_duplicate_notification(args.app_name, args.title, message, args.dedupe_seconds):
        return

    # 2. Kill previous popup instance if running
    kill_previous_instance()

    # 3. Find target window to focus
    target_window_id = find_target_window(
        window_id_arg=args.window_id,
        caller_pid=args.caller_pid,
        project_hint=args.project_hint,
        caller_tty=args.caller_tty,
        terminal_screen=getattr(args, "terminal_screen", ""),
        session_id=args.session_id,
    )

    # 4. Manage pending notification queue
    queue_key = get_queue_key(
        session_id=args.session_id,
        window_id=target_window_id,
        caller_pid=args.caller_pid,
        project_hint=args.project_hint,
    )

    is_completion = any(k in args.title.lower() for k in ["hoàn thành", "complete", "finish", "done", "thành công"])

    if not is_completion:
        notif_item = {
            "key": queue_key,
            "app_name": args.app_name,
            "title": args.title,
            "message": message,
            "questions_json": args.questions_json,
            "urgency": args.urgency,
            "sound": args.sound,
            "target_window_id": target_window_id,
            "caller_pid": args.caller_pid,
            "project_hint": args.project_hint,
            "session_id": args.session_id,
            "timeout": args.timeout,
            "created_at": time.time(),
        }
        save_to_queue(queue_key, notif_item)
    else:
        remove_from_queue(queue_key)

    # 5. Play sound asynchronously
    if args.sound:
        play_sound_async(args.sound)

    # 6. Dispatch optional webhooks asynchronously
    dispatch_webhooks_async(args.app_name, args.title, message)

    # 7. Display desktop popup on connected monitors
    show_multi_monitor_popup(
        args.app_name,
        args.title,
        message,
        questions_json=args.questions_json,
        target_window_id=target_window_id,
        timeout=args.timeout,
        caller_pid=args.caller_pid,
        project_hint=args.project_hint,
        session_id=args.session_id,
        queue_key=queue_key,
        auto_dismiss_delay=args.auto_dismiss_delay,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
