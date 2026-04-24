//! `agent-nexus evolution` — self-evolution engine commands.

use std::path::PathBuf;

use anyhow::{Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

fn evolution_db_path() -> PathBuf {
    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );
    root.join("evolution.db")
}

/// Run `evolution status` command.
pub fn run_status(output: &OutputFormatter) -> Result<()> {
    let db_path = evolution_db_path();
    let store = ap_evolution::EvolutionStore::new(&db_path)
        .context("Failed to open evolution store")?;
    let engine = ap_evolution::EvolutionEngine::new(store);

    let skill_count = engine.get_skill_count().unwrap_or(0);
    let health_score = engine.get_health_score();

    if output.is_json() {
        output.data(&serde_json::json!({
            "status": "ok",
            "health_score": health_score,
            "skill_count": skill_count,
        }));
    } else {
        output.info("Evolution Engine status:");
        output.info(&format!("  Health score: {health_score:.2}"));
        output.info(&format!("  Skills tracked: {skill_count}"));
    }
    Ok(())
}

/// Run `evolution health [<skill>] [-v]` command.
pub fn run_health(skill_name: Option<&str>, verbose: bool, output: &OutputFormatter) -> Result<()> {
    let db_path = evolution_db_path();
    let store = ap_evolution::EvolutionStore::new(&db_path)
        .context("Failed to open evolution store")?;

    if let Some(name) = skill_name {
        let skill = store.get_skill_by_name(name)
            .context("Failed to query store")?
            .with_context(|| format!("Skill '{name}' not found"))?;

        if output.is_json() {
            output.data(&serde_json::json!({
                "name": skill.name,
                "version": skill.version,
                "active": skill.is_active,
                "selections": skill.total_selections,
                "completions": skill.total_completions,
                "fallbacks": skill.total_fallbacks,
            }));
        } else {
            println!("Skill: {}", skill.name);
            println!("  Version: {}", skill.version);
            println!("  Active: {}", skill.is_active);
            println!("  Selections: {}", skill.total_selections);
            println!("  Completions: {}", skill.total_completions);
            println!("  Fallbacks: {}", skill.total_fallbacks);
            if verbose {
                println!("  ID: {}", skill.id);
                println!("  Origin: {}", skill.lineage_origin);
                println!("  Generation: {}", skill.lineage_generation);
            }
        }
    } else {
        let skills = store.get_active_skills().context("Failed to query store")?;
        if output.is_json() {
            let arr: Vec<_> = skills
                .iter()
                .map(|s| serde_json::json!({"name": s.name, "version": s.version, "completions": s.total_completions}))
                .collect();
            output.data(&arr);
        } else {
            if skills.is_empty() {
                output.info("No active skills.");
            } else {
                println!("{:<30} {:<10} {:<12} Active", "Skill", "Version", "Completions");
                println!("{}", "-".repeat(65));
                for skill in &skills {
                    println!("{:<30} {:<10} {:<12} {}", skill.name, skill.version, skill.total_completions, skill.is_active);
                }
            }
        }
    }
    Ok(())
}

/// Run `evolution list [--all]` command.
pub fn run_list(all: bool, output: &OutputFormatter) -> Result<()> {
    let db_path = evolution_db_path();
    let store = ap_evolution::EvolutionStore::new(&db_path)
        .context("Failed to open evolution store")?;

    let skills = if all {
        store.get_all_skills().context("Failed to query store")?
    } else {
        store.get_active_skills().context("Failed to query store")?
    };

    if output.is_json() {
        let arr: Vec<_> = skills
            .iter()
            .map(|s| serde_json::json!({"name": s.name, "version": s.version, "active": s.is_active, "completions": s.total_completions}))
            .collect();
        output.data(&arr);
    } else {
        if skills.is_empty() {
            output.info(if all { "No skills found." } else { "No active skills." });
        } else {
            println!("{:<30} {:<10} {:<12} Active", "Name", "Version", "Completions");
            println!("{}", "-".repeat(65));
            for skill in &skills {
                println!("{:<30} {:<10} {:<12} {}", skill.name, skill.version, skill.total_completions, skill.is_active);
            }
        }
    }
    Ok(())
}

/// Run `evolution history <skill>` command.
pub fn run_history(skill_name: &str, output: &OutputFormatter) -> Result<()> {
    let db_path = evolution_db_path();
    let store = ap_evolution::EvolutionStore::new(&db_path)
        .context("Failed to open evolution store")?;

    // Find the skill first to get its ID
    let skill = store.get_skill_by_name(skill_name)
        .context("Failed to query store")?
        .with_context(|| format!("Skill '{skill_name}' not found"))?;

    let ancestry = store.get_ancestry(&skill.id, 10).context("Failed to query ancestry")?;

    if output.is_json() {
        let arr: Vec<_> = ancestry
            .iter()
            .map(|s| serde_json::json!({"name": s.name, "version": s.version, "generation": s.lineage_generation}))
            .collect();
        output.data(&arr);
    } else {
        if ancestry.is_empty() {
            output.info(&format!("No history found for skill '{skill_name}'."));
        } else {
            println!("Version lineage for '{skill_name}':");
            for (i, skill) in ancestry.iter().enumerate() {
                println!("  {} v{} (gen: {})", "→".repeat(i + 1), skill.version, skill.lineage_generation);
            }
        }
    }
    Ok(())
}

/// Run `evolution metrics [-a <agent>]` command.
pub fn run_metrics(agent: Option<&str>, output: &OutputFormatter) -> Result<()> {
    let db_path = evolution_db_path();
    let store = ap_evolution::EvolutionStore::new(&db_path)
        .context("Failed to open evolution store")?;

    let skills: Vec<_> = if let Some(agent_name) = agent {
        store.get_active_skills()
            .unwrap_or_default()
            .into_iter()
            .filter(|s| s.name.contains(agent_name))
            .collect()
    } else {
        store.get_active_skills().unwrap_or_default()
    };

    let count = skills.len();
    let total_completions: i64 = skills.iter().map(|s| s.total_completions).sum();
    let total_selections: i64 = skills.iter().map(|s| s.total_selections).sum();

    if output.is_json() {
        output.data(&serde_json::json!({
            "total_skills": count,
            "total_completions": total_completions,
            "total_selections": total_selections,
        }));
    } else {
        println!("Evolution metrics:");
        println!("  Total skills: {count}");
        println!("  Total completions: {total_completions}");
        println!("  Total selections: {total_selections}");
    }
    Ok(())
}

/// Run `evolution fix <skill_name>` command.
pub fn run_fix(skill_name: &str, output: &OutputFormatter) -> Result<()> {
    let db_path = evolution_db_path();
    let store = ap_evolution::EvolutionStore::new(&db_path)
        .context("Failed to open evolution store")?;
    let engine = ap_evolution::EvolutionEngine::new(store);

    output.info(&format!("Triggering FIX evolution for skill '{skill_name}'..."));

    let trigger = ap_evolution::engine::EvolveTrigger::Failure {
        skill_name: skill_name.to_string(),
        reason: "Manual fix triggered via CLI".to_string(),
    };

    let result = engine.evolve(trigger).map_err(|e| anyhow::anyhow!("Fix failed: {e}"))?;

    let successes: Vec<_> = result.outcomes.iter().filter(|o| matches!(o, ap_evolution::evolver::EvolutionOutcome::Success { .. })).collect();
    if !successes.is_empty() {
        output.success(&format!("Skill '{skill_name}' fix completed ({} outcome(s)).", successes.len()));
    } else {
        output.info(&format!("No changes made for skill '{skill_name}'."));
    }
    Ok(())
}

/// Run `evolution promote <skill_name>` command.
pub fn run_promote(skill: &str, output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(skill)
        .with_context(|| format!("Invalid skill name: {skill}"))?;

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    let db_path = root.join("evolution.db");
    let store = ap_evolution::EvolutionStore::new(&db_path)
        .context("Failed to open evolution store")?;

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
