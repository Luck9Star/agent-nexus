//! EvolutionEngine — top-level facade that ties together the store, analyzer,
//! and health tracker.

use std::sync::{Arc, Mutex};

use crate::analyzer::{Analyzer, EvolutionSuggestion, TaskResult};
use crate::health::HealthTracker;
use crate::store::EvolutionStore;

/// The main evolution engine.
///
/// Coordinates post-task analysis and health tracking.
pub struct EvolutionEngine {
    store: Arc<Mutex<EvolutionStore>>,
    analyzer: Analyzer,
    health: Mutex<HealthTracker>,
}

impl EvolutionEngine {
    /// Create a new engine backed by the given store.
    pub fn new(store: EvolutionStore) -> Self {
        Self {
            store: Arc::new(Mutex::new(store)),
            analyzer: Analyzer::new(),
            health: Mutex::new(HealthTracker::new()),
        }
    }

    /// Run post-task evolution analysis.
    ///
    /// Returns a list of evolution suggestions (may be empty).
    /// Updates the health tracker based on success/failure.
    pub fn post_task_evolve(&self, result: &TaskResult) -> Vec<EvolutionSuggestion> {
        // Update health tracker — recover from poisoned lock
        let mut health = self.health.lock().unwrap_or_else(|e| e.into_inner());
        if result.success {
            health.record_success();
        } else {
            health.record_failure();
        }

        // Run analysis
        self.analyzer.analyze(result)
    }

    /// Get the current health score (0.0 - 1.0).
    pub fn get_health_score(&self) -> f64 {
        self.health
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get_health_score()
    }

    /// Count the number of active skills in the store.
    pub fn get_skill_count(&self) -> crate::store::error::Result<usize> {
        let store = self.store.lock().map_err(|e| {
            crate::store::StoreError::Io(std::io::Error::other(format!(
                "lock poisoned: {e}"
            )))
        })?;
        Ok(store.count_active_skills()? as usize)
    }

    /// Get a reference to the underlying store (for advanced use).
    pub fn store(&self) -> &Arc<Mutex<EvolutionStore>> {
        &self.store
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_engine() -> EvolutionEngine {
        let store = EvolutionStore::new_in_memory().unwrap();
        EvolutionEngine::new(store)
    }

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
        // 2/3 = 0.666...
        assert!((score - 0.6666).abs() < 0.01);
    }

    #[test]
    fn get_skill_count_after_insert() {
        let engine = make_engine();
        let store = engine.store();
        let store = store.lock().unwrap();

        let skill = crate::store::SkillRecord {
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
        store.insert_skill(&skill).unwrap();
        drop(store); // release lock

        assert_eq!(engine.get_skill_count().unwrap(), 1);
    }
}
