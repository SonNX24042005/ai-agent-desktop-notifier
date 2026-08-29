use anoti_platform::PlatformError;
use x11rb::connection::Connection;
use x11rb::protocol::randr::ConnectionExt as RandrConnectionExt;
use x11rb::protocol::xproto::{
    Atom, AtomEnum, ClientMessageEvent, ConnectionExt, EventMask, MapState, Window,
};
use x11rb::rust_connection::RustConnection;

use crate::{LinuxMonitor, LinuxWindow};

fn x11_error(context: &str, error: impl std::fmt::Display) -> PlatformError {
    PlatformError::Operation(format!("X11 {context}: {error}"))
}

struct Client {
    connection: RustConnection,
    root: Window,
}

impl Client {
    fn connect() -> Result<Self, PlatformError> {
        let (connection, screen) =
            x11rb::connect(None).map_err(|error| x11_error("connect", error))?;
        let root = connection.setup().roots[screen].root;
        Ok(Self { connection, root })
    }

    fn atom(&self, name: &[u8]) -> Result<Atom, PlatformError> {
        self.connection
            .intern_atom(false, name)
            .map_err(|error| x11_error("intern atom request", error))?
            .reply()
            .map(|reply| reply.atom)
            .map_err(|error| x11_error("intern atom reply", error))
    }

    fn u32_property(&self, window: Window, atom: Atom) -> Result<Vec<u32>, PlatformError> {
        self.connection
            .get_property(false, window, atom, AtomEnum::CARDINAL, 0, u32::MAX)
            .map_err(|error| x11_error("property request", error))?
            .reply()
            .map_err(|error| x11_error("property reply", error))?
            .value32()
            .map_or_else(Vec::new, Iterator::collect)
            .pipe(Ok)
    }

    fn bytes_property(
        &self,
        window: Window,
        atom: Atom,
        type_atom: Atom,
    ) -> Result<Vec<u8>, PlatformError> {
        self.connection
            .get_property(false, window, atom, type_atom, 0, u32::MAX)
            .map_err(|error| x11_error("text property request", error))?
            .reply()
            .map(|reply| reply.value)
            .map_err(|error| x11_error("text property reply", error))
    }
}

trait Pipe: Sized {
    fn pipe<T>(self, function: impl FnOnce(Self) -> T) -> T {
        function(self)
    }
}
impl<T> Pipe for T {}

pub fn active_window() -> Result<Option<u64>, PlatformError> {
    let client = Client::connect()?;
    let atom = client.atom(b"_NET_ACTIVE_WINDOW")?;
    Ok(client
        .connection
        .get_property(false, client.root, atom, AtomEnum::WINDOW, 0, 1)
        .map_err(|error| x11_error("active window request", error))?
        .reply()
        .map_err(|error| x11_error("active window reply", error))?
        .value32()
        .and_then(|mut values| values.next())
        .filter(|window| *window != 0)
        .map(u64::from))
}

pub fn enumerate_windows() -> Result<Vec<LinuxWindow>, PlatformError> {
    let client = Client::connect()?;
    let pid_atom = client.atom(b"_NET_WM_PID")?;
    let name_atom = client.atom(b"_NET_WM_NAME")?;
    let utf8_atom = client.atom(b"UTF8_STRING")?;
    let class_atom = AtomEnum::WM_CLASS.into();
    let desktop_atom = client.atom(b"_NET_WM_DESKTOP")?;
    let client_list_atom = client.atom(b"_NET_CLIENT_LIST_STACKING")?;
    let mut children = client
        .connection
        .get_property(
            false,
            client.root,
            client_list_atom,
            AtomEnum::WINDOW,
            0,
            u32::MAX,
        )
        .map_err(|error| x11_error("EWMH client list request", error))?
        .reply()
        .map_err(|error| x11_error("EWMH client list reply", error))?
        .value32()
        .map_or_else(Vec::new, Iterator::collect);
    if children.is_empty() {
        children = client
            .connection
            .query_tree(client.root)
            .map_err(|error| x11_error("query tree request", error))?
            .reply()
            .map_err(|error| x11_error("query tree reply", error))?
            .children;
    }
    let mut windows = Vec::new();
    for window in children {
        let attributes = match client.connection.get_window_attributes(window) {
            Ok(cookie) => match cookie.reply() {
                Ok(attributes) => attributes,
                Err(_) => continue,
            },
            Err(_) => continue,
        };
        if attributes.map_state != MapState::VIEWABLE || attributes.override_redirect {
            continue;
        }
        let pid = client
            .u32_property(window, pid_atom)
            .ok()
            .and_then(|values| values.first().copied())
            .unwrap_or(0);
        let title = client
            .bytes_property(window, name_atom, utf8_atom)
            .ok()
            .map(|value| String::from_utf8_lossy(&value).into_owned())
            .unwrap_or_default();
        let class_name = client
            .bytes_property(window, class_atom, AtomEnum::STRING.into())
            .ok()
            .map(|value| {
                value
                    .split(|byte| *byte == 0)
                    .filter(|part| !part.is_empty())
                    .map(|part| String::from_utf8_lossy(part))
                    .collect::<Vec<_>>()
                    .join(" ")
            })
            .unwrap_or_default();
        let desktop = client
            .u32_property(window, desktop_atom)
            .ok()
            .and_then(|values| values.first().copied());
        windows.push(LinuxWindow {
            id: u64::from(window),
            pid,
            title,
            app_id: class_name,
            desktop,
        });
    }
    Ok(windows)
}

pub fn request_focus(window: u64) -> Result<(), PlatformError> {
    let window = u32::try_from(window)
        .map_err(|_| PlatformError::Operation("XID exceeds 32-bit X11 range".to_owned()))?;
    let client = Client::connect()?;
    let active_atom = client.atom(b"_NET_ACTIVE_WINDOW")?;
    let window_desktop_atom = client.atom(b"_NET_WM_DESKTOP")?;
    let current_desktop_atom = client.atom(b"_NET_CURRENT_DESKTOP")?;
    let target_desktop = client
        .u32_property(window, window_desktop_atom)?
        .first()
        .copied();
    let current_desktop = client
        .u32_property(client.root, current_desktop_atom)?
        .first()
        .copied();
    if let Some(target_desktop) = target_desktop
        && target_desktop != u32::MAX
        && Some(target_desktop) != current_desktop
    {
        let desktop_event = ClientMessageEvent::new(
            32,
            client.root,
            current_desktop_atom,
            [target_desktop, 0, 0, 0, 0],
        );
        client
            .connection
            .send_event(
                false,
                client.root,
                EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY,
                desktop_event,
            )
            .map_err(|error| x11_error("workspace event", error))?;
    }
    // Source indication 2 means pager/desktop tool; timestamp 0 means CURRENT_TIME.
    let active_event = ClientMessageEvent::new(32, window, active_atom, [2_u32, 0, 0, 0, 0]);
    client
        .connection
        .send_event(
            false,
            client.root,
            EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY,
            active_event,
        )
        .map_err(|error| x11_error("active window event", error))?;
    client
        .connection
        .flush()
        .map_err(|error| x11_error("flush", error))
}

pub fn enumerate_monitors() -> Result<Vec<LinuxMonitor>, PlatformError> {
    let client = Client::connect()?;
    let monitors = client
        .connection
        .randr_get_monitors(client.root, true)
        .map_err(|error| x11_error("RandR monitor request", error))?
        .reply()
        .map_err(|error| x11_error("RandR monitor reply", error))?
        .monitors;
    let workarea_atom = client.atom(b"_NET_WORKAREA")?;
    let desktop_atom = client.atom(b"_NET_CURRENT_DESKTOP")?;
    let desktop = client
        .u32_property(client.root, desktop_atom)?
        .first()
        .copied()
        .unwrap_or(0) as usize;
    let workareas = client.u32_property(client.root, workarea_atom)?;
    let work = workareas.get(desktop.saturating_mul(4)..desktop.saturating_mul(4) + 4);
    Ok(monitors
        .into_iter()
        .map(|monitor| {
            let left = i32::from(monitor.x);
            let top = i32::from(monitor.y);
            let right = left + i32::from(monitor.width);
            let bottom = top + i32::from(monitor.height);
            let (work_left, work_top, work_right, work_bottom) =
                work.map_or((left, top, right, bottom), |work| {
                    let global_left = i32::try_from(work[0]).unwrap_or(i32::MAX);
                    let global_top = i32::try_from(work[1]).unwrap_or(i32::MAX);
                    let global_right =
                        global_left.saturating_add(i32::try_from(work[2]).unwrap_or(i32::MAX));
                    let global_bottom =
                        global_top.saturating_add(i32::try_from(work[3]).unwrap_or(i32::MAX));
                    (
                        left.max(global_left),
                        top.max(global_top),
                        right.min(global_right),
                        bottom.min(global_bottom),
                    )
                });
            let dpi = if monitor.width_in_millimeters > 0 {
                let pixels = u64::from(monitor.width);
                let millimeters = u64::from(monitor.width_in_millimeters);
                let rounded = pixels
                    .saturating_mul(254)
                    .saturating_add(millimeters.saturating_mul(5))
                    / millimeters.saturating_mul(10);
                u32::try_from(rounded).unwrap_or(u32::MAX)
            } else {
                96
            };
            LinuxMonitor {
                left,
                top,
                right,
                bottom,
                work_left,
                work_top,
                work_right,
                work_bottom,
                dpi,
                primary: monitor.primary,
            }
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use x11rb::protocol::xproto::ClientMessageData;

    use super::*;

    #[test]
    fn ewmh_active_message_uses_pager_source_and_current_time() {
        let event = ClientMessageEvent::new(32, 42, 99_u32, [2_u32, 0, 0, 0, 0]);
        assert_eq!(event.format, 32);
        assert_eq!(event.window, 42);
        assert_eq!(event.data.as_data32(), [2, 0, 0, 0, 0]);
        let _: ClientMessageData = [2_u32, 0, 0, 0, 0].into();
    }
}
