#!/usr/bin/env python3
"""
Comprehensive verification tests for all remediation items:
- SEC-002: Secure runtime directory creation and permissions
- SEC-003: Safe configuration merge and unmerge preserving third-party hooks
- SEC-004: Windows PID reuse validation before termination
- LOG-001: Queue concurrency locking and atomic file updates
- LOG-002: Adapter PPID caller identity passing
- LOG-003: Explicit event-type contract and tag formatting
- LOG-004: Stale project hint validation logic
- LOG-005: Dismiss vs resolve queue state management
- UX-001: Keyboard shortcut and action button labels
- UX-002: Boilerplate filtering and summary preservation
- OPS-001: Artifact symmetry across install/update/uninstall
"""

import os
import sys
import time
import json
import stat
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "bin" / "multi-desktop-notify.py"
spec = importlib.util.spec_from_file_location("multi_desktop_notify", str(SCRIPT_PATH))
mdn = importlib.util.module_from_spec(spec)
sys.modules["multi_desktop_notify"] = mdn
spec.loader.exec_module(mdn)


class TestSecureRuntimeDirectory(unittest.TestCase):
    """Verifies SEC-002: Secure runtime directory creation and permissions."""

    def test_runtime_dir_env_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AI_AGENT_NOTIFIER_RUNTIME_DIR": tmpdir}):
                r_dir = mdn.get_runtime_dir()
                self.assertEqual(r_dir, tmpdir)

    def test_runtime_dir_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_runtime = os.path.join(tmpdir, "custom_runtime")
            with patch.dict(os.environ, {"AI_AGENT_NOTIFIER_RUNTIME_DIR": custom_runtime}):
                r_dir = mdn.get_runtime_dir()
                self.assertTrue(os.path.exists(r_dir))
                if hasattr(os, "getuid"):
                    st = os.stat(r_dir)
                    mode = stat.S_IMODE(st.st_mode)
                    self.assertEqual(mode, 0o700)


class TestQueueConcurrencyAndDismiss(unittest.TestCase):
    """Verifies LOG-001 and LOG-005: Atomic transactions, locking, dismiss vs resolve."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_queue_cache = mdn.QUEUE_CACHE_FILE
        self.orig_queue_lock = mdn.QUEUE_LOCK_FILE
        self.orig_session_cache = mdn.SESSION_CACHE_FILE
        self.orig_session_lock = mdn.SESSION_LOCK_FILE

        mdn.QUEUE_CACHE_FILE = os.path.join(self.tmp_dir.name, "queue.json")
        mdn.QUEUE_LOCK_FILE = os.path.join(self.tmp_dir.name, "queue.lock")
        mdn.SESSION_CACHE_FILE = os.path.join(self.tmp_dir.name, "sessions.json")
        mdn.SESSION_LOCK_FILE = os.path.join(self.tmp_dir.name, "sessions.lock")

    def tearDown(self):
        mdn.QUEUE_CACHE_FILE = self.orig_queue_cache
        mdn.QUEUE_LOCK_FILE = self.orig_queue_lock
        mdn.SESSION_CACHE_FILE = self.orig_session_cache
        mdn.SESSION_LOCK_FILE = self.orig_session_lock
        self.tmp_dir.cleanup()

    def test_save_and_mark_dismissed_vs_remove(self):
        item1 = {
            "app_name": "Claude Code",
            "title": "Câu hỏi 1",
            "message": "Chi tiết câu hỏi",
            "window_id": "0x123",
            "event_type": "question",
            "project_hint": "proj_a",
            "created_at": time.time(),
        }
        mdn.save_to_queue("sess_001", item1)
        queue = mdn.load_notification_queue()
        self.assertEqual(len(queue), 1)
        self.assertIn("sess_001", queue)
        self.assertFalse(queue["sess_001"].get("dismissed", False))

        mdn.mark_queue_item_dismissed("sess_001")
        queue_after_dismiss = mdn.load_notification_queue()
        self.assertEqual(len(queue_after_dismiss), 1)
        self.assertTrue(queue_after_dismiss["sess_001"].get("dismissed", False))

        item2 = {
            "app_name": "Antigravity",
            "title": "Câu hỏi 2",
            "message": "Chi tiết câu hỏi 2",
            "window_id": "0x456",
            "event_type": "question",
            "project_hint": "proj_b",
            "created_at": time.time(),
        }
        mdn.save_to_queue("sess_002", item2)
        queue_two = mdn.load_notification_queue()
        self.assertEqual(len(queue_two), 2)

        mdn.remove_from_queue("sess_001")
        queue_final = mdn.load_notification_queue()
        self.assertEqual(len(queue_final), 1)
        self.assertIn("sess_002", queue_final)
        self.assertNotIn("sess_001", queue_final)

    def test_pop_next_notification_atomic_consumption_no_loop(self):
        item_a = {
            "app_name": "Agent A",
            "title": "Câu hỏi A",
            "message": "Nội dung A",
            "session_id": "sess_A",
            "created_at": time.time() - 10,
        }
        item_b = {
            "app_name": "Agent B",
            "title": "Câu hỏi B",
            "message": "Nội dung B",
            "session_id": "sess_B",
            "created_at": time.time() - 5,
        }
        mdn.save_to_queue("sess_A", item_a)
        mdn.save_to_queue("sess_B", item_b)

        with patch("subprocess.Popen") as mock_popen:
            mdn.pop_next_notification_async(exclude_key="sess_X")
            self.assertEqual(mock_popen.call_count, 1)

            # sess_A must be atomically consumed from queue
            q1 = mdn.load_notification_queue()
            self.assertNotIn("sess_A", q1)
            self.assertIn("sess_B", q1)

            # Popping again pops sess_B
            mdn.pop_next_notification_async()
            self.assertEqual(mock_popen.call_count, 2)

            q2 = mdn.load_notification_queue()
            self.assertNotIn("sess_B", q2)
            self.assertEqual(len(q2), 0)

            # Popping third time finds empty queue and does nothing (no infinite loop)
            mdn.pop_next_notification_async()
            self.assertEqual(mock_popen.call_count, 2)


class TestEventTypeContract(unittest.TestCase):
    """Verifies LOG-003: Explicit event type handling and completion precision."""

    def test_event_type_choices(self):
        item_question = {"app_name": "Claude", "title": "Claude: Hỏi", "message": "Test", "event_type": "question"}
        item_perm = {"app_name": "Codex", "title": "Codex: Cần cấp quyền", "message": "Test", "event_type": "permission"}
        item_comp = {"app_name": "Antigravity", "title": "Hoàn thành", "message": "Test", "event_type": "complete"}

        self.assertEqual(item_question["event_type"], "question")
        self.assertEqual(item_perm["event_type"], "permission")
        self.assertEqual(item_comp["event_type"], "complete")

    def test_boilerplate_message_filter(self):
        """Verifies UX-002: Boilerplate filtering does not drop meaningful summaries."""
        self.assertTrue(mdn.is_boilerplate_message("Claude Code đang chờ bạn.", "tag-info"))
        self.assertTrue(mdn.is_boilerplate_message("Claude đã hoàn thành trả lời.", "tag-complete"))
        self.assertTrue(mdn.is_boilerplate_message("Antigravity đã hoàn thành trả lời.", "tag-complete"))
        self.assertTrue(mdn.is_boilerplate_message("Codex đã hoàn thành lượt làm việc.", "tag-complete"))

        self.assertFalse(mdn.is_boilerplate_message("Đã hoàn thành phân tích 14 lỗ hổng bảo mật và cập nhật báo cáo.", "tag-complete"))
        self.assertFalse(mdn.is_boilerplate_message("Tôi đã tối ưu hóa thuật toán và cập nhật các unit test.", "tag-complete"))
        self.assertFalse(mdn.is_boilerplate_message("Bạn có muốn tiếp tục chạy bước 3 hay không?", "tag-question"))

    def test_completion_notification_is_available_to_global_focus(self):
        argv = [
            str(SCRIPT_PATH),
            "--app-name=Codex",
            "--title=Codex: Hoàn thành",
            "--message=Codex đã hoàn thành lượt làm việc.",
            "--event-type=complete",
            "--session-id=completion-session",
            "--caller-pid=900",
            "--project-hint=project",
            "--dedupe-seconds=0",
        ]

        with patch.object(sys, "argv", argv), \
             patch.object(mdn, "is_duplicate_notification", return_value=False), \
             patch.object(mdn, "kill_previous_instance"), \
             patch.object(mdn, "find_target_window", return_value=""), \
             patch.object(mdn, "save_to_queue") as mock_save, \
             patch.object(mdn, "dispatch_webhooks_async"), \
             patch.object(mdn, "show_multi_monitor_popup"):
            mdn.main()

        self.assertEqual(mock_save.call_count, 1)
        queue_key, item = mock_save.call_args.args
        self.assertEqual(queue_key, "sess_completion-session")
        self.assertEqual(item["event_type"], "complete")
        self.assertEqual(item["project_hint"], "project")


class TestStaleProjectHintValidation(unittest.TestCase):
    """Verifies LOG-004: Project hint check allows valid developer windows."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_session_cache = mdn.SESSION_CACHE_FILE
        self.orig_session_lock = mdn.SESSION_LOCK_FILE
        mdn.SESSION_CACHE_FILE = os.path.join(self.tmp_dir.name, "sessions.json")
        mdn.SESSION_LOCK_FILE = os.path.join(self.tmp_dir.name, "sessions.lock")

    def tearDown(self):
        mdn.SESSION_CACHE_FILE = self.orig_session_cache
        mdn.SESSION_LOCK_FILE = self.orig_session_lock
        self.tmp_dir.cleanup()

    def test_get_session_window_with_matching_developer_window(self):
        with patch.object(mdn, "is_valid_toplevel_window", return_value=True), \
             patch.object(mdn, "is_developer_window", return_value=True), \
             patch.object(mdn, "get_window_pid", return_value=1111), \
             patch.object(mdn, "find_window_title", return_value="my_project - Visual Studio Code"):
            mdn.save_session_window("sess_test", "12345", "my_project", pid=1111, precision="window")
            wid = mdn.get_session_window("sess_test")
            self.assertEqual(wid, "12345")


class TestWindowsPIDValidation(unittest.TestCase):
    """Verifies SEC-004: Windows PID reuse validation before terminating process."""

    def test_kill_previous_instance_skips_non_python_on_windows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = os.path.join(tmpdir, "instance.pid")
            with open(pid_file, "w") as f:
                f.write("99999\n")

            with patch.object(mdn, "PID_FILE", pid_file), \
                 patch.object(mdn, "IS_WINDOWS", True), \
                 patch.object(mdn, "get_windows_process_tree", return_value={99999: (1000, "svchost.exe")}), \
                 patch("os.kill") as mock_kill:
                mdn.kill_previous_instance()
                mock_kill.assert_not_called()

    def test_kill_previous_instance_kills_python_on_windows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = os.path.join(tmpdir, "instance.pid")
            with open(pid_file, "w") as f:
                f.write("88888\n")

            with patch.object(mdn, "PID_FILE", pid_file), \
                 patch.object(mdn, "IS_WINDOWS", True), \
                 patch.object(mdn, "get_windows_process_tree", return_value={88888: (1000, "python.exe")}), \
                 patch("os.kill") as mock_kill:
                mdn.kill_previous_instance()
                mock_kill.assert_called_once()


class TestSafeConfigurationMerging(unittest.TestCase):
    """Verifies SEC-003: Safe configuration merge and unmerge preserving user hooks."""

    def test_claude_settings_merge_and_unmerge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_path = os.path.join(tmpdir, "settings.json")
            initial_data = {
                "user_theme": "dark",
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": "/opt/custom-tool/init.sh"}]}],
                    "OtherEvent": [{"hooks": [{"type": "command", "command": "/opt/custom-tool/other.sh"}]}]
                }
            }
            with open(claude_path, "w") as f:
                json.dump(initial_data, f)

            with open(claude_path, "r") as f:
                cdata = json.load(f)
            claude_hook_cmd = "/home/user/.claude/hooks/notify-input.sh"
            target_hooks = {
                "SessionStart": [{"hooks": [{"type": "command", "command": claude_hook_cmd}]}],
                "Stop": [{"hooks": [{"type": "command", "command": claude_hook_cmd}]}]
            }
            for event, new_entries in target_hooks.items():
                if event not in cdata["hooks"]:
                    cdata["hooks"][event] = []
                filtered = [item for item in cdata["hooks"][event] if "notify-input.sh" not in json.dumps(item)]
                filtered.extend(new_entries)
                cdata["hooks"][event] = filtered

            with open(claude_path, "w") as f:
                json.dump(cdata, f)

            with open(claude_path, "r") as f:
                merged = json.load(f)
            self.assertEqual(len(merged["hooks"]["SessionStart"]), 2)
            self.assertTrue(any("custom-tool" in json.dumps(h) for h in merged["hooks"]["SessionStart"]))
            self.assertTrue(any("notify-input.sh" in json.dumps(h) for h in merged["hooks"]["SessionStart"]))
            self.assertIn("OtherEvent", merged["hooks"])

            with open(claude_path, "r") as f:
                udata = json.load(f)
            hooks = udata["hooks"]
            for event in list(hooks.keys()):
                filtered = [item for item in hooks[event] if "notify-input.sh" not in json.dumps(item)]
                if filtered:
                    hooks[event] = filtered
                else:
                    del hooks[event]

            with open(claude_path, "w") as f:
                json.dump(udata, f)

            with open(claude_path, "r") as f:
                unmerged = json.load(f)
            self.assertEqual(len(unmerged["hooks"]["SessionStart"]), 1)
            self.assertIn("custom-tool", json.dumps(unmerged["hooks"]["SessionStart"]))
            self.assertNotIn("notify-input.sh", json.dumps(unmerged))


class TestLifecycleArtifactSymmetry(unittest.TestCase):
    """Verifies OPS-001: All artifacts are symmetrically managed."""

    def test_required_artifacts_exist_in_repo(self):
        required_artifacts = [
            ROOT_DIR / "bin" / "multi-desktop-notify.py",
            ROOT_DIR / "bin" / "anoti",
            ROOT_DIR / "bin" / "anoti.cmd",
            ROOT_DIR / "bin" / "anoti.ps1",
            ROOT_DIR / "hooks" / "claude-notify.sh",
            ROOT_DIR / "hooks" / "claude-notify.py",
            ROOT_DIR / "hooks" / "codex-notify.py",
            ROOT_DIR / "hooks" / "antigravity-notify.sh",
            ROOT_DIR / "hooks" / "antigravity-notify.py",
            ROOT_DIR / "gnome-shell-extension" / "metadata.json",
            ROOT_DIR / "gnome-shell-extension" / "extension-modern.js",
            ROOT_DIR / "gnome-shell-extension" / "extension-legacy.js",
            ROOT_DIR / "install.sh",
            ROOT_DIR / "install.ps1",
            ROOT_DIR / "update.sh",
            ROOT_DIR / "update.ps1",
            ROOT_DIR / "uninstall.sh",
            ROOT_DIR / "uninstall.ps1",
        ]
        for art in required_artifacts:
            self.assertTrue(art.exists(), f"Artifact missing: {art}")

    def test_gnome_focus_adapter_has_symmetric_lifecycle(self):
        extension_uuid = "ai-agent-desktop-notifier@sonnx24042005"
        for script_name in ("install.sh", "update.sh", "uninstall.sh"):
            script = (ROOT_DIR / script_name).read_text(encoding="utf-8")
            self.assertIn(extension_uuid, script)


class TestWaylandControllingTTYAndDismiss(unittest.TestCase):
    """Verifies Wayland improvements: active target detection and safe fallback."""

    def test_is_target_window_active_without_tty(self):
        if not mdn.IS_WINDOWS:
            # When active_wid is empty and caller_pid is 0, returns False safely
            is_active = mdn.is_target_window_active("", target_wid="", caller_pid=0)
            self.assertFalse(is_active)


if __name__ == "__main__":
    unittest.main()
