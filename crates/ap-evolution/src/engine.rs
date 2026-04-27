//! `EvolutionEngine` — top-level facade that ties together the store, analyzer,
//! evolver, and health tracker.

use std::sync::Arc;

use crate::analyzer::{Analyzer, EvolutionSuggestion, TaskResult};
use crate::evolver::{EvolutionOutcome, EvolverError, SkillEvolver};
use crate::health::HealthTracker;
use crate::store::EvolutionStore;
use crate::thresholds::Thresholds;

/// What initiated an evolution cycle.
///
/// Maps to the Python `EvolutionTrigger` enum. Each variant carries the
/// context needed to dispatch to the correct handler.
#[derive(Debug, Clone)]
pub enum EvolveTrigger {
    /// Post-task analysis: analyze the result and evolve any skills that need fixing.
    Analysis {
        task_id: String,
        agent_name: String,
        success: bool,
        error: Option<String>,
    },
    /// A tool/API the skill depends on has degraded. Evolve affected skills.
    ToolDegradation {
        skill_name: String,
        problem_description: String,
    },
    /// Periodic metric check: scan all skills and evolve those with poor health.
    MetricCheck,
    /// A specific skill failed. Evolve it immediately.
    Failure {
        skill_name: String,
        reason: String,
    },
}

/// Result of a single evolution dispatch cycle.
#[derive(Debug, Clone)]
pub struct EvolveDispatchResult {
    /// The trigger that initiated this cycle.
    pub trigger: EvolveTrigger,
    /// Individual outcomes, one per skill that was evolved.
    pub outcomes: Vec<EvolutionOutcome>,
}

/// Error type for the engine's `evolve()` dispatch.
#[derive(Debug, thiserror::Error)]
pub enum EvolveDispatchError {
    #[error("Store error: {0}")]
    Store(#[from] crate::store::StoreError),

    #[error("Evolver error: {0}")]
    Evolver(#[from] EvolverError),

    #[error("No active skill found with name: {0}")]
    SkillNotFound(String),
}

/// The main evolution engine.
///
/// Coordinates post-task analysis, trigger-based dispatch, and health tracking.
pub struct EvolutionEngine {
    store: Arc<EvolutionStore>,
    analyzer: Analyzer,
    evolver: Arc<SkillEvolver>,
    // std::sync::Mutex chosen deliberately: lock is held only for brief arithmetic
    // updates (increment counters) and never across an .await point.  tokio::sync::Mutex
    // would add unnecessary overhead for this purely synchronous access pattern.
    health: std::sync::Mutex<HealthTracker>,
    thresholds: Thresholds,
}

impl EvolutionEngine {
    /// Create a new engine backed by the given store.
    #[must_use] 
    pub fn new(store: EvolutionStore) -> Self {
        let store = Arc::new(store);
        let evolver = Arc::new(SkillEvolver::new(Arc::clone(&store)));

        // Load persisted health state; fall back to defaults on error.
        let health = match store.load_health_state() {
            Ok((score, total)) => HealthTracker::from_persisted(score, total),
            Err(_) => HealthTracker::new(),
        };

        Self {
            store,
            analyzer: Analyzer::new(),
            evolver,
            health: std::sync::Mutex::new(health),
            thresholds: Thresholds::default(),
        }
    }

    /// Create a new engine with custom thresholds.
    #[must_use] 
    pub fn with_thresholds(store: EvolutionStore, thresholds: Thresholds) -> Self {
        let store = Arc::new(store);
        let evolver = Arc::new(SkillEvolver::new(Arc::clone(&store)));

        // Load persisted health state; fall back to defaults on error.
        let health = match store.load_health_state() {
            Ok((score, total)) => HealthTracker::from_persisted(score, total),
            Err(_) => HealthTracker::new(),
        };

        Self {
            store,
            analyzer: Analyzer::new(),
            evolver,
            health: std::sync::Mutex::new(health),
            thresholds,
        }
    }

    /// Acquire the health mutex, recovering from a poisoned lock.
    fn health_guard(&self) -> std::sync::MutexGuard<'_, HealthTracker> {
        self.health.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Record a health event and persist the updated state to `SQLite`.
    fn record_health(&self, success: bool) {
        let (score, total) = {
            let mut health = self.health_guard();
            if success {
                health.record_success();
            } else {
                health.record_failure();
            }
            (health.get_health_score(), health.total())
        };
        // Persist outside the mutex lock to avoid holding it during I/O.
        if let Err(e) = self.store.save_health_state(score, total) {
            tracing::warn!("Failed to persist health state: {e}");
        }
    }

    /// Unified evolve entry point — dispatches by trigger type.
    ///
    /// This is the primary API for triggering evolution. It routes to the
    /// appropriate handler based on the trigger variant:
    ///
    /// - **Analysis**: Run post-task analysis, extract suggestions, evolve skills.
    /// - **Failure**: Look up the named skill, call `evolve_fix()`.
    /// - **`ToolDegradation`**: Look up the named skill, call `evolve_fix()`.
    /// - **`MetricCheck`**: Scan all active skills, evolve underperforming ones.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn evolve(
        &self,
        trigger: EvolveTrigger,
    ) -> Result<EvolveDispatchResult, EvolveDispatchError> {
        let outcomes = match &trigger {
            EvolveTrigger::Analysis { task_id, agent_name, success, error } => {
                self.dispatch_analysis(task_id, agent_name, *success, error.as_ref())
            }
            EvolveTrigger::Failure { skill_name, reason } => {
                self.dispatch_failure(skill_name, reason)
            }
            EvolveTrigger::ToolDegradation { skill_name, problem_description } => {
                self.dispatch_tool_degradation(skill_name, problem_description)
            }
            EvolveTrigger::MetricCheck => {
                self.dispatch_metric_check()
            }
        }?;

        Ok(EvolveDispatchResult { trigger, outcomes })
    }

    // -----------------------------------------------------------------------
    // Dispatch handlers
    // -----------------------------------------------------------------------

    /// Analysis trigger: analyze the task, then evolve any suggested skills.
    fn dispatch_analysis(
        &self,
        task_id: &str,
        agent_name: &str,
        success: bool,
        error: Option<&String>,
    ) -> Result<Vec<EvolutionOutcome>, EvolveDispatchError> {
        let result = TaskResult {
            success,
            error: error.cloned(),
            agent_name: agent_name.to_string(),
            task_id: task_id.to_string(),
        };

        // Update health tracker and persist
        self.record_health(success);

        // Run analysis to get suggestions
        let suggestions = self.analyzer.analyze(&result);

        // Process each suggestion (I/O) — no health lock held
        let mut outcomes = Vec::with_capacity(suggestions.len());
        for suggestion in &suggestions {
            match self.evolver.evolve_fix(
                &suggestion.skill_name,
                &suggestion.reason,
            ) {
                Ok(outcome) => outcomes.push(outcome),
                Err(EvolverError::SkillNotFound(name)) => {
                    tracing::debug!("Skill not found for evolution: {name}, skipping");
                    // Don't abort — other suggestions may still be processable
                }
                Err(e) => {
                    return Err(e.into());
                }
            }
        }

        Ok(outcomes)
    }

    /// Failure trigger: directly fix the named skill.
    fn dispatch_failure(
        &self,
        skill_name: &str,
        error: &str,
    ) -> Result<Vec<EvolutionOutcome>, EvolveDispatchError> {
        // Update health tracker and persist
        self.record_health(false);

        let outcome = self.evolver.evolve_fix(skill_name, error)?;
        Ok(vec![outcome])
    }

    /// Tool degradation trigger: fix the named skill with degradation context.
    fn dispatch_tool_degradation(
        &self,
        skill_name: &str,
        problem_description: &str,
    ) -> Result<Vec<EvolutionOutcome>, EvolveDispatchError> {
        let outcome = self.evolver.evolve_fix(
            skill_name,
            &format!("Tool degradation: {problem_description}"),
        )?;
        Ok(vec![outcome])
    }

    /// Metric check trigger: scan all active skills, evolve underperforming ones.
    fn dispatch_metric_check(&self) -> Result<Vec<EvolutionOutcome>, EvolveDispatchError> {
        let active_skills = self.store.get_active_skills()?;
        let mut outcomes = Vec::new();

        for skill in &active_skills {
            // Skip skills without enough data points (anti-loop)
            if skill.total_selections < i64::from(self.thresholds.min_selections) {
                continue;
            }

            // Compute success rate: completions / selections
            // Casts from i64 are safe: skill counters are well below 2^52 in practice.
            #[allow(clippy::cast_precision_loss)]
            let success_rate = if skill.total_selections > 0 {
                skill.total_completions as f64 / skill.total_selections as f64
            } else {
                1.0
            };

            // Check if skill is underperforming
            let selections: u32 = skill.total_selections.try_into().unwrap_or(u32::MAX);
            let applied: u32 = skill.total_applied.try_into().unwrap_or(u32::MAX);
            let is_viable = self.thresholds.is_viable(selections, success_rate, applied);

            if !is_viable {
                let reason = format!(
                    "Metric check: skill '{}' health below threshold \
                     (selections={}, completions={}, rate={:.2})",
                    skill.name, skill.total_selections,
                    skill.total_completions, success_rate
                );
                let outcome = self.evolver.evolve_fix(
                    &skill.name,
                    &reason,
                )?;
                outcomes.push(outcome);
            }
        }

        Ok(outcomes)
    }

    // -----------------------------------------------------------------------
    // Legacy / convenience methods (preserved for backward compat)
    // -----------------------------------------------------------------------

    /// Run post-task evolution analysis.
    ///
    /// Returns a list of evolution suggestions (may be empty).
    /// Updates the health tracker based on success/failure.
    pub fn post_task_evolve(&self, result: &TaskResult) -> Vec<EvolutionSuggestion> {
        // Update health tracker and persist
        self.record_health(result.success);

        // Run analysis (no health lock held)
        self.analyzer.analyze(result)
    }

    /// Get the current health score (0.0 - 1.0).
    pub fn get_health_score(&self) -> f64 {
        self.health_guard().get_health_score()
    }

    /// Count the number of active skills in the store.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_skill_count(&self) -> crate::store::error::Result<usize> {
        // Cast from i64 to usize is safe: skill count is always non-negative and small.
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        Ok(self.store.count_active_skills()? as usize)
    }

    /// Get a reference to the underlying store (for advanced use).
    pub fn store(&self) -> &EvolutionStore {
        &self.store
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::SkillRecord;

    fn make_engine() -> EvolutionEngine {
        let store = EvolutionStore::new_in_memory().unwrap();
        EvolutionEngine::new(store)
    }

    fn make_engine_with_skill(skill_name: &str) -> EvolutionEngine {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = SkillRecord {
            id: format!("{skill_name}-id"),
            name: skill_name.to_string(),
            version: "1.0.0".to_string(),
            lineage_origin: "imported".to_string(),
            lineage_generation: 0,
            lineage_content_diff: None,
            lineage_content_snapshot: None,
            directory: Some("/skills/test".to_string()),
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        };
        store.insert_skill(&skill).unwrap();
        EvolutionEngine::new(store)
    }

    fn make_skill_with_metrics(
        store: &EvolutionStore,
        name: &str,
        selections: i64,
        applied: i64,
        completions: i64,
        fallbacks: i64,
    ) {
        let skill = SkillRecord {
            id: format!("{name}-id"),
            name: name.to_string(),
            version: "1.0.0".to_string(),
            lineage_origin: "imported".to_string(),
            lineage_generation: 0,
            lineage_content_diff: None,
            lineage_content_snapshot: None,
            directory: Some(format!("/skills/{name}")),
            is_active: true,
            total_selections: selections,
            total_applied: applied,
            total_completions: completions,
            total_fallbacks: fallbacks,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        };
        store.insert_skill(&skill).unwrap();
    }

    // --- Existing tests (preserved) ---

    #[test]
    fn new_engine_has_health_1() {
        let engine = make_engine();
        assert_eq!(engine.get_health_score(), 1.0);
    }

    #[test]
    fn new_engine_has_zero_skills() {
        let engine = make_engine();
        assert_eq!(engine.get_skill_count().unwrap(), 0);
    }

    #[test]
    fn post_task_success_no_suggestions() {
        let engine = make_engine();
        let result = TaskResult {
            success: true,
            error: None,
            agent_name: "test-agent".to_string(),
            task_id: "t-001".to_string(),
        };
        let suggestions = engine.post_task_evolve(&result);
        assert!(suggestions.is_empty());
        assert_eq!(engine.get_health_score(), 1.0);
    }

    #[test]
    fn post_task_failure_returns_fix() {
        let engine = make_engine();
        let result = TaskResult {
            success: false,
            error: Some("bad error".to_string()),
            agent_name: "failing-agent".to_string(),
            task_id: "t-002".to_string(),
        };
        let suggestions = engine.post_task_evolve(&result);
        assert_eq!(suggestions.len(), 1);
        assert_eq!(
            suggestions[0].evolution_type,
            crate::analyzer::EvolutionType::Fix
        );
    }

    #[test]
    fn health_score_tracks_mixed_results() {
        let engine = make_engine();

        // 2 successes
        for _ in 0..2 {
            let result = TaskResult {
                success: true,
                error: None,
                agent_name: "a".to_string(),
                task_id: "t".to_string(),
            };
            engine.post_task_evolve(&result);
        }

        // 1 failure
        let result = TaskResult {
            success: false,
            error: Some("err".to_string()),
            agent_name: "a".to_string(),
            task_id: "t".to_string(),
        };
        engine.post_task_evolve(&result);

        let score = engine.get_health_score();
        // With EWMA (ALPHA=0.1), the score should be between 0.5 and 1.0
        // but NOT equal to the simple 2/3 ratio.
        assert!(
            score > 0.5 && score < 1.0,
            "Expected 0.5 < score < 1.0 after 2 successes + 1 failure, got {score}"
        );
    }

    #[test]
    fn get_skill_count_after_insert() {
        let engine = make_engine();
        let skill = SkillRecord {
            id: "s-001".to_string(),
            name: "test-skill".to_string(),
            version: "1.0.0".to_string(),
            lineage_origin: "imported".to_string(),
            lineage_generation: 0,
            lineage_content_diff: None,
            lineage_content_snapshot: None,
            directory: None,
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        };
        engine.store().insert_skill(&skill).unwrap();

        assert_eq!(engine.get_skill_count().unwrap(), 1);
    }

    // --- New evolve() dispatch tests ---

    #[test]
    fn evolve_analysis_trigger_dispatches_fix() {
        let engine = make_engine_with_skill("failing-agent");

        let result = engine.evolve(EvolveTrigger::Analysis {
            task_id: "t-100".to_string(),
            agent_name: "failing-agent".to_string(),
            success: false,
            error: Some("task failed".to_string()),
        }).unwrap();

        assert_eq!(result.outcomes.len(), 1);
        assert!(matches!(result.outcomes[0], EvolutionOutcome::Success { .. }));
    }

    #[test]
    fn evolve_failure_trigger_fixes_skill() {
        let engine = make_engine_with_skill("my-skill");

        let result = engine.evolve(EvolveTrigger::Failure {
            skill_name: "my-skill".to_string(),
            reason: "crashed on input".to_string(),
        }).unwrap();

        assert_eq!(result.outcomes.len(), 1);
        if let EvolutionOutcome::Success { new_skill_id, .. } = &result.outcomes[0] {
            assert!(new_skill_id.contains("my-skill"));
            assert!(new_skill_id.contains("__fix_"));
        } else {
            panic!("Expected Success outcome");
        }
    }

    #[test]
    fn evolve_failure_trigger_skill_not_found() {
        let engine = make_engine();

        let result = engine.evolve(EvolveTrigger::Failure {
            skill_name: "nonexistent".to_string(),
            reason: "oops".to_string(),
        });

        assert!(result.is_err());
        match result.unwrap_err() {
            EvolveDispatchError::Evolver(EvolverError::SkillNotFound(name)) => {
                assert_eq!(name, "nonexistent");
            }
            other => panic!("Expected SkillNotFound, got: {other}"),
        }
    }

    #[test]
    fn evolve_tool_degradation_trigger() {
        let engine = make_engine_with_skill("api-skill");

        let result = engine.evolve(EvolveTrigger::ToolDegradation {
            skill_name: "api-skill".to_string(),
            problem_description: "API rate limit hit".to_string(),
        }).unwrap();

        assert_eq!(result.outcomes.len(), 1);
        assert!(matches!(result.outcomes[0], EvolutionOutcome::Success { .. }));
    }

    #[test]
    fn evolve_metric_check_skips_low_selection_skills() {
        let store = EvolutionStore::new_in_memory().unwrap();
        // Skill with only 2 selections — below min_selections=5
        make_skill_with_metrics(&store, "low-data", 2, 1, 0, 1);
        let engine = EvolutionEngine::new(store);

        let result = engine.evolve(EvolveTrigger::MetricCheck).unwrap();
        // Should not evolve — not enough data
        assert!(result.outcomes.is_empty());
    }

    #[test]
    fn evolve_metric_check_evolves_unhealthy_skill() {
        let store = EvolutionStore::new_in_memory().unwrap();
        // Skill with 10 selections, 0 completions — success_rate = 0.0 < 0.7
        make_skill_with_metrics(&store, "unhealthy", 10, 10, 0, 10);
        let engine = EvolutionEngine::new(store);

        let result = engine.evolve(EvolveTrigger::MetricCheck).unwrap();
        assert_eq!(result.outcomes.len(), 1);
        assert!(matches!(result.outcomes[0], EvolutionOutcome::Success { .. }));
    }

    #[test]
    fn evolve_metric_check_skips_healthy_skills() {
        let store = EvolutionStore::new_in_memory().unwrap();
        // Skill with 10 selections, 10 completions — success_rate = 1.0 >= 0.7
        make_skill_with_metrics(&store, "healthy", 10, 10, 10, 0);
        let engine = EvolutionEngine::new(store);

        let result = engine.evolve(EvolveTrigger::MetricCheck).unwrap();
        assert!(result.outcomes.is_empty());
    }

    #[test]
    fn evolve_failure_updates_health_tracker() {
        let engine = make_engine_with_skill("s1");

        assert_eq!(engine.get_health_score(), 1.0);

        engine.evolve(EvolveTrigger::Failure {
            skill_name: "s1".to_string(),
            reason: "err".to_string(),
        }).unwrap();

        // Health should have recorded a failure
        let score = engine.get_health_score();
        assert!(score < 1.0);
    }

    #[test]
    fn evolve_failure_deactivates_old_skill() {
        let engine = make_engine_with_skill("old-skill");

        engine.evolve(EvolveTrigger::Failure {
            skill_name: "old-skill".to_string(),
            reason: "broken".to_string(),
        }).unwrap();

        // Old skill should be deactivated (verify by ID, not name — new skill has same name)
        let old_by_id = engine.store().get_skill_by_id("old-skill-id").unwrap();
        assert!(old_by_id.is_some(), "old skill should still exist in DB");
        assert!(!old_by_id.unwrap().is_active, "old skill should be deactivated");

        // New skill with same name should be the active one
        let active = engine.store().get_skill_by_name("old-skill").unwrap();
        assert!(active.is_some(), "new skill should be found by name");
        assert_ne!(active.unwrap().id, "old-skill-id", "active skill should be the new one, not old");
    }

    #[test]
    fn evolve_fix_creates_new_skill_version() {
        let engine = make_engine_with_skill("versioned");

        let result = engine.evolve(EvolveTrigger::Failure {
            skill_name: "versioned".to_string(),
            reason: "need update".to_string(),
        }).unwrap();

        if let EvolutionOutcome::Success { new_skill_id, .. } = &result.outcomes[0] {
            // New skill should exist in store
            let new_skill = engine.store().get_skill_by_id(new_skill_id).unwrap();
            assert!(new_skill.is_some());
            let new_skill = new_skill.unwrap();
            assert_eq!(new_skill.name, "versioned");
            assert_eq!(new_skill.lineage_origin, "fix");
            assert_eq!(new_skill.lineage_generation, 1);
            assert!(new_skill.is_active);
            assert!(new_skill.lineage_content_diff.is_some());
            assert!(new_skill.lineage_content_diff.unwrap().contains("need update"));
        } else {
            panic!("Expected Success outcome");
        }
    }
}
