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
import subprocess
import sys

PID_FILE = "/tmp/ai_agent_notifier.pid"


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


def find_target_window(window_id_arg="", caller_pid=None):
    """
    Finds the exact X11 window ID for the application (VS Code / Terminal)
    that triggered the notification.
    """
    if window_id_arg and str(window_id_arg).strip().isdigit():
        wid = str(window_id_arg).strip()
        try:
            name = subprocess.check_output(["xdotool", "getwindowname", wid], stderr=subprocess.DEVNULL).decode().strip()
            if name:
                return wid
        except Exception:
            pass

    active_wid = None
    try:
        res = subprocess.check_output(["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL)
        active_wid = res.decode().strip()
    except Exception:
        pass

    curr_pid = caller_pid if caller_pid else os.getppid()
    visited = set()
    best_wid = None

    while curr_pid and curr_pid > 1 and curr_pid not in visited:
        visited.add(curr_pid)
        try:
            res = subprocess.check_output(["xdotool", "search", "--pid", str(curr_pid)], stderr=subprocess.DEVNULL)
            wids = [w.strip() for w in res.decode().splitlines() if w.strip()]
            for wid in wids:
                try:
                    name = subprocess.check_output(["xdotool", "getwindowname", wid], stderr=subprocess.DEVNULL).decode().strip()
                    if name and name != "code":
                        return wid
                    if not best_wid:
                        best_wid = wid
                except Exception:
                    if not best_wid:
                        best_wid = wid
            if best_wid:
                return best_wid
        except Exception:
            pass

        try:
            with open(f"/proc/{curr_pid}/stat", "r") as f:
                stat = f.read().split()
                curr_pid = int(stat[3])
        except Exception:
            break

    if active_wid and active_wid.isdigit():
        return active_wid

    return best_wid


def focus_target_window(window_id):
    """
    Activates and brings to front the specified window ID using xdotool.
    """
    if not window_id:
        return False
    try:
        subprocess.Popen(["xdotool", "windowactivate", "--sync", str(window_id)], stderr=subprocess.DEVNULL)
        return True
    except Exception:
        try:
            subprocess.Popen(["xdotool", "windowraise", str(window_id)], stderr=subprocess.DEVNULL)
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


def show_multi_monitor_popup(app_name, title, message, questions_json="", target_window_id="", timeout=0):
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
        window {{
            background-color: {bg_color};
            border: 1.5px solid {border_color};
            border-radius: 12px;
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
            if target_window_id:
                focus_target_window(target_window_id)
            Gtk.main_quit()

        for i in range(n_monitors):
            monitor = display.get_monitor(i)
            geom = monitor.get_geometry()

            win_width = int(min(560, max(460, geom.width * 0.30)))

            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_decorated(False)
            win.set_keep_above(True)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            win.set_role("notification-popup")

            # EventBox to capture clicks anywhere on the banner
            event_box = Gtk.EventBox()
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

    args = parser.parse_args()

    message = clean_text(args.message)

    # 1. Kill previous popup instance if running
    kill_previous_instance()

    # 2. Find target window to focus
    target_window_id = find_target_window(window_id_arg=args.window_id, caller_pid=args.caller_pid)

    # 3. Play sound asynchronously
    if args.sound:
        play_sound_async(args.sound)

    # 4. Display desktop popup on connected monitors
    show_multi_monitor_popup(
        args.app_name,
        args.title,
        message,
        questions_json=args.questions_json,
        target_window_id=target_window_id,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
