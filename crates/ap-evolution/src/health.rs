//! HealthTracker — tracks success/failure ratio for evolution quality.

/// Tracks successes and failures to compute a health score.
#[derive(Debug, Clone)]
pub struct HealthTracker {
    successes: u64,
    failures: u64,
}

impl HealthTracker {
    /// Create a new tracker with zero counts.
    pub fn new() -> Self {
        Self {
            successes: 0,
            failures: 0,
        }
    }

    /// Record a successful outcome.
    pub fn record_success(&mut self) {
        self.successes += 1;
    }

    /// Record a failed outcome.
    pub fn record_failure(&mut self) {
        self.failures += 1;
    }

    /// Compute the health score: `successes / (successes + failures)`.
    ///
    /// Returns `1.0` if no data has been recorded yet (no evidence of failure).
    pub fn get_health_score(&self) -> f64 {
        let total = self.successes + self.failures;
        if total == 0 {
            return 1.0;
        }
        self.successes as f64 / total as f64
    }

    /// Total number of recorded events.
    pub fn total(&self) -> u64 {
        self.successes + self.failures
    }

    /// Reset the tracker, clearing all recorded counts.
    ///
    /// Useful for periodic health re-evaluation to prevent old failures from
    /// permanently dragging down the score in long-running processes.
    pub fn reset(&mut self) {
        self.successes = 0;
        self.failures = 0;
    }
}

impl Default for HealthTracker {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_tracker_has_score_1() {
        let tracker = HealthTracker::new();
        assert_eq!(tracker.get_health_score(), 1.0);
    }

    #[test]
    fn all_successes_score_1() {
        let mut tracker = HealthTracker::new();
        for _ in 0..10 {
            tracker.record_success();
        }
        assert_eq!(tracker.get_health_score(), 1.0);
    }

    #[test]
    fn mixed_success_failure() {
        let mut tracker = HealthTracker::new();
        tracker.record_success();
        tracker.record_success();
        tracker.record_failure();
        // 2/3 = 0.666...
        let score = tracker.get_health_score();
        assert!((score - 0.6666).abs() < 0.01);
    }

    #[test]
    fn all_failures_score_0() {
        let mut tracker = HealthTracker::new();
        for _ in 0..5 {
            tracker.record_failure();
        }
        assert_eq!(tracker.get_health_score(), 0.0);
    }

    #[test]
    fn default_trait() {
        let tracker = HealthTracker::default();
        assert_eq!(tracker.get_health_score(), 1.0);
    }

    #[test]
    fn total_tracks_count() {
        let mut tracker = HealthTracker::new();
        assert_eq!(tracker.total(), 0);
        tracker.record_success();
        tracker.record_failure();
        tracker.record_success();
        assert_eq!(tracker.total(), 3);
    }

    #[test]
    fn reset_clears_counts() {
        let mut tracker = HealthTracker::new();
        tracker.record_success();
        tracker.record_failure();
        tracker.record_failure();
        assert_eq!(tracker.total(), 3);

        tracker.reset();
        assert_eq!(tracker.total(), 0);
        assert_eq!(tracker.get_health_score(), 1.0);
    }
}
