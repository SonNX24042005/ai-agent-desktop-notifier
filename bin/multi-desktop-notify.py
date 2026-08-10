#!/usr/bin/env python3
"""
Multi-Monitor Interactive Desktop Notifier for AI Coding Agents
(Claude Code, Codex, Google Antigravity, etc.)

Renders lightweight GTK TOPLEVEL popup notifications simultaneously on all connected monitors.
Supports viewing questions and submitting responses directly inside the notification banner:
- Single choice (Radio buttons)
- Multiple choice (Checkboxes)
- Free-text / Open-ended (Text Entry)
- Custom write-in answers ("Khác / Ghi chú...")
- Single or multiple questions (Scrollable panel)
- Auto-copies response to System Clipboard (Ctrl+V ready)
- Saves last answer to /tmp/ai_agent_last_answer.txt
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys

PID_FILE = "/tmp/ai_agent_notifier.pid"
LAST_ANSWER_FILE = "/tmp/ai_agent_last_answer.txt"


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


def clean_text(value, limit=400):
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


def copy_to_system_clipboard(text):
    """Fallback CLI copy to system clipboard for xclip / wl-copy."""
    if not text:
        return
    try:
        p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p.communicate(input=text.encode("utf-8"))
    except Exception:
        try:
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p.communicate(input=text.encode("utf-8"))
        except Exception:
            pass


def parse_questions_payload(questions_json_raw, fallback_title, fallback_message):
    """
    Parses payload / JSON into a normalized list of question dicts:
    [
        {
            "id": 1,
            "question": "Question text...",
            "options": ["Opt 1", "Opt 2"],
            "is_multi_select": False/True
        }
    ]
    """
    data = None
    if isinstance(questions_json_raw, str) and questions_json_raw.strip():
        try:
            data = json.loads(questions_json_raw)
        except Exception:
            data = None
    elif isinstance(questions_json_raw, (dict, list)):
        data = questions_json_raw

    raw_list = []
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        if "questions" in data and isinstance(data["questions"], list):
            raw_list = data["questions"]
        elif "tool_input" in data and isinstance(data["tool_input"], dict):
            ti = data["tool_input"]
            if "questions" in ti and isinstance(ti["questions"], list):
                raw_list = ti["questions"]
            else:
                raw_list = [ti]
        else:
            raw_list = [data]

    questions = []
    idx = 1

    for item in raw_list:
        if isinstance(item, str):
            if item.strip():
                questions.append({
                    "id": idx,
                    "question": item.strip(),
                    "options": [],
                    "is_multi_select": False
                })
                idx += 1
        elif isinstance(item, dict):
            q_text = (
                item.get("question") or
                item.get("title") or
                item.get("prompt") or
                item.get("message") or
                ""
            )
            raw_opts = item.get("options") or item.get("choices") or []
            opts = []
            if isinstance(raw_opts, list):
                for o in raw_opts:
                    if isinstance(o, str) and o.strip():
                        opts.append(o.strip())
                    elif isinstance(o, dict):
                        label = o.get("label") or o.get("text") or o.get("value") or str(o)
                        if label.strip():
                            opts.append(label.strip())

            is_multi = bool(
                item.get("is_multi_select") or
                item.get("multi_select") or
                item.get("isMultiSelect") or
                False
            )

            if q_text:
                questions.append({
                    "id": idx,
                    "question": q_text,
                    "options": opts,
                    "is_multi_select": is_multi
                })
                idx += 1

    # Fallback if no structured questions extracted
    if not questions and fallback_message and fallback_message.strip():
        questions.append({
            "id": 1,
            "question": fallback_message.strip(),
            "options": [],
            "is_multi_select": False
        })

    return questions


def show_multi_monitor_popup(app_name, title, message, questions_json_raw="", timeout=0):
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

    questions = parse_questions_payload(questions_json_raw, title, message)
    is_interactive = len(questions) > 0

    try:
        display = Gdk.Display.get_default()
        if not display:
            return
        n_monitors = display.get_n_monitors()

        # Dark theme color palette
        bg_color = "#18181b"        # Slate dark
        card_bg = "#27272a"         # Elevated zinc card
        border_color = "#3b82f6"    # Primary blue border
        card_border = "#3f3f46"     # Zinc border
        app_color = "#60a5fa"       # Light blue app header
        title_color = "#ffffff"     # White title
        q_color = "#f4f4f5"         # Zinc 100 text
        opt_color = "#e4e4e7"       # Zinc 200 text
        hint_color = "#a1a1aa"      # Muted zinc

        css = f"""
        window {{
            background-color: {bg_color};
            border: 1.5px solid {border_color};
            border-radius: 12px;
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
            font-size: 15px;
            font-weight: bold;
        }}
        .card {{
            background-color: {card_bg};
            border: 1px solid {card_border};
            border-radius: 8px;
            padding: 12px 14px;
        }}
        .q-title {{
            color: {q_color};
            font-size: 14px;
            font-weight: bold;
        }}
        .opt-label {{
            color: {opt_color};
            font-size: 13px;
        }}
        entry {{
            background-color: #18181b;
            color: #ffffff;
            border: 1px solid #52525b;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
        }}
        entry:focus {{
            border-color: #3b82f6;
        }}
        button.submit-btn {{
            background-color: #2563eb;
            color: #ffffff;
            font-weight: bold;
            border-radius: 6px;
            padding: 8px 16px;
            border: none;
            font-size: 13px;
        }}
        button.submit-btn:hover {{
            background-color: #1d4ed8;
        }}
        button.secondary-btn {{
            background-color: #3f3f46;
            color: #e4e4e7;
            border-radius: 6px;
            padding: 8px 14px;
            border: none;
            font-size: 13px;
        }}
        button.secondary-btn:hover {{
            background-color: #52525b;
        }}
        .hint {{
            color: {hint_color};
            font-size: 11px;
            font-style: italic;
        }}
        .toast {{
            color: #34d399;
            font-size: 13px;
            font-weight: bold;
        }}
        notebook {{
            background-color: transparent;
        }}
        notebook header {{
            background-color: transparent;
            border-bottom: 1px solid #3f3f46;
            padding: 0;
            margin: 0;
        }}
        notebook tab {{
            background-color: #27272a;
            color: #a1a1aa;
            border-radius: 5px 5px 0 0;
            padding: 3px 10px;
            font-weight: bold;
            font-size: 12px;
            margin-right: 3px;
            min-height: 24px;
            border: 1px solid #3f3f46;
            border-bottom: none;
        }}
        notebook tab:checked {{
            background-color: #2563eb;
            color: #ffffff;
            border-color: #3b82f6;
        }}
        notebook tab label {{
            color: inherit;
            font-weight: bold;
            padding: 0;
            margin: 0;
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
        all_q_widgets_per_win = []
        all_notebooks = []

        win_width = 520
        # Calculate dynamic window height based on questions & options
        total_items = sum(len(q["options"]) + 2 for q in questions)
        win_height = min(620, max(130, 90 + total_items * 28))

        def get_widget_text(w):
            if not w:
                return ""
            if isinstance(w, Gtk.Entry):
                return w.get_text().strip()
            elif isinstance(w, Gtk.TextView):
                buf = w.get_buffer()
                start, end = buf.get_bounds()
                return buf.get_text(start, end, True).strip()
            return ""

        def collect_answer_for_win(q_widgets):
            answers = []
            for q in q_widgets:
                ans_text = ""
                q_type = q["type"]
                if q_type == "single":
                    selected_opt = None
                    for rb, opt_str in q["radio_buttons"]:
                        if rb.get_active():
                            selected_opt = opt_str
                            break
                    custom_txt = get_widget_text(q["entry_custom"])
                    if q["rb_custom"] and q["rb_custom"].get_active():
                        ans_text = custom_txt if custom_txt else "Khác"
                    elif selected_opt:
                        ans_text = selected_opt
                        if custom_txt:
                            ans_text += f" ({custom_txt})"
                    else:
                        ans_text = custom_txt if custom_txt else "(Chưa chọn)"

                elif q_type == "multi":
                    selected_opts = []
                    for cb, opt_str in q["cb_list"]:
                        if cb.get_active():
                            selected_opts.append(opt_str)
                    custom_txt = get_widget_text(q["entry_custom"])
                    if q["cb_custom"] and q["cb_custom"].get_active():
                        if custom_txt:
                            selected_opts.append(custom_txt)
                        else:
                            selected_opts.append("Khác")
                    elif custom_txt and not selected_opts:
                        selected_opts.append(custom_txt)

                    if selected_opts:
                        ans_text = ", ".join(selected_opts)
                    else:
                        ans_text = custom_txt if custom_txt else "(Chưa chọn)"

                elif q_type == "free":
                    ans_text = get_widget_text(q["entry_free"])

                answers.append(ans_text)

            if len(answers) == 1:
                return answers[0]
            else:
                lines = [f"{idx}. {a}" for idx, a in enumerate(answers, 1)]
                return "\n".join(lines)

        def handle_submit(win_idx):
            q_widgets = all_q_widgets_per_win[win_idx]
            final_answer = collect_answer_for_win(q_widgets)

            if final_answer:
                # Save to file
                try:
                    with open(LAST_ANSWER_FILE, "w", encoding="utf-8") as f:
                        f.write(final_answer)
                except Exception:
                    pass

                # Copy to GTK Clipboard
                clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                clipboard.set_text(final_answer, -1)
                clipboard.store()

                # Fallback to CLI clipboard tools
                copy_to_system_clipboard(final_answer)

                # Print to stdout
                print(final_answer, flush=True)

                play_sound_async("/usr/share/sounds/freedesktop/stereo/complete.oga")

            Gtk.main_quit()

        def handle_copy_questions():
            q_lines = []
            for idx, q in enumerate(questions, 1):
                if len(questions) == 1:
                    q_lines.append(q["question"])
                else:
                    q_lines.append(f"{idx}. {q['question']}")
            full_q_text = "\n".join(q_lines)

            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(full_q_text, -1)
            clipboard.store()
            copy_to_system_clipboard(full_q_text)

        for i in range(n_monitors):
            monitor = display.get_monitor(i)
            geom = monitor.get_geometry()

            # Spacious dimensions: ~45% monitor width, ~48% monitor height
            win_width = int(min(920, max(680, geom.width * 0.45)))
            win_height = int(min(680, max(480, geom.height * 0.48)))

            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_decorated(False)
            win.set_keep_above(True)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            win.set_role("notification-popup")

            vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            vbox_main.set_margin_top(10)
            vbox_main.set_margin_bottom(10)
            vbox_main.set_margin_start(14)
            vbox_main.set_margin_end(14)

            # Parse Category Tag & Topic Title
            category_text = "THÔNG BÁO"
            topic_title = title

            if ":" in title:
                parts = title.split(":", 1)
                first_p = parts[0].strip()
                second_p = parts[1].strip()
                if first_p.lower() in (app_name.lower(), "antigravity", "claude code", "codex", "system"):
                    category_text = second_p.upper()
                    topic_title = message.split("\n")[0] if message else second_p
                else:
                    category_text = first_p.upper()
                    topic_title = second_p
            elif is_interactive:
                category_text = "CÂU HỎI"

            if not topic_title or topic_title.upper() == category_text:
                topic_title = message.split("\n")[0] if message else "Thông báo chú ý"

            # Header Box (Agent Badge + Category Tag + Topic Title + Hint)
            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

            badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            # Clean Agent Name (strictly pure Agent name, e.g., ANTIGRAVITY, CLAUDE CODE, CODEX)
            raw_agent = re.split(r'[:\-_]', app_name)[0].strip()
            agent_name_text = raw_agent.upper() if raw_agent else "AI AGENT"

            lbl_agent = Gtk.Label(label=agent_name_text)
            lbl_agent.get_style_context().add_class("agent-badge")

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

            badge_box.pack_start(lbl_agent, False, False, 0)
            badge_box.pack_start(lbl_cat, False, False, 0)

            lbl_topic = Gtk.Label(label=topic_title, xalign=0)
            lbl_topic.get_style_context().add_class("topic-title")
            lbl_topic.set_ellipsize(Pango.EllipsizeMode.END)

            title_box.pack_start(badge_box, False, False, 0)
            if not is_interactive:
                title_box.pack_start(lbl_topic, False, False, 0)

            header_box.pack_start(title_box, True, True, 0)

            if len(questions) > 1:
                lbl_hint = Gtk.Label(label="←/→ Chuyển câu", xalign=1)
                lbl_hint.get_style_context().add_class("hint")
                header_box.pack_end(lbl_hint, False, False, 0)

            vbox_main.pack_start(header_box, False, False, 0)

            # Questions Body Container
            notebook = None
            if len(questions) > 1:
                notebook = Gtk.Notebook()
                notebook.set_scrollable(True)
                notebook.popup_enable()
            else:
                scroll = Gtk.ScrolledWindow()
                scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                scroll.set_shadow_type(Gtk.ShadowType.NONE)
                scroll.set_propagate_natural_height(True)
                scroll.set_max_content_height(int(geom.height * 0.48))
                cards_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                cards_vbox.set_margin_top(4)
                cards_vbox.set_margin_bottom(4)

            q_widgets_this_win = []

            for q_idx, q in enumerate(questions, start=1):
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                card.get_style_context().add_class("card")

                # Question Label with XML markup escaping & smart number prefixing
                q_text = q["question"]
                starts_with_num = bool(re.match(r'^\s*\(?\d+[\.\:\)\/\]\-]\s*', q_text))
                prefix = f"{q_idx}. " if (len(questions) > 1 and not starts_with_num) else ""
                full_q_str = f"{prefix}{q_text}"
                escaped_q = GLib.markup_escape_text(full_q_str)
                
                lbl_q = Gtk.Label(xalign=0)
                lbl_q.get_style_context().add_class("q-title")
                lbl_q.set_markup(f"<b>{escaped_q}</b>")
                lbl_q.set_line_wrap(True)
                lbl_q.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                card.pack_start(lbl_q, False, False, 0)

                q_info = {
                    "type": "free",
                    "radio_buttons": [],
                    "rb_custom": None,
                    "cb_list": [],
                    "cb_custom": None,
                    "entry_custom": None,
                    "entry_free": None
                }

                def style_opt_btn(btn):
                    child = btn.get_child()
                    if isinstance(child, Gtk.Label):
                        child.get_style_context().add_class("opt-label")
                        child.set_line_wrap(True)
                        child.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                        child.set_ellipsize(Pango.EllipsizeMode.NONE)
                        child.set_xalign(0)

                def create_multiline_input(placeholder_text):
                    frame = Gtk.Frame()
                    frame.set_shadow_type(Gtk.ShadowType.IN)
                    frame.get_style_context().add_class("entry-frame")

                    tv = Gtk.TextView()
                    tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                    tv.set_pixels_above_lines(2)
                    tv.set_pixels_below_lines(2)
                    tv.set_left_margin(8)
                    tv.set_right_margin(8)
                    tv.get_style_context().add_class("custom-textview")
                    
                    # Default 1 line height (~30px)
                    tv.set_size_request(-1, 30)

                    buf = tv.get_buffer()

                    def on_buffer_changed(b):
                        start, end = b.get_bounds()
                        text = b.get_text(start, end, True)
                        explicit_lines = b.get_line_count()
                        wrapped_lines = max(1, (len(text) + 90) // 90) if text else 1
                        lines = min(6, max(explicit_lines, wrapped_lines))
                        new_h = 30 + (lines - 1) * 18
                        tv.set_size_request(-1, new_h)

                    buf.connect("changed", on_buffer_changed)

                    frame.add(tv)
                    return frame, tv

                opts = q["options"]
                is_multi = q["is_multi_select"]

                if opts:
                    if not is_multi:
                        # Single Choice (RadioButtons)
                        q_info["type"] = "single"
                        radio_group = None
                        for opt_str in opts:
                            rb = Gtk.RadioButton.new_with_label_from_widget(radio_group, opt_str)
                            if radio_group is None:
                                radio_group = rb
                            style_opt_btn(rb)
                            card.pack_start(rb, False, False, 0)
                            q_info["radio_buttons"].append((rb, opt_str))

                        # Custom option radio button + entry
                        rb_custom = Gtk.RadioButton.new_with_label_from_widget(radio_group, "Khác / Ghi chú:")
                        style_opt_btn(rb_custom)
                        card.pack_start(rb_custom, False, False, 0)
                        q_info["rb_custom"] = rb_custom

                        frame_custom, tv_custom = create_multiline_input("Nhập câu trả lời hoặc ghi chú...")
                        card.pack_start(frame_custom, False, False, 0)
                        q_info["entry_custom"] = tv_custom

                    else:
                        # Multiple Choice (CheckButtons)
                        q_info["type"] = "multi"
                        for opt_str in opts:
                            cb = Gtk.CheckButton(label=opt_str)
                            style_opt_btn(cb)
                            card.pack_start(cb, False, False, 0)
                            q_info["cb_list"].append((cb, opt_str))

                        cb_custom = Gtk.CheckButton(label="Khác / Ghi chú:")
                        style_opt_btn(cb_custom)
                        card.pack_start(cb_custom, False, False, 0)
                        q_info["cb_custom"] = cb_custom

                        frame_custom, tv_custom = create_multiline_input("Nhập câu trả lời hoặc ghi chú...")
                        card.pack_start(frame_custom, False, False, 0)
                        q_info["entry_custom"] = tv_custom

                else:
                    # Free Text (No pre-defined options)
                    q_info["type"] = "free"
                    frame_free, tv_free = create_multiline_input("Nhập câu trả lời của bạn ở đây...")
                    card.pack_start(frame_free, False, False, 0)
                    q_info["entry_free"] = tv_free

                if len(questions) > 1:
                    page_scroll = Gtk.ScrolledWindow()
                    page_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                    page_scroll.set_shadow_type(Gtk.ShadowType.NONE)
                    page_scroll.set_propagate_natural_height(True)
                    page_scroll.set_max_content_height(int(geom.height * 0.48))
                    page_scroll.set_margin_top(6)
                    page_scroll.add(card)

                    tab_lbl = Gtk.Label(label=f"Câu {q_idx}")
                    notebook.append_page(page_scroll, tab_lbl)
                else:
                    cards_vbox.pack_start(card, False, False, 0)

                q_widgets_this_win.append(q_info)

            all_q_widgets_per_win.append(q_widgets_this_win)

            if len(questions) > 1:
                vbox_main.pack_start(notebook, True, True, 0)
            else:
                scroll.add(cards_vbox)
                vbox_main.pack_start(scroll, True, True, 0)

            # Footer / Action Buttons
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_box.set_margin_top(4)

            btn_submit = Gtk.Button(label="Gửi (Ctrl+Enter)")
            btn_submit.get_style_context().add_class("submit-btn")
            btn_submit.connect("clicked", lambda b, win_i=i: handle_submit(win_i))

            btn_copy_q = Gtk.Button(label="Copy câu hỏi")
            btn_copy_q.get_style_context().add_class("secondary-btn")
            btn_copy_q.connect("clicked", lambda b: handle_copy_questions())

            btn_close = Gtk.Button(label="Đóng (Esc)")
            btn_close.get_style_context().add_class("secondary-btn")
            btn_close.connect("clicked", lambda b: Gtk.main_quit())

            btn_box.pack_start(btn_submit, False, False, 0)
            btn_box.pack_start(btn_copy_q, False, False, 0)
            btn_box.pack_end(btn_close, False, False, 0)

            vbox_main.pack_start(btn_box, False, False, 0)

            # Key press handler (Ctrl+Enter to submit, Esc to close, Left/Right arrow to switch tabs)
            nbook_ref = notebook if len(questions) > 1 else None

            def on_key_press(w, event, win_i=i, nbook=nbook_ref):
                if event.keyval == Gdk.KEY_Escape:
                    Gtk.main_quit()
                    return True
                if (event.state & Gdk.ModifierType.CONTROL_MASK) and (
                    event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                ):
                    handle_submit(win_i)
                    return True

                if nbook is not None:
                    is_alt = bool(event.state & Gdk.ModifierType.MOD1_MASK)
                    focus_w = win.get_focus()
                    is_typing = isinstance(focus_w, (Gtk.TextView, Gtk.Entry))

                    if (
                        (event.keyval in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up)) or
                        (is_alt and event.keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left)) or
                        (not is_typing and event.keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left))
                    ):
                        nbook.prev_page()
                        return True
                    if (
                        (event.keyval in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down)) or
                        (is_alt and event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right)) or
                        (not is_typing and event.keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right))
                    ):
                        nbook.next_page()
                        return True

                return False

            win.connect("key-press-event", on_key_press)
            win.add(vbox_main)
            win.set_size_request(win_width, -1)
            win.set_default_size(win_width, -1)

            # Dynamically re-center window on monitor based on actual allocated width
            def on_size_allocate(w, alloc, gx, gw, gy):
                win_x = gx + (gw - alloc.width) // 2
                win_y = gy + 40
                w.move(win_x, win_y)

            win.connect("size-allocate", lambda w, alloc, gx=geom.x, gw=geom.width, gy=geom.y: on_size_allocate(w, alloc, gx, gw, gy))

            if len(questions) > 1:
                all_notebooks.append(notebook)

            win.show_all()
            windows.append(win)

        # Multi-monitor State Synchronization Layer
        is_syncing = False

        # 1. Sync Notebook Tab switching across monitor windows
        if len(questions) > 1 and len(all_notebooks) > 1:
            def sync_tab(src_nb, target_page):
                nonlocal is_syncing
                if is_syncing:
                    return
                is_syncing = True
                try:
                    for nb in all_notebooks:
                        if nb != src_nb and nb.get_current_page() != target_page:
                            nb.set_current_page(target_page)
                finally:
                    is_syncing = False

            for nb in all_notebooks:
                nb.connect("switch-page", lambda w, page, p_num: sync_tab(w, p_num))

        # 2. Sync Widget Choices and Text Inputs across monitor windows
        if len(all_q_widgets_per_win) > 1:
            n_wins = len(all_q_widgets_per_win)
            for q_idx in range(len(questions)):
                # Sync RadioButtons
                for opt_idx in range(len(questions[q_idx]["options"])):
                    def make_rb_sync(src_w_i, q_i, o_i):
                        def on_rb_toggled(rb):
                            nonlocal is_syncing
                            if is_syncing or not rb.get_active():
                                return
                            is_syncing = True
                            try:
                                for target_w_i in range(n_wins):
                                    if target_w_i != src_w_i:
                                        target_rb, _ = all_q_widgets_per_win[target_w_i][q_i]["radio_buttons"][o_i]
                                        if not target_rb.get_active():
                                            target_rb.set_active(True)
                            finally:
                                is_syncing = False
                        return on_rb_toggled

                    for win_i in range(n_wins):
                        q_w = all_q_widgets_per_win[win_i][q_idx]
                        if q_w["type"] == "single" and opt_idx < len(q_w["radio_buttons"]):
                            rb, _ = q_w["radio_buttons"][opt_idx]
                            rb.connect("toggled", make_rb_sync(win_i, q_idx, opt_idx))

                # Sync Custom RadioButton
                def make_rb_custom_sync(src_w_i, q_i):
                    def on_rb_c_toggled(rb):
                        nonlocal is_syncing
                        if is_syncing or not rb.get_active():
                            return
                        is_syncing = True
                        try:
                            for target_w_i in range(n_wins):
                                if target_w_i != src_w_i:
                                    target_rb_c = all_q_widgets_per_win[target_w_i][q_i]["rb_custom"]
                                    if target_rb_c and not target_rb_c.get_active():
                                        target_rb_c.set_active(True)
                        finally:
                            is_syncing = False
                    return on_rb_c_toggled

                for win_i in range(n_wins):
                    q_w = all_q_widgets_per_win[win_i][q_idx]
                    if q_w["rb_custom"]:
                        q_w["rb_custom"].connect("toggled", make_rb_custom_sync(win_i, q_idx))

                # Sync CheckButtons
                for opt_idx in range(len(questions[q_idx]["options"])):
                    def make_cb_sync(src_w_i, q_i, o_i):
                        def on_cb_toggled(cb):
                            nonlocal is_syncing
                            if is_syncing:
                                return
                            is_syncing = True
                            try:
                                st = cb.get_active()
                                for target_w_i in range(n_wins):
                                    if target_w_i != src_w_i:
                                        target_cb, _ = all_q_widgets_per_win[target_w_i][q_i]["cb_list"][o_i]
                                        if target_cb.get_active() != st:
                                            target_cb.set_active(st)
                            finally:
                                is_syncing = False
                        return on_cb_toggled

                    for win_i in range(n_wins):
                        q_w = all_q_widgets_per_win[win_i][q_idx]
                        if q_w["type"] == "multi" and opt_idx < len(q_w["cb_list"]):
                            cb, _ = q_w["cb_list"][opt_idx]
                            cb.connect("toggled", make_cb_sync(win_i, q_idx, opt_idx))

                # Sync Custom CheckButton
                def make_cb_custom_sync(src_w_i, q_i):
                    def on_cb_c_toggled(cb):
                        nonlocal is_syncing
                        if is_syncing:
                            return
                        is_syncing = True
                        try:
                            st = cb.get_active()
                            for target_w_i in range(n_wins):
                                if target_w_i != src_w_i:
                                    target_cb_c = all_q_widgets_per_win[target_w_i][q_i]["cb_custom"]
                                    if target_cb_c and target_cb_c.get_active() != st:
                                        target_cb_c.set_active(st)
                        finally:
                            is_syncing = False
                    return on_cb_c_toggled

                for win_i in range(n_wins):
                    q_w = all_q_widgets_per_win[win_i][q_idx]
                    if q_w["cb_custom"]:
                        q_w["cb_custom"].connect("toggled", make_cb_custom_sync(win_i, q_idx))

                # Sync TextView / Entry Text
                def make_tv_sync(src_w_i, q_i, key):
                    def on_text_changed(buf):
                        nonlocal is_syncing
                        if is_syncing:
                            return
                        is_syncing = True
                        try:
                            s, e = buf.get_bounds()
                            txt = buf.get_text(s, e, True)
                            for target_w_i in range(n_wins):
                                if target_w_i != src_w_i:
                                    target_tv = all_q_widgets_per_win[target_w_i][q_i][key]
                                    if target_tv:
                                        t_buf = target_tv.get_buffer()
                                        t_s, t_e = t_buf.get_bounds()
                                        if t_buf.get_text(t_s, t_e, True) != txt:
                                            t_buf.set_text(txt)
                        finally:
                            is_syncing = False
                    return on_text_changed

                for win_i in range(n_wins):
                    q_w = all_q_widgets_per_win[win_i][q_idx]
                    if q_w["entry_custom"]:
                        q_w["entry_custom"].get_buffer().connect("changed", make_tv_sync(win_i, q_idx, "entry_custom"))
                    if q_w["entry_free"]:
                        q_w["entry_free"].get_buffer().connect("changed", make_tv_sync(win_i, q_idx, "entry_free"))

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

    args = parser.parse_args()

    message = clean_text(args.message)

    # 1. Kill previous popup instance if running
    kill_previous_instance()

    # 2. Play sound asynchronously
    if args.sound:
        play_sound_async(args.sound)

    # 3. Display interactive GTK popup on all connected monitors
    show_multi_monitor_popup(
        args.app_name, args.title, message, questions_json_raw=args.questions_json, timeout=args.timeout
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
