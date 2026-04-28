//! Trait abstraction for the evolution store.
//!
//! Enables mocking and alternative backends. The concrete `EvolutionStore`
//! (SQLite-backed) implements this trait. Consumers should depend on `impl Store`
//! or `Arc<dyn Store>` to allow substitution in tests.

use super::error::Result;
use super::queries::{SkillRecord};

/// Core persistence operations for the Self-Evolution Engine.
///
/// This trait captures the methods used by `SkillEvolver` and `EvolutionEngine`.
/// Additional store methods (judgments, analyses, agent records) are available on
/// the concrete `EvolutionStore` type and can be added here when needed.
pub trait Store: Send + Sync {
    /// Insert a new skill record.
    fn insert_skill(&self, skill: &SkillRecord) -> Result<()>;

    /// Look up a skill by its human-readable name.
    fn get_skill_by_name(&self, name: &str) -> Result<Option<SkillRecord>>;

    /// Look up a skill by its unique ID.
    fn get_skill_by_id(&self, id: &str) -> Result<Option<SkillRecord>>;

    /// Return all currently active skills.
    fn get_active_skills(&self) -> Result<Vec<SkillRecord>>;

    /// Return the child skill IDs for a given parent.
    fn get_children(&self, parent_id: &str) -> Result<Vec<String>>;

    /// Create an evolved skill: insert the new skill, set up lineage, optionally deactivate parents.
    fn evolve_skill(
        &self,
        new_skill: &SkillRecord,
        parent_ids: &[&str],
        deactivate_parents: bool,
    ) -> Result<()>;

    /// Load the persisted health state (score, `total_evaluations`).
    fn load_health_state(&self) -> Result<(f64, u64)>;

    /// Persist the health state.
    fn save_health_state(&self, score: f64, total: u64) -> Result<()>;

    /// Count the number of active skills.
    fn count_active_skills(&self) -> Result<i64>;
}
