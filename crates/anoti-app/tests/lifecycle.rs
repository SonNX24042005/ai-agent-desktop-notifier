use std::fs;
use std::process::Command;

use tempfile::tempdir;

const LEGACY_RUNTIME_STATE_FILES: &[&str] = &[
    "ai_agent_notifier_sessions.json",
    "ai_agent_notifier_sessions.lock",
    "ai_agent_notifier_queue.json",
    "ai_agent_notifier_queue.lock",
    "ai_agent_notifier_overlay.lock",
];

fn run(profile: &std::path::Path, operation: &str) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_anoti"))
        .arg(operation)
        .env("AI_AGENT_NOTIFIER_PROFILE_ROOT", profile)
        .env("AI_AGENT_NOTIFIER_RUNTIME_DIR", profile.join("runtime"))
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .output()
        .unwrap()
}

#[test]
#[allow(clippy::too_many_lines)]
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

    // Create old legacy GNOME extension directory in sandbox
    let old_extension_dir =
        profile.join(".local/share/gnome-shell/extensions/ai-agent-desktop-notifier@sonnx24042005");
    fs::create_dir_all(&old_extension_dir).unwrap();
    fs::write(old_extension_dir.join("metadata.json"), "{}").unwrap();

    // Create legacy runtime state files in profile config and runtime override
    let config_runtime_dir = profile.join(".config/ai-agent-notifier/runtime");
    let env_runtime_dir = profile.join("runtime");
    fs::create_dir_all(&config_runtime_dir).unwrap();
    fs::create_dir_all(&env_runtime_dir).unwrap();
    for file_name in LEGACY_RUNTIME_STATE_FILES {
        fs::write(config_runtime_dir.join(file_name), "{}").unwrap();
        fs::write(env_runtime_dir.join(file_name), "{}").unwrap();
    }

    let install = run(profile, "install");
    assert!(
        install.status.success(),
        "{}",
        String::from_utf8_lossy(&install.stderr)
    );
    let runtime = profile.join(".local/bin/anoti");
    assert!(runtime.is_file());
    assert!(profile.join(".local/share/anoti/icons/anoti.png").is_file());
    assert!(
        profile
            .join(".local/share/anoti/icons/claude.png")
            .is_file()
    );
    assert!(profile.join(".local/share/anoti/icons/codex.png").is_file());
    assert!(
        profile
            .join(".local/share/anoti/icons/antigravity.png")
            .is_file()
    );
    assert!(fs::read_to_string(&claude).unwrap().contains("third-party"));
    assert!(fs::read_to_string(&claude).unwrap().contains("hook claude"));
    assert!(!legacy.exists());
    assert!(!old_extension_dir.exists());
    for file_name in LEGACY_RUNTIME_STATE_FILES {
        assert!(
            !config_runtime_dir.join(file_name).exists(),
            "file {file_name} should be cleaned from config runtime"
        );
        assert!(
            !env_runtime_dir.join(file_name).exists(),
            "file {file_name} should be cleaned from env runtime"
        );
    }

    let codex_hooks = fs::read_to_string(&codex_hooks_path).unwrap();
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

    // Recreate old extension dir and state files to test update cleans them up
    fs::create_dir_all(&old_extension_dir).unwrap();
    fs::write(old_extension_dir.join("metadata.json"), "{}").unwrap();
    for file_name in LEGACY_RUNTIME_STATE_FILES {
        fs::write(config_runtime_dir.join(file_name), "{}").unwrap();
        fs::write(env_runtime_dir.join(file_name), "{}").unwrap();
    }

    let update = run(profile, "update");
    assert!(
        update.status.success(),
        "{}",
        String::from_utf8_lossy(&update.stderr)
    );
    assert!(!old_extension_dir.exists());
    assert!(profile.join(".local/share/anoti/icons/anoti.png").is_file());
    assert!(
        profile
            .join(".local/share/anoti/icons/claude.png")
            .is_file()
    );
    for file_name in LEGACY_RUNTIME_STATE_FILES {
        assert!(!config_runtime_dir.join(file_name).exists());
        assert!(!env_runtime_dir.join(file_name).exists());
    }
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
    assert!(!profile.join(".local/share/anoti/icons/anoti.png").exists());
    assert!(!profile.join(".local/share/anoti/icons/claude.png").exists());
    assert!(!profile.join(".local/share/anoti/icons/codex.png").exists());
    assert!(
        !profile
            .join(".local/share/anoti/icons/antigravity.png")
            .exists()
    );
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

#[test]
fn sandbox_lifecycle_cleans_sandbox_extension_directory_safely() {
    let directory = tempdir().unwrap();
    let profile = directory.path();
    let old_extension_dir =
        profile.join(".local/share/gnome-shell/extensions/ai-agent-desktop-notifier@sonnx24042005");
    fs::create_dir_all(&old_extension_dir).unwrap();
    fs::write(old_extension_dir.join("metadata.json"), "{}").unwrap();

    let output = run(profile, "install");
    assert!(output.status.success());
    assert!(!old_extension_dir.exists());
}
