//! ap-evolution — Self-Evolution Engine: store, analyzer, evolver, promotion.

pub mod analyzer;
pub mod compaction;
pub mod context_describer;
pub mod engine;
pub mod evolver;
pub mod health;
pub mod promotion;
pub mod store;
pub mod thresholds;

// Re-export main types
pub use analyzer::{Analyzer, EvolutionSuggestion, EvolutionType, TaskResult};
pub use engine::EvolutionEngine;
pub use store::EvolutionStore;
