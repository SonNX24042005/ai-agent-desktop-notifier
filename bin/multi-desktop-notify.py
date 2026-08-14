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
CONFIG_FILE = os.path.expanduser("~/.config/ai-agent-notifier/config.json")


def save_session_window(session_id, window_id, project_hint="", pid=0):
    """Caches target window ID for a session ID to enable 100% precision focus."""
    if not session_id or not window_id:
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
        if s_info and isinstance(s_info, dict):
            return s_info.get("window_id", "")
        elif isinstance(s_info, str):
            return s_info
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
            if old_pid != os.getpid():
                try:
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


def get_all_managed_windows():
    results = []
    try:
        out = subprocess.check_output(["xdotool", "search", "--onlyvisible", ""], stderr=subprocess.DEVNULL).decode()
        for wid in out.splitlines():
            wid = wid.strip()
            if not wid or not is_valid_toplevel_window(wid):
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
        if cached_wid and is_valid_toplevel_window(cached_wid):
            return cached_wid

    project_hint = (project_hint or "").strip().lower()
    managed_windows = get_all_managed_windows()

    # 1. Tier 1: Match by PID tree + project_hint (Exact process owner)
    if caller_pid:
        pid_tree = get_process_ancestors(caller_pid)
        tree_windows = [(wid, name) for wid, name, wpid in managed_windows if wpid in pid_tree]
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
        if tty_window and is_valid_toplevel_window(tty_window):
            if session_id:
                save_session_window(session_id, tty_window, project_hint, caller_pid)
            return tty_window

    # 4. Tier 4: Explicit window_id_arg if valid
    if window_id_arg and str(window_id_arg).strip().isdigit():
        wid = str(window_id_arg).strip()
        if is_valid_toplevel_window(wid):
            if session_id:
                save_session_window(session_id, wid, project_hint, caller_pid)
            return wid

    # 5. Tier 5: Fallback to active window
    try:
        res = subprocess.check_output(["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL)
        active_wid = res.decode().strip()
        if active_wid and is_valid_toplevel_window(active_wid):
            if session_id:
                save_session_window(session_id, active_wid, project_hint, caller_pid)
            return active_wid
    except Exception:
        pass

    return ""


def focus_target_window(window_id):
    """
    Activates and brings to front the specified window ID using GDK native and xdotool / EWMH.
    """
    if not window_id:
        return False

    wid_str = str(window_id).strip()
    if not wid_str.isdigit():
        return False

    wid_int = int(wid_str)

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

    # 2. Xdotool windowactivate, windowraise, and windowfocus
    try:
        subprocess.run(["xdotool", "windowactivate", "--sync", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["xdotool", "windowraise", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["xdotool", "windowfocus", "--sync", wid_str], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


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


def show_multi_monitor_popup(app_name, title, message, questions_json="", target_window_id="", timeout=0, caller_pid=0, project_hint="", session_id=""):
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return

    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        gi.require_version("Pango", "1.0")
        from gi.repository import Gdk, GLib, Gtk, Pango
    except Exception:
        return

    display_text = extract_summary_from_payload(questions_json, message)
    if not display_text:
        display_text = "AI Agent đang chờ bạn tương tác."

    try:
        display = Gdk.Display.get_default()
        if not display:
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
            padding: 12px 16px;
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

        def handle_focus_and_close():
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

            # EventBox to capture clicks anywhere on the banner
            event_box = Gtk.EventBox()
            event_box.set_visible_window(True)
            event_box.get_style_context().add_class("notification-card")
            event_box.connect("button-press-event", lambda w, e: handle_focus_and_close())

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

            vbox_main.pack_start(header_box, False, False, 0)

            # Title & Cleaned Message Text
            lbl_title = Gtk.Label(label=title, xalign=0)
            lbl_title.get_style_context().add_class("topic-title")
            lbl_title.set_ellipsize(Pango.EllipsizeMode.END)

            escaped_msg = GLib.markup_escape_text(clean_text(display_text, limit=260))
            lbl_msg = Gtk.Label(xalign=0)
            lbl_msg.get_style_context().add_class("msg-text")
            lbl_msg.set_markup(escaped_msg)
            lbl_msg.set_line_wrap(True)
            lbl_msg.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)

            vbox_main.pack_start(lbl_title, False, False, 0)
            vbox_main.pack_start(lbl_msg, False, False, 0)

            # Action Buttons Row
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_box.set_margin_top(4)

            btn_focus = Gtk.Button(label="Đến cửa sổ ứng dụng")
            btn_focus.get_style_context().add_class("focus-btn")
            btn_focus.connect("clicked", lambda b: handle_focus_and_close())

            btn_close = Gtk.Button(label="✕ Đóng")
            btn_close.get_style_context().add_class("close-btn")
            btn_close.connect("clicked", lambda b: Gtk.main_quit())

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
                if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
                    handle_focus_and_close()
                    return True
                if event.keyval == Gdk.KEY_Escape:
                    Gtk.main_quit()
                    return True
                return False

            win.connect("key-press-event", on_key_press)
            win.show_all()
            windows.append(win)

        if timeout > 0:
            GLib.timeout_add_seconds(timeout, Gtk.main_quit)

        Gtk.main()
    except Exception:
        pass


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
    parser.add_argument("--dedupe-seconds", type=int, default=2)

    args = parser.parse_args()

    # 0. Session capture mode (Pure side-effect, 0ms execution without rendering UI)
    if args.capture_session:
        target_wid = find_target_window(
            window_id_arg=args.window_id,
            caller_pid=args.caller_pid,
            project_hint=args.project_hint,
            caller_tty=args.caller_tty,
            terminal_screen=args.terminal_screen,
            session_id=args.session_id,
        )
        if target_wid and args.session_id:
            save_session_window(args.session_id, target_wid, args.project_hint, args.caller_pid)
        return

    message = clean_text(args.message)

    # 1. Deduplication check (Skip duplicate notification spam)
    if is_duplicate_notification(args.app_name, args.title, message, args.dedupe_seconds):
        return

    # 2. Kill previous popup instance if running
    kill_previous_instance()

    # 3. Find target window to focus
    target_window_id = find_target_window(
        window_id_arg=args.window_id,
        caller_pid=args.caller_pid,
        project_hint=args.project_hint,
        caller_tty=args.caller_tty,
        terminal_screen=args.terminal_screen,
        session_id=args.session_id,
    )

    # 4. Play sound asynchronously
    if args.sound:
        play_sound_async(args.sound)

    # 5. Dispatch optional webhooks asynchronously
    dispatch_webhooks_async(args.app_name, args.title, message)

    # 6. Display desktop popup on connected monitors
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
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
