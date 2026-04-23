//! AgentPromoter — promotes a mature skill to a standalone agent.
//!
//! Generates:
//! - Agent manifest (YAML)
//! - `pyproject.toml` string
//! - `SKILL.md` string
//!
//! All file writes are atomic: write to `.tmp`, then `fs::rename`.

use std::fs;
use std::path::{Path, PathBuf};

use uuid::Uuid;

use crate::store::EvolutionStore;

/// Errors during promotion.
#[derive(Debug, thiserror::Error)]
pub enum PromotionError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(String),

    #[error("Agent already exists: {0}")]
    AlreadyExists(String),

    #[error("Store error: {0}")]
    Store(#[from] crate::store::StoreError),
}

/// Result of a successful promotion.
#[derive(Debug)]
pub struct PromotionResult {
    pub agent_dir: PathBuf,
    pub manifest_path: PathBuf,
    pub pyproject_path: PathBuf,
    pub skill_md_path: PathBuf,
}

/// Validate that a skill name is safe to use as a directory name.
///
/// Uses a whitelist approach: only alphanumeric, hyphen, and underscore
/// characters are allowed. Maximum length is 64 characters.
fn validate_skill_name(name: &str) -> Result<(), PromotionError> {
    if name.is_empty() {
        return Err(PromotionError::Serialization("Skill name cannot be empty".into()));
    }
    if name.len() > 64 {
        return Err(PromotionError::Serialization(format!(
            "Skill name too long ({} chars, max 64): {name}",
            name.len()
        )));
    }
    if !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return Err(PromotionError::Serialization(format!(
            "Invalid skill name (only alphanumeric, hyphen, underscore allowed): {name}"
        )));
    }
    Ok(())
}

/// Promote a skill into a standalone agent.
///
/// This function:
/// 1. Validates the skill name (rejects path traversal).
/// 2. Checks the agent doesn't already exist in the store.
/// 3. Creates the output directory.
/// 4. Writes manifest YAML, pyproject.toml, and SKILL.md atomically.
/// 5. Registers the agent in the store.
///
/// On failure, rolls back by removing any files already written.
pub fn promote_skill(
    store: &EvolutionStore,
    skill_name: &str,
    output_dir: &Path,
) -> Result<PromotionResult, PromotionError> {
    // 0. Validate skill name
    validate_skill_name(skill_name)?;

    // 1. Check if agent already exists
    if let Some(_existing) = store.get_agent_record(skill_name)? {
        return Err(PromotionError::AlreadyExists(skill_name.to_string()));
    }

    let agent_dir = output_dir.join(skill_name);
    fs::create_dir_all(&agent_dir)?;

    let manifest_path = agent_dir.join("agent.yaml");
    let pyproject_path = agent_dir.join("pyproject.toml");
    let skill_md_path = agent_dir.join("SKILL.md");

    // Track written files for rollback
    let mut written: Vec<PathBuf> = Vec::new();

    // 2. Generate content
    let manifest_yaml = generate_manifest(skill_name);
    let pyproject_toml = generate_pyproject(skill_name);
    let skill_md = generate_skill_md(skill_name);

    // 3. Atomic writes — write to .tmp then rename
    if let Err(e) = atomic_write(&manifest_path, &manifest_yaml) {
        rollback(&written);
        return Err(e);
    }
    written.push(manifest_path.clone());

    if let Err(e) = atomic_write(&pyproject_path, &pyproject_toml) {
        rollback(&written);
        return Err(e);
    }
    written.push(pyproject_path.clone());

    if let Err(e) = atomic_write(&skill_md_path, &skill_md) {
        rollback(&written);
        return Err(e);
    }
    written.push(skill_md_path.clone());

    // 4. Register in store
    let agent_id = uuid::Uuid::new_v4().to_string();
    if let Err(e) = store.upsert_agent_record(
        &agent_id,
        skill_name,
        "atomic",
        "[]",
        None,
    ) {
        rollback(&written);
        return Err(e.into());
    }

    Ok(PromotionResult {
        agent_dir,
        manifest_path,
        pyproject_path,
        skill_md_path,
    })
}

/// Generate an agent manifest YAML string.
fn generate_manifest(skill_name: &str) -> String {
    format!(
        r#"name: {skill_name}
type: atomic
version: "1.0.0"
description: "Auto-promoted skill: {skill_name}"
entrypoint: main.py
"#
    )
}

/// Generate a pyproject.toml string.
fn generate_pyproject(skill_name: &str) -> String {
    let sanitized = skill_name.replace('-', "_");
    format!(
        r#"[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{skill_name}"
version = "1.0.0"
description = "Auto-promoted skill: {skill_name}"
requires-python = ">=3.11"

[tool.hatch.build.targets.wheel]
packages = ["{sanitized}"]
"#
    )
}

/// Generate a SKILL.md string.
fn generate_skill_md(skill_name: &str) -> String {
    format!(
        r#"# {skill_name}

Auto-promoted from skill evolution.

## Description

This agent was automatically promoted from the skill `{skill_name}` after
demonstrating sufficient maturity (selections, success rate, applications).

## Usage

Run as an MCP standalone agent or via the platform router.
"#
    )
}

/// Write content to a file atomically: write to `.tmp` then `fs::rename`.
fn atomic_write(path: &Path, content: &str) -> Result<(), PromotionError> {
    let tmp_path = path.with_file_name(format!(
        ".{}.tmp",
        Uuid::new_v4()
    ));
    fs::write(&tmp_path, content)?;
    fs::rename(&tmp_path, path)?;
    Ok(())
}

/// Remove all files that were already written (rollback on error).
fn rollback(files: &[PathBuf]) {
    for path in files {
        let _ = fs::remove_file(path);
    }
    // Try to remove the parent dir if it's empty
    if let Some(parent) = files.first().and_then(|p| p.parent()) {
        let _ = fs::remove_dir(parent);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_store() -> EvolutionStore {
        EvolutionStore::new_in_memory().unwrap()
    }

    #[test]
    fn generate_manifest_is_valid_yaml() {
        let yaml = generate_manifest("my-agent");
        assert!(yaml.contains("name: my-agent"));
        assert!(yaml.contains("type: atomic"));
        assert!(yaml.contains("entrypoint: main.py"));
        // Should parse as valid YAML
        let parsed: serde_yaml::Value = serde_yaml::from_str(&yaml).unwrap();
        assert_eq!(parsed["name"].as_str(), Some("my-agent"));
    }

    #[test]
    fn generate_pyproject_is_valid_toml() {
        let toml_str = generate_pyproject("my-agent");
        assert!(toml_str.contains("name = \"my-agent\""));
        assert!(toml_str.contains("requires-python"));
        // Parse as TOML to verify validity
        let parsed: toml::Value = toml_str.parse().unwrap();
        assert_eq!(
            parsed["project"]["name"].as_str(),
            Some("my-agent")
        );
    }

    #[test]
    fn generate_skill_md_contains_name() {
        let md = generate_skill_md("my-agent");
        assert!(md.contains("# my-agent"));
        assert!(md.contains("Auto-promoted"));
    }

    #[test]
    fn promote_skill_creates_files() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();
        let result = promote_skill(&store, "promoted-agent", dir.path()).unwrap();

        assert!(result.manifest_path.exists());
        assert!(result.pyproject_path.exists());
        assert!(result.skill_md_path.exists());
        assert!(result.agent_dir.exists());

        // Verify content
        let manifest = fs::read_to_string(&result.manifest_path).unwrap();
        assert!(manifest.contains("promoted-agent"));
    }

    #[test]
    fn promote_skill_registers_in_store() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();
        promote_skill(&store, "registered-agent", dir.path()).unwrap();

        let record = store.get_agent_record("registered-agent").unwrap();
        assert!(record.is_some());
        let record = record.unwrap();
        assert_eq!(record.agent_type, "atomic");
    }

    #[test]
    fn promote_skill_rejects_duplicate() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();
        promote_skill(&store, "dup-agent", dir.path()).unwrap();

        let result = promote_skill(&store, "dup-agent", dir.path());
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("already exists"));
    }

    #[test]
    fn promote_skill_rejects_path_traversal() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();

        let result = promote_skill(&store, "../../etc/passwd", dir.path());
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("only alphanumeric"),
            "Expected whitelist error, got: {err}"
        );
    }

    #[test]
    fn promote_skill_rejects_empty_name() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();

        let result = promote_skill(&store, "", dir.path());
        assert!(result.is_err());
    }

    #[test]
    fn promote_skill_rejects_name_with_spaces() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();

        let result = promote_skill(&store, "my skill", dir.path());
        assert!(result.is_err());
    }

    #[test]
    fn promote_skill_rejects_name_with_dots() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();

        let result = promote_skill(&store, "skill.name", dir.path());
        assert!(result.is_err());
    }

    #[test]
    fn promote_skill_rejects_overly_long_name() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();

        let long_name = "a".repeat(65);
        let result = promote_skill(&store, &long_name, dir.path());
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("too long"));
    }

    #[test]
    fn promote_skill_accepts_valid_names() {
        let store = test_store();
        let dir = tempfile::tempdir().unwrap();

        let result = promote_skill(&store, "my-skill_v2", dir.path());
        assert!(result.is_ok());
    }

    #[test]
    fn atomic_write_creates_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.txt");
        atomic_write(&path, "hello world").unwrap();
        assert!(path.exists());
        assert_eq!(fs::read_to_string(&path).unwrap(), "hello world");
    }

    #[test]
    fn rollback_removes_files() {
        let dir = tempfile::tempdir().unwrap();
        let f1 = dir.path().join("a.txt");
        let f2 = dir.path().join("b.txt");
        fs::write(&f1, "a").unwrap();
        fs::write(&f2, "b").unwrap();
        rollback(&[f1.clone(), f2.clone()]);
        assert!(!f1.exists());
        assert!(!f2.exists());
    }
}
