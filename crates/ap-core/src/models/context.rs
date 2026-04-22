//! Context window models for token budget management.
//!
//! Python source: models/context.py
//!
//! IMPORTANT (F-05 fix): ContextBudget has 10 configurable fields with
//! cross-field validators, NOT 4 simple fields.

use serde::{Deserialize, Serialize};

/// Tiered context loading levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ContextLevel {
    L0Identity,
    L1Execution,
    L2Extended,
    L3Runtime,
}

/// Alert levels from token budget checking.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BudgetAlertLevel {
    HardCeiling,
    ForcedTruncate,
    Compaction,
}

/// Token budget limits for context tiered loading.
///
/// Python source: models/context.py:43-104
/// All threshold values are fractions in 0.0-1.0 range.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ContextBudget {
    pub l0_max: u32,
    pub l1_max: u32,
    pub bootstrap_max: u32,
    pub single_file_max: u32,
    pub compaction_trigger: f64,
    pub compaction_target: f64,
    pub session_hard_ceiling: f64,
    pub forced_truncate_threshold: f64,
    pub min_turns_between_compactions: u32,
    pub consecutive_compaction_alert: u32,
}

impl Default for ContextBudget {
    fn default() -> Self {
        Self {
            l0_max: 800,
            l1_max: 3000,
            bootstrap_max: 5000,
            single_file_max: 8000,
            compaction_trigger: 0.8,
            compaction_target: 0.4,
            session_hard_ceiling: 0.95,
            forced_truncate_threshold: 0.9,
            min_turns_between_compactions: 5,
            consecutive_compaction_alert: 3,
        }
    }
}

impl ContextBudget {
    /// Validate all cross-field constraints. Mirrors Python's `_validate_thresholds`.
    pub fn validate(&self) -> Result<(), String> {
        for (name, value) in [
            ("compaction_trigger", self.compaction_trigger),
            ("compaction_target", self.compaction_target),
            ("session_hard_ceiling", self.session_hard_ceiling),
            ("forced_truncate_threshold", self.forced_truncate_threshold),
        ] {
            if !(0.0..=1.0).contains(&value) {
                return Err(format!("{name}={value} out of range 0.0-1.0"));
            }
        }
        if self.compaction_trigger <= self.compaction_target {
            return Err(format!(
                "compaction_trigger ({}) must be > compaction_target ({})",
                self.compaction_trigger, self.compaction_target
            ));
        }
        if self.forced_truncate_threshold >= self.session_hard_ceiling {
            return Err(format!(
                "forced_truncate_threshold ({}) must be < session_hard_ceiling ({})",
                self.forced_truncate_threshold, self.session_hard_ceiling
            ));
        }
        if self.l0_max + self.l1_max > self.bootstrap_max {
            return Err(format!(
                "l0_max ({}) + l1_max ({}) = {} exceeds bootstrap_max ({})",
                self.l0_max, self.l1_max, self.l0_max + self.l1_max, self.bootstrap_max
            ));
        }
        Ok(())
    }
}

/// Session-scoped token usage tracking.
///
/// Python source: models/context.py:110-150
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct TokenUsage {
    #[serde(default)]
    pub prompt_tokens: u64,
    #[serde(default)]
    pub completion_tokens: u64,
    #[serde(default)]
    pub compaction_count: u32,
    #[serde(default)]
    pub last_compaction_turn: u32,
}

impl TokenUsage {
    pub fn total_tokens(&self) -> u64 {
        self.prompt_tokens + self.completion_tokens
    }

    /// Return alert level or None if within budget.
    pub fn check_budget(
        &self,
        context_window: u64,
        budget: &ContextBudget,
    ) -> Option<BudgetAlertLevel> {
        let ratio = if context_window == 0 { return None; } else {
            self.total_tokens() as f64 / context_window as f64
        };
        if ratio >= budget.session_hard_ceiling {
            Some(BudgetAlertLevel::HardCeiling)
        } else if ratio >= budget.forced_truncate_threshold {
            Some(BudgetAlertLevel::ForcedTruncate)
        } else if ratio >= budget.compaction_trigger {
            Some(BudgetAlertLevel::Compaction)
        } else {
            None
        }
    }
}

/// Context budget log entry for compaction observability.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ContextBudgetLogEntry {
    pub id: String,
    pub agent_name: String,
    pub event_type: String,
    pub tokens_before: Option<i64>,
    pub tokens_after: Option<i64>,
    pub details: Option<String>,
    pub created_at: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_budget_validates() {
        let budget = ContextBudget::default();
        assert!(budget.validate().is_ok());
    }

    #[test]
    fn trigger_must_exceed_target() {
        let mut budget = ContextBudget::default();
        budget.compaction_trigger = 0.3;
        budget.compaction_target = 0.5;
        assert!(budget.validate().is_err());
    }

    #[test]
    fn bootstrap_must_fit_l0_plus_l1() {
        let mut budget = ContextBudget::default();
        budget.bootstrap_max = 1000;
        assert!(budget.validate().is_err());
    }

    #[test]
    fn forced_truncate_must_be_below_ceiling() {
        let mut budget = ContextBudget::default();
        budget.forced_truncate_threshold = 0.96;
        assert!(budget.validate().is_err());
    }

    #[test]
    fn token_usage_check_budget() {
        let usage = TokenUsage {
            prompt_tokens: 850,
            completion_tokens: 0,
            compaction_count: 0,
            last_compaction_turn: 0,
        };
        let budget = ContextBudget::default();
        assert_eq!(usage.check_budget(1000, &budget), Some(BudgetAlertLevel::Compaction));
    }

    #[test]
    fn token_usage_below_threshold() {
        let usage = TokenUsage {
            prompt_tokens: 500,
            completion_tokens: 0,
            compaction_count: 0,
            last_compaction_turn: 0,
        };
        let budget = ContextBudget::default();
        assert_eq!(usage.check_budget(1000, &budget), None);
    }
}
