use serde::{Deserialize, Serialize};

/// Normalized event categories accepted by all agent adapters.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EventKind {
    Question,
    Permission,
    Complete,
    #[default]
    Info,
}

/// Notification urgency shared by native backends.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Urgency {
    Low,
    #[default]
    Normal,
    Critical,
}

/// Input to the notification runtime after hook normalization.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct NotificationRequest {
    #[serde(default = "default_app_name")]
    pub app_name: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub questions_json: String,
    #[serde(default)]
    pub urgency: Urgency,
    #[serde(default, rename = "event_type", alias = "event_kind")]
    pub event_kind: EventKind,
    #[serde(default)]
    pub sound: String,
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub timeout: u64,
    #[serde(default)]
    pub icon: String,
}

impl NotificationRequest {
    /// Determines the semantic icon identifier for this request.
    #[must_use]
    pub fn resolved_icon_name(&self) -> &str {
        if !self.icon.is_empty() {
            return &self.icon;
        }
        let app = self.app_name.to_ascii_lowercase();
        if app.contains("claude") {
            "claude"
        } else if app.contains("codex") {
            "codex"
        } else if app.contains("antigravity") {
            "antigravity"
        } else {
            "anoti"
        }
    }
}

/// Resolves an icon file path given an icon name/path and optional profile root.
#[must_use]
pub fn resolve_icon_path(
    icon_name: &str,
    profile_root: Option<&std::path::Path>,
) -> Option<std::path::PathBuf> {
    use std::path::{Path, PathBuf};

    if Path::new(icon_name).is_file() {
        return Some(PathBuf::from(icon_name));
    }
    let name = icon_name.to_ascii_lowercase();
    let candidates: &[&str] = match name.as_str() {
        "claude" => &["claude.png", "claude.svg"],
        "codex" => &["codex.png", "codex.svg"],
        "antigravity" => &["antigravity.png", "antigravity.svg"],
        _ => &["anoti.png", "anoti.svg"],
    };

    if let Some(profile) = profile_root {
        let dir = profile.join(".local/share/anoti/icons");
        for file_name in candidates {
            let candidate = dir.join(file_name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
        for fallback_name in &["anoti.png", "anoti.svg"] {
            let fallback = dir.join(fallback_name);
            if fallback.is_file() {
                return Some(fallback);
            }
        }
    }

    let profile = std::env::var_os("AI_AGENT_NOTIFIER_PROFILE_ROOT")
        .or_else(|| std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }))
        .map(PathBuf::from);
    if let Some(profile) = profile {
        let dir = profile.join(".local/share/anoti/icons");
        for file_name in candidates {
            let candidate = dir.join(file_name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
        for fallback_name in &["anoti.png", "anoti.svg"] {
            let fallback = dir.join(fallback_name);
            if fallback.is_file() {
                return Some(fallback);
            }
        }
    }
    None
}

fn default_app_name() -> String {
    "AI agent".to_owned()
}

/// Capability report used by `anoti doctor`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlatformCapabilities {
    pub native_notification: bool,
    pub backend: String,
}
