use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use anoti_hooks::config::{merge_owned_hooks, remove_owned_hooks};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use thiserror::Error;
use wait_timeout::ChildExt;

const MANIFEST_SOURCE: &str = include_str!("../../../artifacts/manifest.json");
const OWNED_MARKERS: &[&str] = &[
    "anoti hook",
    "hook claude",
    "hook codex",
    "hook antigravity",
    "notify-input.sh",
    "notify-claude.py",
    ".codex/notify.py",
    ".codex\\notify.py",
    "notify-antigravity",
    "ai-agent-desktop-notifier",
];
const LEGACY_RUNTIME_ARTIFACTS: &[&str] = &[
    ".local/bin/multi-desktop-notify.py",
    ".claude/hooks/notify-input.sh",
    ".claude/hooks/notify-claude.py",
    ".codex/notify.py",
    ".gemini/hooks/notify-antigravity.sh",
    ".gemini/hooks/notify-antigravity.py",
];
const LEGACY_RUNTIME_STATE_FILES: &[&str] = &[
    "ai_agent_notifier_sessions.json",
    "ai_agent_notifier_sessions.lock",
    "ai_agent_notifier_queue.json",
    "ai_agent_notifier_queue.lock",
    "ai_agent_notifier_overlay.lock",
];

#[derive(Debug, Error)]
pub enum LifecycleError {
    #[error("lifecycle I/O failed for {path}: {source}")]
    Io { path: PathBuf, source: io::Error },
    #[error("artifact manifest is invalid: {0}")]
    Manifest(#[from] serde_json::Error),
    #[error("unsupported lifecycle operation: {0}")]
    UnsupportedOperation(String),
    #[error("installed Rust binary failed health check; rollback was restored")]
    HealthCheck,
    #[error("user profile path is unavailable")]
    ProfileUnavailable,
    #[error("runtime path discovery failed: {0}")]
    State(#[from] anoti_core::StateError),
}

#[derive(Debug, Deserialize)]
struct ArtifactManifest {
    schema_version: u32,
    artifacts: Vec<Artifact>,
}

#[derive(Debug, Deserialize)]
struct Artifact {
    id: String,
    platform: String,
    path: String,
    source: String,
    owned: bool,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct InstallState {
    version: String,
    previous_codex_notify: Option<String>,
    installed_artifacts: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct LifecycleReport {
    pub operation: String,
    pub profile: PathBuf,
    pub artifacts_changed: usize,
    pub rollback_available: bool,
}

pub fn execute(operation: &str) -> Result<LifecycleReport, LifecycleError> {
    let profile = profile_root()?;
    match operation {
        "install" | "update" => install_or_update(operation, &profile),
        "uninstall" => uninstall(&profile),
        other => Err(LifecycleError::UnsupportedOperation(other.to_owned())),
    }
}

pub fn installation_report() -> Result<Value, LifecycleError> {
    let profile = profile_root()?;
    let state = load_state(&profile.join(".config/ai-agent-notifier/install-state.json"));
    let runtime = runtime_path(&profile);
    let expected_version = env!("CARGO_PKG_VERSION");
    let installed_version = installed_binary_version(&runtime);
    Ok(json!({
        "expected_version": expected_version,
        "recorded_version": state.version,
        "recorded_version_drift": !state.version.is_empty() && state.version != expected_version,
        "installed_version": installed_version,
        "version_drift": runtime.is_file() && installed_version.as_deref() != Some(expected_version),
        "runtime_path": runtime,
        "runtime_installed": runtime.is_file(),
        "manifest_artifacts": manifest()?.artifacts.len(),
    }))
}

fn installed_binary_version(binary: &Path) -> Option<String> {
    if !binary.is_file() {
        return None;
    }
    let mut child = Command::new(binary)
        .arg("--version")
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let status = child.wait_timeout(Duration::from_secs(2)).ok()??;
    if !status.success() {
        return None;
    }
    let mut output = String::new();
    child.stdout.take()?.read_to_string(&mut output).ok()?;
    output.split_whitespace().last().map(str::to_owned)
}

fn profile_root() -> Result<PathBuf, LifecycleError> {
    env::var_os("AI_AGENT_NOTIFIER_PROFILE_ROOT")
        .or_else(|| env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }))
        .map(PathBuf::from)
        .ok_or(LifecycleError::ProfileUnavailable)
}

fn manifest() -> Result<ArtifactManifest, LifecycleError> {
    let manifest: ArtifactManifest = serde_json::from_str(MANIFEST_SOURCE)?;
    if manifest.schema_version != 1 {
        return Err(LifecycleError::Manifest(serde_json::Error::io(
            io::Error::new(
                io::ErrorKind::InvalidData,
                "unsupported artifact manifest schema",
            ),
        )));
    }
    Ok(manifest)
}

fn applies(artifact: &Artifact) -> bool {
    artifact.platform == "both"
        || artifact.platform == if cfg!(windows) { "windows" } else { "linux" }
}

fn install_or_update(operation: &str, profile: &Path) -> Result<LifecycleReport, LifecycleError> {
    let manifest = manifest()?;
    let state_path = profile.join(".config/ai-agent-notifier/install-state.json");
    let mut state = load_state(&state_path);
    let binary =
        env::current_exe().map_err(|source| io_error(Path::new("current executable"), source))?;
    let runtime = runtime_path(profile);
    let rollback = profile.join(".config/ai-agent-notifier/rollback/anoti");
    let mut rollback_available = false;
    if runtime.is_file() && runtime != binary {
        copy_file(&runtime, &rollback)?;
        rollback_available = true;
    }

    let mut changed = 0;
    for artifact in manifest
        .artifacts
        .iter()
        .filter(|artifact| applies(artifact))
    {
        let target = profile.join(&artifact.path);
        match artifact.source.as_str() {
            "self" => {
                if target != binary {
                    copy_file(&binary, &target)?;
                    set_executable(&target)?;
                    changed += 1;
                }
            }
            "generated:windows-wrapper" => {
                write_atomic(&target, b"@echo off\r\n\"%~dp0anoti.exe\" %*\r\n")?;
                changed += 1;
            }
            "managed:claude" => {
                merge_json_hooks(&target, &claude_hooks(&runtime))?;
                changed += 1;
            }
            "managed:codex-hooks" => {
                merge_json_hooks(&target, &codex_hooks(&runtime))?;
                changed += 1;
            }
            "managed:antigravity" => {
                merge_json_hooks(&target, &antigravity_hooks(&runtime))?;
                changed += 1;
            }
            "managed:antigravity-json-hooks" => {
                merge_antigravity_namespace(&target, &antigravity_hooks(&runtime))?;
                changed += 1;
            }
            "managed:codex" => {
                state.previous_codex_notify =
                    merge_codex_config(&target, &runtime, state.previous_codex_notify.take())?;
                changed += 1;
            }
            source if source.starts_with("embedded:") && write_embedded_icon(source, &target)? => {
                changed += 1;
            }
            _ => {}
        }
        if artifact.owned {
            state.installed_artifacts.push(artifact.id.clone());
        }
    }
    state.installed_artifacts.sort();
    state.installed_artifacts.dedup();
    env!("CARGO_PKG_VERSION").clone_into(&mut state.version);
    save_state(&state_path, &state)?;
    changed += remove_legacy_runtime_artifacts(profile)?;
    cleanup_old_gnome_extension(profile)?;
    cleanup_old_runtime_state(profile)?;
    if runtime != binary && !health_check(&runtime) {
        if rollback_available {
            copy_file(&rollback, &runtime)?;
        }
        return Err(LifecycleError::HealthCheck);
    }
    Ok(LifecycleReport {
        operation: operation.to_owned(),
        profile: profile.to_path_buf(),
        artifacts_changed: changed,
        rollback_available,
    })
}

fn uninstall(profile: &Path) -> Result<LifecycleReport, LifecycleError> {
    let manifest = manifest()?;
    let state_path = profile.join(".config/ai-agent-notifier/install-state.json");
    let state = load_state(&state_path);
    let runtime = runtime_path(profile);
    let mut changed = 0;
    for artifact in manifest
        .artifacts
        .iter()
        .filter(|artifact| applies(artifact))
    {
        let target = profile.join(&artifact.path);
        if artifact.source.starts_with("managed:") {
            if artifact.source == "managed:codex" {
                remove_codex_config(&target, state.previous_codex_notify.as_deref())?;
            } else if artifact.source == "managed:antigravity-json-hooks" {
                remove_antigravity_namespace(&target)?;
            } else {
                remove_json_hooks(&target)?;
            }
            changed += 1;
        } else if artifact.owned && target.is_file() {
            fs::remove_file(&target).map_err(|source| io_error(&target, source))?;
            changed += 1;
        }
    }
    if runtime.is_file() {
        fs::remove_file(&runtime).map_err(|source| io_error(&runtime, source))?;
    }
    changed += remove_legacy_runtime_artifacts(profile)?;
    cleanup_old_gnome_extension(profile)?;
    cleanup_old_runtime_state(profile)?;
    Ok(LifecycleReport {
        operation: "uninstall".to_owned(),
        profile: profile.to_path_buf(),
        artifacts_changed: changed,
        rollback_available: false,
    })
}

fn runtime_path(profile: &Path) -> PathBuf {
    profile.join(if cfg!(windows) {
        ".local/bin/anoti.exe"
    } else {
        ".local/bin/anoti"
    })
}

fn merge_json_hooks(path: &Path, additions: &Map<String, Value>) -> Result<(), LifecycleError> {
    let mut document = read_json(path);
    merge_owned_hooks(&mut document, additions, OWNED_MARKERS);
    write_json(path, &document)
}

fn remove_json_hooks(path: &Path) -> Result<(), LifecycleError> {
    if !path.exists() {
        return Ok(());
    }
    let mut document = read_json(path);
    remove_owned_hooks(&mut document, OWNED_MARKERS);
    write_json(path, &document)
}

fn merge_antigravity_namespace(
    path: &Path,
    additions: &Map<String, Value>,
) -> Result<(), LifecycleError> {
    let mut document = read_json(path);
    if !document.is_object() {
        document = json!({});
    }
    document
        .as_object_mut()
        .expect("Antigravity hook document was normalized")
        .insert(
            "desktop-notifier".to_owned(),
            Value::Object(additions.clone()),
        );
    write_json(path, &document)
}

fn remove_antigravity_namespace(path: &Path) -> Result<(), LifecycleError> {
    if !path.exists() {
        return Ok(());
    }
    let mut document = read_json(path);
    let removed = document
        .as_object_mut()
        .is_some_and(|root| root.remove("desktop-notifier").is_some());
    if removed {
        write_json(path, &document)?;
    }
    Ok(())
}

fn claude_hooks(binary: &Path) -> Map<String, Value> {
    let command = format!("\"{}\" hook claude", binary.display());
    json!({
        "PreToolUse":[{"matcher":"AskUserQuestion|ask_question","hooks":[{"type":"command","command":command}]}],
        "Notification":[{"matcher":"permission_prompt|agent_completed","hooks":[{"type":"command","command":command}]}],
        "Stop":[{"hooks":[{"type":"command","command":command}]}]
    }).as_object().cloned().unwrap_or_default()
}

fn codex_hooks(binary: &Path) -> Map<String, Value> {
    let command = format!("\"{}\" hook codex", binary.display());
    json!({
        "PermissionRequest":[{"hooks":[{"type":"command","command":command,"timeout":5}]}]
    })
    .as_object()
    .cloned()
    .unwrap_or_default()
}

fn remove_legacy_runtime_artifacts(profile: &Path) -> Result<usize, LifecycleError> {
    let mut changed = 0;
    for relative in LEGACY_RUNTIME_ARTIFACTS {
        let path = profile.join(relative);
        if path.is_file() {
            fs::remove_file(&path).map_err(|source| io_error(&path, source))?;
            changed += 1;
        }
    }
    Ok(changed)
}

#[cfg(target_os = "linux")]
fn is_real_user_home_profile(profile: &Path) -> bool {
    if env::var_os("AI_AGENT_NOTIFIER_PROFILE_ROOT").is_some() {
        return false;
    }
    env::var_os("HOME").is_some_and(|home| home == profile.as_os_str())
}

fn cleanup_old_gnome_extension(profile: &Path) -> Result<(), LifecycleError> {
    #[cfg(target_os = "linux")]
    {
        if is_real_user_home_profile(profile) {
            let _ = Command::new("gnome-extensions")
                .args(["disable", "ai-agent-desktop-notifier@sonnx24042005"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
    }
    let extension_dir =
        profile.join(".local/share/gnome-shell/extensions/ai-agent-desktop-notifier@sonnx24042005");
    if extension_dir.exists() {
        fs::remove_dir_all(&extension_dir).map_err(|source| io_error(&extension_dir, source))?;
    }
    Ok(())
}

fn cleanup_old_runtime_state(profile: &Path) -> Result<(), LifecycleError> {
    let runtime_dir = profile.join(".config/ai-agent-notifier/runtime");
    for file_name in LEGACY_RUNTIME_STATE_FILES {
        let path = runtime_dir.join(file_name);
        if path.exists() {
            fs::remove_file(&path).map_err(|source| io_error(&path, source))?;
        }
    }
    let paths = anoti_core::RuntimePaths::discover()?;
    for file_name in LEGACY_RUNTIME_STATE_FILES {
        let path = paths.root.join(file_name);
        if path.exists() {
            fs::remove_file(&path).map_err(|source| io_error(&path, source))?;
        }
    }
    Ok(())
}

fn antigravity_hooks(binary: &Path) -> Map<String, Value> {
    let command = format!("\"{}\" hook antigravity", binary.display());
    json!({
        "PreToolUse":[{"matcher":"ask_question|AskQuestion|AskUserQuestion","hooks":[{"type":"command","command":command,"timeout":10}]}],
        "Stop":[{"type":"command","command":command,"timeout":10}],
        "Notification":[{"matcher":"permission_prompt|idle_prompt|agent_needs_input|agent_completed","hooks":[{"type":"command","command":command,"timeout":10}]}]
    }).as_object().cloned().unwrap_or_default()
}

fn merge_codex_config(
    path: &Path,
    binary: &Path,
    previous: Option<String>,
) -> Result<Option<String>, LifecycleError> {
    let content = fs::read_to_string(path).unwrap_or_default();
    let owned = "hook\", \"codex";
    let previous = previous.or_else(|| {
        content
            .lines()
            .find(|line| line.trim_start().starts_with("notify =") && !line.contains(owned))
            .map(str::to_owned)
    });
    let escaped = binary
        .to_string_lossy()
        .replace('\\', "\\\\")
        .replace('"', "\\\"");
    let mut lines = content
        .lines()
        .filter(|line| !line.trim_start().starts_with("notify ="))
        .map(str::to_owned)
        .collect::<Vec<_>>();
    lines.insert(0, format!("notify = [\"{escaped}\", \"hook\", \"codex\"]"));
    write_atomic(path, format!("{}\n", lines.join("\n")).as_bytes())?;
    Ok(previous)
}

fn remove_codex_config(path: &Path, previous: Option<&str>) -> Result<(), LifecycleError> {
    if !path.exists() {
        return Ok(());
    }
    let content = fs::read_to_string(path).map_err(|source| io_error(path, source))?;
    let mut lines = content
        .lines()
        .filter(|line| {
            !(line.trim_start().starts_with("notify =") && line.contains("hook\", \"codex"))
        })
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if let Some(previous) = previous {
        lines.insert(0, previous.to_owned());
    }
    write_atomic(path, format!("{}\n", lines.join("\n")).as_bytes())
}

fn read_json(path: &Path) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str(&content).ok())
        .unwrap_or_else(|| json!({}))
}

fn write_json(path: &Path, value: &Value) -> Result<(), LifecycleError> {
    write_atomic(path, &serde_json::to_vec_pretty(value)?)
}

fn load_state(path: &Path) -> InstallState {
    fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str(&content).ok())
        .unwrap_or_default()
}

fn save_state(path: &Path, state: &InstallState) -> Result<(), LifecycleError> {
    write_atomic(path, &serde_json::to_vec_pretty(state)?)
}

fn copy_file(source: &Path, target: &Path) -> Result<(), LifecycleError> {
    let parent = target.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|source| io_error(parent, source))?;
    let temporary = target.with_extension("anoti-new");
    fs::copy(source, &temporary).map_err(|error| io_error(&temporary, error))?;
    if target.exists() {
        fs::remove_file(target).map_err(|source| io_error(target, source))?;
    }
    fs::rename(&temporary, target).map_err(|source| io_error(target, source))?;
    Ok(())
}

fn write_atomic(path: &Path, content: &[u8]) -> Result<(), LifecycleError> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|source| io_error(parent, source))?;
    let temporary = path.with_extension("anoti-new");
    fs::write(&temporary, content).map_err(|source| io_error(&temporary, source))?;
    if path.exists() {
        fs::remove_file(path).map_err(|source| io_error(path, source))?;
    }
    fs::rename(&temporary, path).map_err(|source| io_error(path, source))
}

fn write_embedded_icon(source: &str, target: &Path) -> Result<bool, LifecycleError> {
    let bytes: Option<&'static [u8]> = match source {
        "embedded:icon-anoti" => Some(include_bytes!("../../../assets/icons/anoti.png")),
        "embedded:icon-claude" => Some(include_bytes!("../../../assets/icons/claude.png")),
        "embedded:icon-codex" => Some(include_bytes!("../../../assets/icons/codex.png")),
        "embedded:icon-antigravity" => {
            Some(include_bytes!("../../../assets/icons/antigravity.png"))
        }
        "embedded:icon-anoti-svg" => Some(include_bytes!("../../../assets/icons/anoti.svg")),
        "embedded:icon-claude-svg" => Some(include_bytes!("../../../assets/icons/claude.svg")),
        "embedded:icon-codex-svg" => Some(include_bytes!("../../../assets/icons/codex.svg")),
        _ => None,
    };
    if let Some(content) = bytes {
        write_atomic(target, content)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

#[cfg(unix)]
fn set_executable(path: &Path) -> Result<(), LifecycleError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o755))
        .map_err(|source| io_error(path, source))
}

#[cfg(not(unix))]
#[allow(clippy::unnecessary_wraps)]
fn set_executable(_path: &Path) -> Result<(), LifecycleError> {
    Ok(())
}

fn health_check(binary: &Path) -> bool {
    Command::new(binary)
        .arg("--version")
        .env("AI_AGENT_NOTIFIER_NO_UI", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn io_error(path: &Path, source: io::Error) -> LifecycleError {
    LifecycleError::Io {
        path: path.to_path_buf(),
        source,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_has_unique_ids_and_all_managed_configs() {
        let manifest = manifest().unwrap();
        let mut ids = manifest
            .artifacts
            .iter()
            .map(|artifact| artifact.id.as_str())
            .collect::<Vec<_>>();
        ids.sort_unstable();
        let original = ids.len();
        ids.dedup();
        assert_eq!(ids.len(), original);
        for required in [
            "runtime-linux",
            "runtime-windows",
            "claude-hooks",
            "codex-notify",
            "antigravity-hooks",
            "antigravity-json-hooks",
            "icon-anoti",
            "icon-claude",
            "icon-codex",
            "icon-antigravity",
            "install-state",
            "rollback-runtime",
        ] {
            assert!(ids.contains(&required));
        }
    }

    #[test]
    fn owned_flag_is_never_set_for_shared_agent_config() {
        let manifest = manifest().unwrap();
        assert!(
            manifest
                .artifacts
                .iter()
                .filter(|artifact| artifact.source.starts_with("managed:"))
                .all(|artifact| !artifact.owned)
        );
    }
}
