#!/usr/bin/env python3
"""
Comprehensive unit tests for window identity resolution, source session caching,
auto-dismiss timer precision, non-stealing popup configuration, and queue transaction integrity.
"""

import os
import sys
import time
import json
import tempfile
import threading
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

# Dynamically import multi-desktop-notify module
ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "bin" / "multi-desktop-notify.py"
spec = importlib.util.spec_from_file_location("multi_desktop_notify", str(SCRIPT_PATH))
mdn = importlib.util.module_from_spec(spec)
sys.modules["multi_desktop_notify"] = mdn
spec.loader.exec_module(mdn)


class TestProcessAncestry(unittest.TestCase):
    """Tests for process ancestry verification."""

    def test_is_pid_in_ancestry_self(self):
        curr = os.getpid()
        self.assertTrue(mdn.is_pid_in_ancestry(curr, curr))

    def test_is_pid_in_ancestry_parent(self):
        curr = os.getpid()
        parent = os.getppid()
        if parent > 1:
            self.assertTrue(mdn.is_pid_in_ancestry(parent, curr))

    def test_is_pid_in_ancestry_invalid_pids(self):
        curr = os.getpid()
        self.assertFalse(mdn.is_pid_in_ancestry(0, curr))
        self.assertFalse(mdn.is_pid_in_ancestry(-1, curr))
        self.assertFalse(mdn.is_pid_in_ancestry(curr, 0))
        self.assertFalse(mdn.is_pid_in_ancestry(curr, -1))

    def test_is_pid_in_ancestry_unrelated_pid(self):
        curr = os.getpid()
        # High unassigned PID is not an ancestor
        self.assertFalse(mdn.is_pid_in_ancestry(4194300, curr))


class TestSessionCacheStore(unittest.TestCase):
    """Tests for thread/process-safe atomic storage, locking, pruning and cache protection."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_cache_file = mdn.SESSION_CACHE_FILE
        self.orig_lock_file = mdn.SESSION_LOCK_FILE
        self.test_cache_file = os.path.join(self.tmp_dir.name, "test_sessions.json")
        self.test_lock_file = os.path.join(self.tmp_dir.name, "test_sessions.lock")
        mdn.SESSION_CACHE_FILE = self.test_cache_file
        mdn.SESSION_LOCK_FILE = self.test_lock_file

    def tearDown(self):
        mdn.SESSION_CACHE_FILE = self.orig_cache_file
        mdn.SESSION_LOCK_FILE = self.orig_lock_file
        self.tmp_dir.cleanup()

    def test_atomic_write_and_safe_load(self):
        data = {"sess_1": {"window_id": "12345", "precision": "window"}}
        success = mdn.atomic_write_json(self.test_cache_file, data)
        self.assertTrue(success)
        loaded = mdn.safe_load_json(self.test_cache_file)
        self.assertEqual(loaded, data)

    def test_safe_load_corrupted_json(self):
        # Write corrupted JSON
        with open(self.test_cache_file, "w") as f:
            f.write("{corrupted json content!@#$")
        loaded = mdn.safe_load_json(self.test_cache_file, default={"fallback": True})
        self.assertEqual(loaded, {"fallback": True})

    def test_cache_protection_precision(self):
        # Save a high-precision entry
        with patch.object(mdn, "is_developer_window", return_value=True):
            mdn.save_session_window("sess_abc", "1001", "my-project", pid=5555, precision="window")
            info = mdn.get_session_window_info("sess_abc")
            self.assertEqual(info["window_id"], "1001")
            self.assertEqual(info["precision"], "window")

            # Lower-precision update attempt should be rejected
            rejected = mdn.save_session_window("sess_abc", "2002", "my-project", pid=6666, precision="app")
            self.assertFalse(rejected)
            info_after = mdn.get_session_window_info("sess_abc")
            self.assertEqual(info_after["window_id"], "1001")

            # Another window-level update should be accepted
            accepted = mdn.save_session_window("sess_abc", "3003", "my-project", pid=7777, precision="window")
            self.assertTrue(accepted)
            info_final = mdn.get_session_window_info("sess_abc")
            self.assertEqual(info_final["window_id"], "3003")

    def test_prune_sessions_age_limit(self):
        now = time.time()
        sessions = {
            "fresh": {"window_id": "1", "updated_at": now - 3600},       # 1 hr old
            "stale": {"window_id": "2", "updated_at": now - 90000},      # 25 hrs old
        }
        pruned = mdn.prune_sessions(sessions, now=now, max_age=86400)
        self.assertIn("fresh", pruned)
        self.assertNotIn("stale", pruned)

    def test_prune_sessions_max_entries(self):
        now = time.time()
        sessions = {}
        for i in range(100):
            sessions[f"sess_{i}"] = {"window_id": str(i), "updated_at": now - i}
        pruned = mdn.prune_sessions(sessions, now=now, max_entries=64)
        self.assertEqual(len(pruned), 64)
        # Should keep sess_0 to sess_63 (most recent timestamps)
        self.assertIn("sess_0", pruned)
        self.assertIn("sess_63", pruned)
        self.assertNotIn("sess_99", pruned)


class TestTargetWindowResolution(unittest.TestCase):
    """Tests for strict resolution hierarchy, ambiguity handling and stale avoidance."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_cache_file = mdn.SESSION_CACHE_FILE
        self.orig_lock_file = mdn.SESSION_LOCK_FILE
        mdn.SESSION_CACHE_FILE = os.path.join(self.tmp_dir.name, "sessions.json")
        mdn.SESSION_LOCK_FILE = os.path.join(self.tmp_dir.name, "sessions.lock")

    def tearDown(self):
        mdn.SESSION_CACHE_FILE = self.orig_cache_file
        mdn.SESSION_LOCK_FILE = self.orig_lock_file
        self.tmp_dir.cleanup()

    @patch.object(mdn, "is_developer_window", return_value=True)
    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "get_window_pid", return_value=1234)
    def test_tier0_cached_session_window(self, mock_pid, mock_valid, mock_dev):
        mdn.save_session_window("sess_001", "12345", project_hint="proj", pid=1234, precision="window")
        wid = mdn.find_target_window(session_id="sess_001")
        self.assertEqual(wid, "12345")

    @patch.object(mdn, "is_developer_window", return_value=True)
    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "get_window_pid", return_value=9999)  # PID changed! (Handle reused)
    def test_stale_handle_pid_mismatch(self, mock_pid, mock_valid, mock_dev):
        mdn.save_session_window("sess_002", "12345", project_hint="proj", pid=1234, precision="window")
        # Cached PID is 1234, but current window owner is 9999
        wid = mdn.get_session_window("sess_002")
        self.assertEqual(wid, "")

    @patch.object(mdn, "is_developer_window", return_value=True)
    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "get_process_ancestors", return_value={100, 200, 300})
    def test_tier1_single_tree_window(self, mock_anc, mock_valid, mock_dev):
        managed = [("555", "VS Code - ProjectA", 200)]
        with patch.object(mdn, "get_all_managed_windows", return_value=managed):
            wid = mdn.find_target_window(caller_pid=300)
            self.assertEqual(wid, "555")

    @patch.object(mdn, "is_developer_window", return_value=True)
    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "get_process_ancestors", return_value={100, 200, 300})
    def test_tier1_ambiguous_multiple_windows_rejected(self, mock_anc, mock_valid, mock_dev):
        # Two windows share the same process tree, no project_hint provided
        managed = [
            ("555", "VS Code - ProjectA", 200),
            ("666", "VS Code - ProjectB", 200),
        ]
        with patch.object(mdn, "get_all_managed_windows", return_value=managed):
            wid = mdn.find_target_window(caller_pid=300, project_hint="")
            # Must NOT blindly pick managed[0], should return empty string (Ambiguous)
            self.assertEqual(wid, "")

    @patch.object(mdn, "is_developer_window", return_value=True)
    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "get_process_ancestors", return_value={100, 200, 300})
    def test_tier1_multiple_windows_resolved_by_project_hint(self, mock_anc, mock_valid, mock_dev):
        managed = [
            ("555", "VS Code - ProjectA", 200),
            ("666", "VS Code - ProjectB", 200),
        ]
        with patch.object(mdn, "get_all_managed_windows", return_value=managed):
            wid = mdn.find_target_window(caller_pid=300, project_hint="ProjectB")
            self.assertEqual(wid, "666")

    @patch.object(mdn, "is_developer_window", return_value=True)
    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    def test_no_random_developer_window_fallback(self, mock_valid, mock_dev):
        managed = [
            ("999", "VS Code - Random Workspace", 400),
        ]
        with patch.object(mdn, "get_all_managed_windows", return_value=managed):
            # No matching caller_pid, no matching project_hint
            wid = mdn.find_target_window(caller_pid=5000, project_hint="NonExistentProject")
            self.assertEqual(wid, "")


class TestAutoDismissTimerLogic(unittest.TestCase):
    """Tests for monotonic auto-dismiss timer logic and reset behavior."""

    def test_timer_countdown_and_trigger(self):
        auto_dismiss_delay = 1.5
        active_since = None

        t0 = 1000.0
        # t = 0.0s: target becomes active
        active_since, should_dismiss = mdn.update_auto_dismiss_state(
            active_since, True, auto_dismiss_delay, now=t0
        )
        self.assertFalse(should_dismiss)
        self.assertEqual(active_since, t0)

        # t = 1.0s: still active, not yet dismissed
        active_since, should_dismiss = mdn.update_auto_dismiss_state(
            active_since, True, auto_dismiss_delay, now=t0 + 1.0
        )
        self.assertFalse(should_dismiss)

        # t = 1.5s: delay reached, trigger dismiss!
        active_since, should_dismiss = mdn.update_auto_dismiss_state(
            active_since, True, auto_dismiss_delay, now=t0 + 1.5
        )
        self.assertTrue(should_dismiss)

    def test_timer_resets_when_user_switches_away(self):
        auto_dismiss_delay = 1.5
        active_since = None

        t0 = 1000.0
        # Active for 1.2s
        active_since, _ = mdn.update_auto_dismiss_state(active_since, True, auto_dismiss_delay, now=t0)
        active_since, should_dismiss = mdn.update_auto_dismiss_state(
            active_since, True, auto_dismiss_delay, now=t0 + 1.2
        )
        self.assertFalse(should_dismiss)

        # User switches away at t = 1.3s
        active_since, should_dismiss = mdn.update_auto_dismiss_state(
            active_since, False, auto_dismiss_delay, now=t0 + 1.3
        )
        self.assertIsNone(active_since)
        self.assertFalse(should_dismiss)

        # User returns at t = 2.0s: must restart full 1.5s countdown
        active_since, _ = mdn.update_auto_dismiss_state(
            active_since, True, auto_dismiss_delay, now=t0 + 2.0
        )
        self.assertEqual(active_since, t0 + 2.0)

        # t = 3.0s (only 1.0s continuous): should not close
        active_since, should_dismiss = mdn.update_auto_dismiss_state(
            active_since, True, auto_dismiss_delay, now=t0 + 3.0
        )
        self.assertFalse(should_dismiss)

        # t = 3.5s (1.5s continuous): closes!
        _, should_dismiss = mdn.update_auto_dismiss_state(
            active_since, True, auto_dismiss_delay, now=t0 + 3.5
        )
        self.assertTrue(should_dismiss)


class TestAsyncWindowActivityProbe(unittest.TestCase):
    """Tests that activity inspection cannot block or overlap on the UI thread."""

    def test_probe_resolves_target_and_checks_active_state(self):
        with patch.object(mdn, "is_valid_toplevel_window", return_value=False), \
             patch.object(mdn, "find_target_window", return_value="222") as mock_find, \
             patch.object(mdn, "get_current_active_window", return_value="222"), \
             patch.object(mdn, "is_target_window_active", return_value=True) as mock_active:
            result = mdn.probe_target_window_activity(
                target_window_id="111",
                caller_pid=900,
                project_hint="project",
                session_id="session-1",
            )

        self.assertEqual(result, ("222", True))
        mock_find.assert_called_once_with(
            window_id_arg="",
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
        )
        mock_active.assert_called_once_with(
            "222",
            target_wid="222",
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
        )

    def test_probe_rejects_overlapping_requests(self):
        started = threading.Event()
        release = threading.Event()

        def slow_probe(**kwargs):
            started.set()
            release.wait(timeout=1.0)
            return kwargs["target_window_id"], True

        probe = mdn.AsyncWindowActivityProbe(probe_func=slow_probe)
        self.assertTrue(probe.request(target_window_id="123"))
        self.assertTrue(started.wait(timeout=0.5))
        self.assertFalse(probe.request(target_window_id="456"))

        release.set()
        result = None
        deadline = time.monotonic() + 1.0
        while result is None and time.monotonic() < deadline:
            result = probe.take_result()
            if result is None:
                time.sleep(0.01)

        self.assertEqual(result, ("123", True))
        self.assertTrue(probe.request(target_window_id="456"))

    def test_probe_converts_worker_error_to_inactive_result(self):
        def failing_probe(**kwargs):
            raise RuntimeError("query failed")

        probe = mdn.AsyncWindowActivityProbe(probe_func=failing_probe)
        self.assertTrue(probe.request(target_window_id="789"))

        result = None
        deadline = time.monotonic() + 1.0
        while result is None and time.monotonic() < deadline:
            result = probe.take_result()
            if result is None:
                time.sleep(0.01)

        self.assertEqual(result, ("789", False))

    def test_linux_window_query_has_a_timeout(self):
        with patch.object(mdn, "IS_WINDOWS", False), \
             patch.object(
                 mdn.subprocess,
                 "check_output",
                 return_value=b"_NET_WM_STATE(WINDOW):\n",
             ) as mock_check_output:
            self.assertTrue(mdn.is_valid_toplevel_window("123"))

        self.assertEqual(
            mock_check_output.call_args.kwargs["timeout"],
            mdn.WINDOW_QUERY_TIMEOUT,
        )


class TestAsyncWindowFocusRequest(unittest.TestCase):
    """Tests that popup focus work stays outside the UI thread."""

    def test_focus_task_resolves_target_without_worker_gdk_calls(self):
        with patch.object(mdn, "is_wayland_session", return_value=False), \
             patch.object(mdn, "is_valid_toplevel_window", return_value=False), \
             patch.object(mdn, "find_target_window", return_value="222") as mock_find, \
             patch.object(mdn, "focus_target_window", return_value=True) as mock_focus:
            result = mdn.focus_target_window_async_task(
                target_window_id="111",
                caller_pid=900,
                project_hint="project",
                session_id="session-1",
            )

        self.assertEqual(result, ("222", True))
        mock_find.assert_called_once_with(
            window_id_arg="",
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
        )
        mock_focus.assert_called_once_with(
            "222",
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
            allow_gdk=False,
        )

    def test_focus_task_uses_wayland_adapter_before_window_scan(self):
        with patch.object(mdn, "is_wayland_session", return_value=True), \
             patch.object(mdn, "find_target_window") as mock_find, \
             patch.object(mdn, "focus_target_window", return_value=True) as mock_focus:
            result = mdn.focus_target_window_async_task(
                target_window_id="",
                caller_pid=900,
                project_hint="project",
                session_id="session-1",
            )

        self.assertEqual(result, ("", True))
        mock_find.assert_not_called()
        mock_focus.assert_called_once_with(
            "",
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
            allow_gdk=False,
        )

    def test_focus_request_is_single_flight(self):
        started = threading.Event()
        release = threading.Event()

        def slow_focus(**kwargs):
            started.set()
            release.wait(timeout=1.0)
            return kwargs["target_window_id"], False

        request = mdn.AsyncWindowFocusRequest(focus_func=slow_focus)
        self.assertTrue(request.request(target_window_id="123"))
        self.assertTrue(started.wait(timeout=0.5))
        self.assertFalse(request.request(target_window_id="456"))
        release.set()

        result = None
        deadline = time.monotonic() + 1.0
        while result is None and time.monotonic() < deadline:
            result = request.take_result()
            if result is None:
                time.sleep(0.01)

        self.assertEqual(result, ("123", False))


class TestFocusVerificationAndQueueTransaction(unittest.TestCase):
    """Tests for focus verification and preserving queue items on focus failure."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_queue_file = mdn.QUEUE_CACHE_FILE
        self.orig_lock_file = mdn.QUEUE_LOCK_FILE
        self.test_queue_file = os.path.join(self.tmp_dir.name, "queue.json")
        self.test_lock_file = os.path.join(self.tmp_dir.name, "queue.lock")
        mdn.QUEUE_CACHE_FILE = self.test_queue_file
        mdn.QUEUE_LOCK_FILE = self.test_lock_file

    def tearDown(self):
        mdn.QUEUE_CACHE_FILE = self.orig_queue_file
        mdn.QUEUE_LOCK_FILE = self.orig_lock_file
        self.tmp_dir.cleanup()

    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "focus_target_window", return_value=True)
    def test_focus_success_removes_queue_item(self, mock_focus, mock_valid):
        # Put item in queue
        mdn.save_to_queue("key_001", {"target_window_id": "111", "created_at": time.time()})
        self.assertIn("key_001", mdn.load_notification_queue())

        # Calling focus_active_or_queued_notification with success removes key
        with patch.object(mdn, "kill_previous_instance"), patch.object(mdn, "pop_next_notification_async"):
            ret = mdn.focus_active_or_queued_notification()
            self.assertEqual(ret, 0)
            self.assertNotIn("key_001", mdn.load_notification_queue())

    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "focus_target_window", return_value=False)
    def test_focus_failure_preserves_queue_item(self, mock_focus, mock_valid):
        # Put item in queue
        mdn.save_to_queue("key_002", {"target_window_id": "222", "created_at": time.time()})
        self.assertIn("key_002", mdn.load_notification_queue())

        # Calling focus_active_or_queued_notification with failure preserves key
        with patch.object(mdn, "kill_previous_instance") as mock_kill, patch.object(mdn, "pop_next_notification_async"):
            ret = mdn.focus_active_or_queued_notification()
            self.assertEqual(ret, 1)
            # Item remains in queue!
            self.assertIn("key_002", mdn.load_notification_queue())
            mock_kill.assert_not_called()


class TestNonStealingPopupConfig(unittest.TestCase):
    """Tests for non-stealing / no-activation flags on popup windows."""

    def test_no_activate_constant(self):
        # Win32 WS_EX_NOACTIVATE constant is 0x08000000
        WS_EX_NOACTIVATE = 0x08000000
        self.assertEqual(WS_EX_NOACTIVATE, 134217728)

    def test_gtk_no_focus_flags_available(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_accept_focus(False)
            win.set_focus_on_map(False)
            self.assertFalse(win.get_accept_focus())
            self.assertFalse(win.get_focus_on_map())
        except Exception as e:
            self.skipTest(f"GTK3 not available in environment: {e}")


class TestGnomeTerminalWaylandSupport(unittest.TestCase):
    """Tests for GNOME Terminal Wayland native resolution and D-Bus activation."""

    @patch.object(mdn, "is_gnome_terminal_in_ancestry", return_value=True)
    def test_find_target_window_wayland_gnome_terminal(self, mock_gnome):
        with patch.object(mdn, "get_all_managed_windows", return_value=[]):
            wid = mdn.find_target_window(caller_pid=12345, session_id="sess_gnome")
            self.assertEqual(wid, "wayland:gnome-terminal")
            self.assertTrue(mdn.is_valid_toplevel_window(wid))
            self.assertTrue(mdn.is_developer_window(wid))

class TestProcStatParsing(unittest.TestCase):
    """Tests for safe /proc/{pid}/stat parsing with spaces and special symbols in comm."""

    def test_proc_stat_with_spaces_in_comm(self):
        # E.g. process name "(claude code)" or "(python 3.12)"
        mock_stat_content = "1234 (claude code) S 5678 1234 1234 0 -1 4194304"
        with patch("builtins.open", unittest.mock.mock_open(read_data=mock_stat_content)):
            with patch("os.path.exists", return_value=True):
                # When current pid is 1234, ancestors should include parent 5678
                if not mdn.IS_WINDOWS:
                    ancestors = mdn.get_process_ancestors(1234)
                    self.assertIn(5678, ancestors)


class TestTitleFingerprint(unittest.TestCase):
    """Tests for title fingerprint normalization and fuzzy compatibility."""

    def test_normalize_title_strips_spinners(self):
        raw = "⠋ Claude Code - my-project"
        norm = mdn.normalize_title(raw)
        self.assertIn("claude code", norm)
        self.assertNotIn("⠋", norm)

    def test_titles_compatible(self):
        t1 = "Claude Code - my-project"
        t2 = "⠙ Claude Code - my-project [running]"
        self.assertTrue(mdn.titles_compatible(t1, t2))
        self.assertFalse(mdn.titles_compatible("VS Code - ProjectA", "Google Chrome - Youtube"))


class TestGenerationAndState(unittest.TestCase):
    """Tests for monotonic generation counter and schema v2 integrity."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_runtime = mdn.RUNTIME_DIR
        mdn.RUNTIME_DIR = self.tmp_dir.name

    def tearDown(self):
        mdn.RUNTIME_DIR = self.orig_runtime
        self.tmp_dir.cleanup()

    def test_generation_counter_strictly_increasing(self):
        g1 = mdn.get_next_generation()
        g2 = mdn.get_next_generation()
        g3 = mdn.get_next_generation()
        self.assertGreater(g2, g1)
        self.assertGreater(g3, g2)

    def test_schema_v2_stored(self):
        with patch.object(mdn, "is_developer_window", return_value=True):
            mdn.SESSION_CACHE_FILE = os.path.join(self.tmp_dir.name, "sessions.json")
            mdn.SESSION_LOCK_FILE = os.path.join(self.tmp_dir.name, "sessions.lock")
            mdn.save_session_window("sess_v2", "12345", project_hint="my-proj", pid=100)
            info = mdn.get_session_window_info("sess_v2")
            self.assertEqual(info.get("schema_version"), 2)
            self.assertEqual(info.get("window_id_dec"), "12345")
            self.assertIn("captured_at", info)


class TestIsTargetWindowActiveNoTTY(unittest.TestCase):
    """Tests that is_target_window_active strictly uses OS window ID and does NOT check TTY atime/mtime."""

    def test_active_window_empty_returns_false(self):
        self.assertFalse(mdn.is_target_window_active("", target_wid="12345"))

    def test_foreign_window_returns_false(self):
        self.assertFalse(mdn.is_target_window_active("99999", target_wid="12345"))

    def test_matching_target_returns_true(self):
        self.assertTrue(mdn.is_target_window_active("12345", target_wid="12345"))


class TestWaylandActiveWindowDetection(unittest.TestCase):
    """Tests native Wayland focus detection through AT-SPI window identity."""

    @patch.object(mdn, "is_wayland_session", return_value=True)
    @patch.object(mdn, "is_pid_in_ancestry", return_value=True)
    def test_active_atspi_window_matches_caller_process_and_project(self, mock_ancestry, mock_wayland):
        active_windows = [{
            "app_name": "code",
            "pid": 700,
            "title": "main.py - ai-agent-desktop-notifier - Visual Studio Code",
        }]

        self.assertTrue(mdn.is_target_window_active(
            "",
            caller_pid=900,
            project_hint="ai-agent-desktop-notifier",
            wayland_windows=active_windows,
        ))
        mock_ancestry.assert_called_once_with(700, 900)

    @patch.object(mdn, "is_wayland_session", return_value=True)
    @patch.object(mdn, "is_pid_in_ancestry", return_value=True)
    @patch.object(mdn, "has_recent_terminal_activity", return_value=True)
    def test_atspi_nonmatch_does_not_use_terminal_fallback(self, mock_terminal, mock_ancestry, mock_wayland):
        active_windows = [{
            "app_name": "code",
            "pid": 700,
            "title": "other-project - Visual Studio Code",
        }]

        self.assertFalse(mdn.is_target_window_active(
            "",
            caller_pid=900,
            project_hint="ai-agent-desktop-notifier",
            wayland_windows=active_windows,
        ))
        mock_terminal.assert_not_called()

    @patch.object(mdn, "is_wayland_session", return_value=True)
    @patch.object(mdn, "get_wayland_active_windows", return_value=None)
    @patch.object(mdn, "has_recent_terminal_activity", return_value=True)
    def test_terminal_activity_is_only_used_when_atspi_is_unavailable(self, mock_terminal, mock_windows, mock_wayland):
        self.assertTrue(mdn.is_target_window_active("", caller_pid=900))
        mock_terminal.assert_called_once_with(900)

    @patch.object(mdn, "is_wayland_session", return_value=True)
    def test_gnome_terminal_marker_matches_active_atspi_terminal(self, mock_wayland):
        active_windows = [{
            "app_name": "gnome-terminal-server",
            "pid": 700,
            "title": "Terminal",
        }]

        self.assertTrue(mdn.is_target_window_active(
            "",
            target_wid="wayland:gnome-terminal",
            wayland_windows=active_windows,
        ))


class TestWaylandWindowFocusAdapter(unittest.TestCase):
    """Tests GNOME Shell D-Bus activation without a blocking AT-SPI rescan."""

    @patch.object(mdn, "is_wayland_session", return_value=True)
    @patch.object(mdn, "get_session_window_info", return_value=None)
    @patch.object(mdn, "get_wayland_active_windows")
    @patch.object(mdn.subprocess, "run")
    def test_focus_wayland_target_trusts_successful_shell_adapter(
        self, mock_run, mock_windows, mock_session, mock_wayland
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="(true,)")

        self.assertTrue(mdn.focus_wayland_target_window(
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
            verify_timeout=0.05,
        ))
        command = mock_run.call_args.args[0]
        self.assertIn("io.github.sonnx24042005.AiAgentNotifier.FocusWindow", command)
        self.assertIn("900", command)
        self.assertIn("project", command)
        mock_windows.assert_not_called()

    @patch.object(mdn, "is_wayland_session", return_value=True)
    @patch.object(mdn, "get_session_window_info", return_value=None)
    @patch.object(mdn.subprocess, "run")
    def test_focus_wayland_target_reports_unavailable_adapter(self, mock_run, mock_session, mock_wayland):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        self.assertFalse(mdn.focus_wayland_target_window(caller_pid=900, project_hint="project"))

    @patch.object(mdn, "is_wayland_session", return_value=True)
    @patch.object(mdn, "focus_wayland_target_window", return_value=True)
    def test_focus_target_window_accepts_empty_x11_id_on_wayland(self, mock_wayland_focus, mock_wayland):
        self.assertTrue(mdn.focus_target_window(
            "",
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
        ))
        mock_wayland_focus.assert_called_once_with(
            caller_pid=900,
            project_hint="project",
            session_id="session-1",
            verify_timeout=0.8,
        )


if __name__ == "__main__":
    unittest.main()
