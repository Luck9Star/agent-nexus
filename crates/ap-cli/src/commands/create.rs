//! `agent-nexus create agent <name>` -- generate agent scaffold.

use std::path::Path;

use anyhow::{Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

/// SKILL.md template for new agents.
const SKILL_TEMPLATE: &str = r#"# {name}

## Description

{description}

## Capabilities

- (list agent capabilities here)

## Usage

```bash
agent-nexus run {name} "<task>"
```

## Configuration

Default model: `openai:gpt-4o`

## Dependencies

(none)
"#;

/// Default agent main.py template.
const MAIN_PY_TEMPLATE: &str = r#""""{name} agent -- auto-generated scaffold."""

def main():
    print("Hello from {name}!")


if __name__ == "__main__":
    main()
"#;

/// Default pyproject.toml template.
const PYPROJECT_TEMPLATE: &str = r#"[project]
name = "{name}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.11"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"#;

/// Run `create agent <name>` command.
pub fn run_agent(name: &str, output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(name)?;
    let root = commands::find_project_root(&std::env::current_dir()?);
    create_agent_in_root(name, &root, output)
}

/// Create agent scaffold in a specific root directory (testable without cwd).
fn create_agent_in_root(name: &str, root: &Path, output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(name)?;

    let target = root.join("agents").join("atomic").join(name);

    if target.exists() {
        anyhow::bail!(
            "Agent directory '{}' already exists. Choose a different name or remove the existing directory.",
            target.display()
        );
    }

    // Create directory structure
    let agent_dir = target.join(format!("agent_{name}"));
    let tests_dir = target.join("tests");
    std::fs::create_dir_all(&agent_dir)
        .with_context(|| format!("Failed to create agent directory '{}'", agent_dir.display()))?;
    std::fs::create_dir_all(&tests_dir)
        .with_context(|| format!("Failed to create tests directory '{}'", tests_dir.display()))?;

    let description = format!("Agent {name}");

    // Write SKILL.md
    let skill_content = SKILL_TEMPLATE
        .replace("{name}", name)
        .replace("{description}", &description);
    std::fs::write(target.join("SKILL.md"), skill_content)?;

    // Write agent module __init__.py and main.py
    std::fs::write(agent_dir.join("__init__.py"), "")?;
    let main_content = MAIN_PY_TEMPLATE.replace("{name}", name);
    std::fs::write(agent_dir.join("main.py"), main_content)?;

    // Write pyproject.toml
    let pyproject_content = PYPROJECT_TEMPLATE
        .replace("{name}", name)
        .replace("{description}", &description);
    std::fs::write(target.join("pyproject.toml"), pyproject_content)?;

    output.success(&format!("Created agent scaffold at {}", target.display()));
    output.info("Edit SKILL.md to define the agent's capabilities, then implement in agent_<name>/main.py");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_agent_scaffold() {
        let dir = tempfile::tempdir().unwrap();
        let output = OutputFormatter::new(true, false);
        create_agent_in_root("test-agent", dir.path(), &output).unwrap();

        let target = dir.path().join("agents").join("atomic").join("test-agent");
        assert!(target.join("SKILL.md").exists());
        assert!(target.join("pyproject.toml").exists());
        assert!(target.join("agent_test-agent").join("main.py").exists());
        assert!(target.join("tests").exists());

        let skill = std::fs::read_to_string(target.join("SKILL.md")).unwrap();
        assert!(skill.contains("test-agent"));
    }

    #[test]
    fn reject_existing_directory() {
        let dir = tempfile::tempdir().unwrap();
        // Create existing directory
        let existing = dir.path().join("agents").join("atomic").join("duplicate");
        std::fs::create_dir_all(&existing).unwrap();

        let output = OutputFormatter::new(true, false);
        let result = create_agent_in_root("duplicate", dir.path(), &output);
        assert!(result.is_err());
    }

    #[test]
    fn reject_path_traversal_in_name() {
        let dir = tempfile::tempdir().unwrap();
        let output = OutputFormatter::new(true, false);
        let result = create_agent_in_root("../../evil", dir.path(), &output);
        assert!(result.is_err());
    }
}
