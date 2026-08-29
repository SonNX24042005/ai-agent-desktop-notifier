//! Interfaces implemented by Linux and Windows runtime backends.

use anoti_core::{FocusOutcome, NotificationRequest, PlatformCapabilities, WindowIdentity};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum PlatformError {
    #[error("platform capability is unavailable: {0}")]
    Unsupported(String),
    #[error("platform operation timed out: {0}")]
    Timeout(String),
    #[error("platform operation failed: {0}")]
    Operation(String),
}

#[derive(Debug, Clone, Default)]
pub struct IdentityQuery {
    pub window_id: String,
    pub window_instance_id: String,
    pub window_pid: u32,
    pub process_start_time: u64,
    pub caller_pid: u32,
    pub caller_pid_chain: Vec<u32>,
    pub project_hint: String,
    pub session_id: String,
    pub app_hint: String,
    pub title_fingerprint: String,
    pub caller_tty: String,
    pub terminal_screen: String,
    pub generation: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WindowTarget {
    pub id: String,
    pub instance_id: String,
    pub pid: u32,
    pub process_start_time: u64,
    pub title: String,
    pub app_id: String,
    pub generation: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OverlayOutcome {
    pub displayed: bool,
    pub dismissed: bool,
    pub focused: bool,
}

pub trait PlatformBackend: Send + Sync {
    fn capabilities(&self) -> PlatformCapabilities;
    fn capture_identity(
        &self,
        query: &IdentityQuery,
    ) -> Result<Option<WindowIdentity>, PlatformError>;
    fn resolve_target(&self, query: &IdentityQuery) -> Result<Option<WindowTarget>, PlatformError>;
    fn is_active(
        &self,
        target: &WindowTarget,
        query: &IdentityQuery,
    ) -> Result<bool, PlatformError>;
    fn focus(
        &self,
        target: &WindowTarget,
        query: &IdentityQuery,
    ) -> Result<FocusOutcome, PlatformError>;
    fn show_overlay(&self, request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError>;
    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError>;
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError>;
}
