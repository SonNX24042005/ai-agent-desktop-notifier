#!/usr/bin/env python3
"""
Unit tests for platform-specific focus adapters (Linux X11/Wayland, Windows Win32),
foreground lock bypass, action controller dispatch, click snapshot preservation, and Alt+Q queue prioritization.
"""

import os
import sys
import time
import json
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock, call

# Dynamically import multi-desktop-notify module
ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "bin" / "multi-desktop-notify.py"
spec = importlib.util.spec_from_file_location("multi_desktop_notify", str(SCRIPT_PATH))
mdn = importlib.util.module_from_spec(spec)
sys.modules["multi_desktop_notify"] = mdn
spec.loader.exec_module(mdn)


class TestPlatformCapabilityDetection(unittest.TestCase):
    """Tests for platform backend and capability detection."""

    def test_should_use_x11_overlay_with_display(self):
        env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}
        self.assertTrue(mdn.should_use_x11_overlay(env))

    def test_should_use_x11_overlay_without_display(self):
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        self.assertFalse(mdn.should_use_x11_overlay(env))


class TestWindowsForceForegroundSequence(unittest.TestCase):
    """Tests for Windows Win32 force foreground, SW_RESTORE and AttachThreadInput logic."""

    @patch.object(mdn, "IS_WINDOWS", True)
    def test_windows_minimized_window_calls_restore(self):
        mock_user32 = MagicMock()
        mock_kernel32 = MagicMock()

        # Window is valid and iconic (minimized)
        mock_user32.IsWindow.return_value = 1
        mock_user32.IsIconic.return_value = 1
        mock_user32.GetForegroundWindow.return_value = 12345
        mock_user32.GetWindowThreadProcessId.return_value = 100
        mock_kernel32.GetCurrentThreadId.return_value = 100

        mock_windll = MagicMock()
        mock_windll.user32 = mock_user32
        mock_windll.kernel32 = mock_kernel32

        with patch.object(mdn.ctypes, "windll", mock_windll, create=True):
            res = mdn.focus_target_window("12345", verify_timeout=0.05)
            # Verify ShowWindow was called with SW_RESTORE (9)
            mock_user32.ShowWindow.assert_called_with(12345, 9)
            self.assertTrue(res)

    @patch.object(mdn, "IS_WINDOWS", True)
    def test_windows_verification_failure_returns_false(self):
        mock_user32 = MagicMock()
        mock_kernel32 = MagicMock()

        mock_user32.IsWindow.return_value = 1
        mock_user32.IsIconic.return_value = 0
        # Foreground window never matches target 12345
        mock_user32.GetForegroundWindow.return_value = 99999
        mock_user32.GetWindowThreadProcessId.return_value = 100
        mock_kernel32.GetCurrentThreadId.return_value = 100

        mock_windll = MagicMock()
        mock_windll.user32 = mock_user32
        mock_windll.kernel32 = mock_kernel32

        with patch.object(mdn.ctypes, "windll", mock_windll, create=True):
            res = mdn.focus_target_window("12345", verify_timeout=0.05)
            self.assertFalse(res)


class TestActionControllerAndClickPreservation(unittest.TestCase):
    """Tests that notification click uses immutable identity snapshot and Alt+Q selects oldest item."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_queue = mdn.QUEUE_CACHE_FILE
        self.orig_lock = mdn.QUEUE_LOCK_FILE
        self.orig_sess = mdn.SESSION_CACHE_FILE
        self.orig_sess_lock = mdn.SESSION_LOCK_FILE

        mdn.QUEUE_CACHE_FILE = os.path.join(self.tmp_dir.name, "queue.json")
        mdn.QUEUE_LOCK_FILE = os.path.join(self.tmp_dir.name, "queue.lock")
        mdn.SESSION_CACHE_FILE = os.path.join(self.tmp_dir.name, "sessions.json")
        mdn.SESSION_LOCK_FILE = os.path.join(self.tmp_dir.name, "sessions.lock")

    def tearDown(self):
        mdn.QUEUE_CACHE_FILE = self.orig_queue
        mdn.QUEUE_LOCK_FILE = self.orig_lock
        mdn.SESSION_CACHE_FILE = self.orig_sess
        mdn.SESSION_LOCK_FILE = self.orig_sess_lock
        self.tmp_dir.cleanup()

    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "focus_target_window", return_value=True)
    def test_alt_q_selects_oldest_queued_notification(self, mock_focus, mock_valid):
        # Insert 3 notifications with distinct timestamps
        t0 = time.time() - 100
        t1 = time.time() - 50
        t2 = time.time()

        mdn.save_to_queue("key_0", {"target_window_id": "1000", "created_at": t0, "session_id": "s0"})
        mdn.save_to_queue("key_1", {"target_window_id": "2000", "created_at": t1, "session_id": "s1"})
        mdn.save_to_queue("key_2", {"target_window_id": "3000", "created_at": t2, "session_id": "s2"})

        with patch.object(mdn, "kill_previous_instance"), patch.object(mdn, "pop_next_notification_async"):
            ret = mdn.focus_active_or_queued_notification()
            self.assertEqual(ret, 0)
            # Oldest (key_0) should have been focused and removed first!
            mock_focus.assert_called_with("1000")
            q = mdn.load_notification_queue()
            self.assertNotIn("key_0", q)
            self.assertIn("key_1", q)
            self.assertIn("key_2", q)

    @patch.object(mdn, "is_valid_toplevel_window", return_value=True)
    @patch.object(mdn, "focus_target_window", return_value=True)
    def test_click_uses_target_snapshot_not_polluted_session_cache(self, mock_focus, mock_valid):
        # Notification A was created with target_window_id "1001"
        mdn.save_to_queue("key_A", {"target_window_id": "1001", "created_at": time.time(), "session_id": "sess_A"})

        # Session cache is then overwritten by session B with "2002"
        with patch.object(mdn, "is_developer_window", return_value=True):
            mdn.save_session_window("sess_B", "2002", project_hint="proj_B")

        # When action controller processes key_A, it must focus "1001"
        with patch.object(mdn, "kill_previous_instance"), patch.object(mdn, "pop_next_notification_async"):
            ret = mdn.focus_active_or_queued_notification()
            self.assertEqual(ret, 0)
            mock_focus.assert_called_with("1001")


if __name__ == "__main__":
    unittest.main()
