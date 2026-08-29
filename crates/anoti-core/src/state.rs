//! Cross-platform persistent state with transactional read-modify-write operations.

use std::{
    collections::HashMap,
    env,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use fs2::FileExt;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;
use thiserror::Error;

use crate::{QueueItem, QueueStatus, SessionRecord, WindowIdentity, generate_window_instance_id};

const SESSION_MAX_AGE: f64 = 86_400.0;
const SESSION_MAX_ENTRIES: usize = 64;
const QUEUE_MAX_AGE: f64 = 14_400.0;
const DEDUPE_RETENTION: f64 = 60.0;

#[derive(Debug, Error)]
pub enum StateError {
    #[error("state I/O failed for {path}: {source}")]
    Io { path: PathBuf, source: io::Error },
    #[error("state serialization failed: {0}")]
    Json(#[from] serde_json::Error),
    #[error("timed out acquiring state lock: {0}")]
    LockTimeout(PathBuf),
}

fn io_error(path: &Path, source: io::Error) -> StateError {
    StateError::Io {
        path: path.to_path_buf(),
        source,
    }
}

#[derive(Debug, Clone)]
pub struct RuntimePaths {
    pub root: PathBuf,
    pub sessions: PathBuf,
    pub session_lock: PathBuf,
    pub queue: PathBuf,
    pub queue_lock: PathBuf,
    pub dedupe: PathBuf,
    pub dedupe_lock: PathBuf,
    pub overlay_lock: PathBuf,
}

impl RuntimePaths {
    pub fn discover() -> Result<Self, StateError> {
        let root = if let Some(override_path) = env::var_os("AI_AGENT_NOTIFIER_RUNTIME_DIR") {
            PathBuf::from(override_path)
        } else if cfg!(windows) {
            let base = env::var_os("LOCALAPPDATA")
                .or_else(|| env::var_os("TEMP"))
                .map_or_else(env::temp_dir, PathBuf::from);
            base.join("ai-agent-notifier").join("runtime")
        } else if let Some(xdg) = env::var_os("XDG_RUNTIME_DIR") {
            PathBuf::from(xdg).join("ai-agent-notifier")
        } else {
            env::temp_dir().join(format!("ai-agent-notifier-{}", current_user_id()))
        };
        Self::from_root(root)
    }

    pub fn from_root(root: PathBuf) -> Result<Self, StateError> {
        fs::create_dir_all(&root).map_err(|error| io_error(&root, error))?;
        set_private_dir_permissions(&root)?;
        Ok(Self {
            sessions: root.join("ai_agent_notifier_sessions.json"),
            session_lock: root.join("ai_agent_notifier_sessions.lock"),
            queue: root.join("ai_agent_notifier_queue.json"),
            queue_lock: root.join("ai_agent_notifier_queue.lock"),
            dedupe: root.join("ai_agent_notifier_dedupe.json"),
            dedupe_lock: root.join("ai_agent_notifier_dedupe.lock"),
            overlay_lock: root.join("ai_agent_notifier_overlay.lock"),
            root,
        })
    }
}

#[cfg(target_os = "linux")]
fn current_user_id() -> u32 {
    use std::os::unix::fs::MetadataExt;
    fs::metadata("/proc/self").map_or(0, |metadata| metadata.uid())
}

#[cfg(not(target_os = "linux"))]
fn current_user_id() -> u32 {
    0
}

#[cfg(unix)]
fn set_private_dir_permissions(path: &Path) -> Result<(), StateError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|error| io_error(path, error))
}

#[cfg(not(unix))]
#[allow(clippy::unnecessary_wraps)]
fn set_private_dir_permissions(_path: &Path) -> Result<(), StateError> {
    Ok(())
}

#[cfg(unix)]
fn set_private_file_permissions(path: &Path) -> Result<(), StateError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|error| io_error(path, error))
}

#[cfg(not(unix))]
#[allow(clippy::unnecessary_wraps)]
fn set_private_file_permissions(_path: &Path) -> Result<(), StateError> {
    Ok(())
}

struct StateLock {
    file: File,
}

impl StateLock {
    fn acquire(path: &Path, timeout: Duration) -> Result<Self, StateError> {
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path)
            .map_err(|error| io_error(path, error))?;
        set_private_file_permissions(path)?;
        let started = Instant::now();
        loop {
            match file.try_lock_exclusive() {
                Ok(()) => return Ok(Self { file }),
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    if started.elapsed() >= timeout {
                        return Err(StateError::LockTimeout(path.to_path_buf()));
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => return Err(io_error(path, error)),
            }
        }
    }
}

impl Drop for StateLock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

fn load_json_or_default<T>(path: &Path) -> T
where
    T: DeserializeOwned + Default,
{
    fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str(&content).ok())
        .unwrap_or_default()
}

fn atomic_write_json<T: Serialize>(path: &Path, value: &T) -> Result<(), StateError> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|error| io_error(parent, error))?;
    let mut temporary = NamedTempFile::new_in(parent).map_err(|error| io_error(parent, error))?;
    serde_json::to_writer(&mut temporary, value)?;
    temporary
        .write_all(b"\n")
        .map_err(|error| io_error(temporary.path(), error))?;
    temporary
        .as_file()
        .sync_all()
        .map_err(|error| io_error(temporary.path(), error))?;
    set_private_file_permissions(temporary.path())?;
    temporary
        .persist(path)
        .map_err(|error| io_error(path, error.error))?;
    Ok(())
}

#[must_use]
pub fn epoch_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

#[derive(Debug, Clone)]
pub struct SessionStore {
    paths: RuntimePaths,
}

impl SessionStore {
    #[must_use]
    pub const fn new(paths: RuntimePaths) -> Self {
        Self { paths }
    }

    pub fn get(&self, session_id: &str) -> Result<Option<SessionRecord>, StateError> {
        let _lock = StateLock::acquire(&self.paths.session_lock, Duration::from_millis(500))?;
        let entries: HashMap<String, Value> = load_json_or_default(&self.paths.sessions);
        Ok(entries.get(session_id).and_then(session_from_value))
    }

    /// Resolves an exact record by session id first, then by verified caller provenance.
    /// Caller fallback is accepted only when every strongest candidate identifies the same
    /// native window, so a shared application process can never select an arbitrary window.
    pub fn resolve_exact(
        &self,
        identity: &WindowIdentity,
    ) -> Result<Option<SessionRecord>, StateError> {
        let _lock = StateLock::acquire(&self.paths.session_lock, Duration::from_millis(500))?;
        let now = epoch_seconds();
        let entries: HashMap<String, Value> = load_json_or_default(&self.paths.sessions);
        let records = entries
            .values()
            .filter_map(session_from_value)
            .filter(|record| {
                record.has_exact_window_identity() && now - record.updated_at < SESSION_MAX_AGE
            })
            .collect::<Vec<_>>();

        if let Some(record) = entries
            .get(identity.session_id.trim())
            .and_then(session_from_value)
            .filter(SessionRecord::has_exact_window_identity)
            .filter(|record| caller_is_compatible(identity, record))
        {
            if identity.generation > 0 && record.generation > identity.generation {
                return Ok(None);
            }
            if !identity.window_instance_id.is_empty()
                && !record.window_instance_id.is_empty()
                && record.window_instance_id != identity.window_instance_id
            {
                return Ok(None);
            }
            if identity.process_start_time > 0
                && record.process_start_time > 0
                && record.process_start_time != identity.process_start_time
            {
                return Ok(None);
            }
            return Ok(Some(record));
        }

        let best_strength = records
            .iter()
            .map(|record| caller_match_strength(identity, record))
            .max()
            .unwrap_or(0);
        if best_strength == 0 {
            return Ok(None);
        }
        let candidates = records
            .into_iter()
            .filter(|record| caller_match_strength(identity, record) == best_strength)
            .collect::<Vec<_>>();
        let Some(first) = candidates.first() else {
            return Ok(None);
        };
        if candidates.iter().any(|record| {
            record.window_id != first.window_id
                || record.window_pid != first.window_pid
                || (record.process_start_time > 0
                    && first.process_start_time > 0
                    && record.process_start_time != first.process_start_time)
                || (!record.window_instance_id.is_empty()
                    && !first.window_instance_id.is_empty()
                    && record.window_instance_id != first.window_instance_id)
        }) {
            return Ok(None);
        }
        Ok(candidates
            .into_iter()
            .max_by(|left, right| left.updated_at.total_cmp(&right.updated_at)))
    }

    pub fn save(&self, session_id: &str, record: SessionRecord) -> Result<(), StateError> {
        self.save_with_policy(session_id, record, false)
    }

    /// Saves an explicit session-start capture. An exact identity may move only when the
    /// capture comes from a different concrete caller process, which represents a new agent
    /// runtime rather than a repeated hook from the existing runtime.
    pub fn save_capture(&self, session_id: &str, record: SessionRecord) -> Result<(), StateError> {
        self.save_with_policy(session_id, record, true)
    }

    fn save_with_policy(
        &self,
        session_id: &str,
        mut record: SessionRecord,
        allow_new_source: bool,
    ) -> Result<(), StateError> {
        if session_id.trim().is_empty() {
            return Ok(());
        }
        let _lock = StateLock::acquire(&self.paths.session_lock, Duration::from_millis(1500))?;
        let now = epoch_seconds();
        let mut entries: HashMap<String, Value> = load_json_or_default(&self.paths.sessions);
        entries.retain(|_, value| {
            session_from_value(value).is_some_and(|entry| now - entry.updated_at < SESSION_MAX_AGE)
        });
        if let Some(existing) = entries.get(session_id).and_then(session_from_value)
            && existing.has_exact_window_identity()
        {
            if !record.has_exact_window_identity() {
                return Ok(());
            }
            let source_changed = capture_source_changed(&existing, &record);
            let window_changed = existing.window_id != record.window_id
                || (existing.process_start_time > 0
                    && record.process_start_time > 0
                    && existing.process_start_time != record.process_start_time);
            if window_changed || source_changed {
                if !allow_new_source || !source_changed {
                    return Ok(());
                }
                record.generation = existing.generation + 1;
                record.window_instance_id = generate_window_instance_id();
            } else {
                record.generation = existing.generation.max(1);
                record.window_instance_id = existing.window_instance_id;
            }
        } else {
            if record.generation == 0 {
                record.generation = 1;
            }
            if record.window_instance_id.is_empty() && !record.window_id.is_empty() {
                record.window_instance_id = generate_window_instance_id();
            }
        }
        entries.insert(session_id.to_owned(), serde_json::to_value(record)?);
        if entries.len() > SESSION_MAX_ENTRIES {
            let mut oldest = entries
                .iter()
                .map(|(key, value)| {
                    (
                        key.clone(),
                        session_from_value(value).map_or(0.0, |entry| entry.updated_at),
                    )
                })
                .collect::<Vec<_>>();
            oldest.sort_by(|left, right| left.1.total_cmp(&right.1));
            for (key, _) in oldest.into_iter().take(entries.len() - SESSION_MAX_ENTRIES) {
                entries.remove(&key);
            }
        }
        atomic_write_json(&self.paths.sessions, &entries)
    }
}

fn capture_source_changed(existing: &SessionRecord, incoming: &SessionRecord) -> bool {
    if existing.caller_pid > 1
        && incoming.caller_pid > 1
        && existing.caller_pid != incoming.caller_pid
    {
        return true;
    }
    if existing.process_start_time > 0
        && incoming.process_start_time > 0
        && existing.process_start_time != incoming.process_start_time
    {
        return true;
    }
    if !existing.terminal_screen.is_empty()
        && !incoming.terminal_screen.is_empty()
        && existing.terminal_screen != incoming.terminal_screen
    {
        return true;
    }
    if !existing.caller_tty.is_empty()
        && !incoming.caller_tty.is_empty()
        && existing.caller_tty != incoming.caller_tty
    {
        return true;
    }
    false
}

fn caller_is_compatible(identity: &WindowIdentity, record: &SessionRecord) -> bool {
    identity.caller_pid <= 1
        || record.caller_pid <= 1
        || caller_match_strength(identity, record) > 0
}

fn caller_match_strength(identity: &WindowIdentity, record: &SessionRecord) -> u8 {
    if identity.caller_pid <= 1 || record.caller_pid <= 1 {
        return 0;
    }
    if identity.caller_pid == record.caller_pid {
        return 3;
    }
    if identity.caller_pid_chain.contains(&record.caller_pid)
        || record.caller_pid_chain.contains(&identity.caller_pid)
    {
        return 2;
    }
    0
}

fn session_from_value(value: &Value) -> Option<SessionRecord> {
    if let Some(window_id) = value.as_str() {
        return Some(SessionRecord {
            window_id: window_id.to_owned(),
            ..SessionRecord::default()
        });
    }
    serde_json::from_value(value.clone())
        .ok()
        .filter(|record: &SessionRecord| record.schema_version <= 4)
}

#[derive(Debug, Clone)]
pub struct QueueStore {
    paths: RuntimePaths,
}

impl QueueStore {
    #[must_use]
    pub const fn new(paths: RuntimePaths) -> Self {
        Self { paths }
    }

    pub fn load(&self) -> Result<HashMap<String, QueueItem>, StateError> {
        let _lock = StateLock::acquire(&self.paths.queue_lock, Duration::from_secs(1))?;
        let now = epoch_seconds();
        let mut queue: HashMap<String, QueueItem> = load_json_or_default(&self.paths.queue);
        queue.retain(|_, item| now - item.created_at < QUEUE_MAX_AGE);
        Ok(queue)
    }

    pub fn save(&self, key: &str, mut item: QueueItem) -> Result<(), StateError> {
        if key.is_empty() {
            return Ok(());
        }
        let _lock = StateLock::acquire(&self.paths.queue_lock, Duration::from_millis(1500))?;
        let now = epoch_seconds();
        let mut queue: HashMap<String, QueueItem> = load_json_or_default(&self.paths.queue);
        queue.retain(|existing_key, existing| {
            now - existing.created_at < QUEUE_MAX_AGE
                && !(existing_key != key
                    && !item.session_id.is_empty()
                    && existing.session_id == item.session_id)
        });
        if item.status == QueueStatus::Displaying {
            for existing in queue.values_mut() {
                if existing.status == QueueStatus::Displaying {
                    existing.status = QueueStatus::Queued;
                }
            }
        }
        if item.generation == 0 {
            item.generation = queue
                .values()
                .map(|existing| existing.generation)
                .max()
                .unwrap_or(0)
                + 1;
        }
        queue.insert(key.to_owned(), item);
        atomic_write_json(&self.paths.queue, &queue)
    }

    pub fn remove(&self, key: &str) -> Result<bool, StateError> {
        let _lock = StateLock::acquire(&self.paths.queue_lock, Duration::from_millis(1500))?;
        let mut queue: HashMap<String, QueueItem> = load_json_or_default(&self.paths.queue);
        let removed = queue.remove(key).is_some();
        if removed {
            atomic_write_json(&self.paths.queue, &queue)?;
        }
        Ok(removed)
    }

    pub fn requeue_displaying(&self, exclude_key: Option<&str>) -> Result<(), StateError> {
        let _lock = StateLock::acquire(&self.paths.queue_lock, Duration::from_millis(1500))?;
        let mut queue: HashMap<String, QueueItem> = load_json_or_default(&self.paths.queue);
        for (key, item) in &mut queue {
            if item.status == QueueStatus::Displaying && exclude_key != Some(key.as_str()) {
                item.status = QueueStatus::Queued;
            }
        }
        atomic_write_json(&self.paths.queue, &queue)
    }

    pub fn oldest_pending(&self) -> Result<Option<(String, QueueItem)>, StateError> {
        Ok(self
            .load()?
            .into_iter()
            .filter(|(_, item)| item.status == QueueStatus::Queued)
            .min_by(|left, right| left.1.created_at.total_cmp(&right.1.created_at)))
    }

    pub fn oldest_actionable(&self) -> Result<Option<(String, QueueItem)>, StateError> {
        Ok(self.load()?.into_iter().min_by(|left, right| {
            let left_rank = u8::from(left.1.status != QueueStatus::Displaying);
            let right_rank = u8::from(right.1.status != QueueStatus::Displaying);
            left_rank
                .cmp(&right_rank)
                .then_with(|| left.1.created_at.total_cmp(&right.1.created_at))
        }))
    }
}

#[must_use]
pub fn queue_key(identity: &WindowIdentity) -> String {
    let session = identity.session_id.trim();
    if !session.is_empty() {
        return if session.starts_with("sess_") {
            session.to_owned()
        } else {
            format!("sess_{session}")
        };
    }
    let window = identity.window_id.trim();
    if !window.is_empty() && window.chars().all(|character| character.is_ascii_digit()) {
        return if window.starts_with("win_") {
            window.to_owned()
        } else {
            format!("win_{window}")
        };
    }
    if identity.caller_pid > 0 {
        return format!("pid_{}", identity.caller_pid);
    }
    let project = identity.project_hint.trim();
    if !project.is_empty() {
        return format!("proj_{project}");
    }
    "default_target".to_owned()
}

#[derive(Debug, Clone)]
pub struct DedupeStore {
    paths: RuntimePaths,
}

impl DedupeStore {
    #[must_use]
    pub const fn new(paths: RuntimePaths) -> Self {
        Self { paths }
    }

    pub fn check_and_record(
        &self,
        app_name: &str,
        title: &str,
        message: &str,
        window: Duration,
    ) -> Result<bool, StateError> {
        let _lock = StateLock::acquire(&self.paths.dedupe_lock, Duration::from_secs(1))?;
        let now = epoch_seconds();
        let mut cache: HashMap<String, f64> = load_json_or_default(&self.paths.dedupe);
        cache.retain(|_, timestamp| now - *timestamp < DEDUPE_RETENTION);
        let digest = Sha256::digest(format!("{app_name}|{title}|{message}").as_bytes());
        let key = hex::encode(digest);
        let duplicate = cache
            .get(&key)
            .is_some_and(|timestamp| now - *timestamp < window.as_secs_f64());
        cache.insert(key, now);
        atomic_write_json(&self.paths.dedupe, &cache)?;
        Ok(duplicate)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Barrier};

    use super::*;
    use crate::{EventKind, Urgency};

    fn paths() -> (tempfile::TempDir, RuntimePaths) {
        let directory = tempfile::tempdir().unwrap();
        let paths = RuntimePaths::from_root(directory.path().join("runtime")).unwrap();
        (directory, paths)
    }

    #[test]
    fn legacy_string_session_is_read() {
        let (_directory, paths) = paths();
        fs::write(&paths.sessions, r#"{"session":"12345"}"#).unwrap();
        let record = SessionStore::new(paths).get("session").unwrap().unwrap();
        assert_eq!(record.window_id, "12345");
        assert_eq!(record.schema_version, 4);
    }

    #[test]
    fn corrupted_session_falls_back_to_empty() {
        let (_directory, paths) = paths();
        fs::write(&paths.sessions, "{broken").unwrap();
        assert!(SessionStore::new(paths).get("session").unwrap().is_none());
    }

    #[test]
    fn unknown_future_session_schema_falls_back_to_empty() {
        let (_directory, paths) = paths();
        fs::write(
            &paths.sessions,
            r#"{"session":{"schema_version":99,"window_id":"12345"}}"#,
        )
        .unwrap();
        assert!(SessionStore::new(paths).get("session").unwrap().is_none());
    }

    #[test]
    fn exact_session_identity_is_not_rebound_to_another_window() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        store
            .save(
                "session",
                SessionRecord {
                    window_id: "wayland:41".into(),
                    window_pid: 410,
                    title_fingerprint: "first terminal".into(),
                    precision: "window".into(),
                    updated_at: epoch_seconds(),
                    ..SessionRecord::default()
                },
            )
            .unwrap();
        store
            .save(
                "session",
                SessionRecord {
                    window_id: "wayland:42".into(),
                    window_pid: 420,
                    title_fingerprint: "second terminal".into(),
                    precision: "window".into(),
                    updated_at: epoch_seconds(),
                    ..SessionRecord::default()
                },
            )
            .unwrap();

        assert_eq!(
            store.get("session").unwrap().unwrap().window_id,
            "wayland:41"
        );
    }

    #[test]
    fn explicit_capture_rebinds_exact_identity_for_a_new_agent_process() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        store
            .save_capture(
                "session",
                SessionRecord {
                    window_id: "wayland:41".into(),
                    window_pid: 410,
                    caller_pid: 411,
                    title_fingerprint: "first terminal".into(),
                    precision: "window".into(),
                    updated_at: epoch_seconds(),
                    ..SessionRecord::default()
                },
            )
            .unwrap();
        store
            .save_capture(
                "session",
                SessionRecord {
                    window_id: "wayland:42".into(),
                    window_pid: 420,
                    caller_pid: 421,
                    title_fingerprint: "second terminal".into(),
                    precision: "window".into(),
                    updated_at: epoch_seconds(),
                    ..SessionRecord::default()
                },
            )
            .unwrap();

        let record = store.get("session").unwrap().unwrap();
        assert_eq!(record.window_id, "wayland:42");
        assert_eq!(record.caller_pid, 421);
    }

    #[test]
    fn repeated_capture_cannot_rebind_the_same_agent_process() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        for window_id in ["wayland:41", "wayland:42"] {
            store
                .save_capture(
                    "session",
                    SessionRecord {
                        window_id: window_id.into(),
                        window_pid: 410,
                        caller_pid: 411,
                        title_fingerprint: "terminal".into(),
                        precision: "window".into(),
                        updated_at: epoch_seconds(),
                        ..SessionRecord::default()
                    },
                )
                .unwrap();
        }

        assert_eq!(
            store.get("session").unwrap().unwrap().window_id,
            "wayland:41"
        );
    }

    #[test]
    fn exact_identity_resolves_by_caller_when_agent_event_ids_differ() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        store
            .save_capture(
                "session-start-id",
                SessionRecord {
                    window_id: "wayland:51".into(),
                    window_pid: 510,
                    caller_pid: 511,
                    caller_pid_chain: vec![511, 500],
                    title_fingerprint: "second agent window".into(),
                    precision: "window".into(),
                    updated_at: epoch_seconds(),
                    ..SessionRecord::default()
                },
            )
            .unwrap();

        let resolved = store
            .resolve_exact(&WindowIdentity {
                session_id: "completion-event-id".into(),
                caller_pid: 511,
                caller_pid_chain: vec![511, 500],
                ..WindowIdentity::default()
            })
            .unwrap()
            .unwrap();
        assert_eq!(resolved.window_id, "wayland:51");
    }

    #[test]
    fn caller_fallback_rejects_multiple_native_windows_at_the_same_strength() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        for (session_id, window_id) in [
            ("first-session", "wayland:51"),
            ("second-session", "wayland:52"),
        ] {
            store
                .save_capture(
                    session_id,
                    SessionRecord {
                        window_id: window_id.into(),
                        window_pid: 510,
                        caller_pid: 511,
                        caller_pid_chain: vec![511, 500],
                        title_fingerprint: "shared application".into(),
                        precision: "window".into(),
                        updated_at: epoch_seconds(),
                        ..SessionRecord::default()
                    },
                )
                .unwrap();
        }

        assert!(
            store
                .resolve_exact(&WindowIdentity {
                    session_id: "completion-event-id".into(),
                    caller_pid: 511,
                    caller_pid_chain: vec![511, 500],
                    ..WindowIdentity::default()
                })
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn displaying_item_requeues_previous_and_oldest_is_stable() {
        let (_directory, paths) = paths();
        let store = QueueStore::new(paths);
        let first = QueueItem {
            app_name: "Claude Code".into(),
            urgency: Urgency::Critical,
            event_kind: EventKind::Permission,
            status: QueueStatus::Displaying,
            created_at: epoch_seconds(),
            ..QueueItem::default()
        };
        store.save("sess_first", first).unwrap();
        let second = QueueItem {
            app_name: "Codex".into(),
            status: QueueStatus::Displaying,
            created_at: epoch_seconds() + 1.0,
            ..QueueItem::default()
        };
        store.save("sess_second", second).unwrap();
        let queue = store.load().unwrap();
        assert_eq!(queue["sess_first"].status, QueueStatus::Queued);
        assert_eq!(queue["sess_second"].status, QueueStatus::Displaying);
        assert_eq!(store.oldest_pending().unwrap().unwrap().0, "sess_first");
    }

    #[test]
    fn focus_prefers_the_notification_currently_being_displayed() {
        let (_directory, paths) = paths();
        let store = QueueStore::new(paths);
        let now = epoch_seconds();
        store
            .save(
                "queued",
                QueueItem {
                    status: QueueStatus::Queued,
                    created_at: now - 1.0,
                    ..QueueItem::default()
                },
            )
            .unwrap();
        store
            .save(
                "displaying",
                QueueItem {
                    status: QueueStatus::Displaying,
                    created_at: now,
                    ..QueueItem::default()
                },
            )
            .unwrap();
        assert_eq!(store.oldest_actionable().unwrap().unwrap().0, "displaying");
    }

    #[test]
    fn focus_failure_can_preserve_item() {
        let (_directory, paths) = paths();
        let store = QueueStore::new(paths);
        store
            .save(
                "sess_one",
                QueueItem {
                    created_at: epoch_seconds(),
                    ..QueueItem::default()
                },
            )
            .unwrap();
        assert!(store.load().unwrap().contains_key("sess_one"));
    }

    #[test]
    fn dedupe_is_one_atomic_operation() {
        let (_directory, paths) = paths();
        let store = DedupeStore::new(paths);
        assert!(
            !store
                .check_and_record("Codex", "Done", "Message", Duration::from_secs(2))
                .unwrap()
        );
        assert!(
            store
                .check_and_record("Codex", "Done", "Message", Duration::from_secs(2))
                .unwrap()
        );
    }

    #[test]
    fn concurrent_queue_writers_preserve_every_distinct_item() {
        let (_directory, paths) = paths();
        let store = Arc::new(QueueStore::new(paths));
        let start = Arc::new(Barrier::new(4));
        let handles = (0..4)
            .map(|index| {
                let store = Arc::clone(&store);
                let start = Arc::clone(&start);
                std::thread::spawn(move || {
                    start.wait();
                    store
                        .save(
                            &format!("sess_{index}"),
                            QueueItem {
                                session_id: format!("{index}"),
                                created_at: epoch_seconds(),
                                ..QueueItem::default()
                            },
                        )
                        .unwrap();
                })
            })
            .collect::<Vec<_>>();
        for handle in handles {
            handle.join().unwrap();
        }
        let queue = store.load().unwrap();
        assert_eq!(queue.len(), 4);
        let generations = queue
            .values()
            .map(|item| item.generation)
            .collect::<std::collections::HashSet<_>>();
        assert_eq!(generations.len(), 4);
    }

    #[test]
    fn concurrent_dedupe_has_exactly_one_first_writer() {
        let (_directory, paths) = paths();
        let store = Arc::new(DedupeStore::new(paths));
        let start = Arc::new(Barrier::new(4));
        let handles = (0..4)
            .map(|_| {
                let store = Arc::clone(&store);
                let start = Arc::clone(&start);
                std::thread::spawn(move || {
                    start.wait();
                    store
                        .check_and_record("Codex", "Done", "Message", Duration::from_secs(2))
                        .unwrap()
                })
            })
            .collect::<Vec<_>>();
        let duplicates = handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .filter(|duplicate| *duplicate)
            .count();
        assert_eq!(duplicates, 3);
    }

    #[test]
    fn queue_key_matches_legacy_precedence() {
        let identity = WindowIdentity {
            session_id: "abc".into(),
            window_id: "123".into(),
            caller_pid: 9,
            project_hint: "project".into(),
            ..WindowIdentity::default()
        };
        assert_eq!(queue_key(&identity), "sess_abc");
    }

    #[test]
    fn generation_increments_when_session_rebinds_to_new_window() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        let record1 = SessionRecord {
            window_id: "x11:100".to_owned(),
            window_instance_id: "x11:100:200".to_owned(),
            window_pid: 200,
            caller_pid: 50,
            title_fingerprint: "agent terminal".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store.save_capture("session-1", record1).unwrap();

        let resolved = store
            .resolve_exact(&WindowIdentity {
                session_id: "session-1".to_owned(),
                caller_pid: 50,
                ..WindowIdentity::default()
            })
            .unwrap()
            .unwrap();
        assert_eq!(resolved.generation, 1);
        assert_eq!(resolved.window_id, "x11:100");

        // Now session rebinds to a new window from a new caller process
        let record2 = SessionRecord {
            window_id: "x11:200".to_owned(),
            window_instance_id: "x11:200:200".to_owned(),
            window_pid: 200,
            caller_pid: 51,
            title_fingerprint: "agent terminal 2".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store.save_capture("session-1", record2).unwrap();

        let resolved2 = store
            .resolve_exact(&WindowIdentity {
                session_id: "session-1".to_owned(),
                caller_pid: 51,
                ..WindowIdentity::default()
            })
            .unwrap()
            .unwrap();
        assert_eq!(resolved2.generation, 2);
        assert_eq!(resolved2.window_id, "x11:200");

        // Stale notification with older generation 1 is rejected
        let stale_query = WindowIdentity {
            session_id: "session-1".to_owned(),
            caller_pid: 51,
            generation: 1,
            ..WindowIdentity::default()
        };
        assert!(store.resolve_exact(&stale_query).unwrap().is_none());

        // Current generation 2 is accepted
        let current_query = WindowIdentity {
            session_id: "session-1".to_owned(),
            caller_pid: 51,
            generation: 2,
            ..WindowIdentity::default()
        };
        assert!(store.resolve_exact(&current_query).unwrap().is_some());
    }

    #[test]
    fn two_agent_windows_have_distinct_session_records_and_resolve_correctly() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        let record_a = SessionRecord {
            window_id: "x11:101".to_owned(),
            window_instance_id: "x11:101:300:1:0".to_owned(),
            window_pid: 300,
            caller_pid: 301,
            title_fingerprint: "agent window A".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        let record_b = SessionRecord {
            window_id: "x11:102".to_owned(),
            window_instance_id: "x11:102:300:1:0".to_owned(),
            window_pid: 300,
            caller_pid: 302,
            title_fingerprint: "agent window B".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store.save_capture("session-a", record_a).unwrap();
        store.save_capture("session-b", record_b).unwrap();

        let resolved_a = store
            .resolve_exact(&WindowIdentity {
                session_id: "session-a".to_owned(),
                caller_pid: 301,
                ..WindowIdentity::default()
            })
            .unwrap()
            .unwrap();
        assert_eq!(resolved_a.window_id, "x11:101");
        assert_eq!(resolved_a.window_instance_id, "x11:101:300:1:0");

        let resolved_b = store
            .resolve_exact(&WindowIdentity {
                session_id: "session-b".to_owned(),
                caller_pid: 302,
                ..WindowIdentity::default()
            })
            .unwrap()
            .unwrap();
        assert_eq!(resolved_b.window_id, "x11:102");
        assert_eq!(resolved_b.window_instance_id, "x11:102:300:1:0");
    }

    #[test]
    fn session_rebind_on_process_restart_with_new_start_time_increments_generation() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        let record1 = SessionRecord {
            window_id: "x11:100".to_owned(),
            window_instance_id: "x11:100:200:1:5000".to_owned(),
            window_pid: 200,
            process_start_time: 5000,
            caller_pid: 50,
            title_fingerprint: "agent terminal".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store.save_capture("session-restart", record1).unwrap();

        let resolved = store
            .resolve_exact(&WindowIdentity {
                session_id: "session-restart".to_owned(),
                caller_pid: 50,
                process_start_time: 5000,
                ..WindowIdentity::default()
            })
            .unwrap()
            .unwrap();
        assert_eq!(resolved.generation, 1);

        // Process restarted with same PID 200 and same window 100, but new process start time 9000
        let record2 = SessionRecord {
            window_id: "x11:100".to_owned(),
            window_instance_id: "x11:100:200:1:9000".to_owned(),
            window_pid: 200,
            process_start_time: 9000,
            caller_pid: 50,
            title_fingerprint: "agent terminal".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store.save_capture("session-restart", record2).unwrap();

        let resolved2 = store
            .resolve_exact(&WindowIdentity {
                session_id: "session-restart".to_owned(),
                caller_pid: 50,
                process_start_time: 9000,
                ..WindowIdentity::default()
            })
            .unwrap()
            .unwrap();
        assert_eq!(resolved2.generation, 2);
        assert_eq!(resolved2.process_start_time, 9000);

        // Stale query with old start_time 5000 is rejected
        let stale_query = WindowIdentity {
            session_id: "session-restart".to_owned(),
            caller_pid: 50,
            process_start_time: 5000,
            generation: 1,
            ..WindowIdentity::default()
        };
        assert!(store.resolve_exact(&stale_query).unwrap().is_none());
    }

    #[test]
    fn repeated_capture_same_lifetime_preserves_uuid_and_generation() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        let record = SessionRecord {
            window_id: "x11:100".to_owned(),
            window_pid: 200,
            caller_pid: 50,
            process_start_time: 1000,
            title_fingerprint: "agent terminal".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store
            .save_capture("session-repeat", record.clone())
            .unwrap();

        let first = store.get("session-repeat").unwrap().unwrap();
        let initial_uuid = first.window_instance_id.clone();
        assert!(!initial_uuid.is_empty());
        assert_eq!(first.generation, 1);

        // Repeated capture from the same caller and same window
        store.save_capture("session-repeat", record).unwrap();
        let second = store.get("session-repeat").unwrap().unwrap();
        assert_eq!(second.window_instance_id, initial_uuid);
        assert_eq!(second.generation, 1);
    }

    #[test]
    fn rebind_or_restart_generates_new_uuid_and_makes_old_notification_fail_closed() {
        let (_directory, paths) = paths();
        let store = SessionStore::new(paths);
        let record1 = SessionRecord {
            window_id: "x11:100".to_owned(),
            window_pid: 200,
            caller_pid: 50,
            process_start_time: 1000,
            title_fingerprint: "agent terminal".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store.save_capture("session-rebind-uuid", record1).unwrap();

        let first = store.get("session-rebind-uuid").unwrap().unwrap();
        let uuid_v1 = first.window_instance_id.clone();
        assert_eq!(first.generation, 1);

        // Rebind to a new caller process (proven rebind)
        let record2 = SessionRecord {
            window_id: "x11:200".to_owned(),
            window_pid: 300,
            caller_pid: 51,
            process_start_time: 2000,
            title_fingerprint: "new agent terminal".to_owned(),
            precision: "window".to_owned(),
            backend: "x11".to_owned(),
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        store.save_capture("session-rebind-uuid", record2).unwrap();

        let second = store.get("session-rebind-uuid").unwrap().unwrap();
        let uuid_v2 = second.window_instance_id.clone();
        assert_eq!(second.generation, 2);
        assert_ne!(uuid_v1, uuid_v2);

        // Stale notification with uuid_v1 and generation 1 fails closed
        let stale_query = WindowIdentity {
            session_id: "session-rebind-uuid".to_owned(),
            window_instance_id: uuid_v1,
            caller_pid: 51,
            generation: 1,
            ..WindowIdentity::default()
        };
        assert!(store.resolve_exact(&stale_query).unwrap().is_none());

        // Current notification with uuid_v2 and generation 2 succeeds
        let current_query = WindowIdentity {
            session_id: "session-rebind-uuid".to_owned(),
            window_instance_id: uuid_v2.clone(),
            caller_pid: 51,
            generation: 2,
            ..WindowIdentity::default()
        };
        let resolved = store.resolve_exact(&current_query).unwrap().unwrap();
        assert_eq!(resolved.window_instance_id, uuid_v2);
        assert_eq!(resolved.generation, 2);
    }
}
