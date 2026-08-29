//! Webhook configuration and delivery policy shared by platform backends.

use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use thiserror::Error;

use anoti_core::NotificationRequest;

#[derive(Debug, Error)]
pub enum DeliveryError {
    #[error("delivery configuration path is unavailable")]
    ConfigPathUnavailable,
    #[error("delivery configuration I/O failed for {path}: {source}")]
    Io { path: PathBuf, source: io::Error },
    #[error("delivery configuration is invalid: {0}")]
    InvalidConfig(#[from] serde_json::Error),
    #[error("delivery failed: {0}")]
    Failed(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebhookEndpoint {
    pub name: String,
    pub url: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct OverlayState {
    pub focus_in_flight: bool,
    pub closed: bool,
    pub dismissed: bool,
    active_since: Option<f64>,
}

impl OverlayState {
    pub fn request_focus(&mut self) -> bool {
        if self.closed || self.focus_in_flight {
            return false;
        }
        self.focus_in_flight = true;
        true
    }

    pub fn complete_focus(&mut self, verified: bool) {
        self.focus_in_flight = false;
        if verified {
            self.closed = true;
        }
    }

    pub fn dismiss(&mut self) {
        self.dismissed = true;
        self.closed = true;
    }

    pub fn background_click(&mut self, originated_from_button: bool) {
        if !originated_from_button {
            self.dismiss();
        }
    }

    pub fn poll_active(&mut self, now: f64, active: bool, delay: f64) -> bool {
        if !active {
            self.active_since = None;
            return false;
        }
        let active_since = *self.active_since.get_or_insert(now);
        if now - active_since >= delay.max(0.0) {
            self.dismiss();
            return true;
        }
        false
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebhookFailure {
    pub endpoint_name: String,
    pub reason: String,
}

/// Sends only approved presentation fields; identity and questions stay local.
#[must_use]
pub fn dispatch_webhooks(
    endpoints: &[WebhookEndpoint],
    request: &NotificationRequest,
) -> Vec<WebhookFailure> {
    let text = format!(
        "[{}] {}\n{}",
        request.app_name, request.title, request.message
    );
    endpoints
        .iter()
        .filter_map(|endpoint| {
            let name = endpoint.name.to_ascii_lowercase();
            let body = if name.contains("feishu") || name.contains("lark") {
                json!({"msg_type":"text","content":{"text":text}})
            } else if name.contains("dingtalk") {
                json!({"msgtype":"text","text":{"content":text}})
            } else if name.contains("slack") || name.contains("discord") {
                json!({"text":text})
            } else {
                json!({"title":request.title,"message":request.message,"app":request.app_name})
            };
            ureq::post(&endpoint.url)
                .timeout(std::time::Duration::from_secs(3))
                .set("Content-Type", "application/json")
                .send_json(body)
                .err()
                .map(|error| WebhookFailure {
                    endpoint_name: endpoint.name.clone(),
                    reason: match error {
                        ureq::Error::Status(code, _) => format!("HTTP status {code}"),
                        ureq::Error::Transport(_) => "transport failure".to_owned(),
                    },
                })
        })
        .collect()
}

pub fn dispatch_webhooks_async(
    endpoints: Vec<WebhookEndpoint>,
    request: NotificationRequest,
) -> io::Result<std::thread::JoinHandle<Vec<WebhookFailure>>> {
    std::thread::Builder::new()
        .name("anoti-webhooks".to_owned())
        .spawn(move || dispatch_webhooks(&endpoints, &request))
}

/// Discovers the user configuration without assuming one platform's home variable.
pub fn config_path() -> Result<PathBuf, DeliveryError> {
    if let Some(path) = env::var_os("AI_AGENT_NOTIFIER_CONFIG") {
        return Ok(PathBuf::from(path));
    }
    #[cfg(windows)]
    let directory = env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)
        .map(|home| home.join(".config"));
    #[cfg(not(windows))]
    let directory = env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")));
    directory
        .map(|directory| directory.join("ai-agent-notifier").join("config.json"))
        .ok_or(DeliveryError::ConfigPathUnavailable)
}

/// Creates a sample only when no config exists and never overwrites user settings.
pub fn ensure_default_config(path: &Path) -> Result<bool, DeliveryError> {
    if path.exists() {
        return Ok(false);
    }
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|source| DeliveryError::Io {
        path: parent.to_path_buf(),
        source,
    })?;
    let sample = json!({
        "webhooks": {
            "slack": "",
            "discord": "",
            "ntfy": ""
        }
    });
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|source| DeliveryError::Io {
            path: path.to_path_buf(),
            source,
        })?;
    serde_json::to_writer_pretty(&mut file, &sample)?;
    file.write_all(b"\n")
        .and_then(|()| file.sync_all())
        .map_err(|source| DeliveryError::Io {
            path: path.to_path_buf(),
            source,
        })?;
    Ok(true)
}

/// Loads only valid HTTP(S) endpoints while leaving the source document untouched.
pub fn load_webhooks(path: &Path) -> Result<Vec<WebhookEndpoint>, DeliveryError> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path).map_err(|source| DeliveryError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    let config: Value = serde_json::from_str(&content)?;
    let Some(webhooks) = config.get("webhooks").and_then(Value::as_object) else {
        return Ok(Vec::new());
    };
    Ok(webhooks
        .iter()
        .filter_map(|(name, url)| {
            let url = url.as_str()?.trim();
            if name.trim().is_empty()
                || !(url.starts_with("https://") || url.starts_with("http://"))
            {
                return None;
            }
            Some(WebhookEndpoint {
                name: name.clone(),
                url: url.to_owned(),
            })
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn default_config_does_not_overwrite_existing_data() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("config.json");
        assert!(ensure_default_config(&path).unwrap());
        fs::write(
            &path,
            r#"{"third_party":true,"webhooks":{"slack":"https://example.test/hook"}}"#,
        )
        .unwrap();
        assert!(!ensure_default_config(&path).unwrap());
        assert!(fs::read_to_string(path).unwrap().contains("third_party"));
    }

    #[test]
    fn webhook_validation_accepts_only_http_endpoints() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("config.json");
        fs::write(
            &path,
            r#"{"unknown":{"keep":true},"webhooks":{"slack":"https://example.test/a","local":"http://127.0.0.1/hook","file":"file:///secret","empty":""}}"#,
        )
        .unwrap();
        let endpoints = load_webhooks(&path).unwrap();
        assert_eq!(endpoints.len(), 2);
        assert!(
            endpoints
                .iter()
                .all(|endpoint| endpoint.url.starts_with("http"))
        );
    }

    #[test]
    fn overlay_focus_is_single_flight_and_failure_does_not_close() {
        let mut state = OverlayState::default();
        assert!(state.request_focus());
        assert!(!state.request_focus());
        state.complete_focus(false);
        assert!(!state.closed);
        assert!(state.request_focus());
        state.complete_focus(true);
        assert!(state.closed);
    }

    #[test]
    fn button_click_is_not_consumed_as_background_dismiss() {
        let mut state = OverlayState::default();
        state.background_click(true);
        assert!(!state.closed);
        state.background_click(false);
        assert!(state.dismissed);
    }

    #[test]
    fn auto_dismiss_requires_continuous_activity() {
        let mut state = OverlayState::default();
        assert!(!state.poll_active(1.0, true, 1.5));
        assert!(!state.poll_active(2.0, false, 1.5));
        assert!(!state.poll_active(3.0, true, 1.5));
        assert!(state.poll_active(4.5, true, 1.5));
        assert!(state.dismissed);
    }
}
