use std::process::Command;

#[test]
fn run_notify_delivers_native_notification_directly() {
    let directory = tempfile::tempdir().unwrap();
    let runtime = directory.path().join("runtime");

    let status = Command::new(env!("CARGO_BIN_EXE_anoti"))
        .args([
            "notify",
            "--app-name=Claude Code",
            "--title=Direct notification",
            "--message=Task finished",
        ])
        .env("AI_AGENT_NOTIFIER_RUNTIME_DIR", &runtime)
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .status()
        .unwrap();

    assert!(status.success());
}
