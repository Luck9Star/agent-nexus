//! `agent-nexus evolution` — self-evolution engine commands.

use std::path::PathBuf;

use anyhow::{Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

/// Run `evolution status` command.
///
/// Delegates to `EvolutionEngine` for skill count and health score.
pub fn run_status(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    let db_path = root.join("evolution.db");
    let store = if db_path.exists() {
        ap_evolution::EvolutionStore::new(&db_path)
            .context("Failed to open evolution store")?
    } else {
        ap_evolution::EvolutionStore::new_in_memory()
            .context("Failed to create in-memory evolution store")?
    };
    let engine = ap_evolution::EvolutionEngine::new(store);

    let skill_count = engine.get_skill_count().unwrap_or(0);
    let health_score = engine.get_health_score();

    output.info("Evolution Engine status:");
    output.info(&format!("  Health score: {health_score:.2}"));
    output.info(&format!("  Skills tracked: {skill_count}"));
    Ok(())
}

/// Run `evolution promote <skill>` command.
///
/// Wires into `ap_evolution::promotion::promote_skill` to create agent scaffolding.
pub fn run_promote(skill: &str, output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    // Ensure the evolution database exists
    let db_path = root.join("evolution.db");
    let store = if db_path.exists() {
        ap_evolution::EvolutionStore::new(&db_path)
            .context("Failed to open evolution store")?
    } else {
        ap_evolution::EvolutionStore::new_in_memory()
            .context("Failed to create in-memory evolution store")?
    };

    // Promoted agents go under .agents/promoted/
    let output_dir = root.join(".agents").join("promoted");

    output.info(&format!("Promoting skill '{skill}' to agent..."));

    let result = ap_evolution::promotion::promote_skill(&store, skill, &output_dir)
        .map_err(|e| anyhow::anyhow!("Promotion failed: {e}"))?;

    output.success(&format!(
        "Skill '{}' promoted to agent at: {}",
        skill,
        result.agent_dir.display()
    ));
    output.info(&format!("  Manifest: {}", result.manifest_path.display()));
    output.info(&format!("  Package:  {}", result.pyproject_path.display()));
    output.info(&format!("  Skill doc: {}", result.skill_md_path.display()));

    Ok(())
}
