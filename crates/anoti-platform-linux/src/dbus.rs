use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use anoti_platform::{IdentityQuery, PlatformError};

use crate::{AdapterCall, CapturedWindow};
use anoti_core::NotificationRequest;

const DESTINATION: &str = "io.github.sonnx24042005.AiAgentNotifier";
const OBJECT_PATH: &str = "/io/github/sonnx24042005/AiAgentNotifier";
const INTERFACE: &str = "io.github.sonnx24042005.AiAgentNotifier";
const TIMEOUT: Duration = Duration::from_millis(750);

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

fn proxy() -> Result<(zbus::blocking::Connection, zbus::blocking::Proxy<'static>), String> {
    let connection = zbus::blocking::Connection::session().map_err(|error| error.to_string())?;
    let proxy = zbus::blocking::Proxy::new_owned(
        connection.clone(),
        DESTINATION.to_owned(),
        OBJECT_PATH.to_owned(),
        INTERFACE.to_owned(),
    )
    .map_err(|error| error.to_string())?;
    Ok((connection, proxy))
}

fn unavailable(error: &PlatformError) -> bool {
    let text = error.to_string().to_ascii_lowercase();
    [
        "serviceunknown",
        "namehasnoowner",
        "unknownmethod",
        "unknownobject",
        "unknowninterface",
    ]
    .iter()
    .any(|needle| text.contains(needle))
}

pub fn capture(query: &IdentityQuery) -> AdapterCall<Option<CapturedWindow>> {
    let chain = query.caller_pid_chain.clone();
    let project = query.project_hint.chars().take(300).collect::<String>();
    let app = query.app_hint.chars().take(100).collect::<String>();
    map_adapter_result(call_with_timeout("capture", move || {
        let (_connection, proxy) = proxy()?;
        let (matched, id, pid, title, app_id): (bool, String, u32, String, String) = proxy
            .call("CaptureActiveWindowV3", &(chain, project, app))
            .map_err(|error| error.to_string())?;
        Ok(matched.then_some(CapturedWindow {
            id,
            pid,
            title,
            app_id,
        }))
    }))
}

pub fn capture_title_marker(
    query: &IdentityQuery,
    marker: &str,
) -> AdapterCall<Option<CapturedWindow>> {
    let query = query.clone();
    let marker = marker.chars().take(100).collect::<String>();
    map_adapter_result(call_with_timeout("capture-title-marker", move || {
        let (_connection, proxy) = proxy()?;
        let version = proxy
            .call::<_, _, u32>("GetContractVersion", &())
            .unwrap_or(1);
        if version >= 5 {
            let (matched, id, pid, title, app_id): (bool, String, u32, String, String) = proxy
                .call("CaptureWindowByTitleV5", &(marker,))
                .map_err(|error| error.to_string())?;
            return Ok(matched.then_some(CapturedWindow {
                id,
                pid,
                title,
                app_id,
            }));
        }
        if version < 3 {
            return Err("title-marker capture requires GNOME extension contract v3".to_owned());
        }

        let focused: bool = if version >= 4 {
            proxy
                .call(
                    "FocusWindowV4",
                    &(
                        "",
                        0_u32,
                        query.caller_pid_chain.clone(),
                        marker.clone(),
                        marker.clone(),
                        query.app_hint.clone(),
                    ),
                )
                .map_err(|error| error.to_string())?
        } else {
            proxy
                .call(
                    "FocusWindowV3",
                    &(
                        query.caller_pid_chain.clone(),
                        marker.clone(),
                        marker.clone(),
                        query.app_hint.clone(),
                    ),
                )
                .map_err(|error| error.to_string())?
        };
        if !focused {
            return Ok(None);
        }
        thread::sleep(Duration::from_millis(30));
        let (matched, id, pid, title, app_id): (bool, String, u32, String, String) = proxy
            .call(
                "CaptureActiveWindowV3",
                &(query.caller_pid_chain, marker.clone(), query.app_hint),
            )
            .map_err(|error| error.to_string())?;
        Ok(matched.then_some(CapturedWindow {
            id,
            pid,
            title,
            app_id,
        }))
    }))
}

pub fn focus(query: &IdentityQuery) -> AdapterCall<bool> {
    call_bool(
        "focus",
        "FocusWindowV4",
        "FocusWindowV3",
        "FocusWindow",
        query,
    )
}

pub fn is_active(query: &IdentityQuery) -> AdapterCall<bool> {
    call_bool(
        "active-probe",
        "IsWindowActiveV4",
        "IsWindowActiveV3",
        "IsWindowActive",
        query,
    )
}

fn call_bool(
    operation: &'static str,
    v4_method: &'static str,
    v3_method: &'static str,
    legacy_method: &'static str,
    query: &IdentityQuery,
) -> AdapterCall<bool> {
    let query = query.clone();
    map_adapter_result(call_with_timeout(operation, move || {
        let (_connection, proxy) = proxy()?;
        let version = proxy
            .call::<_, _, u32>("GetContractVersion", &())
            .unwrap_or(1);
        if version >= 4 {
            proxy
                .call(
                    v4_method,
                    &(
                        query.window_id,
                        query.window_pid,
                        query.caller_pid_chain,
                        query.project_hint,
                        query.title_fingerprint,
                        query.app_hint,
                    ),
                )
                .map_err(|error| error.to_string())
        } else if version >= 3 {
            proxy
                .call(
                    v3_method,
                    &(
                        query.caller_pid_chain,
                        query.project_hint,
                        query.title_fingerprint,
                        query.app_hint,
                    ),
                )
                .map_err(|error| error.to_string())
        } else if legacy_method == "FocusWindow" {
            proxy
                .call(
                    legacy_method,
                    &(
                        query.caller_pid,
                        query.project_hint,
                        query.title_fingerprint,
                    ),
                )
                .map_err(|error| error.to_string())
        } else {
            proxy
                .call(
                    legacy_method,
                    &(
                        query.caller_pid,
                        query.project_hint,
                        query.title_fingerprint,
                        query.app_hint,
                    ),
                )
                .map_err(|error| error.to_string())
        }
    }))
}

fn map_adapter_result<T>(result: Result<T, PlatformError>) -> AdapterCall<T> {
    match result {
        Ok(value) => AdapterCall::Available(value),
        Err(error) if unavailable(&error) => AdapterCall::Unavailable,
        Err(error) => AdapterCall::Failed(error.to_string()),
    }
}

pub fn native_notify(request: &NotificationRequest) -> Result<(), PlatformError> {
    let app = request.app_name.clone();
    let title = request.title.clone();
    let message = request.message.clone();
    let timeout = i32::try_from(request.timeout.saturating_mul(1000)).unwrap_or(i32::MAX);
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
        let hints = std::collections::HashMap::<String, zbus::zvariant::OwnedValue>::new();
        let _: u32 = proxy
            .call(
                "Notify",
                &(
                    app,
                    0_u32,
                    "",
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
