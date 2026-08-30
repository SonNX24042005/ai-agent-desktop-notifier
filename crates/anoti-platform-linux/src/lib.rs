//! Linux backend using the freedesktop desktop notification service.

use anoti_core::{NotificationRequest, PlatformCapabilities};
use anoti_platform::{PlatformBackend, PlatformError};

#[cfg(target_os = "linux")]
mod dbus;

pub trait LinuxApi: Send + Sync {
    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError>;
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError>;
}

#[derive(Debug, Default)]
pub struct SystemLinuxApi;

#[cfg(target_os = "linux")]
impl LinuxApi for SystemLinuxApi {
    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        dbus::native_notify(request)
    }

    fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
        use std::path::Path;
        use std::process::{Command, Stdio};
        use std::time::Duration;
        use wait_timeout::ChildExt;

        if sound.trim().is_empty() {
            return Ok(());
        }
        if !Path::new(sound).is_file() {
            return Err(PlatformError::Operation(
                "notification sound is not a regular file".to_owned(),
            ));
        }
        for player in [
            "/usr/bin/paplay",
            "/usr/bin/pw-play",
            "/usr/bin/canberra-gtk-play",
            "/usr/bin/aplay",
        ] {
            if !Path::new(player).is_file() {
                continue;
            }
            let mut command = Command::new(player);
            if player.ends_with("canberra-gtk-play") {
                command.arg("-f");
            }
            let mut child = command
                .arg(sound)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|error| {
                    PlatformError::Operation(format!("start sound player: {error}"))
                })?;
            if child
                .wait_timeout(Duration::from_secs(3))
                .map_err(|error| {
                    PlatformError::Operation(format!("wait for sound player: {error}"))
                })?
                .is_none()
            {
                let _ = child.kill();
                let _ = child.wait();
                return Err(PlatformError::Timeout("sound playback".to_owned()));
            }
            return Ok(());
        }
        Err(PlatformError::Unsupported(
            "no supported Linux sound player is installed".to_owned(),
        ))
    }
}

#[cfg(not(target_os = "linux"))]
impl LinuxApi for SystemLinuxApi {
    fn native_notify(&self, _request: &NotificationRequest) -> Result<(), PlatformError> {
        Err(PlatformError::Unsupported(
            "Linux backend is not available on this target".to_owned(),
        ))
    }
    fn play_sound(&self, _sound: &str) -> Result<(), PlatformError> {
        Err(PlatformError::Unsupported(
            "Linux backend is not available on this target".to_owned(),
        ))
    }
}

#[derive(Debug)]
pub struct LinuxBackend<A = SystemLinuxApi> {
    api: A,
}

impl Default for LinuxBackend<SystemLinuxApi> {
    fn default() -> Self {
        Self::new(SystemLinuxApi)
    }
}

impl<A> LinuxBackend<A> {
    #[must_use]
    pub const fn new(api: A) -> Self {
        Self { api }
    }
}

impl<A: LinuxApi> PlatformBackend for LinuxBackend<A> {
    fn capabilities(&self) -> PlatformCapabilities {
        PlatformCapabilities {
            native_notification: true,
            backend: "freedesktop-dbus".to_owned(),
        }
    }

    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        self.api.native_notify(request)
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
    struct MockLinuxApi {
        last_request: Mutex<Option<NotificationRequest>>,
        last_sound: Mutex<Option<String>>,
    }

    impl LinuxApi for MockLinuxApi {
        fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
            *self.last_request.lock().unwrap() = Some(request.clone());
            Ok(())
        }
        fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
            *self.last_sound.lock().unwrap() = Some(sound.to_owned());
            Ok(())
        }
    }

    #[test]
    fn linux_backend_reports_freedesktop_capabilities() {
        let backend = LinuxBackend::new(MockLinuxApi::default());
        let caps = backend.capabilities();
        assert!(caps.native_notification);
        assert_eq!(caps.backend, "freedesktop-dbus");
    }

    #[test]
    fn linux_backend_forwards_notification_and_sound() {
        let api = MockLinuxApi::default();
        let backend = LinuxBackend::new(api);
        let request = NotificationRequest {
            app_name: "Claude Code".to_owned(),
            title: "Câu hỏi".to_owned(),
            message: "Bạn có muốn tiếp tục?".to_owned(),
            sound: "/usr/share/sounds/stereo/bell.oga".to_owned(),
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
            Some("/usr/share/sounds/stereo/bell.oga")
        );
    }
}
