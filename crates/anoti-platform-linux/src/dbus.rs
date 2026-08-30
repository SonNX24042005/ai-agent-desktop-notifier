use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use anoti_core::{NotificationRequest, Urgency};
use anoti_platform::PlatformError;

const TIMEOUT: Duration = Duration::from_millis(1500);

fn call_with_timeout<T, F>(operation: &'static str, call: F) -> Result<T, PlatformError>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T, String> + Send + 'static,
{
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::Builder::new()
        .name(format!("anoti-dbus-{operation}"))
        .spawn(move || {
            let _ = sender.send(call());
        })
        .map_err(|error| PlatformError::Operation(format!("start D-Bus worker: {error}")))?;
    match receiver.recv_timeout(TIMEOUT) {
        Ok(Ok(value)) => Ok(value),
        Ok(Err(error)) => Err(PlatformError::Operation(error)),
        Err(mpsc::RecvTimeoutError::Timeout) => {
            Err(PlatformError::Timeout(format!("D-Bus {operation}")))
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => Err(PlatformError::Operation(format!(
            "D-Bus {operation} worker disconnected"
        ))),
    }
}

pub fn native_notify(request: &NotificationRequest) -> Result<(), PlatformError> {
    let app = request.app_name.clone();
    let title = request.title.clone();
    let message = request.message.clone();
    let urgency_byte: u8 = match request.urgency {
        Urgency::Low => 0,
        Urgency::Normal => 1,
        Urgency::Critical => 2,
    };
    let timeout = if request.timeout > 0 {
        i32::try_from(request.timeout.saturating_mul(1000)).unwrap_or(i32::MAX)
    } else {
        -1
    };
    let icon_name = request.resolved_icon_name().to_owned();
    let icon_path = anoti_core::resolve_icon_path(&icon_name, None);
    let app_icon = icon_path.as_ref().map_or_else(
        || {
            if icon_name == "anoti" {
                "anoti".to_owned()
            } else {
                format!("anoti-{icon_name}")
            }
        },
        |path| path.to_string_lossy().into_owned(),
    );

    call_with_timeout("native-notification", move || {
        let connection =
            zbus::blocking::Connection::session().map_err(|error| error.to_string())?;
        let proxy = zbus::blocking::Proxy::new(
            &connection,
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
        )
        .map_err(|error| error.to_string())?;
        let mut hints = std::collections::HashMap::<String, zbus::zvariant::OwnedValue>::new();
        if let Ok(urgency_val) = zbus::zvariant::Value::U8(urgency_byte).try_into() {
            hints.insert("urgency".to_owned(), urgency_val);
        }
        if let Some(path) = icon_path.as_ref() {
            let path_str = path.to_string_lossy().into_owned();
            if let Ok(val) = zbus::zvariant::Value::Str(path_str.into()).try_into() {
                hints.insert("image-path".to_owned(), val);
            }
        }
        let _: u32 = proxy
            .call(
                "Notify",
                &(
                    app,
                    0_u32,
                    app_icon,
                    title,
                    message,
                    Vec::<String>::new(),
                    hints,
                    timeout,
                ),
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    })
}
