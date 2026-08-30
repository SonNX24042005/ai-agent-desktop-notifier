//! Windows toast notification and audio backend.

use anoti_core::{NotificationRequest, PlatformCapabilities};
use anoti_platform::{PlatformBackend, PlatformError};

pub mod xml;

#[cfg(windows)]
mod native;

pub const APP_USER_MODEL_ID: &str = "io.github.sonnx24042005.AiAgentNotifier";

pub trait WindowsApi: Send + Sync {
    fn show_toast(&self, request: &NotificationRequest) -> Result<(), PlatformError>;
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError>;
}

#[derive(Debug, Default)]
pub struct SystemWindowsApi;

#[cfg(windows)]
impl WindowsApi for SystemWindowsApi {
    fn show_toast(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        native::show_toast(request)
    }

    fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
        native::play_sound(sound)
    }
}

#[cfg(not(windows))]
impl WindowsApi for SystemWindowsApi {
    fn show_toast(&self, _request: &NotificationRequest) -> Result<(), PlatformError> {
        Err(PlatformError::Unsupported(
            "Windows backend is not available on this target".to_owned(),
        ))
    }
    fn play_sound(&self, _sound: &str) -> Result<(), PlatformError> {
        Err(PlatformError::Unsupported(
            "Windows backend is not available on this target".to_owned(),
        ))
    }
}

#[derive(Debug)]
pub struct WindowsBackend<A = SystemWindowsApi> {
    api: A,
}

impl Default for WindowsBackend<SystemWindowsApi> {
    fn default() -> Self {
        Self::new(SystemWindowsApi)
    }
}

impl<A> WindowsBackend<A> {
    #[must_use]
    pub const fn new(api: A) -> Self {
        Self { api }
    }
}

impl<A: WindowsApi> PlatformBackend for WindowsBackend<A> {
    fn capabilities(&self) -> PlatformCapabilities {
        PlatformCapabilities {
            native_notification: true,
            backend: "win32-toast".to_owned(),
        }
    }

    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        self.api.show_toast(request)
    }

    fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
        self.api.play_sound(sound)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;

    #[derive(Debug, Default)]
    struct MockWindowsApi {
        last_request: Mutex<Option<NotificationRequest>>,
        last_sound: Mutex<Option<String>>,
    }

    impl WindowsApi for MockWindowsApi {
        fn show_toast(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
            *self.last_request.lock().unwrap() = Some(request.clone());
            Ok(())
        }
        fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
            *self.last_sound.lock().unwrap() = Some(sound.to_owned());
            Ok(())
        }
    }

    #[test]
    fn windows_backend_reports_capabilities() {
        let backend = WindowsBackend::new(MockWindowsApi::default());
        let caps = backend.capabilities();
        assert!(caps.native_notification);
        assert_eq!(caps.backend, "win32-toast");
    }

    #[test]
    fn windows_backend_forwards_toast_and_sound() {
        let api = MockWindowsApi::default();
        let backend = WindowsBackend::new(api);
        let request = NotificationRequest {
            app_name: "OpenAI Codex".to_owned(),
            title: "Codex: Cần cấp quyền".to_owned(),
            message: "Codex đang chờ cấp quyền".to_owned(),
            sound: "C:\\Windows\\Media\\notify.wav".to_owned(),
            ..NotificationRequest::default()
        };
        backend.native_notify(&request).unwrap();
        backend.play_sound(&request.sound).unwrap();
        assert_eq!(
            backend.api.last_request.lock().unwrap().as_ref().unwrap(),
            &request
        );
        assert_eq!(
            backend.api.last_sound.lock().unwrap().as_deref(),
            Some("C:\\Windows\\Media\\notify.wav")
        );
    }
}
