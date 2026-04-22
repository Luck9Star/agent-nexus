//! SkillEvolver — applies evolution actions to skills.
//!
//! Currently stubbed: `evolve_fix` loads a skill from the store and returns
//! a mocked outcome. Full IPC-based evolution will be added when the runtime
//! subprocess bridge is available.

use std::sync::{Arc, Mutex};

use crate::store::{EvolutionStore, SkillRecord};

/// Outcome of an evolution attempt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EvolutionOutcome {
    /// Evolution succeeded; `new_code` is the updated skill content.
    Success { new_code: String },
    /// No change was needed or possible.
    NoChange,
    /// Evolution failed with a reason.
    Failed { reason: String },
}

/// Errors from the evolver.
#[derive(Debug, thiserror::Error)]
pub enum EvolverError {
    #[error("Skill not found: {0}")]
    SkillNotFound(String),

    #[error("IPC error: {0}")]
    Ipc(String),

    #[error("Store error: {0}")]
    Store(#[from] crate::store::StoreError),
}

/// Skill evolver — holds a reference to the store and applies evolution.
pub struct SkillEvolver {
    store: Arc<Mutex<EvolutionStore>>,
}

impl SkillEvolver {
    /// Create a new evolver backed by the given store.
    pub fn new(store: Arc<Mutex<EvolutionStore>>) -> Self {
        Self { store }
    }

    /// Attempt a fix evolution on the named skill.
    ///
    /// For now, this loads the skill from the store and returns a mocked
    /// result. Real IPC-based evolution will be implemented later.
    pub fn evolve_fix(
        &self,
        skill_name: &str,
        error: &str,
    ) -> Result<EvolutionOutcome, EvolverError> {
        let store = self.store.lock().map_err(|e| {
            EvolverError::Ipc(format!("store lock poisoned: {e}"))
        })?;

        let skill: Option<SkillRecord> = store.get_skill_by_name(skill_name)?;

        let Some(skill) = skill else {
            return Err(EvolverError::SkillNotFound(skill_name.to_string()));
        };

        // Stub: return a mocked success. In production this would:
        // 1. Send the skill + error to the runtime subprocess via IPC
        // 2. Get back a patched version
        // 3. Store the new version as a child in the lineage tree
        let _ = error; // used in real implementation
        Ok(EvolutionOutcome::Success {
            new_code: format!(
                "# Evolved version of {} (gen {})\n# Original content preserved.\n",
                skill.name, skill.lineage_generation + 1
            ),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_store_with_skill() -> Arc<Mutex<EvolutionStore>> {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = SkillRecord {
            id: "s-001".to_string(),
            name: "test-skill".to_string(),
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
        Arc::new(Mutex::new(store))
    }

    #[test]
    fn evolve_fix_success() {
        let store = make_store_with_skill();
        let evolver = SkillEvolver::new(store);
        let outcome = evolver
            .evolve_fix("test-skill", "some error")
            .unwrap();
        assert!(matches!(outcome, EvolutionOutcome::Success { .. }));
        if let EvolutionOutcome::Success { new_code } = outcome {
            assert!(new_code.contains("test-skill"));
            assert!(new_code.contains("gen 1"));
        }
    }

    #[test]
    fn evolve_fix_skill_not_found() {
        let store = make_store_with_skill();
        let evolver = SkillEvolver::new(store);
        let result = evolver.evolve_fix("nonexistent", "error");
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(err, EvolverError::SkillNotFound(_)));
        assert!(err.to_string().contains("nonexistent"));
    }

    #[test]
    fn evolution_outcome_equality() {
        let success = EvolutionOutcome::Success {
            new_code: "code".to_string(),
        };
        let no_change = EvolutionOutcome::NoChange;
        let failed = EvolutionOutcome::Failed {
            reason: "bad".to_string(),
        };
        assert_eq!(success, EvolutionOutcome::Success {
            new_code: "code".to_string(),
        });
        assert_ne!(success, no_change);
        assert_ne!(no_change, failed);
    }

    #[test]
    fn evolver_error_display() {
        let err = EvolverError::SkillNotFound("my-skill".to_string());
        assert!(err.to_string().contains("my-skill"));

        let err = EvolverError::Ipc("connection reset".to_string());
        assert!(err.to_string().contains("IPC error"));
    }
}
