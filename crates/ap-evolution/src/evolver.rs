//! `SkillEvolver` — applies evolution actions to skills.
//!
//! Supports FIX evolution: loads a skill, creates a new version with proper
//! lineage, deactivates the old version atomically.

use std::sync::Arc;

use crate::store::{EvolutionStore, SkillRecord, StoreError};

/// Outcome of an evolution attempt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EvolutionOutcome {
    /// Evolution succeeded; new skill created.
    Success {
        /// The ID of the newly created skill record.
        new_skill_id: String,
        /// Human-readable description of what changed.
        description: String,
    },
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

    #[error("Concurrent modification detected for skill: {0}")]
    ConcurrentModification(String),

    #[error("Store error: {0}")]
    Store(#[from] crate::store::StoreError),
}

/// Skill evolver — holds a reference to the store and applies evolution.
///
/// The `EvolutionStore` is already thread-safe internally (uses
/// `std::sync::Mutex<rusqlite::Connection>`), so no outer `Mutex` is needed.
pub struct SkillEvolver {
    store: Arc<EvolutionStore>,
}

impl SkillEvolver {
    /// Create a new evolver backed by the given store.
    #[must_use] 
    pub fn new(store: Arc<EvolutionStore>) -> Self {
        Self { store }
    }

    /// Attempt a fix evolution on the named skill.
    ///
    /// This performs a real FIX evolution:
    /// 1. Look up the active skill by name.
    /// 2. Create a new skill version with incremented generation and
    ///    `lineage_origin = "fix"`.
    /// 3. Store the error context in `lineage_content_diff`.
    /// 4. Atomically deactivate the old skill and insert the new one via
    ///    `store.evolve_skill()`.
    /// 5. Return the new skill ID.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn evolve_fix(
        &self,
        skill_name: &str,
        error: &str,
    ) -> Result<EvolutionOutcome, EvolverError> {
        let skill = self.store.get_skill_by_name(skill_name)?;

        let Some(skill) = skill else {
            return Err(EvolverError::SkillNotFound(skill_name.to_string()));
        };

        // Generate a new skill ID: {name}__fix_{uuid16}
        // 16 hex chars (64 bits) gives ~77k skills before 50% birthday collision,
        // far beyond realistic evolution cycles.
        let new_id = format!(
            "{}__fix_{}",
            skill.name,
            &uuid::Uuid::new_v4().to_string()[..16]
        );

        let new_generation = skill.lineage_generation + 1;
        let now = chrono::Utc::now().to_rfc3339();

        let new_skill = SkillRecord {
            id: new_id.clone(),
            name: skill.name.clone(),
            version: format!("{new_generation}.0.0"),
            lineage_origin: "fix".to_string(),
            lineage_generation: new_generation,
            lineage_content_diff: Some(format!(
                "Fix evolution: error was '{error}'"
            )),
            lineage_content_snapshot: skill.lineage_content_snapshot.clone(),
            directory: skill.directory.clone(),
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
            created_at: now.clone(),
            updated_at: now,
        };

        // Atomically deactivate parent and insert new skill with lineage link.
        // Catch concurrent modification (H11): if two callers evolve the same
        // skill simultaneously, one wins and the other hits a unique constraint
        // violation on skill name.
        if let Err(e) = self.store.evolve_skill(
            &new_skill,
            &[&skill.id],
            true, // deactivate parent (FIX is in-place replacement)
        ) {
            if is_constraint_violation(&e) {
                return Err(EvolverError::ConcurrentModification(skill_name.to_string()));
            }
            return Err(EvolverError::Store(e));
        }

        Ok(EvolutionOutcome::Success {
            new_skill_id: new_id,
            description: format!(
                "Fixed '{}' (gen {} -> gen {}): {}",
                skill.name,
                skill.lineage_generation,
                new_generation,
                error
            ),
        })
    }
}

/// Check whether a [`StoreError`] originated from an SQLite constraint violation
/// (e.g. UNIQUE, CHECK, NOT NULL).  Uses rusqlite's typed error code instead
/// of string matching so it is locale-independent and forwards-compatible.
fn is_constraint_violation(err: &StoreError) -> bool {
    match err {
        StoreError::Sqlite(rusqlite::Error::SqliteFailure(failure, _)) => {
            failure.code == rusqlite::ErrorCode::ConstraintViolation
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_store_with_skill() -> Arc<EvolutionStore> {
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
        Arc::new(store)
    }

    fn make_store_with_skill_gen2() -> Arc<EvolutionStore> {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = SkillRecord {
            id: "s-002".to_string(),
            name: "gen2-skill".to_string(),
            version: "2.0.0".to_string(),
            lineage_origin: "fix".to_string(),
            lineage_generation: 2,
            lineage_content_diff: Some("previous fix".to_string()),
            lineage_content_snapshot: None,
            directory: Some("/skills/gen2".to_string()),
            is_active: true,
            total_selections: 5,
            total_applied: 3,
            total_completions: 2,
            total_fallbacks: 1,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        };
        store.insert_skill(&skill).unwrap();
        Arc::new(store)
    }

    #[test]
    fn evolve_fix_success() {
        let store = make_store_with_skill();
        let evolver = SkillEvolver::new(store.clone());
        let outcome = evolver
            .evolve_fix("test-skill", "some error")
            .unwrap();
        assert!(matches!(outcome, EvolutionOutcome::Success { .. }));
        if let EvolutionOutcome::Success { new_skill_id, description } = outcome {
            assert!(new_skill_id.starts_with("test-skill__fix_"));
            assert!(description.contains("gen 0 -> gen 1"));
            assert!(description.contains("some error"));

            // Verify new skill exists in store
            let new_skill = store.get_skill_by_id(&new_skill_id).unwrap().unwrap();
            assert_eq!(new_skill.name, "test-skill");
            assert_eq!(new_skill.lineage_origin, "fix");
            assert_eq!(new_skill.lineage_generation, 1);
            assert!(new_skill.is_active);
            assert_eq!(new_skill.total_selections, 0);
        }
    }

    #[test]
    fn evolve_fix_deactivates_old_skill() {
        let store = make_store_with_skill();
        let evolver = SkillEvolver::new(store.clone());

        evolver.evolve_fix("test-skill", "broken").unwrap();

        // Old skill should be deactivated (verify by ID, not name — new skill has same name)
        let old_by_id = store.get_skill_by_id("s-001").unwrap();
        assert!(old_by_id.is_some(), "old skill should still exist in DB");
        assert!(!old_by_id.unwrap().is_active, "old skill should be deactivated");

        // New skill with same name should be the active one
        let active = store.get_skill_by_name("test-skill").unwrap();
        assert!(active.is_some(), "new skill should be found by name");
        assert_ne!(active.unwrap().id, "s-001", "active skill should be the new one, not old");
    }

    #[test]
    fn evolve_fix_stores_error_context() {
        let store = make_store_with_skill();
        let evolver = SkillEvolver::new(store.clone());

        let outcome = evolver
            .evolve_fix("test-skill", "critical failure XYZ")
            .unwrap();

        if let EvolutionOutcome::Success { new_skill_id, .. } = outcome {
            let new_skill = store.get_skill_by_id(&new_skill_id).unwrap().unwrap();
            let diff = new_skill.lineage_content_diff.unwrap();
            assert!(diff.contains("critical failure XYZ"));
        }
    }

    #[test]
    fn evolve_fix_increments_generation() {
        let store = make_store_with_skill_gen2();
        let evolver = SkillEvolver::new(store.clone());

        let outcome = evolver
            .evolve_fix("gen2-skill", "need another fix")
            .unwrap();

        if let EvolutionOutcome::Success { new_skill_id, .. } = outcome {
            let new_skill = store.get_skill_by_id(&new_skill_id).unwrap().unwrap();
            assert_eq!(new_skill.lineage_generation, 3);
            assert_eq!(new_skill.version, "3.0.0");
        }
    }

    #[test]
    fn evolve_fix_creates_lineage_link() {
        let store = make_store_with_skill();
        let evolver = SkillEvolver::new(store.clone());

        let outcome = evolver
            .evolve_fix("test-skill", "fix needed")
            .unwrap();

        if let EvolutionOutcome::Success { new_skill_id, .. } = outcome {
            // Parent should have the new skill as a child
            let children = store.get_children("s-001").unwrap();
            assert_eq!(children.len(), 1);
            assert_eq!(children[0], new_skill_id);
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
            new_skill_id: "id-1".to_string(),
            description: "fixed".to_string(),
        };
        let no_change = EvolutionOutcome::NoChange;
        let failed = EvolutionOutcome::Failed {
            reason: "bad".to_string(),
        };
        assert_eq!(
            success,
            EvolutionOutcome::Success {
                new_skill_id: "id-1".to_string(),
                description: "fixed".to_string(),
            }
        );
        assert_ne!(success, no_change);
        assert_ne!(no_change, failed);
    }

    #[test]
    fn evolver_error_display() {
        let err = EvolverError::SkillNotFound("my-skill".to_string());
        assert!(err.to_string().contains("my-skill"));
    }

    /// Verify that SkillEvolver is Send + Sync (required for cross-thread use).
    /// This is a compile-time assertion — if it fails, the code won't compile.
    #[test]
    fn skill_evolver_is_send_and_sync() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<SkillEvolver>();
    }
}
