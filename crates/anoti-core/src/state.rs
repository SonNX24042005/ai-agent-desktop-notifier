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

use crate::{QueueItem, QueueStatus, SessionRecord, WindowIdentity};

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

    pub fn save(&self, session_id: &str, record: SessionRecord) -> Result<(), StateError> {
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
            && (!record.has_exact_window_identity() || existing.window_id != record.window_id)
        {
            return Ok(());
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
}
