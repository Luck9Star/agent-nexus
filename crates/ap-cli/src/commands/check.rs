//! `agent-nexus check` — verify environment health.

use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::Result;

use crate::output::OutputFormatter;

/// Run `check` command.
///
/// Checks: python3 available, config.toml valid, sources.yaml readable.
/// Prints status for each check. Returns error if any check fails.
pub fn run(output: &OutputFormatter) -> Result<()> {
    let mut all_passed = true;
    let root = super::find_project_root(&std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));

    // Check 1: python3 available
    if check_python(output) {
        output.success("python3: available");
    } else {
        output.error("python3: not found (install Python 3.11+)");
        all_passed = false;
    }

    // Check 2: config.toml valid
    match check_config(&root, output) {
        Ok(()) => output.success("config.toml: valid"),
        Err(e) => {
            output.error(&format!("config.toml: {}", e));
            all_passed = false;
        }
    }

    // Check 3: sources.yaml readable
    match check_sources(&root, output) {
        Ok(()) => output.success("sources.yaml: readable"),
        Err(e) => {
            output.error(&format!("sources.yaml: {}", e));
            all_passed = false;
        }
    }

    if all_passed {
        output.success("All checks passed");
        Ok(())
    } else {
        Err(anyhow::anyhow!("Some checks failed — see messages above"))
    }
}

fn check_python(_output: &OutputFormatter) -> bool {
    Command::new("python3")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn check_config(root: &Path, _output: &OutputFormatter) -> Result<()> {
    let path = root.join("config.toml");
    if !path.exists() {
        return Err(anyhow::anyhow!("not found (run `agent-nexus init` to create)"));
    }
    let content = std::fs::read_to_string(path)?;
    let _config: toml::Value = toml::from_str(&content)?;
    Ok(())
}

fn check_sources(root: &Path, _output: &OutputFormatter) -> Result<()> {
    let path = root.join("sources.yaml");
    if !path.exists() {
        return Err(anyhow::anyhow!("not found (run `agent-nexus init` to create)"));
    }
    let content = std::fs::read_to_string(path)?;
    let _sources: serde_yaml::Value = serde_yaml::from_str(&content)?;
    Ok(())
}
