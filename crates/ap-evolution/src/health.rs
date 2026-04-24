//! `HealthTracker` — tracks success/failure ratio using EWMA for decay.

/// Smoothing factor for EWMA. Higher values weight recent events more.
/// 0.1 means ~10% weight on the newest event, slow decay of old state.
const ALPHA: f64 = 0.1;

/// Tracks health score using an exponentially weighted moving average.
///
/// Unlike a simple success/total ratio, EWMA naturally decays the influence
/// of old events: after many new successes, an ancient failure has negligible
/// impact on the score. This prevents the "frozen score" problem where
/// `successes / (successes + failures)` becomes nearly constant after
/// thousands of operations.
#[derive(Debug, Clone)]
pub struct HealthTracker {
    score: f64,
    total: u64,
}

impl HealthTracker {
    /// Create a new tracker starting at perfect health (1.0).
    #[must_use]
    pub fn new() -> Self {
        Self {
            score: 1.0,
            total: 0,
        }
    }

    /// Create a tracker restored from persisted state.
    #[must_use]
    pub fn from_persisted(score: f64, total: u64) -> Self {
        Self {
            score: score.clamp(0.0, 1.0),
            total: total.min(1_000_000),
        }
    }

    /// Record a successful outcome.
    ///
    /// The EWMA update pulls the score toward 1.0:
    /// `score = score * (1 - ALPHA) + 1.0 * ALPHA`
    pub fn record_success(&mut self) {
        self.score = self.score * (1.0 - ALPHA) + 1.0 * ALPHA;
        self.total += 1;
    }

    /// Record a failed outcome.
    ///
    /// The EWMA update pulls the score toward 0.0:
    /// `score = score * (1 - ALPHA) + 0.0 * ALPHA`
    pub fn record_failure(&mut self) {
        self.score = self.score * (1.0 - ALPHA) + 0.0 * ALPHA;
        self.total += 1;
    }

    /// Compute the current health score (0.0 to 1.0).
    ///
    /// Returns `1.0` if no data has been recorded yet (no evidence of failure).
    #[must_use] 
    pub fn get_health_score(&self) -> f64 {
        self.score
    }

    /// Total number of recorded events.
    #[must_use] 
    pub fn total(&self) -> u64 {
        self.total
    }

    /// Reset the tracker to initial state (score 1.0, zero events).
    pub fn reset(&mut self) {
        self.score = 1.0;
        self.total = 0;
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
    fn all_successes_stay_at_1() {
        let mut tracker = HealthTracker::new();
        for _ in 0..10 {
            tracker.record_success();
        }
        // With ALPHA=0.1, 10 successes converge close to 1.0 but not exactly
        let score = tracker.get_health_score();
        assert!(score > 0.99, "Expected score > 0.99, got {score}");
    }

    #[test]
    fn mixed_success_failure() {
        let mut tracker = HealthTracker::new();
        tracker.record_success();
        tracker.record_success();
        tracker.record_failure();
        // After 2 successes then 1 failure, score should be between 0.5 and 1.0
        let score = tracker.get_health_score();
        assert!(score > 0.5 && score < 1.0, "Expected 0.5 < score < 1.0, got {score}");
    }

    #[test]
    fn all_failures_converge_to_0() {
        let mut tracker = HealthTracker::new();
        for _ in 0..100 {
            tracker.record_failure();
        }
        let score = tracker.get_health_score();
        assert!(score < 0.01, "Expected score < 0.01, got {score}");
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
    fn reset_clears_state() {
        let mut tracker = HealthTracker::new();
        tracker.record_success();
        tracker.record_failure();
        tracker.record_failure();
        assert_eq!(tracker.total(), 3);

        tracker.reset();
        assert_eq!(tracker.total(), 0);
        assert_eq!(tracker.get_health_score(), 1.0);
    }

    #[test]
    fn recent_events_weight_more_than_old() {
        // This is the key EWMA property: after many new successes,
        // old failures should have negligible impact.
        let mut tracker = HealthTracker::new();

        // Record 5 failures
        for _ in 0..5 {
            tracker.record_failure();
        }
        let score_after_failures = tracker.get_health_score();

        // Record 50 successes
        for _ in 0..50 {
            tracker.record_success();
        }
        let score_after_recovery = tracker.get_health_score();

        // The recovery should bring the score close to 1.0
        assert!(
            score_after_recovery > 0.9,
            "Expected score > 0.9 after 50 successes, got {score_after_recovery} (was {score_after_failures} after 5 failures)"
        );
    }

    #[test]
    fn failure_drops_score_quickly_from_perfect() {
        let mut tracker = HealthTracker::new();
        // Start at 1.0, one failure
        tracker.record_failure();
        // Score should drop by ALPHA: 1.0 * 0.9 + 0.0 * 0.1 = 0.9
        let score = tracker.get_health_score();
        assert!(
            (score - 0.9).abs() < 0.001,
            "Expected ~0.9 after one failure from perfect, got {score}"
        );
    }
}
