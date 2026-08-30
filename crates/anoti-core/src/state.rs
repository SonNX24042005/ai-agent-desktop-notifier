//! Cross-platform persistent state with deduplication store.

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
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;
use thiserror::Error;

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
    pub dedupe: PathBuf,
    pub dedupe_lock: PathBuf,
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
            dedupe: root.join("ai_agent_notifier_dedupe.json"),
            dedupe_lock: root.join("ai_agent_notifier_dedupe.lock"),
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

    fn paths() -> (tempfile::TempDir, RuntimePaths) {
        let directory = tempfile::tempdir().unwrap();
        let paths = RuntimePaths::from_root(directory.path().join("runtime")).unwrap();
        (directory, paths)
    }

    #[test]
    fn dedupe_is_one_atomic_operation() {
        let (_directory, paths) = paths();
        let store = DedupeStore::new(paths);
        assert!(
            !store
                .check_and_record(
                    "Claude",
                    "Done",
                    "All tasks finished",
                    Duration::from_secs(2)
                )
                .unwrap()
        );
        assert!(
            store
                .check_and_record(
                    "Claude",
                    "Done",
                    "All tasks finished",
                    Duration::from_secs(2)
                )
                .unwrap()
        );
    }

    #[test]
    fn concurrent_dedupe_has_exactly_one_first_writer() {
        let (_directory, paths) = paths();
        let store = Arc::new(DedupeStore::new(paths));
        let barrier = Arc::new(Barrier::new(4));
        let mut handles = Vec::new();
        for _ in 0..4 {
            let store = Arc::clone(&store);
            let barrier = Arc::clone(&barrier);
            handles.push(thread::spawn(move || {
                barrier.wait();
                store
                    .check_and_record("Anoti", "Concurrent", "Body", Duration::from_secs(2))
                    .unwrap()
            }));
        }
        let results = handles
            .into_iter()
            .map(|h| h.join().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(results.iter().filter(|&&dup| !dup).count(), 1);
    }
}
