//! Bounded AT-SPI fallback for checking the active GNOME Wayland window.

use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use anoti_core::titles_compatible;
use anoti_platform::IdentityQuery;
use atspi::proxy::accessible::{AccessibleProxy, ObjectRefExt};
use atspi::proxy::bus::BusProxy;
use atspi::{State, zbus};

use crate::AdapterCall;

const ADAPTER_TIMEOUT: Duration = Duration::from_millis(750);
const METHOD_TIMEOUT: Duration = Duration::from_millis(400);

pub fn is_active(query: &IdentityQuery) -> AdapterCall<bool> {
    if !has_identity_hint(query) {
        return AdapterCall::Available(false);
    }

    let query = query.clone();
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let result = async_io::block_on(is_active_async(&query));
        let _ = sender.send(result);
    });

    match receiver.recv_timeout(ADAPTER_TIMEOUT) {
        Ok(Ok(active)) => AdapterCall::Available(active),
        Ok(Err(error)) => AdapterCall::Failed(error),
        Err(mpsc::RecvTimeoutError::Timeout) => {
            AdapterCall::Failed("AT-SPI active-window query timed out".to_owned())
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            AdapterCall::Failed("AT-SPI active-window worker stopped unexpectedly".to_owned())
        }
    }
}

async fn is_active_async(query: &IdentityQuery) -> Result<bool, String> {
    let session = zbus::connection::Builder::session()
        .map_err(|error| format!("open session bus: {error}"))?
        .method_timeout(METHOD_TIMEOUT)
        .build()
        .await
        .map_err(|error| format!("connect to session bus: {error}"))?;
    let bus = BusProxy::new(&session)
        .await
        .map_err(|error| format!("create accessibility bus proxy: {error}"))?;
    let address = bus
        .get_address()
        .await
        .map_err(|error| format!("obtain accessibility bus address: {error}"))?;
    let accessibility = zbus::connection::Builder::address(address.as_str())
        .map_err(|error| format!("parse accessibility bus address: {error}"))?
        .method_timeout(METHOD_TIMEOUT)
        .build()
        .await
        .map_err(|error| format!("connect to accessibility bus: {error}"))?;

    let registry = AccessibleProxy::builder(&accessibility)
        .destination("org.a11y.atspi.Registry")
        .map_err(|error| format!("set accessibility registry destination: {error}"))?
        .path("/org/a11y/atspi/accessible/root")
        .map_err(|error| format!("set accessibility registry path: {error}"))?
        .cache_properties(zbus::proxy::CacheProperties::No)
        .build()
        .await
        .map_err(|error| format!("create accessibility registry proxy: {error}"))?;
    let applications = registry
        .get_children()
        .await
        .map_err(|error| format!("enumerate accessible applications: {error}"))?;

    for application_ref in applications {
        if application_ref.is_null() {
            continue;
        }
        let Ok(application) = application_ref.as_accessible_proxy(&accessibility).await else {
            continue;
        };
        let application_name = application.name().await.unwrap_or_default();
        let Ok(windows) = application.get_children().await else {
            continue;
        };
        for window_ref in windows {
            if window_ref.is_null() {
                continue;
            }
            let Ok(window) = window_ref.as_accessible_proxy(&accessibility).await else {
                continue;
            };
            let Ok(states) = window.get_state().await else {
                continue;
            };
            if !states.contains(State::Active) {
                continue;
            }
            let window_name = window.name().await.unwrap_or_default();
            return Ok(matches_identity(query, &application_name, &window_name));
        }
    }
    Ok(false)
}

fn has_identity_hint(query: &IdentityQuery) -> bool {
    [
        query.title_fingerprint.as_str(),
        query.project_hint.as_str(),
        query.app_hint.as_str(),
    ]
    .iter()
    .any(|value| !value.trim().is_empty())
}

fn matches_identity(query: &IdentityQuery, application_name: &str, window_name: &str) -> bool {
    let application = application_name.trim().to_lowercase();
    let window = window_name.trim().to_lowercase();
    let title = query.title_fingerprint.trim();
    if !title.is_empty() && titles_compatible(title, window_name) {
        return true;
    }

    let project = query.project_hint.trim().to_lowercase();
    if !project.is_empty() && window.contains(&project) {
        return true;
    }

    let app = query.app_hint.trim().to_lowercase();
    !app.is_empty() && (application.contains(&app) || window.contains(&app))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn query() -> IdentityQuery {
        IdentityQuery {
            project_hint: "sample-project".to_owned(),
            app_hint: "codex".to_owned(),
            title_fingerprint: "sample-project — Codex".to_owned(),
            ..IdentityQuery::default()
        }
    }

    #[test]
    fn active_object_requires_an_identity_hint() {
        assert!(!has_identity_hint(&IdentityQuery::default()));
        assert!(has_identity_hint(&query()));
    }

    #[test]
    fn title_project_or_application_can_match() {
        assert!(matches_identity(
            &query(),
            "Codex",
            "sample-project — Codex"
        ));
        assert!(matches_identity(&query(), "Codex", "another project"));

        let project_only = IdentityQuery {
            project_hint: "sample-project".to_owned(),
            ..IdentityQuery::default()
        };
        assert!(matches_identity(
            &project_only,
            "terminal",
            "sample-project"
        ));
        assert!(!matches_identity(&project_only, "terminal", "unrelated"));
    }

    #[test]
    #[ignore = "requires a live Linux desktop accessibility bus"]
    fn native_accessibility_bus_returns_a_bounded_non_match() {
        let started = std::time::Instant::now();
        let missing = IdentityQuery {
            app_hint: "anoti-native-probe-that-does-not-exist".to_owned(),
            ..IdentityQuery::default()
        };
        assert_eq!(is_active(&missing), AdapterCall::Available(false));
        assert!(started.elapsed() < Duration::from_secs(2));
    }
}
