#!/usr/bin/env python3
"""
End-to-end integration test harness for all AI agent hook adapters:
- Google Antigravity (hooks/antigravity-notify.sh, hooks/antigravity-notify.py)
- Claude Code (hooks/claude-notify.sh, hooks/claude-notify.py)
- OpenAI Codex (hooks/codex-notify.py)

Validates complete lifecycle contracts:
- SessionStart / PreInvocation (session capture)
- AskUserQuestion / ask_question (critical question notification)
- PermissionRequest / permission_prompt (permission notification)
- Stop / agent_completed / agent-turn-complete (genuine completion verification)
- Fast-path filtering of idle/non-actionable notifications
"""

import os
import sys
import time
import json
import tempfile
import unittest
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PYTHON3 = sys.executable

HOOK_AGY_SH = ROOT_DIR / "hooks" / "antigravity-notify.sh"
HOOK_AGY_PY = ROOT_DIR / "hooks" / "antigravity-notify.py"
HOOK_CLAUDE_SH = ROOT_DIR / "hooks" / "claude-notify.sh"
HOOK_CLAUDE_PY = ROOT_DIR / "hooks" / "claude-notify.py"
HOOK_CODEX_PY = ROOT_DIR / "hooks" / "codex-notify.py"


class BaseHookHarness(unittest.TestCase):
    """Sets up a mock runner capturing multi-desktop-notify invocations."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.bin_dir = Path(self.tmp_dir.name) / ".local" / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        # Mock multi-desktop-notify.py that logs CLI arguments to a JSON lines file
        self.log_file = Path(self.tmp_dir.name) / "notify_calls.jsonl"
        self.mock_notify = self.bin_dir / "multi-desktop-notify.py"

        mock_script = f"""#!/usr/bin/env python3
import sys, json
with open({repr(str(self.log_file))}, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
"""
        with open(self.mock_notify, "w", encoding="utf-8") as f:
            f.write(mock_script)
        self.mock_notify.chmod(0o755)

        self.env = os.environ.copy()
        self.env["HOME"] = self.tmp_dir.name
        self.env["USERPROFILE"] = self.tmp_dir.name
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def get_captured_calls(self):
        if not self.log_file.exists():
            return []
        with open(self.log_file, "r", encoding="utf-8") as f:
            return [json.loads(line.strip()) for line in f if line.strip()]

    def wait_for_captured_calls(self, min_count=1, timeout=0.8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            calls = self.get_captured_calls()
            if len(calls) >= min_count:
                return calls
            time.sleep(0.02)
        return self.get_captured_calls()


class TestAntigravityHooksE2E(BaseHookHarness):
    """Tests Antigravity hook contracts for both .sh and .py implementations."""

    def _run_agy_hook(self, script_path, payload_dict):
        raw_in = json.dumps(payload_dict)
        res = subprocess.run(
            [PYTHON3, str(script_path)],
            input=raw_in,
            text=True,
            capture_output=True,
            env=self.env,
            timeout=5
        )
        return res

    def test_pre_invocation_capture_session(self):
        for script in [HOOK_AGY_SH, HOOK_AGY_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "hook_event_name": "PreInvocation",
                    "conversationId": "agy-conv-123",
                    "invocationNum": 1,
                    "workspacePaths": ["/home/user/my-project"]
                }
                res = self._run_agy_hook(script, payload)
                self.assertEqual(res.returncode, 0)
                self.assertEqual(res.stdout.strip(), "{}")

                calls = self.wait_for_captured_calls(min_count=prev_count + 1)
                self.assertTrue(any("--capture-session" in call for call in calls))
                capture_call = [c for c in calls if "--capture-session" in c][-1]
                self.assertIn("--session-id=agy-conv-123", capture_call)
                self.assertIn("--project-hint=my-project", capture_call)

    def test_pre_tool_use_ask_question(self):
        for script in [HOOK_AGY_SH, HOOK_AGY_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "conversationId": "agy-conv-456",
                    "toolCall": {
                        "name": "ask_question",
                        "args": {
                            "questions": [{"question": "Bạn có muốn tiếp tục không?"}]
                        }
                    }
                }
                res = self._run_agy_hook(script, payload)
                self.assertEqual(res.returncode, 0)
                self.assertEqual(res.stdout.strip(), '{"decision": "allow"}')

                calls = self.wait_for_captured_calls(min_count=prev_count + 1)
                question_call = calls[-1]
                self.assertIn("--app-name=Antigravity", question_call)
                self.assertIn("--event-type=question", question_call)
                self.assertIn("--urgency=critical", question_call)
                self.assertTrue(any("Bạn có muốn tiếp tục không?" in arg for arg in question_call))

    def test_pre_tool_use_non_ask_tool_immediate_allow(self):
        for script in [HOOK_AGY_SH, HOOK_AGY_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "conversationId": "agy-conv-456",
                    "toolCall": {
                        "name": "run_command",
                        "args": {"command": "git status"}
                    }
                }
                res = self._run_agy_hook(script, payload)
                self.assertEqual(res.returncode, 0)
                self.assertEqual(res.stdout.strip(), '{"decision": "allow"}')

                calls = self.get_captured_calls()
                self.assertEqual(len(calls), prev_count + 1)
                self.assertIn("--dismiss", calls[-1])
                self.assertIn("--session-id=agy-conv-456", calls[-1])

    def test_idle_prompt_fast_path(self):
        for script in [HOOK_AGY_SH, HOOK_AGY_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {"notification_type": "idle_prompt"}
                res = self._run_agy_hook(script, payload)
                self.assertEqual(res.returncode, 0)
                self.assertEqual(res.stdout.strip(), "{}")
                self.assertEqual(len(self.get_captured_calls()), prev_count)

    def test_genuine_completion_notification(self):
        for script in [HOOK_AGY_SH, HOOK_AGY_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                transcript_file = Path(self.tmp_dir.name) / "transcript.jsonl"
                with open(transcript_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"source": "MODEL", "type": "PLANNER_RESPONSE", "tool_calls": []}) + "\n")

                payload = {
                    "conversationId": "agy-conv-789",
                    "hook_event_name": "Stop",
                    "terminationReason": "STOP",
                    "fullyIdle": True,
                    "transcriptPath": str(transcript_file)
                }
                res = self._run_agy_hook(script, payload)
                self.assertEqual(res.returncode, 0)

                calls = self.wait_for_captured_calls(min_count=prev_count + 1)
                comp_call = calls[-1]
                self.assertIn("--app-name=Antigravity", comp_call)
                self.assertIn("--event-type=complete", comp_call)
                self.assertIn("--urgency=normal", comp_call)


class TestClaudeCodeHooksE2E(BaseHookHarness):
    """Tests Claude Code hook contracts for both .sh and .py implementations."""

    def _run_claude_hook(self, script_path, payload_dict):
        raw_in = json.dumps(payload_dict)
        cmd = ["bash", str(script_path)] if str(script_path).endswith(".sh") else [PYTHON3, str(script_path)]
        res = subprocess.run(
            cmd,
            input=raw_in,
            text=True,
            capture_output=True,
            env=self.env,
            timeout=5
        )
        return res

    def test_session_start_capture(self):
        for script in [HOOK_CLAUDE_SH, HOOK_CLAUDE_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "hook_event_name": "SessionStart",
                    "session_id": "claude-sess-101",
                    "cwd": "/workspace/project-alpha"
                }
                res = self._run_claude_hook(script, payload)
                self.assertEqual(res.returncode, 0)

                calls = self.wait_for_captured_calls(min_count=prev_count + 1)
                self.assertTrue(any("--capture-session" in call for call in calls))
                capture_call = [c for c in calls if "--capture-session" in c][-1]
                self.assertIn("--session-id=claude-sess-101", capture_call)
                self.assertIn("--project-hint=project-alpha", capture_call)

    def test_ask_user_question_notification(self):
        for script in [HOOK_CLAUDE_SH, HOOK_CLAUDE_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "tool_name": "AskUserQuestion",
                    "tool_input": {
                        "questions": [{"question": "Bạn chọn phương án 1 hay 2?"}]
                    },
                    "session_id": "claude-sess-102",
                    "cwd": "/workspace/project-beta"
                }
                res = self._run_claude_hook(script, payload)
                self.assertEqual(res.returncode, 0)

                calls = self.wait_for_captured_calls(min_count=prev_count + 1)
                q_call = calls[-1]
                self.assertIn("--app-name=Claude Code", q_call)
                self.assertIn("--event-type=question", q_call)
                self.assertIn("--urgency=critical", q_call)

    def test_permission_prompt_notification(self):
        for script in [HOOK_CLAUDE_SH, HOOK_CLAUDE_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "notification_type": "permission_prompt",
                    "tool_name": "Bash",
                    "tool_input": {"command": "rm -rf build/"},
                    "session_id": "claude-sess-103",
                    "cwd": "/workspace/project-gamma"
                }
                res = self._run_claude_hook(script, payload)
                self.assertEqual(res.returncode, 0)

                calls = self.wait_for_captured_calls(min_count=prev_count + 1)
                p_call = calls[-1]
                self.assertIn("--app-name=Claude Code", p_call)
                self.assertIn("--event-type=permission", p_call)
                self.assertIn("--urgency=critical", p_call)

    def test_agent_completed_notification(self):
        for script in [HOOK_CLAUDE_SH, HOOK_CLAUDE_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "notification_type": "agent_completed",
                    "message": "Đã hoàn thành toàn bộ công việc.",
                    "session_id": "claude-sess-104",
                    "cwd": "/workspace/project-delta"
                }
                res = self._run_claude_hook(script, payload)
                self.assertEqual(res.returncode, 0)

                calls = self.wait_for_captured_calls(min_count=prev_count + 1)
                c_call = calls[-1]
                self.assertIn("--app-name=Claude Code", c_call)
                self.assertIn("--event-type=complete", c_call)
                self.assertIn("--urgency=normal", c_call)

    def test_idle_prompt_ignored(self):
        for script in [HOOK_CLAUDE_SH, HOOK_CLAUDE_PY]:
            with self.subTest(script=script.name):
                prev_count = len(self.get_captured_calls())
                payload = {
                    "notification_type": "idle_prompt",
                    "session_id": "claude-sess-105"
                }
                res = self._run_claude_hook(script, payload)
                self.assertEqual(res.returncode, 0)
                self.assertEqual(len(self.get_captured_calls()), prev_count)


class TestCodexHooksE2E(BaseHookHarness):
    """Tests OpenAI Codex hook contracts."""

    def test_session_start_capture(self):
        prev_count = len(self.get_captured_calls())
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "codex-sess-201"
        }
        res = subprocess.run(
            [PYTHON3, str(HOOK_CODEX_PY)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.env,
            timeout=5
        )
        self.assertEqual(res.returncode, 0)
        calls = self.wait_for_captured_calls(min_count=prev_count + 1)
        self.assertTrue(any("--capture-session" in call for call in calls))
        capture_call = [c for c in calls if "--capture-session" in c][-1]
        self.assertIn("--session-id=codex-sess-201", capture_call)

    def test_permission_request_notification(self):
        prev_count = len(self.get_captured_calls())
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "execute_command",
            "tool_input": {"description": "Chạy lệnh kiểm thử pytest"},
            "session_id": "codex-sess-202"
        }
        res = subprocess.run(
            [PYTHON3, str(HOOK_CODEX_PY)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.env,
            timeout=5
        )
        self.assertEqual(res.returncode, 0)
        calls = self.wait_for_captured_calls(min_count=prev_count + 1)
        p_call = calls[-1]
        self.assertIn("--app-name=Codex", p_call)
        self.assertIn("--event-type=permission", p_call)
        self.assertIn("--urgency=critical", p_call)

    def test_agent_turn_complete_cli_arg(self):
        prev_count = len(self.get_captured_calls())
        payload = {
            "type": "agent-turn-complete",
            "session_id": "codex-sess-203"
        }
        res = subprocess.run(
            [PYTHON3, str(HOOK_CODEX_PY), json.dumps(payload)],
            capture_output=True,
            env=self.env,
            timeout=5
        )
        self.assertEqual(res.returncode, 0)
        calls = self.wait_for_captured_calls(min_count=prev_count + 1)
        c_call = calls[-1]
        self.assertIn("--app-name=Codex", c_call)
        self.assertIn("--event-type=complete", c_call)
        self.assertIn("--urgency=normal", c_call)

    def test_agent_turn_complete_stdin(self):
        prev_count = len(self.get_captured_calls())
        payload = {
            "type": "agent-turn-complete",
            "session_id": "codex-sess-204"
        }
        res = subprocess.run(
            [PYTHON3, str(HOOK_CODEX_PY)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.env,
            timeout=5
        )
        self.assertEqual(res.returncode, 0)
        calls = self.wait_for_captured_calls(min_count=prev_count + 1)
        c_call = calls[-1]
        self.assertIn("--app-name=Codex", c_call)
        self.assertIn("--event-type=complete", c_call)
        self.assertIn("--urgency=normal", c_call)


if __name__ == "__main__":
    unittest.main()
