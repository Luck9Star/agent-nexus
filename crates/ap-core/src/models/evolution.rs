//! Self-Evolution Engine models: SkillRecord, EvolutionType, SkillOrigin, SkillLineage.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::models::common::utc_now;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvolutionType {
    Fix,
    Derived,
    Captured,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum SkillOrigin {
    #[default]
    Imported,
    Captured,
    Derived,
    Fixed,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SkillLineage {
    #[serde(default)]
    pub origin: SkillOrigin,
    #[serde(default)]
    pub generation: u32,
    #[serde(default)]
    pub parent_skill_ids: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content_diff: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content_snapshot: Option<std::collections::HashMap<String, String>>,
}

impl Default for SkillLineage {
    fn default() -> Self {
        Self {
            origin: SkillOrigin::Imported,
            generation: 0,
            parent_skill_ids: Vec::new(),
            content_diff: None,
            content_snapshot: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SkillRecord {
    pub id: String,
    pub name: String,
    #[serde(default = "default_version")]
    pub version: String,
    #[serde(default)]
    pub lineage: SkillLineage,
    #[serde(default)]
    pub directory: String,
    #[serde(default = "default_true")]
    pub is_active: bool,
    #[serde(default)]
    pub total_selections: u64,
    #[serde(default)]
    pub total_applied: u64,
    #[serde(default)]
    pub total_completions: u64,
    #[serde(default)]
    pub total_fallbacks: u64,
    #[serde(default = "utc_now")]
    pub first_seen: DateTime<Utc>,
    #[serde(default = "utc_now")]
    pub last_updated: DateTime<Utc>,
}

fn default_version() -> String { "1.0.0".to_string() }
fn default_true() -> bool { true }

impl SkillRecord {
    /// Validate counter invariants.
    /// Python source: models/evolution.py `_validate_counters` — 5 checks.
    pub fn validate_counters(&self) -> Result<(), String> {
        if self.total_selections == 0
            && (self.total_applied != 0 || self.total_fallbacks != 0)
        {
            return Err("zero selections requires zero applied and zero fallbacks".into());
        }
        if self.total_applied > self.total_selections {
            return Err("total_applied cannot exceed total_selections".into());
        }
        if self.total_completions > self.total_applied {
            return Err("total_completions cannot exceed total_applied".into());
        }
        if self.total_fallbacks > self.total_applied {
            return Err("total_fallbacks cannot exceed total_applied".into());
        }
        if self.total_completions + self.total_fallbacks > self.total_applied {
            return Err("total_completions + total_fallbacks cannot exceed total_applied".into());
        }
        Ok(())
    }

    /// Shared counter validation logic used by both SkillRecord and EvolutionMetrics.
    pub fn validate_counters_from_parts(
        selections: u64,
        applied: u64,
        completions: u64,
        fallbacks: u64,
    ) -> Result<(), String> {
        if selections == 0 && (applied != 0 || fallbacks != 0) {
            return Err("zero selections requires zero applied and zero fallbacks".into());
        }
        if applied > selections {
            return Err("total_applied cannot exceed total_selections".into());
        }
        if completions > applied {
            return Err("total_completions cannot exceed total_applied".into());
        }
        if fallbacks > applied {
            return Err("total_fallbacks cannot exceed total_applied".into());
        }
        if completions + fallbacks > applied {
            return Err("total_completions + total_fallbacks cannot exceed total_applied".into());
        }
        Ok(())
    }
}

/// Standalone evolution metrics with same counter validators as SkillRecord.
///
/// Python source: models/evolution.py:97-123 `EvolutionMetrics`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct EvolutionMetrics {
    #[serde(default)]
    pub total_selections: u64,
    #[serde(default)]
    pub total_applied: u64,
    #[serde(default)]
    pub total_completions: u64,
    #[serde(default)]
    pub total_fallbacks: u64,
}

impl EvolutionMetrics {
    pub fn validate(&self) -> Result<(), String> {
        SkillRecord::validate_counters_from_parts(
            self.total_selections, self.total_applied,
            self.total_completions, self.total_fallbacks,
        )
    }
}

/// Context passed to evolver with task/agent metadata.
///
/// Python source: models/evolution.py:126-141 `EvolutionContext`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct EvolutionContext {
    #[serde(default)]
    pub agent_id: String,
    #[serde(default)]
    pub task_id: String,
    #[serde(default)]
    pub skill_ids_used: Vec<String>,
    #[serde(default)]
    pub task_description: String,
    #[serde(default)]
    pub task_result: Option<String>,
    #[serde(default)]
    pub error_info: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_record() -> SkillRecord {
        SkillRecord {
            id: "s1".into(),
            name: "test-skill".into(),
            version: default_version(),
            lineage: SkillLineage::default(),
            directory: String::new(),
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
            first_seen: utc_now(),
            last_updated: utc_now(),
        }
    }

    #[test]
    fn valid_zero_counters() {
        assert!(make_record().validate_counters().is_ok());
    }

    #[test]
    fn invalid_applied_exceeds_selections() {
        let mut r = make_record();
        r.total_selections = 5;
        r.total_applied = 10;
        assert!(r.validate_counters().is_err());
    }

    #[test]
    fn valid_nonzero_counters() {
        let mut r = make_record();
        r.total_selections = 100;
        r.total_applied = 80;
        r.total_completions = 70;
        r.total_fallbacks = 10;
        assert!(r.validate_counters().is_ok());
    }

    #[test]
    fn evolution_metrics_validate_delegates() {
        let metrics = EvolutionMetrics {
            total_selections: 10,
            total_applied: 5,
            total_completions: 4,
            total_fallbacks: 1,
        };
        assert!(metrics.validate().is_ok());
    }

    #[test]
    fn evolution_metrics_invalid() {
        let metrics = EvolutionMetrics {
            total_selections: 5,
            total_applied: 10,
            total_completions: 0,
            total_fallbacks: 0,
        };
        assert!(metrics.validate().is_err());
    }
}
