//! Windows 10/11 window discovery, focus and notification backend.

use std::collections::HashSet;
use std::thread;
use std::time::{Duration, Instant};

use anoti_core::{
    CandidateEvidence, FocusOutcome, NotificationRequest, PlatformCapabilities, WindowCandidate,
    WindowIdentity, generate_window_instance_id, normalize_pid_chain, resolve_candidate,
    titles_compatible,
};
use anoti_platform::{IdentityQuery, OverlayOutcome, PlatformBackend, PlatformError, WindowTarget};

#[cfg(windows)]
mod native;

pub const APP_USER_MODEL_ID: &str = "io.github.sonnx24042005.AiAgentNotifier";
pub const FOCUS_PROTOCOL: &str = "anoti-focus";
pub const TOAST_ACTIVATOR_CLSID: &str = "{DBF1E7D1-92C4-4D9C-9867-722CE3519ED4}";

#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct NativeWindow {
    pub handle: u64,
    pub pid: u32,
    pub title: String,
    pub class_name: String,
    pub visible: bool,
    pub owned: bool,
    pub tool_window: bool,
    pub minimized: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MonitorArea {
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

/// Safe boundary around the small target-gated Win32 implementation.
pub trait WindowsApi: Send + Sync {
    fn enumerate_windows(&self) -> Result<Vec<NativeWindow>, PlatformError>;
    fn foreground_window(&self) -> Result<Option<u64>, PlatformError>;
    fn is_window(&self, handle: u64) -> bool;
    fn window_pid(&self, handle: u64) -> Result<u32, PlatformError>;
    fn window_title(&self, handle: u64) -> Result<String, PlatformError>;
    fn process_ancestry(&self, start_pid: u32) -> Result<Vec<u32>, PlatformError>;
    fn process_start_time(&self, pid: u32) -> Result<u64, PlatformError>;
    /// Requests activation. Callers must independently verify foreground state.
    fn request_activation(&self, handle: u64) -> Result<(), PlatformError>;
    fn enumerate_monitors(&self) -> Result<Vec<MonitorArea>, PlatformError>;
    fn show_overlay(&self, request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError>;
    fn show_toast(&self, request: &NotificationRequest) -> Result<(), PlatformError>;
    fn play_sound(&self, sound: &str) -> Result<(), PlatformError>;
}

#[derive(Debug, Default)]
pub struct SystemWindowsApi;

#[cfg(windows)]
impl WindowsApi for SystemWindowsApi {
    fn enumerate_windows(&self) -> Result<Vec<NativeWindow>, PlatformError> {
        native::enumerate_windows()
    }

    fn foreground_window(&self) -> Result<Option<u64>, PlatformError> {
        native::foreground_window()
    }

    fn is_window(&self, handle: u64) -> bool {
        native::is_window(handle)
    }

    fn window_pid(&self, handle: u64) -> Result<u32, PlatformError> {
        native::window_pid(handle)
    }

    fn window_title(&self, handle: u64) -> Result<String, PlatformError> {
        native::window_title(handle)
    }

    fn process_ancestry(&self, start_pid: u32) -> Result<Vec<u32>, PlatformError> {
        native::process_ancestry(start_pid)
    }

    fn process_start_time(&self, pid: u32) -> Result<u64, PlatformError> {
        native::process_start_time(pid)
    }

    fn request_activation(&self, handle: u64) -> Result<(), PlatformError> {
        native::request_activation(handle)
    }

    fn enumerate_monitors(&self) -> Result<Vec<MonitorArea>, PlatformError> {
        native::enumerate_monitors()
    }

    fn show_overlay(&self, request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError> {
        native::show_overlay(request)
    }

    fn show_toast(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        native::show_toast(request)
    }

    fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
        native::play_sound(sound)
    }
}

#[cfg(not(windows))]
impl WindowsApi for SystemWindowsApi {
    fn enumerate_windows(&self) -> Result<Vec<NativeWindow>, PlatformError> {
        Err(unavailable())
    }
    fn foreground_window(&self) -> Result<Option<u64>, PlatformError> {
        Err(unavailable())
    }
    fn is_window(&self, _handle: u64) -> bool {
        false
    }
    fn window_pid(&self, _handle: u64) -> Result<u32, PlatformError> {
        Err(unavailable())
    }
    fn window_title(&self, _handle: u64) -> Result<String, PlatformError> {
        Err(unavailable())
    }
    fn process_ancestry(&self, _start_pid: u32) -> Result<Vec<u32>, PlatformError> {
        Err(unavailable())
    }
    fn process_start_time(&self, _pid: u32) -> Result<u64, PlatformError> {
        Ok(0)
    }
    fn request_activation(&self, _handle: u64) -> Result<(), PlatformError> {
        Err(unavailable())
    }
    fn enumerate_monitors(&self) -> Result<Vec<MonitorArea>, PlatformError> {
        Err(unavailable())
    }
    fn show_overlay(
        &self,
        _request: &NotificationRequest,
    ) -> Result<OverlayOutcome, PlatformError> {
        Err(unavailable())
    }
    fn show_toast(&self, _request: &NotificationRequest) -> Result<(), PlatformError> {
        Err(unavailable())
    }
    fn play_sound(&self, _sound: &str) -> Result<(), PlatformError> {
        Err(unavailable())
    }
}

#[cfg(not(windows))]
fn unavailable() -> PlatformError {
    PlatformError::Unsupported("Windows backend is not available on this target".to_owned())
}

#[derive(Debug)]
pub struct WindowsBackend<A = SystemWindowsApi> {
    api: A,
    focus_timeout: Duration,
}

impl Default for WindowsBackend<SystemWindowsApi> {
    fn default() -> Self {
        Self::new(SystemWindowsApi)
    }
}

impl<A> WindowsBackend<A> {
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

impl<A: WindowsApi> WindowsBackend<A> {
    fn inventory(&self, query: &IdentityQuery) -> Result<Vec<WindowCandidate>, PlatformError> {
        let ancestry = normalize_pid_chain(
            query
                .caller_pid_chain
                .iter()
                .copied()
                .chain(self.api.process_ancestry(query.caller_pid)?),
            query.caller_pid,
        )
        .into_iter()
        .collect::<HashSet<_>>();
        Ok(self
            .api
            .enumerate_windows()?
            .into_iter()
            .filter(|window| {
                (window.visible || window.minimized) && !window.owned && !window.tool_window
            })
            .map(|window| {
                let id = window.handle.to_string();
                let current_start_time = self.api.process_start_time(window.pid).unwrap_or(0);
                let direct_id_match = !query.window_id.is_empty() && query.window_id == id;
                let pid_reused = query.process_start_time > 0
                    && current_start_time > 0
                    && query.process_start_time != current_start_time;
                let stale = direct_id_match
                    && ((query.window_pid > 0 && query.window_pid != window.pid) || pid_reused);
                let exact_instance_match = direct_id_match
                    && !stale
                    && (query.window_pid == 0 || query.window_pid == window.pid);
                let session_match = !query.session_id.is_empty()
                    && direct_id_match
                    && !stale
                    && (query.window_pid == 0 || query.window_pid == window.pid);
                let project_match = !query.project_hint.trim().is_empty()
                    && window
                        .title
                        .to_ascii_lowercase()
                        .contains(&query.project_hint.trim().to_ascii_lowercase());
                let title_match = !query.title_fingerprint.trim().is_empty()
                    && titles_compatible(&query.title_fingerprint, &window.title);
                let app_match = app_matches(&window.class_name, &query.app_hint);
                let instance_id = if direct_id_match && !query.window_instance_id.is_empty() {
                    query.window_instance_id.clone()
                } else {
                    String::new()
                };
                WindowCandidate {
                    id,
                    instance_id,
                    pid: window.pid,
                    title: window.title,
                    app_id: window.class_name.clone(),
                    generation: query.generation,
                    evidence: CandidateEvidence {
                        exact_instance_match,
                        session_match,
                        pid_match: ancestry.contains(&window.pid),
                        project_match,
                        direct_id_match: direct_id_match && !stale,
                        app_match,
                        title_match,
                        stale,
                        developer_window: is_developer_window(&window.class_name),
                    },
                }
            })
            .collect())
    }

    fn validate_target(&self, target: &WindowTarget) -> Result<bool, PlatformError> {
        let handle = parse_handle(&target.id)?;
        if !self.api.is_window(handle) {
            return Ok(false);
        }
        let current_pid = self.api.window_pid(handle)?;
        if target.pid > 0 && current_pid > 0 && target.pid != current_pid {
            return Ok(false);
        }
        let current_start_time = self.api.process_start_time(current_pid).unwrap_or(0);
        if target.process_start_time > 0
            && current_start_time > 0
            && target.process_start_time != current_start_time
        {
            return Ok(false);
        }
        Ok(true)
    }
}

impl<A: WindowsApi> PlatformBackend for WindowsBackend<A> {
    fn capabilities(&self) -> PlatformCapabilities {
        PlatformCapabilities {
            window_discovery: true,
            active_window_probe: true,
            focus: true,
            precise_multi_monitor_placement: true,
            native_notification: true,
            global_hotkey: false,
            backend: "win32".to_owned(),
        }
    }

    fn capture_identity(
        &self,
        query: &IdentityQuery,
    ) -> Result<Option<WindowIdentity>, PlatformError> {
        let Some(foreground) = self.api.foreground_window()? else {
            return Ok(None);
        };
        let ancestry = normalize_pid_chain(
            query
                .caller_pid_chain
                .iter()
                .copied()
                .chain(self.api.process_ancestry(query.caller_pid)?),
            query.caller_pid,
        );
        let Some(window) = self
            .api
            .enumerate_windows()?
            .into_iter()
            .find(|window| window.handle == foreground)
        else {
            return Ok(None);
        };
        if !is_developer_window(&window.class_name)
            || !ancestry.contains(&window.pid)
            || window.title.trim().is_empty()
        {
            return Ok(None);
        }
        let current_start_time = self.api.process_start_time(window.pid).unwrap_or(0);
        let instance_id = if query.window_instance_id.is_empty() {
            generate_window_instance_id()
        } else {
            query.window_instance_id.clone()
        };
        Ok(Some(WindowIdentity {
            window_id: window.handle.to_string(),
            window_instance_id: instance_id,
            window_pid: window.pid,
            process_start_time: current_start_time,
            caller_pid: query.caller_pid,
            caller_pid_chain: ancestry,
            project_hint: query.project_hint.clone(),
            title_fingerprint: window.title,
            app_hint: query.app_hint.clone(),
            session_id: query.session_id.clone(),
            generation: query.generation,
        }))
    }

    fn resolve_target(&self, query: &IdentityQuery) -> Result<Option<WindowTarget>, PlatformError> {
        let inventory = self.inventory(query)?;
        resolve_candidate(&inventory)
            .map_err(|outcome| {
                PlatformError::Operation(format!("identity resolution: {outcome:?}"))
            })
            .map(|candidate| {
                candidate.map(|candidate| WindowTarget {
                    id: candidate.id.clone(),
                    instance_id: candidate.instance_id.clone(),
                    pid: candidate.pid,
                    process_start_time: 0,
                    title: candidate.title.clone(),
                    app_id: candidate.app_id.clone(),
                    generation: candidate.generation,
                })
            })
    }

    fn is_active(
        &self,
        target: &WindowTarget,
        _query: &IdentityQuery,
    ) -> Result<bool, PlatformError> {
        if !self.validate_target(target)? {
            return Ok(false);
        }
        Ok(self.api.foreground_window()? == Some(parse_handle(&target.id)?))
    }

    fn focus(
        &self,
        target: &WindowTarget,
        query: &IdentityQuery,
    ) -> Result<FocusOutcome, PlatformError> {
        if !self.validate_target(target)? {
            return Ok(FocusOutcome::NotFound);
        }
        let inventory = self.inventory(query)?;
        if let Some(candidate) = inventory.iter().find(|c| c.id == target.id) {
            if candidate.evidence.stale {
                return Ok(FocusOutcome::NotFound);
            }
        }
        let handle = parse_handle(&target.id)?;
        self.api.request_activation(handle)?;
        let deadline = Instant::now() + self.focus_timeout;
        loop {
            if self.api.foreground_window()? == Some(handle) {
                return Ok(FocusOutcome::Focused {
                    window_id: target.id.clone(),
                });
            }
            if Instant::now() >= deadline {
                return Ok(FocusOutcome::Failed {
                    reason: "foreground verification timed out".to_owned(),
                });
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn show_overlay(&self, request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError> {
        self.api.show_overlay(request)
    }

    fn native_notify(&self, request: &NotificationRequest) -> Result<(), PlatformError> {
        self.api.show_toast(request)
    }

    fn play_sound(&self, sound: &str) -> Result<(), PlatformError> {
        self.api.play_sound(sound)
    }
}

fn parse_handle(value: &str) -> Result<u64, PlatformError> {
    let value = value.trim();
    let parsed = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
        .map_or_else(|| value.parse::<u64>(), |hex| u64::from_str_radix(hex, 16));
    parsed.map_err(|_| PlatformError::Operation("invalid HWND value".to_owned()))
}

fn is_developer_window(class_name: &str) -> bool {
    const CLASSES: &[&str] = &[
        "cascadia_hosting_window_class",
        "consolewindowclass",
        "mintty",
        "code",
        "vscodium",
        "cursor",
        "windsurf",
        "antigravity",
        "chatgpt",
        "codex",
        "pycharm",
        "idea",
        "clion",
        "webstorm",
        "goland",
        "windowsterminal",
    ];
    let value = class_name.trim().to_ascii_lowercase();
    CLASSES.iter().any(|class| value.contains(class))
}

fn app_matches(class_name: &str, app_hint: &str) -> bool {
    let class_name = class_name.to_ascii_lowercase();
    match app_hint.trim().to_ascii_lowercase().as_str() {
        "antigravity" => class_name.contains("antigravity"),
        "codex" => class_name.contains("codex") || class_name.contains("chatgpt"),
        "claude" => is_developer_window(&class_name),
        "" => false,
        hint => class_name.contains(hint),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToastRegistrationPlan {
    pub app_user_model_id: &'static str,
    pub protocol: &'static str,
    pub activator_clsid: &'static str,
    pub install_registry_keys: Vec<String>,
    pub uninstall_registry_keys: Vec<String>,
}

#[must_use]
pub fn toast_registration_plan() -> ToastRegistrationPlan {
    let protocol_key = format!(r"HKCU\Software\Classes\{FOCUS_PROTOCOL}");
    let activator_key = format!(r"HKCU\Software\Classes\CLSID\{TOAST_ACTIVATOR_CLSID}");
    ToastRegistrationPlan {
        app_user_model_id: APP_USER_MODEL_ID,
        protocol: FOCUS_PROTOCOL,
        activator_clsid: TOAST_ACTIVATOR_CLSID,
        install_registry_keys: vec![protocol_key.clone(), activator_key.clone()],
        uninstall_registry_keys: vec![protocol_key, activator_key],
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::sync::Mutex;

    use super::*;

    #[derive(Debug)]
    struct FakeApi {
        windows: Vec<NativeWindow>,
        foreground: Mutex<Option<u64>>,
        activation_succeeds: bool,
        start_times: Mutex<HashMap<u32, u64>>,
    }

    impl WindowsApi for FakeApi {
        fn enumerate_windows(&self) -> Result<Vec<NativeWindow>, PlatformError> {
            Ok(self.windows.clone())
        }
        fn foreground_window(&self) -> Result<Option<u64>, PlatformError> {
            Ok(*self.foreground.lock().unwrap())
        }
        fn is_window(&self, handle: u64) -> bool {
            self.windows.iter().any(|window| window.handle == handle)
        }
        fn window_pid(&self, handle: u64) -> Result<u32, PlatformError> {
            Ok(self
                .windows
                .iter()
                .find(|window| window.handle == handle)
                .map_or(0, |window| window.pid))
        }
        fn window_title(&self, handle: u64) -> Result<String, PlatformError> {
            Ok(self
                .windows
                .iter()
                .find(|window| window.handle == handle)
                .map_or_else(String::new, |window| window.title.clone()))
        }
        fn process_ancestry(&self, start_pid: u32) -> Result<Vec<u32>, PlatformError> {
            Ok(vec![start_pid, 200, 100])
        }
        fn process_start_time(&self, pid: u32) -> Result<u64, PlatformError> {
            if pid == 0 {
                Ok(0)
            } else {
                Ok(*self.start_times.lock().unwrap().get(&pid).unwrap_or(&1000))
            }
        }
        fn request_activation(&self, handle: u64) -> Result<(), PlatformError> {
            if self.activation_succeeds {
                *self.foreground.lock().unwrap() = Some(handle);
            }
            Ok(())
        }
        fn enumerate_monitors(&self) -> Result<Vec<MonitorArea>, PlatformError> {
            Ok(vec![MonitorArea {
                left: -1920,
                right: 0,
                dpi: 144,
                ..MonitorArea::default()
            }])
        }
        fn show_overlay(
            &self,
            _request: &NotificationRequest,
        ) -> Result<OverlayOutcome, PlatformError> {
            Ok(OverlayOutcome {
                displayed: true,
                ..OverlayOutcome::default()
            })
        }
        fn show_toast(&self, _request: &NotificationRequest) -> Result<(), PlatformError> {
            Ok(())
        }
        fn play_sound(&self, _sound: &str) -> Result<(), PlatformError> {
            Ok(())
        }
    }

    fn window(handle: u64, pid: u32, title: &str) -> NativeWindow {
        NativeWindow {
            handle,
            pid,
            title: title.to_owned(),
            class_name: "CASCADIA_HOSTING_WINDOW_CLASS".to_owned(),
            visible: true,
            ..NativeWindow::default()
        }
    }

    fn query() -> IdentityQuery {
        IdentityQuery {
            caller_pid: 300,
            caller_pid_chain: vec![300, 200, 100],
            process_start_time: 1000,
            project_hint: "project".to_owned(),
            app_hint: "codex".to_owned(),
            generation: 1,
            ..IdentityQuery::default()
        }
    }

    #[test]
    fn unique_pid_window_resolves_and_verified_focus_succeeds() {
        let api = FakeApi {
            windows: vec![window(10, 200, "project - Codex")],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let backend = WindowsBackend::new(api);
        let target = backend.resolve_target(&query()).unwrap().unwrap();
        assert_eq!(
            backend.focus(&target, &query()).unwrap(),
            FocusOutcome::Focused {
                window_id: "10".to_owned()
            }
        );
    }

    #[test]
    fn activation_call_without_foreground_change_is_not_success() {
        let api = FakeApi {
            windows: vec![window(10, 200, "project - Codex")],
            foreground: Mutex::new(None),
            activation_succeeds: false,
            start_times: Mutex::new(HashMap::new()),
        };
        let backend = WindowsBackend::new(api).with_focus_timeout(Duration::from_millis(1));
        let target = backend.resolve_target(&query()).unwrap().unwrap();
        assert!(matches!(
            backend.focus(&target, &query()).unwrap(),
            FocusOutcome::Failed { .. }
        ));
    }

    #[test]
    fn ambiguous_same_tier_and_stale_reuse_are_rejected() {
        let api = FakeApi {
            windows: vec![
                window(10, 200, "project - Codex"),
                window(11, 200, "project - Codex"),
            ],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        assert!(WindowsBackend::new(api).resolve_target(&query()).is_err());

        let api = FakeApi {
            windows: vec![window(10, 999, "unrelated")],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let backend = WindowsBackend::new(api);
        let target = WindowTarget {
            id: "10".to_owned(),
            instance_id: "win32:10:200:1:1000".to_owned(),
            pid: 200,
            process_start_time: 1000,
            title: "project - Codex".to_owned(),
            app_id: String::new(),
            generation: 1,
        };
        assert_eq!(
            backend.focus(&target, &query()).unwrap(),
            FocusOutcome::NotFound
        );
    }

    #[test]
    fn foreground_capture_requires_ancestor_developer_window() {
        let api = FakeApi {
            windows: vec![window(10, 200, "project - Codex")],
            foreground: Mutex::new(Some(10)),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let identity = WindowsBackend::new(api)
            .capture_identity(&query())
            .unwrap()
            .unwrap();
        assert_eq!(identity.window_id, "10");
        assert_eq!(identity.window_pid, 200);
        assert_eq!(identity.title_fingerprint, "project - Codex");
    }

    #[test]
    fn monitor_coordinates_and_registration_symmetry_are_preserved() {
        let api = FakeApi {
            windows: Vec::new(),
            foreground: Mutex::new(None),
            activation_succeeds: false,
            start_times: Mutex::new(HashMap::new()),
        };
        let monitors = api.enumerate_monitors().unwrap();
        assert_eq!(monitors[0].left, -1920);
        assert_eq!(monitors[0].dpi, 144);
        let plan = toast_registration_plan();
        assert_eq!(plan.install_registry_keys, plan.uninstall_registry_keys);
        assert_eq!(plan.app_user_model_id, APP_USER_MODEL_ID);
    }

    #[test]
    fn windows_exact_instance_resolves_when_multiple_windows_share_pid_and_title() {
        let api = FakeApi {
            windows: vec![
                window(10, 200, "project - Codex"),
                window(11, 200, "project - Codex"),
            ],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let mut q = query();
        q.window_id = "11".to_owned();
        q.window_instance_id = "uuid-11".to_owned();
        let target = WindowsBackend::new(api)
            .resolve_target(&q)
            .unwrap()
            .unwrap();
        assert_eq!(target.id, "11");
        assert_eq!(target.instance_id, "uuid-11");
    }

    #[test]
    fn windows_title_change_does_not_fail_validation() {
        let api = FakeApi {
            windows: vec![window(10, 200, "New Dynamic Terminal Title")],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let backend = WindowsBackend::new(api);
        let target = WindowTarget {
            id: "10".to_owned(),
            instance_id: "win32:10:200:1:1000".to_owned(),
            pid: 200,
            process_start_time: 1000,
            title: "Original Title".to_owned(),
            app_id: "CASCADIA_HOSTING_WINDOW_CLASS".to_owned(),
            generation: 1,
        };
        assert!(backend.validate_target(&target).unwrap());
    }

    #[test]
    fn windows_minimized_window_is_included_in_inventory() {
        let mut minimized_win = window(12, 200, "project - Codex");
        minimized_win.visible = false;
        minimized_win.minimized = true;

        let api = FakeApi {
            windows: vec![minimized_win],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let mut q = query();
        q.window_id = "12".to_owned();
        let target = WindowsBackend::new(api)
            .resolve_target(&q)
            .unwrap()
            .unwrap();
        assert_eq!(target.id, "12");
    }

    #[test]
    fn windows_handle_reuse_with_different_pid_is_rejected_as_stale() {
        let api = FakeApi {
            windows: vec![window(10, 999, "unrelated")], // HWND 10 reused by unrelated PID 999
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let backend = WindowsBackend::new(api);
        let mut q = query();
        q.window_id = "10".to_owned();
        q.window_pid = 200;
        q.window_instance_id = "win32:10:200:1:1000".to_owned();

        assert!(backend.resolve_target(&q).unwrap().is_none());

        let target = WindowTarget {
            id: "10".to_owned(),
            instance_id: "win32:10:200:1:1000".to_owned(),
            pid: 200,
            process_start_time: 1000,
            title: "project - Codex".to_owned(),
            app_id: "CASCADIA_HOSTING_WINDOW_CLASS".to_owned(),
            generation: 1,
        };
        assert_eq!(backend.focus(&target, &q).unwrap(), FocusOutcome::NotFound);
    }

    #[test]
    fn windows_exact_instance_resolves_and_focuses_when_title_changed_completely() {
        let api = FakeApi {
            windows: vec![window(
                10,
                200,
                "completely different dynamic title - PowerShell",
            )],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let backend = WindowsBackend::new(api);
        let mut q = query();
        q.window_id = "10".to_owned();
        q.window_pid = 200;
        q.title_fingerprint = "project - Codex".to_owned();
        q.window_instance_id = "win32:10:200:1:1000".to_owned();

        let target = backend.resolve_target(&q).unwrap().unwrap();
        assert_eq!(target.id, "10");
        assert_eq!(target.instance_id, "win32:10:200:1:1000");
        assert_eq!(
            backend.focus(&target, &q).unwrap(),
            FocusOutcome::Focused {
                window_id: "10".to_owned()
            }
        );
    }

    #[test]
    fn windows_pid_reuse_after_restart_is_rejected_as_stale() {
        let api = FakeApi {
            windows: vec![window(10, 200, "project - Codex")],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        // Process restarted and got new start_time 3000
        api.start_times.lock().unwrap().insert(200, 3000);
        let backend = WindowsBackend::new(api);
        let mut q = query();
        q.window_id = "10".to_owned();
        q.window_pid = 200;
        q.process_start_time = 1000; // Old notification had start_time 1000
        q.window_instance_id = "win32:10:200:1:1000".to_owned();

        assert!(backend.resolve_target(&q).unwrap().is_none());

        let target = WindowTarget {
            id: "10".to_owned(),
            instance_id: "win32:10:200:1:1000".to_owned(),
            pid: 200,
            process_start_time: 1000,
            title: "project - Codex".to_owned(),
            app_id: "CASCADIA_HOSTING_WINDOW_CLASS".to_owned(),
            generation: 1,
        };
        assert_eq!(backend.focus(&target, &q).unwrap(), FocusOutcome::NotFound);
    }

    #[test]
    fn windows_dual_window_distinct_sessions_route_correctly() {
        let api = FakeApi {
            windows: vec![
                window(10, 200, "project-a - Codex"),
                window(11, 200, "project-b - Codex"),
            ],
            foreground: Mutex::new(None),
            activation_succeeds: true,
            start_times: Mutex::new(HashMap::new()),
        };
        let backend = WindowsBackend::new(api);

        let mut q_a = query();
        q_a.window_id = "10".to_owned();
        q_a.window_instance_id = "win32:10:200:1:1000".to_owned();
        q_a.project_hint = "project-a".to_owned();

        let mut q_b = query();
        q_b.window_id = "11".to_owned();
        q_b.window_instance_id = "win32:11:200:1:1000".to_owned();
        q_b.project_hint = "project-b".to_owned();

        let target_a = backend.resolve_target(&q_a).unwrap().unwrap();
        assert_eq!(target_a.id, "10");
        assert_eq!(target_a.instance_id, "win32:10:200:1:1000");

        let target_b = backend.resolve_target(&q_b).unwrap().unwrap();
        assert_eq!(target_b.id, "11");
        assert_eq!(target_b.instance_id, "win32:11:200:1:1000");
    }
}
