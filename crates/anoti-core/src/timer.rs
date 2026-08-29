//! Monotonic auto-dismiss state independent from UI event loops.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

/// Non-blocking guard that permits at most one activity probe at a time.
#[derive(Debug, Default)]
pub struct SingleFlight {
    running: AtomicBool,
}

impl SingleFlight {
    #[must_use]
    pub fn try_begin(&self) -> Option<SingleFlightGuard<'_>> {
        self.running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .ok()
            .map(|_| SingleFlightGuard { owner: self })
    }

    #[must_use]
    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::Acquire)
    }
}

#[derive(Debug)]
pub struct SingleFlightGuard<'a> {
    owner: &'a SingleFlight,
}

impl Drop for SingleFlightGuard<'_> {
    fn drop(&mut self) {
        self.owner.running.store(false, Ordering::Release);
    }
}

#[derive(Debug, Clone, Default)]
pub struct AutoDismissTimer {
    active_since: Option<Instant>,
}

impl AutoDismissTimer {
    #[must_use]
    pub fn update(&mut self, active: bool, delay: Duration, now: Instant) -> bool {
        if !active || delay.is_zero() {
            self.active_since = None;
            return false;
        }
        let active_since = *self.active_since.get_or_insert(now);
        now.saturating_duration_since(active_since) >= delay
    }

    pub fn reset(&mut self) {
        self.active_since = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn continuous_activity_triggers_and_switching_away_resets() {
        let mut timer = AutoDismissTimer::default();
        let start = Instant::now();
        let delay = Duration::from_millis(1500);
        assert!(!timer.update(true, delay, start));
        assert!(!timer.update(true, delay, start + Duration::from_millis(1200)));
        assert!(!timer.update(false, delay, start + Duration::from_millis(1300)));
        assert!(!timer.update(true, delay, start + Duration::from_secs(2)));
        assert!(timer.update(true, delay, start + Duration::from_millis(3500)));
    }

    #[test]
    fn single_flight_never_waits_and_releases_on_drop() {
        let flight = SingleFlight::default();
        let guard = flight.try_begin().unwrap();
        assert!(flight.is_running());
        assert!(flight.try_begin().is_none());
        drop(guard);
        assert!(flight.try_begin().is_some());
    }
}
