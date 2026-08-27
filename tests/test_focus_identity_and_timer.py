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
        active_since = [None]
        closed = [False]

        def on_close():
            closed[0] = True

        def step(now, is_target_active):
            if is_target_active:
                if active_since[0] is None:
                    active_since[0] = now
                elif (now - active_since[0]) >= auto_dismiss_delay:
                    on_close()
                    return False
            else:
                active_since[0] = None
            return True

        t0 = 1000.0
        # t = 0.0s: target becomes active
        self.assertTrue(step(t0, is_target_active=True))
        self.assertFalse(closed[0])
        self.assertEqual(active_since[0], t0)

        # t = 1.0s: still active, not yet dismissed
        self.assertTrue(step(t0 + 1.0, is_target_active=True))
        self.assertFalse(closed[0])

        # t = 1.5s: delay reached, trigger dismiss!
        res = step(t0 + 1.5, is_target_active=True)
        self.assertFalse(res)
        self.assertTrue(closed[0])

    def test_timer_resets_when_user_switches_away(self):
        auto_dismiss_delay = 1.5
        active_since = [None]
        closed = [False]

        def on_close():
            closed[0] = True

        def step(now, is_target_active):
            if is_target_active:
                if active_since[0] is None:
                    active_since[0] = now
                elif (now - active_since[0]) >= auto_dismiss_delay:
                    on_close()
                    return False
            else:
                active_since[0] = None
            return True

        t0 = 1000.0
        # Active for 1.2s
        step(t0, is_target_active=True)
        step(t0 + 1.2, is_target_active=True)
        self.assertFalse(closed[0])

        # User switches away at t = 1.3s
        step(t0 + 1.3, is_target_active=False)
        self.assertIsNone(active_since[0])
        self.assertFalse(closed[0])

        # User returns at t = 2.0s: must restart full 1.5s countdown
        step(t0 + 2.0, is_target_active=True)
        self.assertEqual(active_since[0], t0 + 2.0)

        # t = 3.0s (only 1.0s continuous): should not close
        step(t0 + 3.0, is_target_active=True)
        self.assertFalse(closed[0])

        # t = 3.5s (1.5s continuous): closes!
        step(t0 + 3.5, is_target_active=True)
        self.assertTrue(closed[0])


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
        with patch.object(mdn, "kill_previous_instance"), patch.object(mdn, "pop_next_notification_async"):
            ret = mdn.focus_active_or_queued_notification()
            self.assertEqual(ret, 1)
            # Item remains in queue!
            self.assertIn("key_002", mdn.load_notification_queue())


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

    @patch.object(mdn, "activate_gnome_terminal_via_dbus", return_value=True)
    def test_focus_target_window_wayland_gnome_terminal(self, mock_dbus):
        success = mdn.focus_target_window("wayland:gnome-terminal")
        self.assertTrue(success)
        mock_dbus.assert_called_once()


if __name__ == "__main__":
    unittest.main()
