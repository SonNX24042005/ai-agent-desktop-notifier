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
import tempfile
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32" or os.name == "nt"

import ctypes

if IS_WINDOWS:
    from ctypes import wintypes
    PYTHON3 = sys.executable or "python"
else:
    PYTHON3 = sys.executable or "/usr/bin/python3"


def get_runtime_dir():
    """Returns a secure, user-private runtime directory for PID, state, and lock files.
    On Linux: Prefers $AI_AGENT_NOTIFIER_RUNTIME_DIR, then $XDG_RUNTIME_DIR/ai-agent-notifier,
    falling back to /tmp/ai-agent-notifier-<uid> with 0700 permissions.
    On Windows: Uses $LOCALAPPDATA/ai-agent-notifier/runtime or user temp directory.
    """
    env_override = os.environ.get("AI_AGENT_NOTIFIER_RUNTIME_DIR")
    if env_override:
        os.makedirs(env_override, mode=0o700, exist_ok=True)
        return env_override

    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.path.expanduser("~\\AppData\\Local")
        runtime_dir = os.path.join(base, "ai-agent-notifier", "runtime")
    else:
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime and os.path.isdir(xdg_runtime):
            runtime_dir = os.path.join(xdg_runtime, "ai-agent-notifier")
        else:
            uid = os.getuid() if hasattr(os, "getuid") else 1000
            runtime_dir = os.path.join(tempfile.gettempdir(), f"ai-agent-notifier-{uid}")

    try:
        os.makedirs(runtime_dir, mode=0o700, exist_ok=True)
        if not IS_WINDOWS and os.path.exists(runtime_dir):
            try:
                os.chmod(runtime_dir, 0o700)
            except Exception:
                pass
    except Exception:
        pass
    return runtime_dir


RUNTIME_DIR = get_runtime_dir()
TEMP_DIR = RUNTIME_DIR
PID_FILE = os.path.join(RUNTIME_DIR, "ai_agent_notifier.pid")
SESSION_CACHE_FILE = os.path.join(RUNTIME_DIR, "ai_agent_notifier_sessions.json")
SESSION_LOCK_FILE = os.path.join(RUNTIME_DIR, "ai_agent_notifier_sessions.lock")
DEDUPE_CACHE_FILE = os.path.join(RUNTIME_DIR, "ai_agent_notifier_dedupe.json")
DEDUPE_LOCK_FILE = os.path.join(RUNTIME_DIR, "ai_agent_notifier_dedupe.lock")
QUEUE_CACHE_FILE = os.path.join(RUNTIME_DIR, "ai_agent_notifier_queue.json")
QUEUE_LOCK_FILE = os.path.join(RUNTIME_DIR, "ai_agent_notifier_queue.lock")
CONFIG_FILE = os.path.expanduser("~/.config/ai-agent-notifier/config.json")
FOCUS_MAX_ENTRIES = 64
FOCUS_MAX_AGE = 86400  # 24 hours

# Ensure DISPLAY and XAUTHORITY are available in background hook processes on Linux
if not IS_WINDOWS:
    if not os.environ.get("DISPLAY"):
        for disp in [":1", ":0"]:
            if os.path.exists(f"/tmp/.X11-unix/X{disp.lstrip(':')}"):
                os.environ["DISPLAY"] = disp
                break
        else:
            if not os.environ.get("WAYLAND_DISPLAY"):
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
else:
    def get_windows_process_tree():
        """POSIX fallback for get_windows_process_tree."""
        return {}

    def get_windows_monitors():
        """POSIX fallback for get_windows_monitors."""
        return []


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
# Process-Safe File Locking & Atomic JSON Operations
# ---------------------------------------------------------------------------
import contextlib

@contextlib.contextmanager
def file_lock(lock_path, timeout=1.0):
    """Cross-platform inter-process file lock using flock on Linux and spin-mkdir on Windows."""
    start_time = time.monotonic()
    fd = None
    acquired = False
    try:
        if IS_WINDOWS:
            lock_dir = lock_path + ".dirlock"
            while time.monotonic() - start_time < timeout:
                try:
                    os.mkdir(lock_dir)
                    acquired = True
                    break
                except OSError:
                    time.sleep(0.02)
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        os.rmdir(lock_dir)
                    except Exception:
                        pass
        else:
            import fcntl
            try:
                flags = os.O_CREAT | os.O_RDWR
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(lock_path, flags, 0o600)
                while time.monotonic() - start_time < timeout:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except (BlockingIOError, OSError):
                        time.sleep(0.02)
            except Exception:
                acquired = False

            try:
                yield acquired
            finally:
                if acquired and fd is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
                if fd is not None:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
    except Exception:
        yield False


def atomic_write_json(file_path, data):
    """Atomically writes dictionary/list data to a JSON file via a temporary file and rename."""
    tmp_path = f"{file_path}.tmp.{os.getpid()}_{int(time.time()*1000)}"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(tmp_path, flags, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, file_path)
        return True
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False


def safe_load_json(file_path, default=None):
    """Safely reads JSON data from file, resetting gracefully on missing or corrupted file."""
    if default is None:
        default = {}
    if not os.path.exists(file_path):
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except Exception:
        return default


def normalize_title(title):
    """Normalizes window title by stripping leading non-alphanumeric icons/spinners and whitespace."""
    if not title:
        return ""
    s = str(title).strip().lower()
    s = re.sub(r"^[^\w\s]+", "", s).strip()
    return " ".join(s.split())


def titles_compatible(expected, current):
    """Checks if expected title fingerprint matches current window title."""
    exp = normalize_title(expected)
    cur = normalize_title(current)
    if not exp or not cur:
        return True
    return exp == cur or exp in cur or cur in exp


def prune_sessions(sessions, now=None, max_entries=FOCUS_MAX_ENTRIES, max_age=FOCUS_MAX_AGE):
    """Prunes expired sessions (>24h) and caps total count to max_entries (keeps newest)."""
    if now is None:
        now = time.time()
    valid = {}
    for sid, entry in sessions.items():
        if isinstance(entry, dict):
            if now - entry.get("updated_at", 0) < max_age:
                valid[str(sid)] = entry
        elif isinstance(entry, str) and entry.strip():
            valid[str(sid)] = {"window_id": entry.strip(), "updated_at": now, "precision": "window", "schema_version": 2}

    if len(valid) <= max_entries:
        return valid

    # Sort descending by updated_at
    sorted_items = sorted(valid.items(), key=lambda item: item[1].get("updated_at", 0), reverse=True)
    return dict(sorted_items[:max_entries])


# ---------------------------------------------------------------------------
# Session Cache & Window Identity Store (Schema v2)
# ---------------------------------------------------------------------------
def save_session_window(session_id, window_id, project_hint="", pid=0, precision="window", app_hint="", title_fingerprint=""):
    """Caches target window identity for a session ID with lock, atomic write, and cache protection."""
    if not session_id or not window_id:
        return False
    wid_str = str(window_id).strip()
    if not is_developer_window(wid_str):
        return False

    c_pid = int(pid or 0)
    if c_pid > 1:
        wpid = get_window_pid(wid_str)
        if wpid > 1 and not is_pid_in_ancestry(wpid, c_pid) and not is_developer_window(wid_str):
            return False

    fingerprint = str(title_fingerprint or "").strip()
    if not fingerprint:
        fingerprint = normalize_title(find_window_title(wid_str))

    with file_lock(SESSION_LOCK_FILE, timeout=1.0):
        sessions = safe_load_json(SESSION_CACHE_FILE, default={})
        existing = sessions.get(str(session_id))
        if existing and isinstance(existing, dict):
            # Cache protection: do not overwrite high-precision cache with lower or unverified precision
            existing_prec = existing.get("precision", "window")
            if existing_prec == "window" and precision not in ("window", "authoritative"):
                return False

        dec_wid = wid_str
        try:
            if wid_str.startswith(("0x", "0X")):
                dec_wid = str(int(wid_str, 16))
            elif wid_str.isdigit():
                dec_wid = str(int(wid_str))
        except Exception:
            pass

        sessions[str(session_id)] = {
            "schema_version": 2,
            "session_id": str(session_id),
            "window_id": wid_str,
            "window_id_dec": dec_wid,
            "project_hint": str(project_hint or "").strip(),
            "pid": c_pid,
            "app_hint": str(app_hint or "").strip(),
            "title_fingerprint": fingerprint,
            "precision": precision,
            "backend": "windows" if IS_WINDOWS else "x11",
            "captured_at": time.time(),
            "updated_at": time.time(),
        }
        sessions = prune_sessions(sessions)
        return atomic_write_json(SESSION_CACHE_FILE, sessions)


def get_session_window_info(session_id):
    """Retrieves cached identity metadata dictionary for a session ID."""
    if not session_id or not os.path.exists(SESSION_CACHE_FILE):
        return None
    with file_lock(SESSION_LOCK_FILE, timeout=0.5):
        sessions = safe_load_json(SESSION_CACHE_FILE, default={})
        entry = sessions.get(str(session_id))
        if isinstance(entry, dict):
            return entry
        elif isinstance(entry, str) and entry.strip():
            return {"window_id": entry.strip(), "precision": "window", "schema_version": 2}
    return None


def get_session_window(session_id):
    """Retrieves and validates cached window ID for a session.

    Performs stale handle, PID reuse, and fingerprint validation to avoid misfocusing
    reused or closed window IDs.
    """
    info = get_session_window_info(session_id)
    if not info:
        return ""
    wid = info.get("window_id", "")
    if not wid or not is_valid_toplevel_window(wid):
        return ""
    if not is_developer_window(wid):
        return ""

    # Stale handle / PID verification
    cached_pid = int(info.get("pid", 0))
    if cached_pid > 0:
        cur_pid = get_window_pid(wid)
        if cur_pid > 0 and cur_pid != cached_pid:
            # PID mismatch: window ID reused by OS for another process
            return ""

    # Title fingerprint verification
    cached_fingerprint = info.get("title_fingerprint", "")
    if cached_fingerprint:
        cur_title = find_window_title(wid)
        if cur_title and not titles_compatible(cached_fingerprint, cur_title):
            return ""

    cached_hint = (info.get("project_hint") or "").strip().lower()
    if cached_hint:
        title = find_window_title(wid)
        if title and cached_hint not in title.lower():
            return ""

    return wid


def is_duplicate_notification(app_name, title, message, dedupe_seconds=2):
    """Checks and sets deduplication state to prevent notification spam with process-safe lock."""
    if dedupe_seconds <= 0:
        return False
    import hashlib
    key_raw = f"{app_name}|{title}|{message}"
    key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    now = time.time()
    with file_lock(DEDUPE_LOCK_FILE, timeout=1.0):
        dedupe_data = safe_load_json(DEDUPE_CACHE_FILE, default={})
        # Prune expired keys (older than 60s)
        dedupe_data = {k: v for k, v in dedupe_data.items() if isinstance(v, (int, float)) and now - v < 60}
        last_time = dedupe_data.get(key, 0)
        if now - last_time < dedupe_seconds:
            return True
        dedupe_data[key] = now
        atomic_write_json(DEDUPE_CACHE_FILE, dedupe_data)
        return False


def load_notification_queue():
    """Loads all pending notifications currently waiting in queue."""
    if not os.path.exists(QUEUE_CACHE_FILE):
        return {}
    with file_lock(QUEUE_LOCK_FILE, timeout=1.0):
        queue = safe_load_json(QUEUE_CACHE_FILE, default={})
        now = time.time()
        # Discard expired notifications older than 4 hours
        active_queue = {k: v for k, v in queue.items() if isinstance(v, dict) and now - v.get("created_at", 0) < 14400}
        return active_queue


def get_next_generation():
    """Generates a strictly increasing generation ID for notifications."""
    gen_file = os.path.join(RUNTIME_DIR, "ai_agent_notifier_generation.txt")
    lock_file = os.path.join(RUNTIME_DIR, "ai_agent_notifier_generation.lock")
    with file_lock(lock_file, timeout=1.0):
        try:
            if os.path.exists(gen_file):
                with open(gen_file, "r") as f:
                    val = int(f.read().strip() or "0")
            else:
                val = 0
        except Exception:
            val = 0
        val += 1
        try:
            with open(gen_file, "w") as f:
                f.write(str(val))
        except Exception:
            pass
        return val


def save_to_queue(key, notif_item):
    """Saves or updates a pending notification item in the persistent queue atomically, coalescing duplicates."""
    if not key or not notif_item:
        return
    with file_lock(QUEUE_LOCK_FILE, timeout=1.5):
        queue = safe_load_json(QUEUE_CACHE_FILE, default={})
        now = time.time()
        active_queue = {}
        target_sess = notif_item.get("session_id")
        for k, v in queue.items():
            if isinstance(v, dict) and now - v.get("created_at", 0) < 1800:
                # If existing entry is for the same session and not this key, supersede it
                if target_sess and v.get("session_id") == target_sess and k != key:
                    continue
                active_queue[k] = v

        if "generation" not in notif_item:
            notif_item["generation"] = get_next_generation()
        if "status" not in notif_item:
            notif_item["status"] = "queued"

        active_queue[key] = notif_item
        atomic_write_json(QUEUE_CACHE_FILE, active_queue)


def remove_from_queue(key):
    """Removes a resolved notification from the persistent queue atomically."""
    if not key:
        return
    with file_lock(QUEUE_LOCK_FILE, timeout=1.5):
        queue = safe_load_json(QUEUE_CACHE_FILE, default={})
        if key in queue:
            del queue[key]
            atomic_write_json(QUEUE_CACHE_FILE, queue)


def mark_queue_item_dismissed(key):
    """Marks a notification as dismissed from screen so it is not re-popped automatically, but remains accessible for focus."""
    if not key:
        return
    with file_lock(QUEUE_LOCK_FILE, timeout=1.5):
        queue = safe_load_json(QUEUE_CACHE_FILE, default={})
        if key in queue and isinstance(queue[key], dict):
            queue[key]["dismissed"] = True
            atomic_write_json(QUEUE_CACHE_FILE, queue)


def pop_next_notification_async(exclude_key=""):
    """Pops and launches the next pending notification from the queue if any exist."""
    with file_lock(QUEUE_LOCK_FILE, timeout=1.5):
        queue = safe_load_json(QUEUE_CACHE_FILE, default={})
        if exclude_key and exclude_key in queue:
            del queue[exclude_key]

        now = time.time()
        pending = []
        for k, v in list(queue.items()):
            if isinstance(v, dict) and now - v.get("created_at", 0) < 1800:
                if not v.get("dismissed", False) and k != exclude_key:
                    pending.append((v.get("created_at", 0), k, v))
        pending.sort(key=lambda x: x[0])

        if not pending:
            atomic_write_json(QUEUE_CACHE_FILE, queue)
            return

        _, next_key, item = pending[0]
        # Atomically consume the popped item from queue to prevent looping
        del queue[next_key]
        atomic_write_json(QUEUE_CACHE_FILE, queue)

        app_name = item.get("app_name", "AI Agent")
        title = item.get("title", "Thông báo")
        message = item.get("message", "")
        questions_json = item.get("questions_json", "")
        urgency = item.get("urgency", "normal")
        event_type = item.get("event_type", "info")
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
            f"--event-type={event_type}",
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
    """Ensure new notification replaces any active popup to prevent stacking, verifying process identity."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                content = f.read().strip()
                old_pid = int(content) if content.isdigit() else 0
            if old_pid != os.getpid() and old_pid > 1:
                try:
                    if IS_WINDOWS:
                        tree = get_windows_process_tree()
                        if old_pid in tree:
                            exe_name = tree[old_pid][1].lower()
                            if "python" in exe_name:
                                os.kill(old_pid, signal.SIGTERM)
                    else:
                        cmdline_path = f"/proc/{old_pid}/cmdline"
                        if os.path.exists(cmdline_path):
                            with open(cmdline_path, "rb") as cf:
                                cmdline = cf.read().decode(errors="ignore")
                            if "multi-desktop-notify" in cmdline:
                                os.kill(old_pid, signal.SIGTERM)
                except OSError:
                    pass
        except Exception:
            pass
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(PID_FILE, flags, 0o600)
        with open(fd, "w") as f:
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
    if wid_str == "wayland:gnome-terminal":
        return True
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
                content = f.read()
                comm_end = content.rfind(")")
                if comm_end != -1:
                    rest = content[comm_end + 1:].split()
                    curr = int(rest[1])
                else:
                    break
        except Exception:
            break
    return ancestors


def get_window_pid(wid):
    """Returns the process ID owning the given window ID, or 0 if unavailable."""
    if not wid or not str(wid).strip().isdigit():
        return 0
    wid_str = str(wid).strip()
    if IS_WINDOWS:
        try:
            hwnd = int(wid_str)
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value or 0)
        except Exception:
            return 0
    try:
        wpid_str = subprocess.check_output(["xdotool", "getwindowpid", wid_str], stderr=subprocess.DEVNULL).decode().strip()
        if wpid_str.isdigit():
            return int(wpid_str)
    except Exception:
        pass
    try:
        out = subprocess.check_output(["xprop", "-id", wid_str, "_NET_WM_PID"], stderr=subprocess.DEVNULL).decode()
        if "=" in out:
            val = out.split("=")[1].strip()
            if val.isdigit():
                return int(val)
    except Exception:
        pass
    return 0


def is_pid_in_ancestry(target_pid, start_pid=0):
    """Checks whether target_pid exists in the ancestor process chain of start_pid."""
    t_pid = int(target_pid or 0)
    s_pid = int(start_pid or 0)
    if t_pid <= 1 or s_pid <= 1:
        return False
    if t_pid == s_pid:
        return True
    ancestors = get_process_ancestors(s_pid)
    return t_pid in ancestors


def is_gnome_terminal_in_ancestry(start_pid):
    """Checks if gnome-terminal-server exists in the ancestor process chain of start_pid."""
    curr = int(start_pid or 0)
    if curr <= 1 or IS_WINDOWS:
        return False
    visited = set()
    while curr > 1 and curr not in visited:
        visited.add(curr)
        try:
            with open(f"/proc/{curr}/comm", "r") as f:
                comm = f.read().strip().lower()
                if "gnome-terminal" in comm:
                    return True
        except Exception:
            pass
        try:
            with open(f"/proc/{curr}/cmdline", "r") as f:
                cmdline = f.read().lower()
                if "gnome-terminal" in cmdline:
                    return True
        except Exception:
            pass
        try:
            with open(f"/proc/{curr}/stat", "r") as f:
                curr = int(f.read().split()[3])
        except Exception:
            break
    return False


def activate_gnome_terminal_via_dbus():
    """Activates and raises GNOME Terminal via D-Bus session bus on GNOME Wayland."""
    try:
        res = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Terminal",
                "--object-path", "/org/gnome/Terminal",
                "--method", "org.gtk.Application.Activate",
                "{}"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False
        )
        return res.returncode == 0
    except Exception:
        return False


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
    if str(wid).strip() == "wayland:gnome-terminal":
        return True
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
    """Finds the exact window ID for the application (VS Code or Terminal)
    that triggered the notification based on verified identity.
    """
    # 0. Tier 0: Check validated session cache if session_id is provided
    if session_id:
        cached_wid = get_session_window(session_id)
        if cached_wid and is_developer_window(cached_wid):
            return cached_wid

    project_hint = (project_hint or "").strip().lower()
    managed_windows = get_all_managed_windows()

    # 1. Tier 1: Match by PID tree + project_hint
    c_pid = int(caller_pid or 0)
    if c_pid > 1:
        pid_tree = get_process_ancestors(c_pid)
        tree_windows = [(wid, name, wpid) for wid, name, wpid in managed_windows if wpid in pid_tree and is_developer_window(wid)]
        if len(tree_windows) == 1:
            wid = tree_windows[0][0]
            if session_id:
                save_session_window(session_id, wid, project_hint, tree_windows[0][2], precision="window")
            return wid
        elif len(tree_windows) > 1:
            if project_hint:
                hint_matched = [w for w in tree_windows if project_hint in w[1].lower()]
                if len(hint_matched) == 1:
                    wid = hint_matched[0][0]
                    if session_id:
                        save_session_window(session_id, wid, project_hint, hint_matched[0][2], precision="window")
                    return wid
            # Multiple windows in same PID tree and no unique project hint: Ambiguous!
            # If explicit window_id_arg is valid and belongs to tree_windows, use it
            if window_id_arg and str(window_id_arg).strip().isdigit():
                wid_arg_str = str(window_id_arg).strip()
                if any(w[0] == wid_arg_str for w in tree_windows):
                    if session_id:
                        save_session_window(session_id, wid_arg_str, project_hint, c_pid, precision="window")
                    return wid_arg_str
            # Do NOT guess tree_windows[0][0]
            return ""

    # 2. Tier 2: Match project_hint in window title across open developer windows
    if project_hint:
        matching_hint = [(wid, name, wpid) for wid, name, wpid in managed_windows if project_hint in name.lower()]
        if len(matching_hint) == 1:
            wid = matching_hint[0][0]
            if session_id:
                save_session_window(session_id, wid, project_hint, matching_hint[0][2], precision="app")
            return wid
        elif len(matching_hint) > 1:
            # Multiple developer windows match project_hint: Ambiguous! Do not guess
            pass

    # 3. Tier 3: Explicit window_id_arg if valid
    if window_id_arg and str(window_id_arg).strip().isdigit():
        wid = str(window_id_arg).strip()
        if is_valid_toplevel_window(wid) and is_developer_window(wid):
            if c_pid > 1:
                wpid = get_window_pid(wid)
                if wpid > 1 and not is_pid_in_ancestry(wpid, c_pid):
                    # Belongs to a different process outside agent ancestry
                    pass
                else:
                    if session_id:
                        save_session_window(session_id, wid, project_hint, c_pid, precision="window")
                    return wid
            else:
                if session_id:
                    save_session_window(session_id, wid, project_hint, c_pid, precision="app")
                return wid

    # Tier 4: Wayland Native GNOME Terminal resolution via process ancestry
    if c_pid > 1 and is_gnome_terminal_in_ancestry(c_pid):
        wid = "wayland:gnome-terminal"
        if session_id:
            save_session_window(session_id, wid, project_hint, c_pid, precision="app", app_hint="gnome-terminal")
        return wid

    # Tier 5: Do NOT fall back to arbitrary active window or random developer windows
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

    try:
        out = subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"], stderr=subprocess.DEVNULL).decode()
        if "_NET_ACTIVE_WINDOW(WINDOW)" in out and "#" in out:
            hex_val = out.split("#")[-1].strip().split()[0]
            if hex_val.startswith("0x") and hex_val != "0x0":
                return str(int(hex_val, 16))
    except Exception:
        pass

    return ""


def find_controlling_tty(start_pid):
    """Finds the controlling /dev/pts/* device by traversing the process ancestor chain on Linux."""
    if IS_WINDOWS or not start_pid or int(start_pid) <= 1:
        return None
    pid = int(start_pid)
    seen = set()
    while pid > 1 and pid not in seen and len(seen) < 15:
        seen.add(pid)
        try:
            for fd_num in (0, 1, 2):
                fd_path = f"/proc/{pid}/fd/{fd_num}"
                if os.path.exists(fd_path):
                    target = os.path.realpath(fd_path)
                    if target.startswith("/dev/pts/"):
                        return target

            with open(f"/proc/{pid}/stat", "r") as f:
                content = f.read()
                comm_end = content.rfind(")")
                if comm_end != -1:
                    rest = content[comm_end + 1:].split()
                    pid = int(rest[1])  # ppid
                else:
                    break
        except Exception:
            break
    return None


def is_pid_in_foreground(pid):
    """Checks if a Linux process is in the foreground process group of its controlling terminal."""
    if IS_WINDOWS or not pid or int(pid) <= 1:
        return False
    try:
        with open(f"/proc/{int(pid)}/stat", "r") as f:
            content = f.read()
            comm_end = content.rfind(")")
            if comm_end != -1:
                fields = content[comm_end + 1:].split()
                pgrp = int(fields[2])
                tpgid = int(fields[5])
                return pgrp > 0 and pgrp == tpgid
    except Exception:
        pass
    return False


def is_target_window_active(active_wid, target_wid="", caller_pid=0, project_hint="", session_id=""):
    """Checks if active_wid corresponds to the target application window that triggered the notification."""
    # 1. Direct match with active_wid if active_wid is available (X11 / Windows)
    if active_wid:
        def to_dec_str(val):
            s = str(val or "").strip()
            if not s:
                return ""
            try:
                if s.startswith(("0x", "0X")):
                    return str(int(s, 16))
                if s.isdigit():
                    return str(int(s))
            except Exception:
                pass
            return s

        active_wid_str = to_dec_str(active_wid)
        target_wid_str = to_dec_str(target_wid)

        if target_wid_str and active_wid_str and active_wid_str == target_wid_str:
            return True

        if session_id:
            cached_wid = to_dec_str(get_session_window(session_id))
            if cached_wid and cached_wid == active_wid_str:
                return True

        c_pid = int(caller_pid or 0)
        if c_pid > 1 and active_wid_str:
            wpid = get_window_pid(active_wid_str)
            if wpid > 1 and is_pid_in_ancestry(wpid, c_pid):
                if project_hint:
                    title = find_window_title(active_wid_str)
                    if project_hint.lower() in title.lower() or is_developer_window(active_wid_str):
                        return True
                else:
                    if is_developer_window(active_wid_str):
                        return True

    # 2. Wayland native fallback: when active_wid cannot be polled from Wayland compositor
    if not IS_WINDOWS and caller_pid and int(caller_pid) > 1:
        c_pid = int(caller_pid)
        tty_dev = find_controlling_tty(c_pid)
        if tty_dev and os.path.exists(tty_dev):
            try:
                st = os.stat(tty_dev)
                now = time.time()
                # Check if terminal was active recently (within 30s) AND process is in foreground
                if (now - st.st_atime <= 30.0) or (now - st.st_mtime <= 30.0):
                    if is_pid_in_foreground(c_pid):
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


def focus_target_window(window_id, verify_timeout=0.4):
    """Activates and brings to front the specified target window ID.
    Returns True if focus activation was successfully verified, False otherwise.
    """
    if not window_id:
        return False

    wid_str = str(window_id).strip()
    if wid_str == "wayland:gnome-terminal":
        return activate_gnome_terminal_via_dbus()

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
            if hasattr(user32, "SwitchToThisWindow"):
                try:
                    user32.SwitchToThisWindow(hwnd, True)
                except Exception:
                    pass
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)

            if attached_fg:
                user32.AttachThreadInput(fg_thread, target_thread, False)
            if attached_cur:
                user32.AttachThreadInput(cur_thread, target_thread, False)

            # Verification loop
            t_limit = time.monotonic() + verify_timeout
            while time.monotonic() < t_limit:
                if user32.GetForegroundWindow() == hwnd:
                    return True
                time.sleep(0.04)
            return user32.GetForegroundWindow() == hwnd
        except Exception:
            return False

    # Linux implementation
    wid_int = int(wid_str)
    if not is_valid_toplevel_window(wid_str):
        return False

    switch_to_window_workspace(wid_str)
    time.sleep(0.04)

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
    except Exception:
        pass

    # Verification loop
    t_limit = time.monotonic() + verify_timeout
    while time.monotonic() < t_limit:
        act = get_current_active_window()
        if act and str(act).strip() == wid_str:
            return True
        time.sleep(0.04)

    act_final = get_current_active_window()
    if act_final:
        return str(act_final).strip() == wid_str
    # On Wayland native where active window cannot be polled by client, return window validity
    return is_valid_toplevel_window(wid_str)


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
                kill_previous_instance()
                if focus_target_window(wid):
                    remove_from_queue(k)
                    pop_next_notification_async(exclude_key=k)
                    return 0
                else:
                    # Focus failed: preserve in queue, try next
                    continue
            elif is_gnome_terminal_in_ancestry(item.get("caller_pid", 0)):
                kill_previous_instance()
                if activate_gnome_terminal_via_dbus():
                    remove_from_queue(k)
                    pop_next_notification_async(exclude_key=k)
                    return 0

    # Fallback if queue is empty: check session cache
    if os.path.exists(SESSION_CACHE_FILE):
        try:
            with file_lock(SESSION_LOCK_FILE, timeout=0.5):
                sessions = safe_load_json(SESSION_CACHE_FILE, default={})
            valid_sessions = []
            for sid, sinfo in sessions.items():
                if isinstance(sinfo, dict) and sinfo.get("window_id"):
                    valid_sessions.append((sinfo.get("updated_at", 0), sinfo.get("window_id")))
            valid_sessions.sort(key=lambda x: x[0], reverse=True)
            for _, wid in valid_sessions:
                if is_valid_toplevel_window(wid):
                    kill_previous_instance()
                    if focus_target_window(wid):
                        return 0
        except Exception:
            pass

    # Never focus random developer windows when ambiguous
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
    """Checks if message is redundant boilerplate that should be hidden in compact mode.
    Only hides strictly short standard boilerplate phrases, preserving informative summaries.
    """
    if not text or not str(text).strip():
        return True
    cleaned = " ".join(str(text).strip().lower().split())

    boilerplate_phrases = {
        "đã hoàn thành", "hoàn thành", "hoàn thành trả lời", "đã hoàn thành trả lời",
        "hoàn thành công việc", "đã hoàn thành công việc", "hoàn thành nhiệm vụ",
        "đã hoàn thành nhiệm vụ", "hoàn thành lượt làm việc", "đã hoàn thành lượt làm việc",
        "completed", "finished", "đang chờ bạn", "đang chờ bạn tương tác", "đang đặt câu hỏi cho bạn",
        "cần bạn chú ý", "ai agent đang chờ", "claude đã hoàn thành trả lời",
        "claude code đang chờ bạn", "antigravity đã hoàn thành trả lời",
        "antigravity đang chờ bạn", "codex cần bạn chú ý", "done", "success"
    }

    # Clean punctuation for exact match check
    stripped = cleaned.strip(".!?:; ")
    if stripped in boilerplate_phrases:
        return True

    # If the text has significant length or informative content beyond boilerplate, keep it
    words = [w for w in stripped.split() if w not in ["antigravity", "claude", "codex", "gemini", "agent", "ai", "code"]]
    cleaned_words = " ".join(words)
    if cleaned_words in boilerplate_phrases:
        return True

    return False


# ---------------------------------------------------------------------------
# Windows Multi-Monitor Overlay (Tkinter + Windows Toast)
# ---------------------------------------------------------------------------
def show_multi_monitor_popup_windows(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id="", queue_key="", auto_dismiss_delay=1.5, event_type=""):
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

    if event_type == "question" or any(k in cat_lower for k in ["hỏi", "question", "ask", "input"]):
        category_text = "CÂU HỎI" if event_type == "question" else category_text
        cat_color = "#fbbf24"
        border_color = "#fbbf24"
        tag_class = "tag-question"
    elif event_type == "permission" or any(k in cat_lower for k in ["quyền", "permission", "grant", "exec", "run", "critical"]):
        category_text = "CẤP QUYỀN" if event_type == "permission" else category_text
        cat_color = "#f43f5e"
        border_color = "#f43f5e"
        tag_class = "tag-permission"
    elif event_type == "complete" or any(k in cat_lower for k in ["thành", "complete", "finish", "done", "success"]):
        category_text = "HOÀN THÀNH" if event_type == "complete" else category_text
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

        wid_to_focus = target_window_id
        if not wid_to_focus or not is_valid_toplevel_window(wid_to_focus):
            wid_to_focus = find_target_window(
                window_id_arg="",
                caller_pid=caller_pid,
                project_hint=project_hint,
                session_id=session_id,
            )
        focused = False
        if wid_to_focus:
            focused = focus_target_window(wid_to_focus)

        closing[0] = True
        if focused and queue_key:
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
        try:
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            hwnd = int(win.winfo_id())
            cur_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur_style | WS_EX_NOACTIVATE)
        except Exception:
            pass

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
            text="Đến cửa sổ (Alt+Q)",
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
            text="✕ Đóng",
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
            if is_target_window_active(
                active_wid,
                target_wid=target_window_id,
                caller_pid=caller_pid,
                project_hint=project_hint,
                session_id=session_id,
            ):
                now = time.monotonic()
                if active_since[0] is None:
                    active_since[0] = now
                elif (now - active_since[0]) >= auto_dismiss_delay:
                    handle_close_only()
                    return
            else:
                active_since[0] = None

            root.after(100, check_active_timer)

        root.after(100, check_active_timer)

    effective_timeout = timeout if timeout > 0 else 15
    root.after(int(effective_timeout * 1000), handle_close_only)

    root.mainloop()


def should_use_x11_overlay(environ=None):
    """Determines if Linux notification overlay should use X11/XWayland backend.

    Wayland native compositors (such as GNOME Mutter) do not permit client-driven
    window positioning for toplevel surfaces, causing multi-monitor popups to stack/cascade
    on a single display. Using XWayland (when DISPLAY is present in a Wayland session)
    enables precise multi-monitor placement across connected screens.
    """
    if environ is None:
        environ = os.environ

    # Explicit override via NOTIFY_BACKEND takes highest precedence
    notify_backend = environ.get("NOTIFY_BACKEND", "").strip().lower()
    if notify_backend == "wayland":
        return False
    if notify_backend in ("x11", "xwayland"):
        return True

    # Explicit opt-out from XWayland overlay
    if environ.get("NOTIFY_FORCE_WAYLAND") == "1":
        return False

    # In a Wayland session, if XWayland DISPLAY is available, use X11 backend for the overlay
    # to achieve proper multi-monitor placement, even if the ambient environment has GDK_BACKEND=wayland.
    is_wayland_session = bool(
        environ.get("WAYLAND_DISPLAY") or
        environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )
    has_x11_display = bool(environ.get("DISPLAY"))

    return is_wayland_session and has_x11_display


def calculate_overlay_placement(geo_dict, win_width, win_height=0, top_margin=30):
    """Calculates (x, y) coordinates to center an overlay window horizontally
    near the top of the given monitor area.

    geo_dict: dict with 'x', 'y', 'width', 'height'.
    Supports negative coordinates, portrait monitors, and work area offsets.
    """
    x = int(geo_dict.get("x", 0))
    y = int(geo_dict.get("y", 0))
    width = int(geo_dict.get("width", 1920))

    if width > win_width:
        win_x = x + (width - win_width) // 2
    else:
        win_x = x

    win_y = y + top_margin
    return win_x, win_y


def get_target_monitor_indices(n_monitors, can_place_windows):
    """Determines the list of monitor indices to display popups on.

    When client-side placement is supported (X11 / XWayland), returns all monitor indices.
    When client-side placement is not supported (pure Wayland), returns [0] to avoid
    creating multiple toplevel windows that compositor cascades onto a single monitor.
    """
    if n_monitors <= 0:
        return []
    if not can_place_windows and n_monitors > 1:
        return [0]
    return list(range(n_monitors))


# ---------------------------------------------------------------------------
# Linux Multi-Monitor Overlay (GTK3 / GDK)
# ---------------------------------------------------------------------------
def show_multi_monitor_popup_linux(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id="", queue_key="", auto_dismiss_delay=1.5, event_type=""):
    """Renders dark-themed notification overlay across connected displays on Linux via GTK3."""
    display_text = extract_summary_from_payload(questions_json, message)

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        send_fallback_notify(app_name, title, display_text or "AI Agent đang chờ bạn.", urgency="normal", timeout=timeout)
        return

    # Prefer X11/XWayland backend if in a Wayland session with DISPLAY available
    # to allow accurate multi-monitor window positioning across all screens.
    if should_use_x11_overlay():
        os.environ["GDK_BACKEND"] = "x11"

    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        gi.require_version("Pango", "1.0")
        from gi.repository import Gdk, GLib, Gtk, Pango
    except Exception:
        send_fallback_notify(app_name, title, display_text or "AI Agent đang chờ bạn.", urgency="normal", timeout=timeout)
        return

    init_ok = False
    try:
        init_ok = bool(Gtk.init_check()[0])
    except Exception:
        init_ok = False

    if not init_ok:
        if os.environ.get("GDK_BACKEND") == "x11" and os.environ.get("WAYLAND_DISPLAY"):
            os.environ.pop("GDK_BACKEND", None)
            try:
                init_ok = bool(Gtk.init_check()[0])
            except Exception:
                init_ok = False

    if not init_ok:
        send_fallback_notify(app_name, title, display_text or "AI Agent đang chờ bạn.", urgency="normal", timeout=timeout)
        return

    try:
        display = Gdk.Display.get_default()
        if not display:
            send_fallback_notify(app_name, title, display_text or "AI Agent đang chờ bạn.", urgency="normal", timeout=timeout)
            return

        display_type = type(display).__name__
        can_place_windows = "Wayland" not in display_type

        n_monitors = display.get_n_monitors()
        target_monitors = get_target_monitor_indices(n_monitors, can_place_windows)

        is_debug = os.environ.get("DEBUG_NOTIFY") == "1"
        if is_debug:
            print(f"[multi-desktop-notify] display={display_type}, can_place={can_place_windows}, target_monitors={target_monitors}/{n_monitors}")

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

            wid_to_focus = target_window_id
            if not wid_to_focus or not is_valid_toplevel_window(wid_to_focus):
                wid_to_focus = find_target_window(
                    window_id_arg="",
                    caller_pid=caller_pid,
                    project_hint=project_hint,
                    session_id=session_id,
                )

            focused = False
            if wid_to_focus:
                focused = focus_target_window(wid_to_focus)

            closing[0] = True
            if focused and queue_key:
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

        for i in target_monitors:
            monitor = display.get_monitor(i)
            work = monitor.get_workarea()
            geom = monitor.get_geometry()

            base_area = work if (work.width > 0 and work.height > 0) else geom
            geo_dict = {
                "x": base_area.x,
                "y": base_area.y,
                "width": base_area.width,
                "height": base_area.height,
            }

            win_width = int(min(560, max(460, geo_dict["width"] * 0.30)))

            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_decorated(False)
            win.get_style_context().add_class("notification-window")
            win.set_app_paintable(True)
            win.set_accept_focus(False)
            win.set_focus_on_map(False)
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
            if event_type == "question" or any(k in cat_lower for k in ["hỏi", "question", "ask", "input"]):
                category_text = "CÂU HỎI" if event_type == "question" else category_text
                tag_class = "tag-question"
            elif event_type == "permission" or any(k in cat_lower for k in ["quyền", "permission", "grant", "exec", "run", "critical"]):
                category_text = "CẤP QUYỀN" if event_type == "permission" else category_text
                tag_class = "tag-permission"
            elif event_type == "complete" or any(k in cat_lower for k in ["thành", "complete", "finish", "done", "success"]):
                category_text = "HOÀN THÀNH" if event_type == "complete" else category_text
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

            btn_focus = Gtk.Button(label="Đến cửa sổ (Alt+Q)")
            btn_focus.get_style_context().add_class("focus-btn")
            btn_focus.connect("clicked", lambda b: handle_focus_and_close())

            btn_close = Gtk.Button(label="✕ Đóng")
            btn_close.get_style_context().add_class("close-btn")
            btn_close.connect("clicked", lambda b: handle_close_only())

            btn_box.pack_start(btn_focus, False, False, 0)
            btn_box.pack_end(btn_close, False, False, 0)

            vbox_main.pack_start(btn_box, False, False, 0)

            event_box.add(vbox_main)
            win.add(event_box)

            win.set_size_request(win_width, -1)
            win.set_default_size(win_width, -1)

            def on_size_allocate(w, alloc, g_dict, mon_idx):
                win_x, win_y = calculate_overlay_placement(g_dict, alloc.width, alloc.height, top_margin=30)
                w.move(win_x, win_y)
                if is_debug:
                    print(f"[multi-desktop-notify] win mon={mon_idx} placed at ({win_x}, {win_y})")

            win.connect("size-allocate", lambda w, alloc, gd=geo_dict, idx=i: on_size_allocate(w, alloc, gd, idx))

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
                if is_target_window_active(
                    active_wid,
                    target_wid=target_window_id,
                    caller_pid=caller_pid,
                    project_hint=project_hint,
                    session_id=session_id,
                ):
                    now = time.monotonic()
                    if active_since[0] is None:
                        active_since[0] = now
                    elif (now - active_since[0]) >= auto_dismiss_delay:
                        if queue_key:
                            remove_from_queue(queue_key)
                        handle_close_only()
                        return False
                else:
                    active_since[0] = None

                return True

            GLib.timeout_add(100, check_target_window_active_timer)

        effective_timeout = timeout if timeout > 0 else 15
        GLib.timeout_add_seconds(effective_timeout, handle_close_only)

        Gtk.main()
    except Exception:
        send_fallback_notify(app_name, title, display_text, urgency="normal", timeout=timeout)


def show_multi_monitor_popup(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id="", queue_key="", auto_dismiss_delay=1.5, event_type=""):
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
            event_type=event_type,
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
            event_type=event_type,
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
    parser.add_argument("--event-type", choices=["question", "permission", "complete", "info", ""], default="", help="Explicit notification event type.")
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
    parser.add_argument("--dismiss", action="store_true", default=False, help="Dismiss active popup and remove from queue.")

    args, _ = parser.parse_known_args()

    # 0. Dismiss active notification
    if args.dismiss:
        kill_previous_instance()
        queue_key = get_queue_key(
            session_id=args.session_id,
            window_id=args.window_id,
            caller_pid=args.caller_pid,
            project_hint=args.project_hint,
        )
        remove_from_queue(queue_key)
        return

    # 0. Global focus command
    if args.focus:
        sys.exit(focus_active_or_queued_notification())

    # 0. Lifecycle management flags
    if args.update:
        print("[INFO] Dang cap nhat AI Agent Desktop Notifier...")
        script_dir = Path(__file__).resolve().parent.parent
        if IS_WINDOWS:
            local_update = script_dir / "update.ps1"
            if local_update.exists():
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(local_update)], check=False)
            else:
                print("[ERROR] Khong tim thay update.ps1 cuc bo. Vui long cap nhat qua release chinh thuc.")
        else:
            local_update = script_dir / "update.sh"
            if local_update.exists():
                subprocess.run(["bash", str(local_update)], check=False)
            else:
                print("[ERROR] Khong tim thay update.sh cuc bo. Vui long cap nhat qua release chinh thuc.")
        return

    if args.uninstall:
        print("[INFO] Dang go cai dat AI Agent Desktop Notifier...")
        script_dir = Path(__file__).resolve().parent.parent
        if IS_WINDOWS:
            local_uninstall = script_dir / "uninstall.ps1"
            if local_uninstall.exists():
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(local_uninstall)], check=False)
            else:
                print("[ERROR] Khong tim thay uninstall.ps1 cuc bo. Vui long go cai dat thu cong.")
        else:
            local_uninstall = script_dir / "uninstall.sh"
            if local_uninstall.exists():
                subprocess.run(["bash", str(local_uninstall)], check=False)
            else:
                print("[ERROR] Khong tim thay uninstall.sh cuc bo. Vui long go cai dat thu cong.")
        return

    if args.install:
        print("[INFO] Dang cai dat AI Agent Desktop Notifier...")
        script_dir = Path(__file__).resolve().parent.parent
        if IS_WINDOWS:
            local_install = script_dir / "install.ps1"
            if local_install.exists():
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(local_install)], check=False)
            else:
                print("[ERROR] Khong tim thay install.ps1 cuc bo.")
        else:
            local_install = script_dir / "install.sh"
            if local_install.exists():
                subprocess.run(["bash", str(local_install)], check=False)
            else:
                print("[ERROR] Khong tim thay install.sh cuc bo.")
        return

    # 1. Session capture mode
    if args.capture_session:
        target_wid = ""
        c_pid = int(args.caller_pid or 0)
        w_arg = str(args.window_id or "").strip()
        if w_arg and is_valid_toplevel_window(w_arg) and is_developer_window(w_arg):
            if c_pid > 1:
                wpid = get_window_pid(w_arg)
                if wpid > 1 and is_pid_in_ancestry(wpid, c_pid):
                    target_wid = w_arg
            else:
                target_wid = w_arg

        if not target_wid:
            target_wid = find_target_window(
                window_id_arg=args.window_id,
                caller_pid=args.caller_pid,
                project_hint=args.project_hint,
                caller_tty=args.caller_tty,
                terminal_screen=getattr(args, "terminal_screen", ""),
                session_id=args.session_id,
            )

        if target_wid and args.session_id:
            save_session_window(
                session_id=args.session_id,
                window_id=target_wid,
                project_hint=args.project_hint,
                pid=args.caller_pid,
                precision="window",
            )
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

    if args.event_type:
        is_completion = (args.event_type == "complete")
    else:
        # Conservative fallback if --event-type is not provided
        t_low = args.title.lower()
        has_complete_word = any(k in t_low for k in ["hoàn thành", "complete", "finish", "done", "thành công"])
        has_action_word = any(k in t_low for k in ["câu hỏi", "hỏi", "question", "ask", "quyền", "permission", "grant", "exec", "run"])
        is_completion = has_complete_word and not has_action_word

    if not is_completion:
        notif_item = {
            "key": queue_key,
            "app_name": args.app_name,
            "title": args.title,
            "message": message,
            "questions_json": args.questions_json,
            "urgency": args.urgency,
            "event_type": args.event_type or "info",
            "sound": args.sound,
            "target_window_id": target_window_id,
            "caller_pid": args.caller_pid,
            "project_hint": args.project_hint,
            "session_id": args.session_id,
            "timeout": args.timeout,
            "created_at": time.time(),
        }
        if not args.from_queue:
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
        event_type=args.event_type,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
