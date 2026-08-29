use std::io::Write;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use anoti_core::{QueueStore, RuntimePaths};

#[test]
fn antigravity_hook_responds_before_detached_queue_work() {
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

    let paths = RuntimePaths::from_root(runtime).unwrap();
    let store = QueueStore::new(paths);
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        if store.load().unwrap().contains_key("sess_hook-e2e") {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "detached action did not reach queue"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
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
