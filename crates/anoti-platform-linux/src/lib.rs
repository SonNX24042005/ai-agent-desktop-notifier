//! Linux backend with separate X11/XWayland and GNOME Wayland dispatch.

use std::collections::HashSet;
use std::fs::OpenOptions;
use std::io::Write;
use std::thread;
use std::time::{Duration, Instant};

use anoti_core::{
    CandidateEvidence, FocusOutcome, NotificationRequest, PlatformCapabilities, WindowCandidate,
    WindowIdentity, normalize_pid_chain, resolve_candidate, titles_compatible,
};
use anoti_platform::{IdentityQuery, OverlayOutcome, PlatformBackend, PlatformError, WindowTarget};

#[cfg(target_os = "linux")]
mod atspi;
#[cfg(target_os = "linux")]
mod dbus;
#[cfg(target_os = "linux")]
mod process;
#[cfg(target_os = "linux")]
mod ui;
#[cfg(target_os = "linux")]
mod x11;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum SessionKind {
    X11,
    XWayland,
    GnomeWayland,
    #[default]
    Unsupported,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LinuxWindow {
    pub id: u64,
    pub pid: u32,
    pub title: String,
    pub app_id: String,
    pub desktop: Option<u32>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CapturedWindow {
    pub id: String,
    pub pid: u32,
    pub title: String,
    pub app_id: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LinuxMonitor {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
    pub work_left: i32,
    pub work_top: i32,
    pub work_right: i32,
    pub work_bottom: i32,
    pub dpi: u32,
    pub primary: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AdapterCall<T> {
    Available(T),
    Unavailable,
    Failed(String),
}

pub trait LinuxApi: Send + Sync {
    fn session_kind(&self) -> SessionKind;
    fn process_ancestry(&self, pid: u32) -> Vec<u32>;
    fn x11_windows(&self) -> Result<Vec<LinuxWindow>, PlatformError>;
    fn x11_active_window(&self) -> Result<Option<u64>, PlatformError>;
    fn x11_request_focus(&self, window: u64) -> Result<(), PlatformError>;
    fn x11_monitors(&self) -> Result<Vec<LinuxMonitor>, PlatformError>;
    fn gnome_capture(&self, query: &IdentityQuery) -> AdapterCall<Option<CapturedWindow>>;
    fn gnome_capture_title_marker(
        &self,
        query: &IdentityQuery,
        marker: &str,
    ) -> AdapterCall<Option<CapturedWindow>>;
    fn gnome_focus(&self, query: &IdentityQuery) -> AdapterCall<bool>;
    fn gnome_is_active(&self, query: &IdentityQuery) -> AdapterCall<bool>;
    fn atspi_is_active(&self, query: &IdentityQuery) -> AdapterCall<bool>;
    fn show_overlay(&self, request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError>;
    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError>;
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError>;
}

#[derive(Debug, Default)]
pub struct SystemLinuxApi;

#[cfg(target_os = "linux")]
impl LinuxApi for SystemLinuxApi {
    fn session_kind(&self) -> SessionKind {
        detect_session_kind()
    }
    fn process_ancestry(&self, pid: u32) -> Vec<u32> {
        process::process_ancestry(pid)
    }
    fn x11_windows(&self) -> Result<Vec<LinuxWindow>, PlatformError> {
        x11::enumerate_windows()
    }
    fn x11_active_window(&self) -> Result<Option<u64>, PlatformError> {
        x11::active_window()
    }
    fn x11_request_focus(&self, window: u64) -> Result<(), PlatformError> {
        x11::request_focus(window)
    }
    fn x11_monitors(&self) -> Result<Vec<LinuxMonitor>, PlatformError> {
        x11::enumerate_monitors()
    }
    fn gnome_capture(&self, query: &IdentityQuery) -> AdapterCall<Option<CapturedWindow>> {
        dbus::capture(query)
    }
    fn gnome_capture_title_marker(
        &self,
        query: &IdentityQuery,
        marker: &str,
    ) -> AdapterCall<Option<CapturedWindow>> {
        dbus::capture_title_marker(query, marker)
    }
    fn gnome_focus(&self, query: &IdentityQuery) -> AdapterCall<bool> {
        dbus::focus(query)
    }
    fn gnome_is_active(&self, query: &IdentityQuery) -> AdapterCall<bool> {
        dbus::is_active(query)
    }
    fn atspi_is_active(&self, query: &IdentityQuery) -> AdapterCall<bool> {
        atspi::is_active(query)
    }
    fn show_overlay(&self, request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError> {
        use std::sync::Arc;

        let query = query_from_request(request);
        let focus_query = query.clone();
        let active_query = query;
        let focus = Arc::new(move || {
            let backend = LinuxBackend::default();
            backend
                .resolve_target(&focus_query)
                .ok()
                .flatten()
                .and_then(|target| backend.focus(&target, &focus_query).ok())
                .is_some_and(|result| matches!(result, FocusOutcome::Focused { .. }))
        });
        let active = Arc::new(move || {
            let backend = LinuxBackend::default();
            backend
                .resolve_target(&active_query)
                .ok()
                .flatten()
                .and_then(|target| backend.is_active(&target, &active_query).ok())
                .unwrap_or(false)
        });
        let monitors = match detect_session_kind() {
            SessionKind::X11 | SessionKind::XWayland => self.x11_monitors().unwrap_or_default(),
            SessionKind::GnomeWayland | SessionKind::Unsupported => Vec::new(),
        };
        ui::show_overlay(request, &monitors, focus, active)
    }
    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        dbus::native_notify(request)
    }
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
        use std::path::Path;
        use std::process::{Command, Stdio};
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
    fn session_kind(&self) -> SessionKind {
        SessionKind::Unsupported
    }
    fn process_ancestry(&self, _pid: u32) -> Vec<u32> {
        Vec::new()
    }
    fn x11_windows(&self) -> Result<Vec<LinuxWindow>, PlatformError> {
        Err(unavailable())
    }
    fn x11_active_window(&self) -> Result<Option<u64>, PlatformError> {
        Err(unavailable())
    }
    fn x11_request_focus(&self, _window: u64) -> Result<(), PlatformError> {
        Err(unavailable())
    }
    fn x11_monitors(&self) -> Result<Vec<LinuxMonitor>, PlatformError> {
        Err(unavailable())
    }
    fn gnome_capture(&self, _query: &IdentityQuery) -> AdapterCall<Option<CapturedWindow>> {
        AdapterCall::Unavailable
    }
    fn gnome_capture_title_marker(
        &self,
        _query: &IdentityQuery,
        _marker: &str,
    ) -> AdapterCall<Option<CapturedWindow>> {
        AdapterCall::Unavailable
    }
    fn gnome_focus(&self, _query: &IdentityQuery) -> AdapterCall<bool> {
        AdapterCall::Unavailable
    }
    fn gnome_is_active(&self, _query: &IdentityQuery) -> AdapterCall<bool> {
        AdapterCall::Unavailable
    }
    fn atspi_is_active(&self, _query: &IdentityQuery) -> AdapterCall<bool> {
        AdapterCall::Unavailable
    }
    fn show_overlay(
        &self,
        _request: &NotificationRequest,
    ) -> Result<OverlayOutcome, PlatformError> {
        Err(unavailable())
    }
    fn native_notify(&self, _request: &NotificationRequest) -> Result<(), PlatformError> {
        Err(unavailable())
    }
    fn play_sound(&self, _sound: &str) -> Result<(), PlatformError> {
        Err(unavailable())
    }
}

#[cfg(not(target_os = "linux"))]
fn unavailable() -> PlatformError {
    PlatformError::Unsupported("Linux backend is not available on this target".to_owned())
}

#[cfg(target_os = "linux")]
fn detect_session_kind() -> SessionKind {
    let session = std::env::var("XDG_SESSION_TYPE")
        .unwrap_or_default()
        .to_ascii_lowercase();
    let desktop = std::env::var("XDG_CURRENT_DESKTOP")
        .unwrap_or_default()
        .to_ascii_lowercase();
    match session.as_str() {
        "x11" => SessionKind::X11,
        "wayland" if desktop.split(':').any(|part| part == "gnome") => SessionKind::GnomeWayland,
        // A DISPLAY inside Wayland only exposes XWayland clients; the wildcard
        // must not treat it as authority over native Wayland windows.
        _ => SessionKind::Unsupported,
    }
}

#[derive(Debug)]
pub struct LinuxBackend<A = SystemLinuxApi> {
    api: A,
    focus_timeout: Duration,
}

impl Default for LinuxBackend<SystemLinuxApi> {
    fn default() -> Self {
        Self::new(SystemLinuxApi)
    }
}

impl<A> LinuxBackend<A> {
    #[must_use]
    pub const fn new(api: A) -> Self {
        Self {
            api,
            focus_timeout: Duration::from_millis(750),
        }
    }
    #[must_use]
    pub const fn with_focus_timeout(mut self, timeout: Duration) -> Self {
        self.focus_timeout = timeout;
        self
    }
}

impl<A: LinuxApi> LinuxBackend<A> {
    fn pid_chain(&self, query: &IdentityQuery) -> Vec<u32> {
        normalize_pid_chain(
            query
                .caller_pid_chain
                .iter()
                .copied()
                .chain(self.api.process_ancestry(query.caller_pid)),
            query.caller_pid,
        )
    }

    fn x11_inventory(&self, query: &IdentityQuery) -> Result<Vec<WindowCandidate>, PlatformError> {
        let pids = self.pid_chain(query).into_iter().collect::<HashSet<_>>();
        let direct = parse_xid_optional(&query.window_id)?;
        Ok(self
            .api
            .x11_windows()?
            .into_iter()
            .map(|window| {
                let project_match = contains_hint(&window.title, &query.project_hint);
                let title_match = !query.title_fingerprint.trim().is_empty()
                    && titles_compatible(&query.title_fingerprint, &window.title);
                let app_match = app_matches(&window.app_id, &query.app_hint);
                WindowCandidate {
                    id: window.id.to_string(),
                    pid: window.pid,
                    title: window.title,
                    app_id: window.app_id.clone(),
                    evidence: CandidateEvidence {
                        session_match: !query.session_id.is_empty() && direct == Some(window.id),
                        pid_match: pids.contains(&window.pid),
                        project_match,
                        direct_id_match: direct == Some(window.id),
                        app_match,
                        title_match,
                        stale: false,
                        developer_window: is_developer_window(&window.app_id),
                    },
                }
            })
            .collect())
    }

    fn validate_x11_target(&self, target: &WindowTarget) -> Result<bool, PlatformError> {
        let xid = parse_xid(&target.id)?;
        Ok(self.api.x11_windows()?.into_iter().any(|window| {
            window.id == xid
                && (target.pid == 0 || window.pid == target.pid)
                && titles_compatible(&target.title, &window.title)
        }))
    }

    fn gnome_query(&self, query: &IdentityQuery) -> IdentityQuery {
        let mut normalized = query.clone();
        normalized.caller_pid_chain = self.pid_chain(query);
        normalized
    }

    fn capture_gnome_terminal_by_title_marker(
        &self,
        query: &IdentityQuery,
    ) -> AdapterCall<Option<CapturedWindow>> {
        if !query.caller_tty.starts_with("/dev/pts/")
            || !query
                .terminal_screen
                .starts_with("/org/gnome/Terminal/screen/")
        {
            return AdapterCall::Unavailable;
        }
        let marker = format!("anoti-capture-{}-{}", std::process::id(), query.caller_pid);
        let Ok(mut terminal) = OpenOptions::new().write(true).open(&query.caller_tty) else {
            return AdapterCall::Failed("open caller TTY for exact capture".to_owned());
        };
        let set_title = format!("\u{1b}[22;0t\u{1b}]0;{marker}\u{7}");
        if terminal.write_all(set_title.as_bytes()).is_err() || terminal.flush().is_err() {
            return AdapterCall::Failed("write exact-capture title marker".to_owned());
        }

        thread::sleep(Duration::from_millis(40));
        let deadline = Instant::now() + Duration::from_millis(300);
        let result = loop {
            match self.api.gnome_capture_title_marker(query, &marker) {
                AdapterCall::Available(None) if Instant::now() < deadline => {
                    thread::sleep(Duration::from_millis(25));
                }
                result => break result,
            }
        };
        let _ = terminal.write_all(b"\x1b[23;0t");
        let _ = terminal.flush();
        result
    }
}

impl<A: LinuxApi> PlatformBackend for LinuxBackend<A> {
    fn capabilities(&self) -> PlatformCapabilities {
        match self.api.session_kind() {
            SessionKind::X11 => capabilities("x11", true),
            SessionKind::XWayland => capabilities("xwayland", true),
            SessionKind::GnomeWayland => capabilities("gnome-wayland-dbus", false),
            SessionKind::Unsupported => PlatformCapabilities {
                backend: "linux-unsupported".to_owned(),
                native_notification: true,
                ..PlatformCapabilities::default()
            },
        }
    }

    fn capture_identity(
        &self,
        query: &IdentityQuery,
    ) -> Result<Option<WindowIdentity>, PlatformError> {
        match self.api.session_kind() {
            SessionKind::X11 | SessionKind::XWayland => {
                let Some(active) = self.api.x11_active_window()? else {
                    return Ok(None);
                };
                let pids = self.pid_chain(query);
                let Some(window) = self
                    .api
                    .x11_windows()?
                    .into_iter()
                    .find(|window| window.id == active)
                else {
                    return Ok(None);
                };
                if !is_developer_window(&window.app_id) || !pids.contains(&window.pid) {
                    return Ok(None);
                }
                Ok(Some(identity_from_window(
                    query,
                    pids,
                    window.id.to_string(),
                    window.pid,
                    window.title,
                    window.app_id,
                )))
            }
            SessionKind::GnomeWayland => {
                let query = self.gnome_query(query);
                if !query.terminal_screen.is_empty() {
                    return match self.capture_gnome_terminal_by_title_marker(&query) {
                        AdapterCall::Available(Some(window)) => Ok(Some(identity_from_window(
                            &query,
                            query.caller_pid_chain.clone(),
                            window.id,
                            window.pid,
                            window.title,
                            window.app_id,
                        ))),
                        AdapterCall::Available(None) | AdapterCall::Unavailable => Ok(None),
                        AdapterCall::Failed(error) => Err(PlatformError::Operation(error)),
                    };
                }
                match self.api.gnome_capture(&query) {
                    AdapterCall::Available(Some(window)) => Ok(Some(identity_from_window(
                        &query,
                        query.caller_pid_chain.clone(),
                        window.id,
                        window.pid,
                        window.title,
                        window.app_id,
                    ))),
                    AdapterCall::Available(None) | AdapterCall::Unavailable => Ok(None),
                    AdapterCall::Failed(error) => Err(PlatformError::Operation(error)),
                }
            }
            SessionKind::Unsupported => Ok(None),
        }
    }

    fn resolve_target(&self, query: &IdentityQuery) -> Result<Option<WindowTarget>, PlatformError> {
        match self.api.session_kind() {
            SessionKind::X11 | SessionKind::XWayland => {
                resolve_candidate(&self.x11_inventory(query)?)
                    .map_err(|outcome| {
                        PlatformError::Operation(format!("identity resolution: {outcome:?}"))
                    })
                    .map(|candidate| candidate.map(candidate_to_target))
            }
            SessionKind::GnomeWayland => {
                let query = self.gnome_query(query);
                let has_identity = !query.window_id.trim().is_empty()
                    || !query.caller_pid_chain.is_empty()
                    || !query.project_hint.trim().is_empty()
                    || !query.title_fingerprint.trim().is_empty();
                Ok(has_identity.then(|| WindowTarget {
                    id: if query.window_id.trim().is_empty() {
                        "wayland:gnome".to_owned()
                    } else {
                        query.window_id
                    },
                    pid: query.caller_pid,
                    title: query.title_fingerprint,
                    app_id: query.app_hint,
                }))
            }
            SessionKind::Unsupported => Ok(None),
        }
    }

    fn is_active(
        &self,
        target: &WindowTarget,
        query: &IdentityQuery,
    ) -> Result<bool, PlatformError> {
        match self.api.session_kind() {
            SessionKind::X11 | SessionKind::XWayland => {
                if !self.validate_x11_target(target)? {
                    return Ok(false);
                }
                Ok(self.api.x11_active_window()? == Some(parse_xid(&target.id)?))
            }
            SessionKind::GnomeWayland => match self.api.gnome_is_active(&self.gnome_query(query)) {
                AdapterCall::Available(value) => Ok(value),
                AdapterCall::Unavailable => {
                    match self.api.atspi_is_active(&self.gnome_query(query)) {
                        AdapterCall::Available(value) => Ok(value),
                        AdapterCall::Unavailable => Ok(false),
                        AdapterCall::Failed(error) => Err(PlatformError::Operation(error)),
                    }
                }
                AdapterCall::Failed(error) => Err(PlatformError::Operation(error)),
            },
            SessionKind::Unsupported => Ok(false),
        }
    }

    fn focus(
        &self,
        target: &WindowTarget,
        query: &IdentityQuery,
    ) -> Result<FocusOutcome, PlatformError> {
        match self.api.session_kind() {
            SessionKind::X11 | SessionKind::XWayland => {
                if !self.validate_x11_target(target)? {
                    return Ok(FocusOutcome::NotFound);
                }
                let xid = parse_xid(&target.id)?;
                self.api.x11_request_focus(xid)?;
                let deadline = Instant::now() + self.focus_timeout;
                while Instant::now() < deadline {
                    if self.api.x11_active_window()? == Some(xid) {
                        return Ok(FocusOutcome::Focused {
                            window_id: target.id.clone(),
                        });
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                Ok(FocusOutcome::Failed {
                    reason: "active-window verification timed out".to_owned(),
                })
            }
            SessionKind::GnomeWayland => {
                let query = self.gnome_query(query);
                match self.api.gnome_focus(&query) {
                    AdapterCall::Available(true) => {
                        let deadline = Instant::now() + self.focus_timeout;
                        loop {
                            match self.api.gnome_is_active(&query) {
                                AdapterCall::Available(true) => {
                                    return Ok(FocusOutcome::Focused {
                                        window_id: target.id.clone(),
                                    });
                                }
                                AdapterCall::Available(false) => {}
                                AdapterCall::Unavailable => {
                                    return Ok(FocusOutcome::Unsupported {
                                        reason:
                                            "GNOME Shell active-window verification is unavailable"
                                                .to_owned(),
                                    });
                                }
                                AdapterCall::Failed(error) => {
                                    return Ok(FocusOutcome::Failed { reason: error });
                                }
                            }
                            if Instant::now() >= deadline {
                                return Ok(FocusOutcome::Failed {
                                    reason: "GNOME active-window verification timed out".to_owned(),
                                });
                            }
                            thread::sleep(Duration::from_millis(10));
                        }
                    }
                    AdapterCall::Available(false) => Ok(FocusOutcome::NotFound),
                    AdapterCall::Unavailable => Ok(FocusOutcome::Unsupported {
                        reason: "GNOME Shell adapter is unavailable".to_owned(),
                    }),
                    AdapterCall::Failed(error) => Ok(FocusOutcome::Failed { reason: error }),
                }
            }
            SessionKind::Unsupported => Ok(FocusOutcome::Unsupported {
                reason: "unsupported Linux compositor".to_owned(),
            }),
        }
    }

    fn show_overlay(&self, request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError> {
        self.api.show_overlay(request)
    }
    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        self.api.native_notify(request)
    }
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
        self.api.play_sound(sound)
    }
}

fn capabilities(backend: &str, placement: bool) -> PlatformCapabilities {
    PlatformCapabilities {
        window_discovery: true,
        active_window_probe: true,
        focus: true,
        precise_multi_monitor_placement: placement,
        native_notification: true,
        global_hotkey: false,
        backend: backend.to_owned(),
    }
}

#[cfg(target_os = "linux")]
fn query_from_request(request: &NotificationRequest) -> IdentityQuery {
    IdentityQuery {
        window_id: request.identity.window_id.clone(),
        window_pid: request.identity.window_pid,
        caller_pid: request.identity.caller_pid,
        caller_pid_chain: request.identity.caller_pid_chain.clone(),
        project_hint: request.identity.project_hint.clone(),
        session_id: request.identity.session_id.clone(),
        app_hint: request.identity.app_hint.clone(),
        title_fingerprint: request.identity.title_fingerprint.clone(),
        ..IdentityQuery::default()
    }
}

fn identity_from_window(
    query: &IdentityQuery,
    chain: Vec<u32>,
    id: String,
    pid: u32,
    title: String,
    app: String,
) -> WindowIdentity {
    WindowIdentity {
        window_id: id,
        window_pid: pid,
        caller_pid: query.caller_pid,
        caller_pid_chain: chain,
        project_hint: query.project_hint.clone(),
        title_fingerprint: title,
        app_hint: if app.is_empty() {
            query.app_hint.clone()
        } else {
            app
        },
        session_id: query.session_id.clone(),
    }
}

fn candidate_to_target(candidate: &WindowCandidate) -> WindowTarget {
    WindowTarget {
        id: candidate.id.clone(),
        pid: candidate.pid,
        title: candidate.title.clone(),
        app_id: candidate.app_id.clone(),
    }
}
fn contains_hint(value: &str, hint: &str) -> bool {
    !hint.trim().is_empty()
        && value
            .to_ascii_lowercase()
            .contains(&hint.trim().to_ascii_lowercase())
}
fn parse_xid_optional(value: &str) -> Result<Option<u64>, PlatformError> {
    if value.trim().is_empty() {
        Ok(None)
    } else {
        parse_xid(value).map(Some)
    }
}
fn parse_xid(value: &str) -> Result<u64, PlatformError> {
    let value = value.trim();
    value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
        .map_or_else(|| value.parse(), |hex| u64::from_str_radix(hex, 16))
        .map_err(|_| PlatformError::Operation("invalid X11 window ID".to_owned()))
}
fn is_developer_window(app_id: &str) -> bool {
    const APPS: &[&str] = &[
        "code",
        "vscodium",
        "cursor",
        "windsurf",
        "antigravity",
        "terminal",
        "ptyxis",
        "kgx",
        "konsole",
        "alacritty",
        "kitty",
        "wezterm",
        "jetbrains",
        "codex",
        "chatgpt",
    ];
    let value = app_id.to_ascii_lowercase();
    APPS.iter().any(|app| value.contains(app))
}
fn app_matches(app_id: &str, app_hint: &str) -> bool {
    let app_id = app_id.to_ascii_lowercase();
    match app_hint.trim().to_ascii_lowercase().as_str() {
        "claude" => is_developer_window(&app_id),
        "codex" => {
            app_id.contains("codex") || app_id.contains("chatgpt") || is_developer_window(&app_id)
        }
        "antigravity" => app_id.contains("antigravity"),
        "" => false,
        hint => app_id.contains(hint),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[derive(Debug)]
    struct FakeApi {
        session: SessionKind,
        windows: Vec<LinuxWindow>,
        active: Mutex<Option<u64>>,
        focus_succeeds: bool,
        gnome_focus: AdapterCall<bool>,
        gnome_active: AdapterCall<bool>,
        atspi_active: AdapterCall<bool>,
        atspi_calls: Mutex<u32>,
    }
    impl LinuxApi for FakeApi {
        fn session_kind(&self) -> SessionKind {
            self.session
        }
        fn process_ancestry(&self, pid: u32) -> Vec<u32> {
            vec![pid, 200, 100]
        }
        fn x11_windows(&self) -> Result<Vec<LinuxWindow>, PlatformError> {
            Ok(self.windows.clone())
        }
        fn x11_active_window(&self) -> Result<Option<u64>, PlatformError> {
            Ok(*self.active.lock().unwrap())
        }
        fn x11_request_focus(&self, window: u64) -> Result<(), PlatformError> {
            if self.focus_succeeds {
                *self.active.lock().unwrap() = Some(window);
            }
            Ok(())
        }
        fn x11_monitors(&self) -> Result<Vec<LinuxMonitor>, PlatformError> {
            Ok(Vec::new())
        }
        fn gnome_capture(&self, _query: &IdentityQuery) -> AdapterCall<Option<CapturedWindow>> {
            AdapterCall::Unavailable
        }
        fn gnome_capture_title_marker(
            &self,
            _query: &IdentityQuery,
            _marker: &str,
        ) -> AdapterCall<Option<CapturedWindow>> {
            AdapterCall::Unavailable
        }
        fn gnome_focus(&self, _query: &IdentityQuery) -> AdapterCall<bool> {
            self.gnome_focus.clone()
        }
        fn gnome_is_active(&self, _query: &IdentityQuery) -> AdapterCall<bool> {
            self.gnome_active.clone()
        }
        fn atspi_is_active(&self, _query: &IdentityQuery) -> AdapterCall<bool> {
            *self.atspi_calls.lock().unwrap() += 1;
            self.atspi_active.clone()
        }
        fn show_overlay(
            &self,
            _request: &NotificationRequest,
        ) -> Result<OverlayOutcome, PlatformError> {
            Ok(OverlayOutcome::default())
        }
        fn native_notify(&self, _request: &NotificationRequest) -> Result<(), PlatformError> {
            Ok(())
        }
        fn play_sound(&self, _sound: &str) -> Result<(), PlatformError> {
            Ok(())
        }
    }
    fn fake(session: SessionKind) -> FakeApi {
        FakeApi {
            session,
            windows: vec![LinuxWindow {
                id: 42,
                pid: 300,
                title: "project — Codex".to_owned(),
                app_id: "code.Code".to_owned(),
                desktop: Some(2),
            }],
            active: Mutex::new(None),
            focus_succeeds: true,
            gnome_focus: AdapterCall::Available(true),
            gnome_active: AdapterCall::Available(false),
            atspi_active: AdapterCall::Available(true),
            atspi_calls: Mutex::new(0),
        }
    }
    fn query() -> IdentityQuery {
        IdentityQuery {
            caller_pid: 300,
            caller_pid_chain: vec![300, 200],
            project_hint: "project".to_owned(),
            app_hint: "codex".to_owned(),
            ..IdentityQuery::default()
        }
    }
    #[test]
    fn xid_accepts_decimal_and_hex() {
        assert_eq!(parse_xid("42").unwrap(), 42);
        assert_eq!(parse_xid("0x2A").unwrap(), 42);
        assert!(parse_xid("nope").is_err());
    }
    #[test]
    fn x11_focus_is_validated_and_verified() {
        let backend = LinuxBackend::new(fake(SessionKind::X11));
        let target = backend.resolve_target(&query()).unwrap().unwrap();
        assert_eq!(
            backend.focus(&target, &query()).unwrap(),
            FocusOutcome::Focused {
                window_id: "42".to_owned()
            }
        );
    }
    #[test]
    fn stale_xid_is_rejected_before_focus() {
        let backend = LinuxBackend::new(fake(SessionKind::X11));
        let stale = WindowTarget {
            id: "99".to_owned(),
            pid: 300,
            title: "project — Codex".to_owned(),
            app_id: "code.Code".to_owned(),
        };
        assert_eq!(
            backend.focus(&stale, &query()).unwrap(),
            FocusOutcome::NotFound
        );
    }
    #[test]
    fn gnome_focus_requires_active_window_verification() {
        let mut api = fake(SessionKind::GnomeWayland);
        api.gnome_active = AdapterCall::Available(true);
        let backend = LinuxBackend::new(api).with_focus_timeout(Duration::from_millis(20));
        let target = backend.resolve_target(&query()).unwrap().unwrap();
        assert_eq!(
            backend.focus(&target, &query()).unwrap(),
            FocusOutcome::Focused {
                window_id: "wayland:gnome".to_owned()
            }
        );

        let backend = LinuxBackend::new(fake(SessionKind::GnomeWayland))
            .with_focus_timeout(Duration::from_millis(20));
        let target = backend.resolve_target(&query()).unwrap().unwrap();
        assert!(matches!(
            backend.focus(&target, &query()).unwrap(),
            FocusOutcome::Failed { reason } if reason.contains("verification timed out")
        ));
    }
    #[test]
    fn compositor_nonmatch_never_falls_back_to_atspi() {
        let backend = LinuxBackend::new(fake(SessionKind::GnomeWayland));
        assert!(
            !backend
                .is_active(&WindowTarget::default(), &query())
                .unwrap()
        );
        assert_eq!(*backend.api.atspi_calls.lock().unwrap(), 0);
    }
    #[test]
    fn unavailable_compositor_may_fall_back_to_atspi() {
        let mut api = fake(SessionKind::GnomeWayland);
        api.gnome_active = AdapterCall::Unavailable;
        let backend = LinuxBackend::new(api);
        assert!(
            backend
                .is_active(&WindowTarget::default(), &query())
                .unwrap()
        );
        assert_eq!(*backend.api.atspi_calls.lock().unwrap(), 1);
    }
    #[test]
    fn compositor_failure_does_not_fall_back() {
        let mut api = fake(SessionKind::GnomeWayland);
        api.gnome_active = AdapterCall::Failed("timeout".to_owned());
        let backend = LinuxBackend::new(api);
        assert!(
            backend
                .is_active(&WindowTarget::default(), &query())
                .is_err()
        );
        assert_eq!(*backend.api.atspi_calls.lock().unwrap(), 0);
    }
    #[test]
    fn wayland_does_not_claim_precise_placement() {
        assert!(
            !LinuxBackend::new(fake(SessionKind::GnomeWayland))
                .capabilities()
                .precise_multi_monitor_placement
        );
    }
    #[test]
    fn monitor_model_preserves_negative_portrait_geometry() {
        let monitor = LinuxMonitor {
            left: -1080,
            top: 0,
            right: 0,
            bottom: 1920,
            work_left: -1080,
            work_top: 28,
            work_right: 0,
            work_bottom: 1920,
            dpi: 144,
            primary: false,
        };
        assert!(monitor.left < 0);
        assert!((monitor.bottom - monitor.top) > (monitor.right - monitor.left));
    }

    #[test]
    fn equal_strength_x11_candidates_are_rejected() {
        let mut api = fake(SessionKind::X11);
        api.windows.push(LinuxWindow {
            id: 43,
            pid: 300,
            title: "project — second Codex".to_owned(),
            app_id: "code.Code".to_owned(),
            desktop: Some(2),
        });
        let error = LinuxBackend::new(api).resolve_target(&query()).unwrap_err();
        assert!(error.to_string().contains("Ambiguous"));
    }

    #[test]
    fn gjs_contract_keeps_v5_and_legacy_methods() {
        let modern = include_str!("../../../gnome-shell-extension/extension-modern.js");
        let legacy = include_str!("../../../gnome-shell-extension/extension-legacy.js");
        for source in [modern, legacy] {
            for method in [
                "GetContractVersion",
                "FocusWindowV4",
                "IsWindowActiveV4",
                "FocusWindowV3",
                "IsWindowActiveV3",
                "CaptureActiveWindowV3",
                "CaptureWindowByTitleV5",
                "FocusWindow",
                "IsWindowActive",
            ] {
                assert!(source.contains(method), "missing {method}");
            }
            assert!(
                source.contains("if (pidMatches && titleMatches)"),
                "PID ties must be broken with title or project evidence"
            );
            assert!(source.contains("windowToken(window) === token"));
            assert!(source.contains("return 5;"));
            assert!(source.contains("anoti-capture-"));
            assert!(source.contains("canonicalAppIdentity"));
        }
    }

    #[test]
    fn gnome_capture_never_invents_an_ambiguous_terminal_token() {
        let backend = LinuxBackend::new(fake(SessionKind::GnomeWayland));
        assert!(backend.capture_identity(&query()).unwrap().is_none());
    }
}
