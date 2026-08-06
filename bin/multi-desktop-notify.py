#!/usr/bin/env python3

"""
Multi-Monitor Desktop Notifier for AI Coding Agents (Claude Code, Codex, Antigravity)
Renders lightweight GTK TOPLEVEL popup notifications simultaneously on all connected monitors.
"""

import argparse
import os
import subprocess
import sys

PAPLAY = "/usr/bin/paplay"


def clean_text(value, limit=400):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def play_sound_async(sound_path):
    if sound_path and os.path.isfile(sound_path) and os.access(PAPLAY, os.X_OK):
        try:
            subprocess.Popen(
                [PAPLAY, sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def show_multi_monitor_popup(app_name, title, message, timeout=4):
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
    except Exception:
        return

    try:
        display = Gdk.Display.get_default()
        if not display:
            return
        n_monitors = display.get_n_monitors()

        # Single elegant theme for all notifications
        bg_color = "#18181b"       # Modern dark slate
        border_color = "#3b82f6"   # Subtle blue accent border
        app_color = "#60a5fa"      # Light blue app header
        title_color = "#ffffff"    # Pure white title text
        msg_color = "#e4e4e7"      # Light gray message text

        css = f"""
        window {{
            background-color: {bg_color};
            border: 1px solid {border_color};
            border-radius: 8px;
        }}
        .appname {{
            color: {app_color};
            font-size: 11px;
            font-weight: bold;
        }}
        .title {{
            color: {title_color};
            font-size: 14px;
            font-weight: bold;
        }}
        .message {{
            color: {msg_color};
            font-size: 13px;
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
        win_width = 440
        win_height = 80

        for i in range(n_monitors):
            monitor = display.get_monitor(i)
            geom = monitor.get_geometry()

            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_decorated(False)
            win.set_keep_above(True)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            win.set_role("notification-popup")

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            vbox.set_margin_top(10)
            vbox.set_margin_bottom(10)
            vbox.set_margin_start(16)
            vbox.set_margin_end(16)

            lbl_app = Gtk.Label(label=app_name.upper(), xalign=0)
            lbl_app.get_style_context().add_class("appname")

            lbl_title = Gtk.Label(label=title, xalign=0)
            lbl_title.get_style_context().add_class("title")

            lbl_msg = Gtk.Label(label=message, xalign=0)
            lbl_msg.get_style_context().add_class("message")
            lbl_msg.set_line_wrap(True)
            lbl_msg.set_max_width_chars(52)

            vbox.pack_start(lbl_app, False, False, 0)
            vbox.pack_start(lbl_title, False, False, 0)
            vbox.pack_start(lbl_msg, False, False, 0)

            win.add(vbox)
            win.set_default_size(win_width, win_height)

            win_x = geom.x + (geom.width - win_width) // 2
            win_y = geom.y + 40
            win.move(win_x, win_y)

            win.connect("button-press-event", lambda w, e: Gtk.main_quit())
            win.show_all()
            windows.append(win)

        GLib.timeout_add_seconds(timeout, Gtk.main_quit)
        Gtk.main()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Multi-monitor desktop notification")
    parser.add_argument("--app-name", default="System Notification")
    parser.add_argument("--title", default="Notification")
    parser.add_argument("--message", default="")
    parser.add_argument("--urgency", choices=["low", "normal", "critical"], default="normal")
    parser.add_argument("--sound", default="")
    parser.add_argument("--timeout", type=int, default=4)

    args = parser.parse_args()

    message = clean_text(args.message)

    # 1. Play sound asynchronously
    if args.sound:
        play_sound_async(args.sound)

    # 2. Display GTK popup on all connected monitors simultaneously
    show_multi_monitor_popup(
        args.app_name, args.title, message, timeout=args.timeout
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
