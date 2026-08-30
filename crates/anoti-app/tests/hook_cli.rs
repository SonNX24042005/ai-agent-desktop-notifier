use std::io::Write;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[test]
fn antigravity_hook_responds_synchronously_with_allow() {
    let directory = tempfile::tempdir().unwrap();
    let runtime = directory.path().join("runtime");
    let mut child = Command::new(env!("CARGO_BIN_EXE_anoti"))
        .args(["hook", "antigravity"])
        .env("AI_AGENT_NOTIFIER_RUNTIME_DIR", &runtime)
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(
            r#"{"conversationId":"hook-e2e","toolCall":{"name":"ask_question","args":{"questions":[{"question":"Tiếp tục?"}]}}}"#
                .as_bytes(),
        )
        .unwrap();
    drop(child.stdin.take());
    let started = Instant::now();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim(),
        r#"{"decision": "allow"}"#
    );
    assert!(started.elapsed() < Duration::from_secs(1));
}

#[test]
fn malformed_hook_payload_is_fail_open() {
    let output = Command::new(env!("CARGO_BIN_EXE_anoti"))
        .args(["hook", "antigravity", "{broken"])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(String::from_utf8(output.stdout).unwrap().trim(), "{}");
}

#[test]
fn claude_hook_processes_notification() {
    let directory = tempfile::tempdir().unwrap();
    let runtime = directory.path().join("runtime");
    let output = Command::new(env!("CARGO_BIN_EXE_anoti"))
        .args([
            "hook",
            "claude",
            r#"{"hook_event_name":"Notification","notification_type":"agent_completed","session_id":"claude-e2e"}"#,
        ])
        .env("AI_AGENT_NOTIFIER_RUNTIME_DIR", &runtime)
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .output()
        .unwrap();
    assert!(output.status.success());
}

#[test]
fn antigravity_hook_processes_completion() {
    let directory = tempfile::tempdir().unwrap();
    let runtime = directory.path().join("runtime");
    let output = Command::new(env!("CARGO_BIN_EXE_anoti"))
        .args([
            "hook",
            "antigravity",
            r#"{"conversationId":"agy-cli-e2e","executionNum":1,"terminationReason":"model_stop","error":"","fullyIdle":true}"#,
        ])
        .env("AI_AGENT_NOTIFIER_RUNTIME_DIR", &runtime)
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(String::from_utf8(output.stdout).unwrap().trim(), "{}");
}
