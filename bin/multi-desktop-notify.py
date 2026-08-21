#!/usr/bin/env python3
"""
Multi-Monitor Desktop Notifier & Window Focuser for AI Coding Agents
(Claude Code, Codex, Google Antigravity, etc.)

Renders lightweight dark-themed desktop notification banners across connected monitors.
When clicked (or when clicking "Đến cửa sổ ứng dụng"), it automatically focuses and
brings to front the exact application window (VS Code or Terminal) that triggered the notification.
Cross-platform support for Linux (X11 / GNOME / GTK3) and Windows 10/11 (Win32 / Tkinter / Toast).
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
from pathlib import Path

IS_WINDOWS = sys.platform == "win32" or os.name == "nt"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    TEMP_DIR = os.environ.get("TEMP") or os.environ.get("TMP") or os.path.expanduser("~\\AppData\\Local\\Temp")
    PYTHON3 = sys.executable or "python"
else:
    TEMP_DIR = "/tmp"
    PYTHON3 = sys.executable or "/usr/bin/python3"

PID_FILE = os.path.join(TEMP_DIR, "ai_agent_notifier.pid")
SESSION_CACHE_FILE = os.path.join(TEMP_DIR, "ai_agent_notifier_sessions.json")
DEDUPE_CACHE_FILE = os.path.join(TEMP_DIR, "ai_agent_notifier_dedupe.json")
QUEUE_CACHE_FILE = os.path.join(TEMP_DIR, "ai_agent_notifier_queue.json")
CONFIG_FILE = os.path.expanduser("~/.config/ai-agent-notifier/config.json")

# Ensure DISPLAY and XAUTHORITY are available in background hook processes on Linux
if not IS_WINDOWS:
    if not os.environ.get("DISPLAY"):
        for disp in [":1", ":0"]:
            if os.path.exists(f"/tmp/.X11-unix/X{disp.lstrip(':')}"):
                os.environ["DISPLAY"] = disp
                break
        else:
            os.environ["DISPLAY"] = ":1"

    if not os.environ.get("XDG_RUNTIME_DIR"):
        try:
            os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        except AttributeError:
            pass

    if not os.environ.get("XAUTHORITY"):
        try:
            uid = os.getuid()
            for xauth_path in [
                f"/run/user/{uid}/gdm/Xauthority",
                os.path.expanduser("~/.Xauthority"),
                f"/run/user/{uid}/.Xauthority",
            ]:
                if os.path.exists(xauth_path):
                    os.environ["XAUTHORITY"] = xauth_path
                    break
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Windows-specific Win32 Structures and Helpers
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    def get_windows_process_tree():
        """Returns a dict mapping {pid: (parent_pid, exe_name)} via Toolhelp snapshot."""
        tree = {}
        try:
            hSnapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
            if hSnapshot == -1 or hSnapshot == 0xFFFFFFFF:
                return tree
            try:
                pe = PROCESSENTRY32()
                pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
                if ctypes.windll.kernel32.Process32FirstW(hSnapshot, ctypes.byref(pe)):
                    while True:
                        tree[pe.th32ProcessID] = (pe.th32ParentProcessID, pe.szExeFile)
                        if not ctypes.windll.kernel32.Process32NextW(hSnapshot, ctypes.byref(pe)):
                            break
            finally:
                ctypes.windll.kernel32.CloseHandle(hSnapshot)
        except Exception:
            pass
        return tree

    def get_windows_monitors():
        """Enumerates connected display coordinates on Windows."""
        monitors = []
        try:
            def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                if ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                    rc = mi.rcMonitor
                    monitors.append({
                        "x": int(rc.left),
                        "y": int(rc.top),
                        "width": int(rc.right - rc.left),
                        "height": int(rc.bottom - rc.top),
                        "is_primary": bool(mi.dwFlags & 1),
                    })
                return True

            MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)
            ctypes.windll.user32.EnumDisplayMonitors(None, None, MonitorEnumProc(monitor_enum_proc), 0)
        except Exception:
            pass
        return monitors


# ---------------------------------------------------------------------------
# Developer Window Identification Sets
# ---------------------------------------------------------------------------
DEVELOPER_CLASSES = {
    # Terminals
    "gnome-terminal", "gnome-terminal-server", "tilix", "alacritty", "kitty",
    "wezterm", "xfce4-terminal", "konsole", "terminator", "xterm", "uxterm",
    "urxvt", "rxvt", "foot", "contour", "ptyxis", "hyper", "tabby", "rio",
    "cascadia_hosting_window_class", "consolewindowclass", "mintty",
    # IDEs & Editors
    "code", "vscodium", "cursor", "windsurf", "antigravity", "zed",
    "pycharm", "pycharm-community", "idea", "idea-ce", "clion", "webstorm",
    "goland", "phpstorm", "rider", "rubymine", "datagrip", "fleet",
    "sublime_text", "subl", "gedit", "kate", "emacs", "neovim", "gvim",
    "chrome_widgetwin_1", "sunawtframe",
}

EXCLUDED_CLASSES = {
    # File managers
    "nemo", "nautilus", "dolphin", "thunar", "pcmanfm", "caja", "krusader", "doublecmd", "cabinetwclass",
    # System / Window frames / Desktop
    "mutter-x11-frames", "desktop_window", "desktop", "gala-other", "cinnamon", "progman", "workerw", "shell_traywnd",
    # PDF & Document viewers
    "okular", "evince", "atril", "xreader", "zathura", "acroread", "libreoffice",
    # Media & Browsers & Chat
    "spotify", "vlc", "mpv", "discord", "slack", "telegram-desktop",
}

WIN_DEVELOPER_EXES = [
    "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "windowsterminal.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe", "alacritty.exe", "wezterm-gui.exe",
    "idea64.exe", "pycharm64.exe", "clion64.exe", "webstorm64.exe", "rider64.exe",
    "goland64.exe", "mintty.exe", "conemu64.exe", "conemu.exe", "antigravity.exe",
]


# ---------------------------------------------------------------------------
# Session Cache & Queue Management
# ---------------------------------------------------------------------------
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


def load_notification_queue():
    """Loads all pending notifications currently waiting in queue."""
    if not os.path.exists(QUEUE_CACHE_FILE):
        return {}
    try:
        with open(QUEUE_CACHE_FILE, "r") as f:
            queue = json.load(f)
        now = time.time()
        # Discard expired notifications older than 4 hours
        active_queue = {k: v for k, v in queue.items() if isinstance(v, dict) and now - v.get("created_at", 0) < 14400}
        return active_queue
    except Exception:
        return {}


def save_to_queue(key, notif_item):
    """Saves or updates a pending notification item in the persistent queue."""
    if not key or not notif_item:
        return
    queue = load_notification_queue()
    queue[key] = notif_item
    try:
        with open(QUEUE_CACHE_FILE, "w") as f:
            json.dump(queue, f)
    except Exception:
        pass


def remove_from_queue(key):
    """Removes a resolved notification from the persistent queue."""
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


def pop_next_notification_async(exclude_key=""):
    """Pops and launches the next pending notification from the queue if any exist."""
    queue = load_notification_queue()
    if exclude_key and exclude_key in queue:
        del queue[exclude_key]

    if not queue:
        return

    pending = []
    for k, v in queue.items():
        if isinstance(v, dict):
            pending.append((v.get("created_at", 0), k, v))
    pending.sort(key=lambda x: x[0])

    if not pending:
        return

    _, next_key, item = pending[0]
    app_name = item.get("app_name", "AI Agent")
    title = item.get("title", "Thông báo")
    message = item.get("message", "")
    questions_json = item.get("questions_json", "")
    urgency = item.get("urgency", "normal")
    sound = item.get("sound", "")
    target_wid = item.get("target_window_id", "")
    caller_pid = item.get("caller_pid", 0)
    project_hint = item.get("project_hint", "")
    session_id = item.get("session_id", "")
    timeout = item.get("timeout", 0)

    cmd = [
        PYTHON3,
        __file__,
        f"--app-name={app_name}",
        f"--title={title}",
        f"--message={message}",
        f"--urgency={urgency}",
        f"--window-id={target_wid}",
        f"--caller-pid={caller_pid}",
        f"--project-hint={project_hint}",
        f"--session-id={session_id}",
        f"--timeout={timeout}",
        "--from-queue",
    ]
    if sound:
        cmd.append(f"--sound={sound}")
    if questions_json:
        cmd.append(f"--questions-json={questions_json}")

    try:
        creationflags = 0x08000000 if IS_WINDOWS else 0
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            creationflags=creationflags,
        )
    except Exception:
        pass


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
                    if IS_WINDOWS:
                        os.kill(old_pid, signal.SIGTERM)
                    else:
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


def clean_text(value, limit=300):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def play_sound_async(sound_path=""):
    """Plays notification sound asynchronously on Windows or Linux."""
    if IS_WINDOWS:
        try:
            import winsound
            import threading
            def play_win():
                try:
                    if sound_path and os.path.isfile(sound_path) and sound_path.lower().endswith(".wav"):
                        winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    else:
                        winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass
            threading.Thread(target=play_win, daemon=True).start()
            return
        except Exception:
            pass

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


def send_windows_toast_async(app_name, title, message):
    """Sends a native Windows 10/11 Toast notification via PowerShell in the background."""
    if not IS_WINDOWS:
        return

    import threading
    def worker():
        try:
            ps_title = title.replace("`", "``").replace('"', '`"').replace("$", "`$")
            ps_msg = message.replace("`", "``").replace('"', '`"').replace("$", "`$")
            ps_app = app_name.replace("`", "``").replace('"', '`"').replace("$", "`$")

            ps_code = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $template.GetElementsByTagName("text")
$textNodes.Item(0).AppendChild($template.CreateTextNode("{ps_app}: {ps_title}")) > $null
$textNodes.Item(1).AppendChild($template.CreateTextNode("{ps_msg}")) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Agent Notifier")
$notifier.Show($toast)
"""
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = 0x08000000  # CREATE_NO_WINDOW

            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
                startupinfo=startupinfo,
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5
            )
        except Exception:
            pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def send_fallback_notify(app_name, title, message, urgency="normal", timeout=0):
    """Fallback standard desktop notification using notify-send (Linux) or Toast (Windows)."""
    if IS_WINDOWS:
        send_windows_toast_async(app_name, title, message)
        return
    try:
        cmd = ["notify-send", f"[{app_name}] {title}", message, "-u", urgency]
        if timeout > 0:
            cmd.extend(["-t", str(timeout * 1000)])
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cross-Platform Window Management & Inspection
# ---------------------------------------------------------------------------
def is_valid_toplevel_window(wid):
    """Checks if WID is a valid managed top-level window."""
    if not wid:
        return False
    wid_str = str(wid).strip()
    if not wid_str.isdigit():
        return False

    if IS_WINDOWS:
        try:
            hwnd = int(wid_str)
            return bool(ctypes.windll.user32.IsWindow(hwnd) and ctypes.windll.user32.IsWindowVisible(hwnd))
        except Exception:
            return False

    try:
        out = subprocess.check_output(["xprop", "-id", wid_str, "_NET_WM_STATE"], stderr=subprocess.DEVNULL).decode()
        return "_NET_WM_STATE" in out and "not found" not in out
    except Exception:
        return False


def find_window_title(wid):
    """Returns the lowercased window title for a window ID."""
    if not wid:
        return ""
    wid_str = str(wid).strip()
    if not wid_str.isdigit():
        return ""

    if IS_WINDOWS:
        try:
            hwnd = int(wid_str)
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value.strip().lower()
        except Exception:
            return ""

    try:
        return subprocess.check_output(["xdotool", "getwindowname", wid_str], stderr=subprocess.DEVNULL).decode().strip().lower()
    except Exception:
        return ""


def get_process_ancestors(pid):
    """Traverses process tree upwards and returns set of ancestor process IDs."""
    ancestors = set()
    curr = int(pid or 0)
    if curr <= 0:
        return ancestors
    visited = set()

    if IS_WINDOWS:
        tree = get_windows_process_tree()
        while curr in tree and curr not in visited and curr > 0:
            visited.add(curr)
            ancestors.add(curr)
            ppid, _ = tree[curr]
            if ppid == curr or ppid <= 0:
                break
            curr = ppid
        return ancestors

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
    """Returns the window class tuple (instance, class_name) in lowercase."""
    if not wid or not str(wid).strip().isdigit():
        return ("", "")

    if IS_WINDOWS:
        try:
            hwnd = int(str(wid).strip())
            class_buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
            cls_name = class_buf.value.strip().lower()
            return (cls_name, cls_name)
        except Exception:
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


def is_developer_window(wid):
    """Checks whether a window belongs to a known developer host (IDE, editor, or terminal)."""
    if not is_valid_toplevel_window(wid):
        return False

    inst, cls = get_window_wm_class(wid)
    if inst or cls:
        if any(ex in inst or ex in cls for ex in EXCLUDED_CLASSES):
            return False
        if any(dev in inst or dev in cls for dev in DEVELOPER_CLASSES):
            return True

    title = find_window_title(wid)
    if any(app in title for app in ["visual studio code", "code", "terminal", "powershell", "alacritty", "kitty", "tmux", "bash", "zsh", "cursor", "windsurf"]):
        return True

    if IS_WINDOWS:
        try:
            hwnd = int(str(wid).strip())
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            tree = get_windows_process_tree()
            exe = tree.get(pid.value, (0, ""))[1].lower()
            if any(dev_exe in exe for dev_exe in WIN_DEVELOPER_EXES):
                return True
        except Exception:
            pass

    return False


def get_all_managed_windows():
    """Enumerates all top-level developer application windows."""
    results = []
    if IS_WINDOWS:
        try:
            tree = get_windows_process_tree()
            def enum_proc(hwnd, lParam):
                if not ctypes.windll.user32.IsWindowVisible(hwnd):
                    return True
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if not title:
                    return True

                pid = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                wpid = pid.value

                class_buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
                cls_name = class_buf.value.strip().lower()

                exe_name = tree.get(wpid, (0, ""))[1].lower()

                is_dev = False
                if any(dev_exe in exe_name for dev_exe in WIN_DEVELOPER_EXES):
                    is_dev = True
                elif any(dev_cls in cls_name for dev_cls in DEVELOPER_CLASSES):
                    is_dev = True
                elif any(app in title.lower() for app in ["visual studio code", "code", "terminal", "powershell", "alacritty", "cursor"]):
                    is_dev = True

                if is_dev:
                    results.append((str(hwnd), title, wpid))
                return True

            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            ctypes.windll.user32.EnumWindows(EnumWindowsProc(enum_proc), 0)
        except Exception:
            pass
        return results

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
    Finds the exact window ID for the application (VS Code or Terminal)
    that triggered the notification with 100% precision.
    """
    # 0. Tier 0: Check session cache if session_id is provided
    if session_id:
        cached_wid = get_session_window(session_id)
        if cached_wid and is_developer_window(cached_wid):
            return cached_wid

    project_hint = (project_hint or "").strip().lower()
    managed_windows = get_all_managed_windows()

    # 1. Tier 1: Match by PID tree + project_hint
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
    if project_hint:
        for wid, name, wpid in managed_windows:
            if project_hint in name.lower():
                if session_id:
                    save_session_window(session_id, wid, project_hint, wpid)
                return wid

    # 3. Tier 3: Match window from TTY (Linux only)
    if not IS_WINDOWS and caller_tty:
        pass

    # 4. Tier 4: Explicit window_id_arg if valid
    if window_id_arg and str(window_id_arg).strip().isdigit():
        wid = str(window_id_arg).strip()
        if is_developer_window(wid):
            if session_id:
                save_session_window(session_id, wid, project_hint, caller_pid)
            return wid

    # 5. Tier 5: Fallback to current active window
    active_wid = get_current_active_window()
    if active_wid and is_developer_window(active_wid):
        if session_id:
            save_session_window(session_id, active_wid, project_hint, caller_pid)
        return active_wid

    return ""


def get_current_active_window():
    """Returns the currently active window ID in decimal format, or empty string."""
    if IS_WINDOWS:
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd and ctypes.windll.user32.IsWindow(hwnd):
                return str(hwnd)
        except Exception:
            pass
        return ""

    try:
        res = subprocess.check_output(["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL)
        wid = res.decode().strip()
        if wid.isdigit():
            return wid
    except Exception:
        pass
    return ""


def is_target_window_active(active_wid, target_wid="", caller_pid=0, project_hint="", session_id=""):
    """Checks if active_wid corresponds to the target application window that triggered the notification."""
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
            if IS_WINDOWS:
                pid = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(int(active_wid_str), ctypes.byref(pid))
                wpid = pid.value
                if wpid and wpid in get_process_ancestors(caller_pid):
                    return True
            else:
                wpid_str = subprocess.check_output(["xdotool", "getwindowpid", active_wid_str], stderr=subprocess.DEVNULL).decode().strip()
                if wpid_str.isdigit():
                    wpid = int(wpid_str)
                    pid_tree = get_process_ancestors(caller_pid)
                    if wpid in pid_tree:
                        return True
        except Exception:
            pass

    # 4. Match project hint in active window title
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
        return f"pid_{caller_pid}"
    if project_hint and str(project_hint).strip():
        return f"proj_{str(project_hint).strip()}"
    return "default_target"


def get_window_workspace(wid):
    """Linux X11: Returns the workspace where the window resides."""
    if IS_WINDOWS or not wid:
        return None
    wid_str = str(wid).strip()
    if not wid_str.isdigit():
        return None

    try:
        out = subprocess.check_output(["xdotool", "get_desktop_for_window", wid_str], stderr=subprocess.DEVNULL).decode().strip()
        if out.lstrip("-").isdigit():
            return int(out)
    except Exception:
        pass

    try:
        out = subprocess.check_output(["xprop", "-id", wid_str, "_NET_WM_DESKTOP"], stderr=subprocess.DEVNULL).decode()
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
    """Linux X11: Switches virtual desktop to the workspace containing the window."""
    if IS_WINDOWS or not wid:
        return False
    wid_str = str(wid).strip()
    if not wid_str.isdigit():
        return False

    target_desk = get_window_workspace(wid_str)
    if target_desk == -1:
        return True

    try:
        subprocess.run(["xdotool", "set_desktop_to_window", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    if target_desk is not None and target_desk >= 0:
        try:
            subprocess.run(["xdotool", "set_desktop", str(target_desk)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            subprocess.run(["wmctrl", "-s", str(target_desk)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    return True


def focus_target_window(window_id):
    """Activates and brings to front the specified target window ID."""
    if not window_id:
        return False

    wid_str = str(window_id).strip()
    if not wid_str.isdigit():
        return False

    if IS_WINDOWS:
        try:
            hwnd = int(wid_str)
            if not ctypes.windll.user32.IsWindow(hwnd):
                return False

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # Restore if minimized (SW_RESTORE = 9)
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
            else:
                user32.ShowWindow(hwnd, 5)  # SW_SHOW

            # AttachThreadInput trick to bypass Windows foreground lock
            fg_hwnd = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            target_thread = user32.GetWindowThreadProcessId(hwnd, None)
            cur_thread = kernel32.GetCurrentThreadId()

            attached_fg = False
            attached_cur = False
            if fg_thread != target_thread and fg_thread != 0:
                attached_fg = bool(user32.AttachThreadInput(fg_thread, target_thread, True))
            if cur_thread != target_thread:
                attached_cur = bool(user32.AttachThreadInput(cur_thread, target_thread, True))

            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)

            if attached_fg:
                user32.AttachThreadInput(fg_thread, target_thread, False)
            if attached_cur:
                user32.AttachThreadInput(cur_thread, target_thread, False)
            return True
        except Exception:
            return False

    # Linux implementation
    wid_int = int(wid_str)
    switch_to_window_workspace(wid_str)
    time.sleep(0.05)

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

    try:
        subprocess.run(["wmctrl", "-i", "-a", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    try:
        subprocess.run(["xdotool", "windowactivate", "--sync", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["xdotool", "windowraise", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["xdotool", "windowfocus", "--sync", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def focus_active_or_queued_notification():
    """Directly focuses the application window of the currently active or oldest pending notification."""
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
        if any(dev in name.lower() for dev in ["visual studio code", "code", "terminal", "alacritty", "kitty", "cursor"]):
            focus_target_window(wid)
            return 0

    return 1


def extract_summary_from_payload(questions_json_raw, fallback_message):
    """Extracts clean text summary from payload if questions JSON is provided."""
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
        "đã hoàn thành", "hoàn thành trả lời", "hoàn thành công việc", "hoàn thành nhiệm vụ",
        "hoàn thành lượt làm việc", "completed", "finished", "đang chờ bạn",
        "đang chờ bạn tương tác", "đang đặt câu hỏi cho bạn", "cần bạn chú ý", "ai agent đang chờ",
    ]

    for phrase in boilerplate_phrases:
        if cleaned == phrase:
            return True
        if cleaned.startswith(phrase) or cleaned.endswith(phrase):
            words = [w for w in cleaned.replace(".", "").replace("!", "").split() if w not in ["antigravity", "claude", "codex", "gemini", "agent", "ai"]]
            if not words or " ".join(words) in boilerplate_phrases or any(" ".join(words).startswith(p) for p in boilerplate_phrases):
                return True

    return False


# ---------------------------------------------------------------------------
# Windows Multi-Monitor Overlay (Tkinter + Windows Toast)
# ---------------------------------------------------------------------------
def show_multi_monitor_popup_windows(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id="", queue_key="", auto_dismiss_delay=1.5):
    """Renders dark-themed notification overlay across connected displays on Windows."""
    display_text = extract_summary_from_payload(questions_json, message)

    # 1. Dispatch native Windows Toast notification concurrently in background
    send_windows_toast_async(app_name, title, display_text or "AI Agent đang chờ bạn.")

    # 2. Render Tkinter overlay banners on connected screens
    try:
        import tkinter as tk
    except Exception:
        return

    # Enable High DPI awareness on Windows
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    monitors = get_windows_monitors()
    if not monitors:
        monitors = [{"x": 0, "y": 0, "width": 1920, "height": 1080, "is_primary": True}]

    bg_color = "#18181b"        # Slate dark
    border_color = "#3b82f6"    # Primary blue
    msg_color = "#e4e4e7"       # Zinc 200

    category_text = "THÔNG BÁO"
    if ":" in title:
        category_text = title.split(":", 1)[1].strip().upper()
    cat_lower = category_text.lower()
    cat_color = "#38bdf8"
    tag_class = "tag-info"
    if any(k in cat_lower for k in ["hỏi", "question", "ask", "input"]):
        cat_color = "#fbbf24"
        border_color = "#fbbf24"
        tag_class = "tag-question"
    elif any(k in cat_lower for k in ["quyền", "permission", "grant", "exec", "run", "critical"]):
        cat_color = "#f43f5e"
        border_color = "#f43f5e"
        tag_class = "tag-permission"
    elif any(k in cat_lower for k in ["thành", "complete", "finish", "done", "success"]):
        cat_color = "#34d399"
        border_color = "#34d399"
        tag_class = "tag-complete"

    raw_agent = re.split(r'[:\-_]', app_name)[0].strip()
    agent_name_text = raw_agent.upper() if raw_agent else "AI AGENT"

    queue = load_notification_queue()
    total_in_queue = len(queue)
    queue_text = ""
    if total_in_queue > 1:
        queue_keys = list(queue.keys())
        current_idx = (queue_keys.index(queue_key) + 1) if queue_key in queue_keys else 1
        queue_text = f"[{current_idx}/{total_in_queue}]"

    root = tk.Tk()
    root.withdraw()

    windows = []
    closing = [False]

    def handle_focus_and_close(event=None):
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
                w.destroy()
            except Exception:
                pass
        try:
            root.quit()
        except Exception:
            pass

    def handle_close_only(event=None):
        if closing[0]:
            return
        closing[0] = True

        if queue_key:
            remove_from_queue(queue_key)

        pop_next_notification_async(exclude_key=queue_key)

        for w in windows:
            try:
                w.destroy()
            except Exception:
                pass
        try:
            root.quit()
        except Exception:
            pass

    for mon in monitors:
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=border_color)

        inner = tk.Frame(win, bg=bg_color, padx=14, pady=10)
        inner.pack(fill=tk.BOTH, expand=True, padx=1.5, pady=1.5)

        hdr = tk.Frame(inner, bg=bg_color)
        hdr.pack(fill=tk.X, pady=(0, 4))

        lbl_agent = tk.Label(hdr, text=agent_name_text, fg="#60a5fa", bg=bg_color, font=("Segoe UI", 9, "bold"))
        lbl_agent.pack(side=tk.LEFT)

        lbl_dot = tk.Label(hdr, text=" • ", fg="#71717a", bg=bg_color, font=("Segoe UI", 9))
        lbl_dot.pack(side=tk.LEFT)

        lbl_cat = tk.Label(hdr, text=category_text, fg=cat_color, bg=bg_color, font=("Segoe UI", 9, "bold"))
        lbl_cat.pack(side=tk.LEFT)

        if queue_text:
            lbl_q = tk.Label(hdr, text=queue_text, fg="#a1a1aa", bg="#27272a", font=("Segoe UI", 8, "bold"), padx=4, pady=1)
            lbl_q.pack(side=tk.RIGHT)

        if not is_boilerplate_message(display_text, tag_class):
            clean_msg = clean_text(display_text, limit=260)
            lbl_msg = tk.Label(inner, text=clean_msg, fg=msg_color, bg=bg_color, font=("Segoe UI", 10), justify=tk.LEFT, wraplength=480)
            lbl_msg.pack(fill=tk.X, pady=(2, 6), anchor="w")

        btn_frame = tk.Frame(inner, bg=bg_color)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        btn_focus = tk.Button(
            btn_frame,
            text="Đến cửa sổ [Alt+Q]",
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
            command=handle_focus_and_close
        )
        btn_focus.pack(side=tk.LEFT)

        btn_close = tk.Button(
            btn_frame,
            text="✕ Đóng [Esc]",
            bg="#3f3f46",
            fg="#e4e4e7",
            activebackground="#52525b",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=handle_close_only
        )
        btn_close.pack(side=tk.RIGHT)

        for key in ["<Return>", "<KP_Enter>", "<space>", "<f>", "<F>", "<y>", "<Y>"]:
            win.bind(key, handle_focus_and_close)
        for key in ["<Escape>", "<q>", "<Q>", "<n>", "<N>"]:
            win.bind(key, handle_close_only)

        win.update_idletasks()
        win_w = max(460, min(560, int(mon["width"] * 0.32)))
        win_h = inner.winfo_reqheight() + 4
        win_x = mon["x"] + (mon["width"] - win_w) // 2
        win_y = mon["y"] + 30
        win.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")

        windows.append(win)

    if auto_dismiss_delay > 0:
        active_since = [None]
        def check_active_timer():
            if closing[0]:
                return
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
                    return
            else:
                active_since[0] = None

            root.after(250, check_active_timer)

        root.after(250, check_active_timer)

    if timeout > 0:
        root.after(int(timeout * 1000), handle_close_only)

    root.mainloop()


# ---------------------------------------------------------------------------
# Linux Multi-Monitor Overlay (GTK3 / GDK)
# ---------------------------------------------------------------------------
def show_multi_monitor_popup_linux(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id="", queue_key="", auto_dismiss_delay=1.5):
    """Renders dark-themed notification overlay across connected displays on Linux via GTK3."""
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

        bg_color = "#18181b"
        border_color = "#3b82f6"
        title_color = "#ffffff"
        msg_color = "#e4e4e7"

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

            event_box = Gtk.EventBox()
            event_box.set_visible_window(True)
            event_box.get_style_context().add_class("notification-card")
            event_box.connect("button-press-event", lambda w, e: handle_close_only())

            vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            vbox_main.get_style_context().add_class("banner-box")

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

            queue = load_notification_queue()
            total_in_queue = len(queue)
            if total_in_queue > 1:
                queue_keys = list(queue.keys())
                current_idx = (queue_keys.index(queue_key) + 1) if queue_key in queue_keys else 1
                lbl_queue = Gtk.Label(label=f"[{current_idx}/{total_in_queue}]")
                lbl_queue.get_style_context().add_class("queue-badge")
                header_box.pack_end(lbl_queue, False, False, 0)

            vbox_main.pack_start(header_box, False, False, 0)

            if not is_boilerplate_message(display_text, tag_class):
                escaped_msg = GLib.markup_escape_text(clean_text(display_text, limit=260))
                lbl_msg = Gtk.Label(xalign=0)
                lbl_msg.get_style_context().add_class("msg-text")
                lbl_msg.set_markup(escaped_msg)
                lbl_msg.set_line_wrap(True)
                lbl_msg.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                vbox_main.pack_start(lbl_msg, False, False, 0)

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


def show_multi_monitor_popup(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id="", queue_key="", auto_dismiss_delay=1.5):
    """Platform dispatcher for multi-monitor popup."""
    if IS_WINDOWS:
        show_multi_monitor_popup_windows(
            app_name, title, message,
            questions_json=questions_json,
            target_window_id=target_window_id,
            timeout=timeout,
            caller_pid=caller_pid,
            project_hint=project_hint,
            session_id=session_id,
            queue_key=queue_key,
            auto_dismiss_delay=auto_dismiss_delay,
        )
    else:
        show_multi_monitor_popup_linux(
            app_name, title, message,
            questions_json=questions_json,
            target_window_id=target_window_id,
            timeout=timeout,
            caller_pid=caller_pid,
            project_hint=project_hint,
            session_id=session_id,
            queue_key=queue_key,
            auto_dismiss_delay=auto_dismiss_delay,
        )


# ---------------------------------------------------------------------------
# CLI Argument Parser & Entry Point
# ---------------------------------------------------------------------------
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
    parser.add_argument("--auto-dismiss-delay", type=float, default=1.5, help="Seconds to wait before automatically dismissing notification when target window is active.")
    parser.add_argument("--dedupe-seconds", type=int, default=2)
    parser.add_argument("--update", "-u", "--upgrade", action="store_true", default=False, help="Update notification system to latest version.")
    parser.add_argument("--uninstall", action="store_true", default=False, help="Uninstall notification system and restore backups.")
    parser.add_argument("--install", action="store_true", default=False, help="Install notification system into current user profile.")

    args, _ = parser.parse_known_args()

    # 0. Global focus command
    if args.focus:
        sys.exit(focus_active_or_queued_notification())

    # 0. Lifecycle management flags
    if args.update:
        print("[INFO] Dang cap nhat AI Agent Desktop Notifier...")
        if IS_WINDOWS:
            script_dir = Path(__file__).resolve().parent.parent
            local_update = script_dir / "update.ps1"
            if local_update.exists():
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(local_update)], check=False)
            else:
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-Command", "irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/update.ps1 | iex"], check=False)
        else:
            subprocess.run(["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/update.sh | bash"], check=False)
        return

    if args.uninstall:
        print("[INFO] Dang go cai dat AI Agent Desktop Notifier...")
        if IS_WINDOWS:
            script_dir = Path(__file__).resolve().parent.parent
            local_uninstall = script_dir / "uninstall.ps1"
            if local_uninstall.exists():
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(local_uninstall)], check=False)
            else:
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-Command", "irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/uninstall.ps1 | iex"], check=False)
        else:
            subprocess.run(["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/uninstall.sh | bash"], check=False)
        return

    if args.install:
        print("[INFO] Dang cai dat AI Agent Desktop Notifier...")
        if IS_WINDOWS:
            script_dir = Path(__file__).resolve().parent.parent
            local_install = script_dir / "install.ps1"
            if local_install.exists():
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(local_install)], check=False)
            else:
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-Command", "irm https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.ps1 | iex"], check=False)
        else:
            subprocess.run(["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/SonNX24042005/ai-agent-desktop-notifier/master/install.sh | bash"], check=False)
        return

    # 1. Session capture mode
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

    # 2. Deduplication check
    if not args.from_queue and is_duplicate_notification(args.app_name, args.title, message, args.dedupe_seconds):
        return

    # 3. Kill previous popup instance
    kill_previous_instance()

    # 4. Find target window to focus
    target_window_id = find_target_window(
        window_id_arg=args.window_id,
        caller_pid=args.caller_pid,
        project_hint=args.project_hint,
        caller_tty=args.caller_tty,
        terminal_screen=getattr(args, "terminal_screen", ""),
        session_id=args.session_id,
    )

    # 5. Manage pending notification queue
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

    # 6. Play sound asynchronously
    if args.sound:
        play_sound_async(args.sound)

    # 7. Dispatch optional webhooks asynchronously
    dispatch_webhooks_async(args.app_name, args.title, message)

    # 8. Display desktop popup on connected monitors
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
