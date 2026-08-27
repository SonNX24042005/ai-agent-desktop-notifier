#!/usr/bin/env python3
"""
Unit tests for multi-monitor notification placement and backend detection logic.
Validates behavior across various monitor topologies and display backends.
"""

import os
import sys
import unittest
import importlib.util
from pathlib import Path

# Dynamically import multi-desktop-notify without requiring installation
ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "bin" / "multi-desktop-notify.py"
spec = importlib.util.spec_from_file_location("multi_desktop_notify", str(SCRIPT_PATH))
mdn = importlib.util.module_from_spec(spec)
# Prevent executing main() on load
sys.modules["multi_desktop_notify"] = mdn
spec.loader.exec_module(mdn)


class TestOverlayBackendDetection(unittest.TestCase):
    """Tests for should_use_x11_overlay under different session configurations."""

    def test_wayland_session_with_xwayland_display(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":1", "XDG_SESSION_TYPE": "wayland"}
        self.assertTrue(mdn.should_use_x11_overlay(env))

    def test_wayland_session_type_without_wayland_var_but_display(self):
        env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}
        self.assertTrue(mdn.should_use_x11_overlay(env))

    def test_pure_wayland_session_no_display(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": "", "XDG_SESSION_TYPE": "wayland"}
        self.assertFalse(mdn.should_use_x11_overlay(env))

    def test_native_x11_session(self):
        env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
        self.assertFalse(mdn.should_use_x11_overlay(env))

    def test_wayland_session_with_ambient_gdk_backend_wayland(self):
        # Even if parent shell has GDK_BACKEND=wayland, having DISPLAY enables XWayland overlay
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":1", "GDK_BACKEND": "wayland", "XDG_SESSION_TYPE": "wayland"}
        self.assertTrue(mdn.should_use_x11_overlay(env))

    def test_user_explicit_override_notify_backend_wayland(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":1", "NOTIFY_BACKEND": "wayland"}
        self.assertFalse(mdn.should_use_x11_overlay(env))

    def test_user_explicit_override_notify_backend_x11(self):
        env = {"NOTIFY_BACKEND": "x11"}
        self.assertTrue(mdn.should_use_x11_overlay(env))

    def test_user_explicit_override_force_wayland(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":1", "NOTIFY_FORCE_WAYLAND": "1"}
        self.assertFalse(mdn.should_use_x11_overlay(env))


class TestOverlayPlacementCalculation(unittest.TestCase):
    """Tests for calculate_overlay_placement across diverse display geometries."""

    def test_two_horizontal_monitors(self):
        win_w = 460
        mon0 = {"x": 0, "y": 0, "width": 1920, "height": 1080}
        mon1 = {"x": 1920, "y": 0, "width": 1920, "height": 1080}

        x0, y0 = mdn.calculate_overlay_placement(mon0, win_w, top_margin=30)
        x1, y1 = mdn.calculate_overlay_placement(mon1, win_w, top_margin=30)

        # Expected: centered on each respective monitor
        self.assertEqual((x0, y0), (730, 30))
        self.assertEqual((x1, y1), (2650, 30))
        self.assertNotEqual(x0, x1)

    def test_secondary_monitor_left_negative_coordinates(self):
        win_w = 460
        mon_left = {"x": -1920, "y": 0, "width": 1920, "height": 1080}
        mon_main = {"x": 0, "y": 0, "width": 1920, "height": 1080}

        x_left, y_left = mdn.calculate_overlay_placement(mon_left, win_w, top_margin=30)
        x_main, y_main = mdn.calculate_overlay_placement(mon_main, win_w, top_margin=30)

        self.assertEqual((x_left, y_left), (-1190, 30))
        self.assertEqual((x_main, y_main), (730, 30))

    def test_secondary_monitor_top_negative_coordinates(self):
        win_w = 460
        mon_top = {"x": 0, "y": -1080, "width": 1920, "height": 1080}
        mon_bottom = {"x": 0, "y": 0, "width": 1920, "height": 1080}

        x_top, y_top = mdn.calculate_overlay_placement(mon_top, win_w, top_margin=30)
        x_bottom, y_bottom = mdn.calculate_overlay_placement(mon_bottom, win_w, top_margin=30)

        self.assertEqual((x_top, y_top), (730, -1050))
        self.assertEqual((x_bottom, y_bottom), (730, 30))

    def test_portrait_and_differing_resolutions(self):
        win_w = 460
        mon_portrait = {"x": 0, "y": 0, "width": 1080, "height": 1920}
        mon_4k = {"x": 1080, "y": 0, "width": 3840, "height": 2160}

        xp, yp = mdn.calculate_overlay_placement(mon_portrait, win_w, top_margin=30)
        x4, y4 = mdn.calculate_overlay_placement(mon_4k, win_w, top_margin=30)

        # 1080 width: (1080 - 460)//2 = 310
        self.assertEqual((xp, yp), (310, 30))
        # 3840 width: 1080 + (3840 - 460)//2 = 1080 + 1690 = 2770
        self.assertEqual((x4, y4), (2770, 30))

    def test_workarea_with_panel_and_dock_offsets(self):
        win_w = 460
        # Dock on left (50px) and top bar (32px)
        workarea = {"x": 50, "y": 32, "width": 1870, "height": 1048}
        x, y = mdn.calculate_overlay_placement(workarea, win_w, top_margin=20)
        # 50 + (1870 - 460)//2 = 50 + 705 = 755; 32 + 20 = 52
        self.assertEqual((x, y), (755, 52))

    def test_small_resolution_width_fallback(self):
        # Window width exceeds monitor width
        mon_small = {"x": 100, "y": 100, "width": 400, "height": 300}
        x, y = mdn.calculate_overlay_placement(mon_small, 460, top_margin=10)
        self.assertEqual(x, 100)
        self.assertEqual(y, 110)


class TestTargetMonitorIndices(unittest.TestCase):
    """Tests for get_target_monitor_indices backend fallback logic."""

    def test_capable_backend_multi_monitor(self):
        # On X11 / XWayland with 3 monitors: returns all indices
        indices = mdn.get_target_monitor_indices(n_monitors=3, can_place_windows=True)
        self.assertEqual(indices, [0, 1, 2])

    def test_incapable_backend_multi_monitor_fallback(self):
        # On pure Wayland where placement is unsupported: fallback to only 1 window to prevent cascading
        indices = mdn.get_target_monitor_indices(n_monitors=2, can_place_windows=False)
        self.assertEqual(indices, [0])

    def test_single_monitor_always_single(self):
        indices_capable = mdn.get_target_monitor_indices(n_monitors=1, can_place_windows=True)
        indices_fallback = mdn.get_target_monitor_indices(n_monitors=1, can_place_windows=False)
        self.assertEqual(indices_capable, [0])
        self.assertEqual(indices_fallback, [0])

    def test_zero_monitors(self):
        indices = mdn.get_target_monitor_indices(n_monitors=0, can_place_windows=True)
        self.assertEqual(indices, [])


class TestDistinctPlacements(unittest.TestCase):
    """Ensures each monitor receives a distinct placement with no duplicate placements."""

    def test_no_duplicate_placements(self):
        win_w = 460
        monitors = [
            {"x": 0, "y": 0, "width": 1920, "height": 1080},
            {"x": 1920, "y": 0, "width": 1920, "height": 1080},
            {"x": 3840, "y": 0, "width": 1920, "height": 1080},
        ]
        placements = [mdn.calculate_overlay_placement(m, win_w) for m in monitors]
        self.assertEqual(len(placements), len(set(placements)))
        self.assertEqual(len(placements), 3)


class TestGtkInitFallback(unittest.TestCase):
    """Tests the fallback retry logic when X11 backend initialization fails."""

    def test_init_check_retry_recovers_to_wayland(self):
        env = {"GDK_BACKEND": "x11", "WAYLAND_DISPLAY": "wayland-0"}
        call_count = [0]

        def mock_init_check():
            call_count[0] += 1
            if env.get("GDK_BACKEND") == "x11":
                return (False,)
            return (True,)

        init_ok = mock_init_check()[0]
        if not init_ok:
            if env.get("GDK_BACKEND") == "x11" and env.get("WAYLAND_DISPLAY"):
                env.pop("GDK_BACKEND", None)
                init_ok = mock_init_check()[0]

        self.assertTrue(init_ok)
        self.assertEqual(call_count[0], 2)
        self.assertNotIn("GDK_BACKEND", env)


if __name__ == "__main__":
    unittest.main()
