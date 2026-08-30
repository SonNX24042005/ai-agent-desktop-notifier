//! Win32/WinRT toast notification and sound boundary.

#![allow(unsafe_code)]

use anoti_core::NotificationRequest;
use anoti_platform::PlatformError;
use windows::Data::Xml::Dom::XmlDocument;
use windows::UI::Notifications::{ToastNotification, ToastNotificationManager};
use windows::Win32::Media::Audio::{PlaySoundW, SND_ASYNC, SND_FILENAME, SND_NODEFAULT};
use windows::Win32::System::Com::{COINIT_MULTITHREADED, CoInitializeEx, CoUninitialize};
use windows::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID;
use windows::core::{HSTRING, PCWSTR};

use crate::APP_USER_MODEL_ID;

fn operation(context: &str, error: impl std::fmt::Display) -> PlatformError {
    PlatformError::Operation(format!("{context}: {error}"))
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
    let _ = unsafe { SetCurrentProcessExplicitAppUserModelID(&app_id) };
    let icon_uri = anoti_core::resolve_icon_path(request.resolved_icon_name(), None)
        .map(|path| crate::xml::to_file_uri(&path));
    let xml = crate::xml::build_toast_xml(&request.title, &request.message, icon_uri.as_deref());
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
