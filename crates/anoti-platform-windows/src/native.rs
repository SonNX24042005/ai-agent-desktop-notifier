//! Small Win32/WinRT FFI boundary. Every unsafe block states its local invariant.

#![allow(unsafe_code)]

use std::collections::HashMap;
use std::mem::size_of;
use std::sync::Arc;
use std::sync::atomic::{AtomicU8, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use anoti_core::{NotificationRequest, titles_compatible};
use anoti_platform::{OverlayOutcome, PlatformError};
use windows::Data::Xml::Dom::XmlDocument;
use windows::UI::Notifications::{ToastNotification, ToastNotificationManager};
use windows::Win32::Foundation::{
    BOOL, COLORREF, CloseHandle, HANDLE, HINSTANCE, HWND, LPARAM, LRESULT, RECT, WPARAM,
};
use windows::Win32::Graphics::Gdi::{
    BeginPaint, CreateSolidBrush, DT_END_ELLIPSIS, DT_LEFT, DT_WORDBREAK, DeleteObject, DrawTextW,
    EndPaint, EnumDisplayMonitors, FillRect, GetMonitorInfoW, HDC, HMONITOR, MONITORENUMPROC,
    MONITORINFO, PAINTSTRUCT, SetBkMode, SetTextColor, TRANSPARENT,
};
use windows::Win32::Media::Audio::{PlaySoundW, SND_ASYNC, SND_FILENAME, SND_NODEFAULT};
use windows::Win32::System::Com::{COINIT_MULTITHREADED, CoInitializeEx, CoUninitialize};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
use windows::Win32::UI::HiDpi::{
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, GetDpiForMonitor, MDT_EFFECTIVE_DPI,
    SetProcessDpiAwarenessContext,
};
use windows::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID;
use windows::Win32::UI::WindowsAndMessaging::{
    BringWindowToTop, CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW,
    EnumWindows, GW_OWNER, GWL_EXSTYLE, GWLP_USERDATA, GetClassNameW, GetClientRect,
    GetForegroundWindow, GetWindow, GetWindowLongPtrW, GetWindowTextLengthW, GetWindowTextW,
    GetWindowThreadProcessId, HWND_TOPMOST, IsIconic, IsWindow, IsWindowVisible,
    MONITORINFOF_PRIMARY, MSG, PM_REMOVE, PeekMessageW, RegisterClassExW, SW_RESTORE,
    SWP_NOACTIVATE, SetForegroundWindow, SetWindowLongPtrW, SetWindowPos, ShowWindowAsync,
    TranslateMessage, WM_CLOSE, WM_DESTROY, WM_LBUTTONUP, WM_PAINT, WNDCLASSEXW, WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP, WS_VISIBLE,
};
use windows::core::{HSTRING, PCWSTR};

use crate::{APP_USER_MODEL_ID, FOCUS_PROTOCOL, MonitorArea, NativeWindow};

fn operation(context: &str, error: impl std::fmt::Display) -> PlatformError {
    PlatformError::Operation(format!("{context}: {error}"))
}

fn hwnd_from_u64(handle: u64) -> Result<HWND, PlatformError> {
    usize::try_from(handle)
        .map(|raw| HWND(raw as *mut core::ffi::c_void))
        .map_err(|_| PlatformError::Operation("HWND does not fit target pointer width".to_owned()))
}

fn hwnd_to_u64(hwnd: HWND) -> Option<u64> {
    (!hwnd.0.is_null()).then_some(hwnd.0 as usize as u64)
}

#[allow(clippy::unnecessary_wraps)]
pub fn foreground_window() -> Result<Option<u64>, PlatformError> {
    // SAFETY: GetForegroundWindow takes no pointers and returns a borrowed handle.
    let hwnd = unsafe { GetForegroundWindow() };
    Ok(hwnd_to_u64(hwnd).filter(|handle| *handle != 0))
}

pub fn is_window(handle: u64) -> bool {
    hwnd_from_u64(handle).is_ok_and(|hwnd| {
        // SAFETY: IsWindow only inspects the numeric handle; HWND lifetime is revalidated by Win32.
        unsafe { IsWindow(hwnd).as_bool() }
    })
}

pub fn window_pid(handle: u64) -> Result<u32, PlatformError> {
    let hwnd = hwnd_from_u64(handle)?;
    let mut pid = 0;
    // SAFETY: `pid` is a valid writable u32 for the duration of the call.
    unsafe { GetWindowThreadProcessId(hwnd, Some(&raw mut pid)) };
    Ok(pid)
}

pub fn window_title(handle: u64) -> Result<String, PlatformError> {
    Ok(read_window_title(hwnd_from_u64(handle)?))
}

fn read_window_title(hwnd: HWND) -> String {
    // SAFETY: Query functions receive a validated value handle and an owned output slice.
    let length = unsafe { GetWindowTextLengthW(hwnd) };
    if length <= 0 {
        return String::new();
    }
    let mut buffer = vec![0_u16; usize::try_from(length).unwrap_or(0) + 1];
    // SAFETY: windows-rs passes the exact mutable slice length to GetWindowTextW.
    let copied = unsafe { GetWindowTextW(hwnd, &mut buffer) };
    buffer.truncate(usize::try_from(copied.max(0)).unwrap_or(0));
    String::from_utf16_lossy(&buffer)
}

fn read_class_name(hwnd: HWND) -> String {
    let mut buffer = vec![0_u16; 256];
    // SAFETY: windows-rs passes the exact mutable slice length to GetClassNameW.
    let copied = unsafe { GetClassNameW(hwnd, &mut buffer) };
    buffer.truncate(usize::try_from(copied.max(0)).unwrap_or(0));
    String::from_utf16_lossy(&buffer)
}

pub fn enumerate_windows() -> Result<Vec<NativeWindow>, PlatformError> {
    unsafe extern "system" fn callback(hwnd: HWND, data: LPARAM) -> BOOL {
        // SAFETY: EnumWindows is called synchronously below with a live Vec pointer in LPARAM.
        let windows = unsafe { &mut *(data.0 as *mut Vec<NativeWindow>) };
        // SAFETY: callback HWND is supplied by the OS and valid for these non-owning queries.
        let visible = unsafe { IsWindowVisible(hwnd).as_bool() };
        let mut pid = 0;
        // SAFETY: `pid` is writable and lives across the call.
        unsafe { GetWindowThreadProcessId(hwnd, Some(&raw mut pid)) };
        // SAFETY: an absent owner is represented as an error/null result.
        let owned = unsafe { GetWindow(hwnd, GW_OWNER).is_ok() };
        // SAFETY: reading the extended style does not retain the HWND or pointer.
        let ex_style = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) };
        let tool_window = ex_style & isize::try_from(WS_EX_TOOLWINDOW.0).unwrap_or(0) != 0;
        if let Some(handle) = hwnd_to_u64(hwnd) {
            windows.push(NativeWindow {
                handle,
                pid,
                title: read_window_title(hwnd),
                class_name: read_class_name(hwnd),
                visible,
                owned,
                tool_window,
                // SAFETY: IsIconic is a non-owning HWND query.
                minimized: unsafe { IsIconic(hwnd).as_bool() },
            });
        }
        BOOL(1)
    }

    let mut windows = Vec::new();
    // SAFETY: callback does not escape; LPARAM points to `windows` until EnumWindows returns.
    unsafe {
        EnumWindows(
            Some(callback),
            LPARAM((&raw mut windows).cast::<()>() as isize),
        )
    }
    .map_err(|error| operation("EnumWindows", error))?;
    Ok(windows)
}

struct Snapshot(HANDLE);

impl Drop for Snapshot {
    fn drop(&mut self) {
        // SAFETY: Snapshot uniquely owns the valid handle returned by CreateToolhelp32Snapshot.
        let _ = unsafe { CloseHandle(self.0) };
    }
}

pub fn process_ancestry(start_pid: u32) -> Result<Vec<u32>, PlatformError> {
    // SAFETY: TH32CS_SNAPPROCESS with pid zero creates a process-only system snapshot.
    let snapshot = Snapshot(
        unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) }
            .map_err(|error| operation("CreateToolhelp32Snapshot", error))?,
    );
    let mut entry = PROCESSENTRY32W {
        dwSize: u32::try_from(size_of::<PROCESSENTRY32W>()).unwrap_or(u32::MAX),
        ..PROCESSENTRY32W::default()
    };
    let mut parents = HashMap::new();
    // SAFETY: snapshot remains owned and `entry` is initialized with the required dwSize.
    let mut next = unsafe { Process32FirstW(snapshot.0, &raw mut entry) };
    while next.is_ok() {
        parents.insert(entry.th32ProcessID, entry.th32ParentProcessID);
        // SAFETY: same snapshot and output structure remain valid for the next iteration.
        next = unsafe { Process32NextW(snapshot.0, &raw mut entry) };
    }
    let mut chain = Vec::new();
    let mut current = start_pid;
    while current > 1 && chain.len() < 32 && !chain.contains(&current) {
        chain.push(current);
        current = parents.get(&current).copied().unwrap_or(0);
    }
    Ok(chain)
}

pub fn process_start_time(pid: u32) -> Result<u64, PlatformError> {
    if pid <= 1 {
        return Ok(0);
    }
    use windows::Win32::Foundation::FILETIME;
    use windows::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    // SAFETY: OpenProcess handles are closed via CloseHandle.
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }
        .map_err(|error| operation("OpenProcess", error))?;
    let mut creation = FILETIME::default();
    let mut exit = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    let success = unsafe {
        GetProcessTimes(
            handle,
            &raw mut creation,
            &raw mut exit,
            &raw mut kernel,
            &raw mut user,
        )
    };
    let _ = unsafe { CloseHandle(handle) };
    if !success.as_bool() {
        return Err(operation(
            "GetProcessTimes",
            "failed to query creation time",
        ));
    }
    Ok(((creation.dwHighDateTime as u64) << 32) | (creation.dwLowDateTime as u64))
}

struct ThreadAttachment {
    from: u32,
    to: u32,
    attached: bool,
}

impl ThreadAttachment {
    fn new(from: u32, to: u32) -> Self {
        let attached = from != 0
            && to != 0
            && from != to
            // SAFETY: thread IDs are copied values; matching detach is guaranteed by Drop.
            && unsafe { AttachThreadInput(from, to, true).as_bool() };
        Self { from, to, attached }
    }
}

impl Drop for ThreadAttachment {
    fn drop(&mut self) {
        if self.attached {
            // SAFETY: this exactly balances the successful attachment made by `new`.
            let _ = unsafe { AttachThreadInput(self.from, self.to, false) };
        }
    }
}

pub fn request_activation(handle: u64) -> Result<(), PlatformError> {
    let target = hwnd_from_u64(handle)?;
    // SAFETY: IsWindow revalidates the externally stored HWND before all later operations.
    if !unsafe { IsWindow(target).as_bool() } {
        return Err(PlatformError::Operation("target HWND is stale".to_owned()));
    }
    // SAFETY: IsIconic/ShowWindowAsync operate on the revalidated HWND.
    if unsafe { IsIconic(target).as_bool() } {
        let _ = unsafe { ShowWindowAsync(target, SW_RESTORE) };
    }
    // SAFETY: all calls use value handles/IDs; attachments are scoped by RAII guards.
    let current_thread = unsafe { GetCurrentThreadId() };
    let foreground = unsafe { GetForegroundWindow() };
    let foreground_thread = if foreground.0.is_null() {
        0
    } else {
        unsafe { GetWindowThreadProcessId(foreground, None) }
    };
    let target_thread = unsafe { GetWindowThreadProcessId(target, None) };
    let _foreground_attachment = ThreadAttachment::new(current_thread, foreground_thread);
    let _target_attachment = ThreadAttachment::new(current_thread, target_thread);
    // SAFETY: target HWND was revalidated; return values are only requests, verified by caller.
    let _ = unsafe { BringWindowToTop(target) };
    let _ = unsafe { SetForegroundWindow(target) };
    Ok(())
}

pub fn enumerate_monitors() -> Result<Vec<MonitorArea>, PlatformError> {
    unsafe extern "system" fn callback(
        monitor: HMONITOR,
        _hdc: HDC,
        _rect: *mut RECT,
        data: LPARAM,
    ) -> BOOL {
        // SAFETY: callback is synchronous and data points to the live Vec below.
        let monitors = unsafe { &mut *(data.0 as *mut Vec<MonitorArea>) };
        let mut info = MONITORINFO {
            cbSize: u32::try_from(size_of::<MONITORINFO>()).unwrap_or(u32::MAX),
            ..MONITORINFO::default()
        };
        // SAFETY: monitor is supplied by EnumDisplayMonitors and info has the required size.
        if unsafe { GetMonitorInfoW(monitor, &raw mut info).as_bool() } {
            let mut dpi_x = 96;
            let mut dpi_y = 96;
            // SAFETY: monitor is live during callback; both DPI output pointers are writable.
            let _ = unsafe {
                GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &raw mut dpi_x, &raw mut dpi_y)
            };
            monitors.push(MonitorArea {
                left: info.rcMonitor.left,
                top: info.rcMonitor.top,
                right: info.rcMonitor.right,
                bottom: info.rcMonitor.bottom,
                work_left: info.rcWork.left,
                work_top: info.rcWork.top,
                work_right: info.rcWork.right,
                work_bottom: info.rcWork.bottom,
                dpi: dpi_x,
                primary: info.dwFlags & MONITORINFOF_PRIMARY != 0,
            });
        }
        BOOL(1)
    }

    // SAFETY: changing process DPI awareness may fail if already set; either state is safe.
    let _ = unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
    let mut monitors = Vec::new();
    // SAFETY: callback is synchronous and LPARAM points to `monitors` until return.
    let callback: MONITORENUMPROC = Some(callback);
    let success = unsafe {
        EnumDisplayMonitors(
            HDC::default(),
            None,
            callback,
            LPARAM((&raw mut monitors).cast::<()>() as isize),
        )
    };
    if !success.as_bool() {
        return Err(PlatformError::Operation(
            "EnumDisplayMonitors failed".to_owned(),
        ));
    }
    Ok(monitors)
}

#[derive(Debug)]
struct OverlayContext {
    text: Vec<u16>,
    action: Arc<AtomicU8>,
}

unsafe extern "system" fn overlay_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: GWLP_USERDATA is assigned to a live Box<OverlayContext> after window creation.
    let context = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as *const OverlayContext;
    match message {
        WM_PAINT if !context.is_null() => {
            let mut paint = PAINTSTRUCT::default();
            // SAFETY: standard BeginPaint/EndPaint pair for the HWND in WM_PAINT.
            let hdc = unsafe { BeginPaint(hwnd, &raw mut paint) };
            let mut rect = RECT::default();
            let _ = unsafe { GetClientRect(hwnd, &raw mut rect) };
            let brush = unsafe { CreateSolidBrush(COLORREF(0x00F7_F7F7)) };
            unsafe { FillRect(hdc, &raw const rect, brush) };
            let _ = unsafe { DeleteObject(brush) };
            let _ = unsafe { SetBkMode(hdc, TRANSPARENT) };
            let _ = unsafe { SetTextColor(hdc, COLORREF(0x0020_2020)) };
            rect.left += 16;
            rect.top += 14;
            rect.right -= 16;
            rect.bottom -= 14;
            // SAFETY: context pointer and UTF-16 buffer stay alive until all windows are destroyed.
            let mut text = unsafe { (*context).text.clone() };
            unsafe {
                DrawTextW(
                    hdc,
                    &mut text,
                    &raw mut rect,
                    DT_LEFT | DT_WORDBREAK | DT_END_ELLIPSIS,
                )
            };
            let _ = unsafe { EndPaint(hwnd, &raw const paint) };
            LRESULT(0)
        }
        WM_LBUTTONUP if !context.is_null() => {
            let bytes = lparam.0.to_le_bytes();
            let x = i32::from(u16::from_le_bytes([bytes[0], bytes[1]]));
            let mut rect = RECT::default();
            let _ = unsafe { GetClientRect(hwnd, &raw mut rect) };
            let action = if x < (rect.right - rect.left) / 2 {
                1
            } else {
                2
            };
            unsafe { (*context).action.store(action, Ordering::Release) };
            LRESULT(0)
        }
        WM_CLOSE if !context.is_null() => {
            unsafe { (*context).action.store(2, Ordering::Release) };
            LRESULT(0)
        }
        WM_DESTROY => LRESULT(0),
        _ => {
            // SAFETY: unhandled messages are delegated to the OS default procedure.
            unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
        }
    }
}

#[allow(clippy::too_many_lines)]
pub fn show_overlay(request: &NotificationRequest) -> Result<OverlayOutcome, PlatformError> {
    let monitors = enumerate_monitors()?;
    if monitors.is_empty() {
        return Ok(OverlayOutcome::default());
    }
    // SAFETY: module handle is borrowed for the process lifetime.
    let module =
        unsafe { GetModuleHandleW(None) }.map_err(|error| operation("GetModuleHandleW", error))?;
    let class_name = wide_null("AnotiOverlayWindow");
    let class = WNDCLASSEXW {
        cbSize: u32::try_from(size_of::<WNDCLASSEXW>()).unwrap_or(u32::MAX),
        lpfnWndProc: Some(overlay_proc),
        hInstance: HINSTANCE(module.0),
        lpszClassName: PCWSTR(class_name.as_ptr()),
        ..WNDCLASSEXW::default()
    };
    // SAFETY: class pointers reference stack data through this call; duplicate registration is benign.
    unsafe { RegisterClassExW(&raw const class) };

    let action = Arc::new(AtomicU8::new(0));
    let target = request.identity.window_id.parse::<u64>().unwrap_or(0);
    let target_valid = validate_identity(target, &request.identity);
    let text = format!(
        "{}\n\n{}\n\nNhấp nửa trái để focus · nửa phải để đóng",
        request.title, request.message
    );
    let mut contexts = Vec::new();
    let mut windows = Vec::new();
    for monitor in monitors {
        let dpi = i32::try_from(monitor.dpi.max(96)).unwrap_or(96);
        let width = 380_i32.saturating_mul(dpi) / 96;
        let height = 180_i32.saturating_mul(dpi) / 96;
        let x = monitor.work_right - width - 16;
        let y = monitor.work_bottom - height - 16;
        let context = Box::new(OverlayContext {
            text: text.encode_utf16().collect(),
            action: Arc::clone(&action),
        });
        // SAFETY: class is registered, all handle arguments are null/non-owning, dimensions are bounded i32.
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
                PCWSTR(class_name.as_ptr()),
                PCWSTR(class_name.as_ptr()),
                WS_POPUP | WS_VISIBLE,
                x,
                y,
                width,
                height,
                None,
                None,
                HINSTANCE(module.0),
                None,
            )
        }
        .map_err(|error| operation("CreateWindowExW", error))?;
        // SAFETY: Box allocation is stable and kept in `contexts` until DestroyWindow completes.
        unsafe {
            SetWindowLongPtrW(
                hwnd,
                GWLP_USERDATA,
                std::ptr::from_ref::<OverlayContext>(context.as_ref()) as isize,
            )
        };
        // SAFETY: SWP_NOACTIVATE enforces the non-focus-stealing overlay invariant.
        unsafe { SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, SWP_NOACTIVATE) }
            .map_err(|error| operation("SetWindowPos", error))?;
        contexts.push(context);
        windows.push(hwnd);
    }

    let started = Instant::now();
    let timeout = (request.timeout > 0).then(|| Duration::from_secs(request.timeout));
    let mut active_since = None;
    loop {
        let mut message = MSG::default();
        // SAFETY: message buffer is writable; HWND null selects this thread's queue.
        while unsafe { PeekMessageW(&raw mut message, None, 0, 0, PM_REMOVE).as_bool() } {
            let _ = unsafe { TranslateMessage(&raw const message) };
            unsafe { DispatchMessageW(&raw const message) };
        }
        let state = action.load(Ordering::Acquire);
        if state != 0 || timeout.is_some_and(|timeout| started.elapsed() >= timeout) {
            break;
        }
        let active = target_valid && foreground_window()? == Some(target);
        if active {
            let since = *active_since.get_or_insert_with(Instant::now);
            if since.elapsed() >= Duration::from_secs_f64(request.auto_dismiss_delay.max(0.0)) {
                action.store(2, Ordering::Release);
                break;
            }
        } else {
            active_since = None;
        }
        thread::sleep(Duration::from_millis(10));
    }
    let state = action.load(Ordering::Acquire);
    let focused =
        state == 1 && target_valid && activate_and_verify(target, Duration::from_millis(750));
    for hwnd in windows {
        // SAFETY: each HWND was created by this function and is destroyed exactly once.
        let _ = unsafe { DestroyWindow(hwnd) };
    }
    drop(contexts);
    Ok(OverlayOutcome {
        displayed: true,
        dismissed: state == 2,
        focused,
    })
}

fn validate_identity(target: u64, identity: &anoti_core::WindowIdentity) -> bool {
    if target == 0 || !is_window(target) {
        return false;
    }
    if identity.window_pid > 0 && window_pid(target).ok() != Some(identity.window_pid) {
        return false;
    }
    identity.title_fingerprint.is_empty()
        || window_title(target).is_ok_and(|title| {
            !title.is_empty() && titles_compatible(&identity.title_fingerprint, &title)
        })
}

fn activate_and_verify(target: u64, timeout: Duration) -> bool {
    if request_activation(target).is_err() {
        return false;
    }
    let deadline = Instant::now() + timeout;
    loop {
        if foreground_window().ok().flatten() == Some(target) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        thread::sleep(Duration::from_millis(10));
    }
}

struct ComApartment(bool);

impl ComApartment {
    fn initialize() -> Result<Self, PlatformError> {
        // SAFETY: null reserved pointer and valid COINIT flag; balanced by Drop on success.
        unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) }
            .map(|| Self(true))
            .map_err(|error| operation("CoInitializeEx", error))
    }
}

impl Drop for ComApartment {
    fn drop(&mut self) {
        if self.0 {
            // SAFETY: balances this thread's successful CoInitializeEx call.
            unsafe { CoUninitialize() };
        }
    }
}

pub fn show_toast(request: &NotificationRequest) -> Result<(), PlatformError> {
    let _apartment = ComApartment::initialize()?;
    let app_id = HSTRING::from(APP_USER_MODEL_ID);
    // SAFETY: HSTRING owns its UTF-16 storage for the duration of the call.
    unsafe { SetCurrentProcessExplicitAppUserModelID(&app_id) }
        .map_err(|error| operation("SetCurrentProcessExplicitAppUserModelID", error))?;
    let launch = format!(
        "{FOCUS_PROTOCOL}:{}",
        xml_escape(&request.identity.window_id)
    );
    let xml = format!(
        "<toast launch=\"{launch}\"><visual><binding template=\"ToastGeneric\"><text>{}</text><text>{}</text></binding></visual><actions><action content=\"Focus\" activationType=\"protocol\" arguments=\"{launch}\"/></actions></toast>",
        xml_escape(&request.title),
        xml_escape(&request.message),
    );
    let document = XmlDocument::new().map_err(|error| operation("XmlDocument", error))?;
    document
        .LoadXml(&HSTRING::from(xml))
        .map_err(|error| operation("LoadXml", error))?;
    let toast = ToastNotification::CreateToastNotification(&document)
        .map_err(|error| operation("CreateToastNotification", error))?;
    let notifier = ToastNotificationManager::CreateToastNotifierWithId(&app_id)
        .map_err(|error| operation("CreateToastNotifierWithId", error))?;
    notifier
        .Show(&toast)
        .map_err(|error| operation("ToastNotifier.Show", error))
}

pub fn play_sound(sound: &str) -> Result<(), PlatformError> {
    if sound.trim().is_empty() {
        return Ok(());
    }
    let sound = wide_null(sound);
    // SAFETY: null-terminated owned UTF-16 buffer remains alive through PlaySoundW call.
    let played = unsafe {
        PlaySoundW(
            PCWSTR(sound.as_ptr()),
            None,
            SND_FILENAME | SND_ASYNC | SND_NODEFAULT,
        )
    };
    if played.as_bool() {
        Ok(())
    } else {
        Err(PlatformError::Operation("PlaySoundW failed".to_owned()))
    }
}

fn wide_null(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
