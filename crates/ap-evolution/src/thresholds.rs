//! Thresholds — configuration constants for skill evolution and promotion.

/// Thresholds for determining when a skill is ready for promotion or evolution.
#[derive(Debug, Clone)]
pub struct Thresholds {
    /// Minimum number of times a skill has been selected for use.
    pub min_selections: u32,

    /// Minimum success rate (0.0 - 1.0) to consider a skill viable.
    pub min_success_rate: f64,

    /// Minimum number of successful applications.
    pub min_applications: u32,

    /// Success rate threshold above which promotion is recommended.
    pub promotion_threshold: f64,
}

impl Default for Thresholds {
    fn default() -> Self {
        Self {
            min_selections: 5,
            min_success_rate: 0.7,
            min_applications: 3,
            promotion_threshold: 0.8,
        }
    }
}

impl Thresholds {
    /// Create a new Thresholds with default values.
    pub fn new() -> Self {
        Self::default()
    }

    /// Check if the given metrics meet the promotion criteria.
    pub fn is_promotion_eligible(
        &self,
        selections: u32,
        success_rate: f64,
        applications: u32,
    ) -> bool {
        selections >= self.min_selections
            && success_rate >= self.promotion_threshold
            && applications >= self.min_applications
    }

    /// Check if the given metrics meet the minimum viability criteria.
    pub fn is_viable(&self, selections: u32, success_rate: f64, applications: u32) -> bool {
        selections >= self.min_selections
            && success_rate >= self.min_success_rate
            && applications >= self.min_applications
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_values() {
        let t = Thresholds::default();
        assert_eq!(t.min_selections, 5);
        assert!((t.min_success_rate - 0.7).abs() < f64::EPSILON);
        assert_eq!(t.min_applications, 3);
        assert!((t.promotion_threshold - 0.8).abs() < f64::EPSILON);
    }

    #[test]
    fn new_returns_defaults() {
        let t = Thresholds::new();
        assert_eq!(t.min_selections, 5);
    }

    #[test]
    fn is_promotion_eligible_meets_all() {
        let t = Thresholds::default();
        assert!(t.is_promotion_eligible(10, 0.9, 5));
    }

    #[test]
    fn is_promotion_eligible_low_selections() {
        let t = Thresholds::default();
        assert!(!t.is_promotion_eligible(3, 0.9, 5));
    }

    #[test]
    fn is_promotion_eligible_low_success_rate() {
        let t = Thresholds::default();
        assert!(!t.is_promotion_eligible(10, 0.7, 5));
    }

    #[test]
    fn is_promotion_eligible_low_applications() {
        let t = Thresholds::default();
        assert!(!t.is_promotion_eligible(10, 0.9, 2));
    }

    #[test]
    fn is_viable_meets_all() {
        let t = Thresholds::default();
        assert!(t.is_viable(6, 0.75, 4));
    }

    #[test]
    fn is_viable_below_min_success_rate() {
        let t = Thresholds::default();
        assert!(!t.is_viable(6, 0.5, 4));
    }
}
