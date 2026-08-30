//! Interfaces implemented by Linux and Windows runtime backends.

use anoti_core::{NotificationRequest, PlatformCapabilities};
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

pub trait PlatformBackend: Send + Sync {
    fn capabilities(&self) -> PlatformCapabilities;
    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError>;
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError>;
}
