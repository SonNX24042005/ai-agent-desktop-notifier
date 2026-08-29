use serde::{Deserialize, Serialize};

fn default_schema_version() -> u32 {
    4
}

fn default_precision() -> String {
    "app".to_owned()
}

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

/// Stable identity captured from the agent source window.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct WindowIdentity {
    #[serde(default)]
    pub window_id: String,
    #[serde(default)]
    pub window_pid: u32,
    #[serde(default)]
    pub caller_pid: u32,
    #[serde(default)]
    pub caller_pid_chain: Vec<u32>,
    #[serde(default)]
    pub project_hint: String,
    #[serde(default)]
    pub title_fingerprint: String,
    #[serde(default)]
    pub app_hint: String,
    #[serde(default)]
    pub session_id: String,
}

/// Session cache record compatible with the legacy schema v4 writer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionRecord {
    #[serde(default = "default_schema_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub window_id: String,
    #[serde(default)]
    pub window_id_dec: String,
    #[serde(default)]
    pub project_hint: String,
    #[serde(default)]
    pub pid: u32,
    #[serde(default)]
    pub window_pid: u32,
    #[serde(default)]
    pub caller_pid: u32,
    #[serde(default)]
    pub caller_pid_chain: Vec<u32>,
    #[serde(default)]
    pub app_hint: String,
    #[serde(default)]
    pub title_fingerprint: String,
    #[serde(default = "default_precision")]
    pub precision: String,
    #[serde(default)]
    pub backend: String,
    #[serde(default)]
    pub caller_tty: String,
    #[serde(default)]
    pub terminal_screen: String,
    #[serde(default)]
    pub updated_at: f64,
}

impl Default for SessionRecord {
    fn default() -> Self {
        Self {
            schema_version: default_schema_version(),
            window_id: String::new(),
            window_id_dec: String::new(),
            project_hint: String::new(),
            pid: 0,
            window_pid: 0,
            caller_pid: 0,
            caller_pid_chain: Vec::new(),
            app_hint: String::new(),
            title_fingerprint: String::new(),
            precision: default_precision(),
            backend: String::new(),
            caller_tty: String::new(),
            terminal_screen: String::new(),
            updated_at: 0.0,
        }
    }
}

impl SessionRecord {
    /// Returns true only for an identity captured from an actual native window.
    #[must_use]
    pub fn has_exact_window_identity(&self) -> bool {
        if self.precision != "window"
            || self.window_pid <= 1
            || self.window_id.trim().is_empty()
            || self.title_fingerprint.trim().is_empty()
            || self.window_id == "wayland:gnome-terminal"
            || self.window_id == "wayland:gnome"
        {
            return false;
        }
        self.window_id
            .strip_prefix("wayland:")
            .is_none_or(|sequence| {
                !sequence.is_empty() && sequence.chars().all(|character| character.is_ascii_digit())
            })
    }
}

/// Persistent queue lifecycle state.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum QueueStatus {
    Displaying,
    #[default]
    Queued,
}

/// Queue item retaining the complete identity snapshot.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QueueItem {
    #[serde(default)]
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
    pub target_window_id: String,
    #[serde(default)]
    pub window_pid: u32,
    #[serde(default)]
    pub caller_pid: u32,
    #[serde(default)]
    pub caller_pid_chain: Vec<u32>,
    #[serde(default)]
    pub project_hint: String,
    #[serde(default)]
    pub app_hint: String,
    #[serde(default)]
    pub title_fingerprint: String,
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub timeout: u64,
    #[serde(default = "default_auto_dismiss_delay")]
    pub auto_dismiss_delay: f64,
    #[serde(default)]
    pub status: QueueStatus,
    #[serde(default)]
    pub generation: u64,
    #[serde(default)]
    pub created_at: f64,
}

impl Default for QueueItem {
    fn default() -> Self {
        Self {
            app_name: String::new(),
            title: String::new(),
            message: String::new(),
            questions_json: String::new(),
            urgency: Urgency::Normal,
            event_kind: EventKind::Info,
            sound: String::new(),
            target_window_id: String::new(),
            window_pid: 0,
            caller_pid: 0,
            caller_pid_chain: Vec::new(),
            project_hint: String::new(),
            app_hint: String::new(),
            title_fingerprint: String::new(),
            session_id: String::new(),
            timeout: 0,
            auto_dismiss_delay: default_auto_dismiss_delay(),
            status: QueueStatus::Queued,
            generation: 0,
            created_at: 0.0,
        }
    }
}

/// Input to the notification runtime after hook normalization.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct NotificationRequest {
    pub app_name: String,
    pub title: String,
    pub message: String,
    #[serde(default)]
    pub questions_json: String,
    #[serde(default)]
    pub urgency: Urgency,
    #[serde(default)]
    pub event_kind: EventKind,
    #[serde(default)]
    pub sound: String,
    #[serde(default)]
    pub identity: WindowIdentity,
    #[serde(default)]
    pub timeout: u64,
    #[serde(default = "default_auto_dismiss_delay")]
    pub auto_dismiss_delay: f64,
}

const fn default_auto_dismiss_delay() -> f64 {
    1.5
}

/// Verified result of a focus request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FocusOutcome {
    Focused { window_id: String },
    NotFound,
    Ambiguous,
    Failed { reason: String },
    Unsupported { reason: String },
}

/// Capability report used by dispatch and `anoti doctor`.
#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlatformCapabilities {
    pub window_discovery: bool,
    pub active_window_probe: bool,
    pub focus: bool,
    pub precise_multi_monitor_placement: bool,
    pub native_notification: bool,
    pub global_hotkey: bool,
    pub backend: String,
}
