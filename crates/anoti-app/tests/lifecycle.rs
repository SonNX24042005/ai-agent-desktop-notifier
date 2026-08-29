use std::fs;
use std::process::Command;

use tempfile::tempdir;

fn run(profile: &std::path::Path, operation: &str) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_anoti"))
        .arg(operation)
        .env("AI_AGENT_NOTIFIER_PROFILE_ROOT", profile)
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .output()
        .unwrap()
}

#[test]
fn install_update_uninstall_and_rollback_are_symmetric_in_sandbox() {
    let directory = tempdir().unwrap();
    let profile = directory.path();
    let claude = profile.join(".claude/settings.json");
    let codex = profile.join(".codex/config.toml");
    fs::create_dir_all(claude.parent().unwrap()).unwrap();
    fs::create_dir_all(codex.parent().unwrap()).unwrap();
    fs::write(
        &claude,
        r#"{"theme":"dark","hooks":{"Stop":[{"hooks":[{"command":"third-party"}]}]}}"#,
    )
    .unwrap();
    fs::write(&codex, "notify = [\"third-party\"]\nmodel = \"test\"\n").unwrap();
    let codex_hooks_path = profile.join(".codex/hooks.json");
    fs::write(
        &codex_hooks_path,
        r#"{"hooks":{"SessionStart":[{"hooks":[{"command":"third-party"}]},{"hooks":[{"command":"/usr/bin/python3 /tmp/profile/.codex/notify.py"}]},{"hooks":[{"command":"python C:\\Users\\test\\.codex\\notify.py"}]}],"PermissionRequest":[{"hooks":[{"command":"/usr/bin/python3 /tmp/profile/.codex/notify.py"}]}]}}"#,
    )
    .unwrap();
    let antigravity_json_hooks = profile.join(".gemini/config/hooks.json");
    fs::create_dir_all(antigravity_json_hooks.parent().unwrap()).unwrap();
    fs::write(
        &antigravity_json_hooks,
        r#"{"desktop-notifier":{"PreInvocation":[{"command":"/tmp/profile/.gemini/hooks/notify-antigravity.sh"}]},"orca-status":{"Stop":[{"command":"third-party"}]}}"#,
    )
    .unwrap();
    let legacy = profile.join(".codex/notify.py");
    fs::write(&legacy, "legacy").unwrap();

    let install = run(profile, "install");
    assert!(
        install.status.success(),
        "{}",
        String::from_utf8_lossy(&install.stderr)
    );
    let runtime = profile.join(".local/bin/anoti");
    assert!(runtime.is_file());
    assert!(fs::read_to_string(&claude).unwrap().contains("third-party"));
    assert!(fs::read_to_string(&claude).unwrap().contains("hook claude"));
    assert!(!legacy.exists());
    let codex_hooks = fs::read_to_string(&codex_hooks_path).unwrap();
    assert!(codex_hooks.contains("SessionStart"));
    assert!(codex_hooks.contains("PermissionRequest"));
    assert!(codex_hooks.contains("third-party"));
    assert!(!codex_hooks.contains(".codex/notify.py"));
    assert!(!codex_hooks.contains(".codex\\\\notify.py"));
    let antigravity_after_install = fs::read_to_string(&antigravity_json_hooks).unwrap();
    assert!(antigravity_after_install.contains("desktop-notifier"));
    assert!(antigravity_after_install.contains("hook antigravity"));
    assert!(antigravity_after_install.contains("AskQuestion"));
    assert!(!antigravity_after_install.contains("notify-antigravity"));
    assert!(antigravity_after_install.contains("orca-status"));

    let update = run(profile, "update");
    assert!(
        update.status.success(),
        "{}",
        String::from_utf8_lossy(&update.stderr)
    );
    assert!(
        profile
            .join(".config/ai-agent-notifier/rollback/anoti")
            .is_file()
    );

    let uninstall = run(profile, "uninstall");
    assert!(
        uninstall.status.success(),
        "{}",
        String::from_utf8_lossy(&uninstall.stderr)
    );
    assert!(!runtime.exists());
    let claude_after = fs::read_to_string(&claude).unwrap();
    assert!(claude_after.contains("third-party"));
    assert!(!claude_after.contains("hook claude"));
    let codex_after = fs::read_to_string(&codex).unwrap();
    assert!(codex_after.contains("notify = [\"third-party\"]"));
    assert!(codex_after.contains("model = \"test\""));
    assert!(
        fs::read_to_string(&codex_hooks_path)
            .unwrap()
            .contains("third-party")
    );
    assert!(
        fs::read_to_string(&antigravity_json_hooks)
            .unwrap()
            .contains("orca-status")
    );
    assert!(
        !fs::read_to_string(&antigravity_json_hooks)
            .unwrap()
            .contains("desktop-notifier")
    );
}
