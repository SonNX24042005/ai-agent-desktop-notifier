//! Platform-neutral models and contracts for the anoti runtime.

pub mod models;
pub mod state;

pub use models::{
    EventKind, NotificationRequest, PlatformCapabilities, Urgency, resolve_icon_path,
};
pub use state::{DedupeStore, RuntimePaths, StateError, epoch_seconds};
