//! Platform-neutral models and contracts for the anoti runtime.

pub mod identity;
pub mod models;
pub mod state;
pub mod timer;

pub use identity::{
    CandidateEvidence, WindowCandidate, generate_window_instance_id, normalize_pid_chain,
    normalize_title, resolve_candidate, titles_compatible,
};
pub use models::{
    EventKind, FocusOutcome, NotificationRequest, PlatformCapabilities, QueueItem, QueueStatus,
    SessionRecord, Urgency, WindowIdentity,
};
pub use state::{
    DedupeStore, QueueStore, RuntimePaths, SessionStore, StateError, epoch_seconds, queue_key,
};
pub use timer::{AutoDismissTimer, SingleFlight, SingleFlightGuard};
