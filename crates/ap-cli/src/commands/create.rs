//! `agent-nexus create agent <name>` — generate agent scaffold.
//!
//! Matches the Python CLI `create agent` command with --description, --tools,
//! --wizard, and --output flags. Generates: agent-manifest.yaml, agent.py,
//! SKILL.md, pyproject.toml, <pkg>/__init__.py, <pkg>/agent.py, <pkg>/mcp_adapter.py.

use anyhow::{Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

/// Tool pattern modes matching the Python CLI.
const TOOLS_MODE_MAP: &[(&str, &[&str])] = &[
    ("simple", &["run"]),
    ("pipeline", &["analyze", "execute", "report"]),
];

/// Run `create agent <name>` command with full flag support.
pub fn run_agent(
    name: &str,
    description: Option<&str>,
    tools: &str,
    wizard: bool,
    output_dir: Option<&str>,
    output: &OutputFormatter,
) -> Result<()> {
    commands::validate_fs_name(name)?;

    // Resolve tool pattern
    let tool_list = TOOLS_MODE_MAP
        .iter()
        .find(|(k, _)| k == &tools)
        .map(|(_, v)| *v)
        .unwrap_or_else(|| {
            eprintln!("Warning: Unknown tools pattern '{tools}', falling back to 'simple'");
            &["run"] as &[&str]
        });

    if wizard {
        // Interactive wizard mode -- prompt for parameters
        run_wizard(name, output_dir, output)
    } else {
        // Non-interactive mode -- description is required
        let desc = description.unwrap_or({
            // Default description if none provided
            ""
        });
        if desc.is_empty() {
            anyhow::bail!("Error: --description is required without --wizard.");
        }
        create_scaffold(name, desc, tool_list, "standard", "economy", output_dir, output)
    }
}

/// Create the agent scaffold directory and files.
fn create_scaffold(
    name: &str,
    description: &str,
    tools: &[&str],
    recommended_model: &str,
    fallback_model: &str,
    output_dir: Option<&str>,
    output: &OutputFormatter,
) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);

    let base = match output_dir {
        Some(d) => std::path::PathBuf::from(d),
        None => root.join("agents").join("atomic"),
    };
    let agent_dir = base.join(name);

    if agent_dir.exists() {
        anyhow::bail!(
            "Agent directory '{}' already exists. Choose a different name or remove the existing directory.",
            agent_dir.display()
        );
    }

    let pkg_name = name.replace('-', "_");
    let pkg_dir = agent_dir.join(&pkg_name);

    std::fs::create_dir_all(&pkg_dir)
        .with_context(|| format!("Failed to create agent directory '{}'", pkg_dir.display()))?;

    // Generate files
    let manifest_content = gen_manifest(name, description, tools, recommended_model, fallback_model);
    let agent_py_content = gen_top_level_agent(name, &pkg_name, tools);
    let skill_md_content = gen_skill_md(name, description, tools);
    let pyproject_content = gen_pyproject(name, &pkg_name);
    let pkg_init_content = gen_pkg_init(name, &pkg_name);
    let pkg_agent_content = gen_pkg_agent(name, &pkg_name, tools);
    let mcp_adapter_content = gen_mcp_adapter(name, &pkg_name, tools);

    std::fs::write(agent_dir.join("agent-manifest.yaml"), manifest_content)?;
    std::fs::write(agent_dir.join("agent.py"), agent_py_content)?;
    std::fs::write(agent_dir.join("SKILL.md"), skill_md_content)?;
    std::fs::write(agent_dir.join("pyproject.toml"), pyproject_content)?;
    std::fs::write(pkg_dir.join("__init__.py"), pkg_init_content)?;
    std::fs::write(pkg_dir.join("agent.py"), pkg_agent_content)?;
    std::fs::write(pkg_dir.join("mcp_adapter.py"), mcp_adapter_content)?;

    let file_count = std::fs::read_dir(&agent_dir)
        .map(|d| d.count())
        .unwrap_or(0)
        + std::fs::read_dir(&pkg_dir)
            .map(|d| d.count())
            .unwrap_or(0);

    output.success(&format!("Created agent: {}", agent_dir.display()));
    output.info(&format!("  Tools: {}", tools.join(", ")));
    output.info(&format!("  Files: {file_count} generated"));

    if !output.is_json() {
        println!();
        println!("Next steps:");
        println!(
            "  1. Edit {}/agent.py -- implement logic",
            pkg_dir.display()
        );
        println!("  2. Edit {}/SKILL.md -- document capabilities", agent_dir.display());
        println!("  3. Test:  cd {} && uv run pytest", agent_dir.display());
    }

    Ok(())
}

/// Run interactive wizard mode.
fn run_wizard(name: &str, output_dir: Option<&str>, output: &OutputFormatter) -> Result<()> {
    // In Rust CLI we use stdin/stdout for interactive prompts.
    // Simplified wizard compared to Python (which uses questionary).
    use std::io::{self, Write};

    println!("=== Create Agent: {name} ===\n");

    print!("Description: ");
    io::stdout().flush()?;
    let mut description = String::new();
    io::stdin().read_line(&mut description)?;
    let description = description.trim();

    println!("\nTool pattern:");
    println!("  1) simple   -- single `run` tool");
    println!("  2) pipeline -- analyze / execute / report");
    print!("Choose [1]: ");
    io::stdout().flush()?;
    let mut choice = String::new();
    io::stdin().read_line(&mut choice)?;
    let tools_key = if choice.trim() == "2" {
        "pipeline"
    } else {
        "simple"
    };

    let tool_list = TOOLS_MODE_MAP
        .iter()
        .find(|(k, _)| *k == tools_key)
        .map(|(_, v)| *v)
        .unwrap_or(&["run"]);

    println!("\nModel tier:");
    println!("  1) lightweight");
    println!("  2) standard");
    println!("  3) premium");
    print!("Recommended model [2]: ");
    io::stdout().flush()?;
    let mut model_choice = String::new();
    io::stdin().read_line(&mut model_choice)?;
    let recommended = match model_choice.trim() {
        "1" => "lightweight",
        "3" => "premium",
        _ => "standard",
    };

    create_scaffold(
        name,
        description,
        tool_list,
        recommended,
        "economy",
        output_dir,
        output,
    )
}

// ---------------------------------------------------------------------------
// Template generators (matching Python's create_cmd.py)
// ---------------------------------------------------------------------------

fn gen_manifest(
    name: &str,
    description: &str,
    tools: &[&str],
    recommended_model: &str,
    fallback_model: &str,
) -> String {
    let tools_yaml = tools
        .iter()
        .map(|t| format!("- {t}"))
        .collect::<Vec<_>>()
        .join("\n    ");

    format!(
        r#"name: {name}
type: atomic
version: "0.1.0"
description: "{description}"
capabilities:
  - general-purpose
mcp:
  tools:
    {tools_yaml}
permissions:
  mode: default
  allowed_tools:
    - file_read
    - grep
    - glob
  denied_tools:
    - bash
model_config:
  recommended: {recommended_model}
  fallback: {fallback_model}
"#
    )
}

fn gen_top_level_agent(name: &str, pkg_name: &str, tools: &[&str]) -> String {
    let fn_name = name.replace('-', "_");

    if tools == ["run"] {
        format!(
            r#""""Top-level entry point for {name} agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the {name} agent task."""
    from {pkg_name}.agent import {fn_name}_run
    return await {fn_name}_run(task, _context)
"#
        )
    } else {
        let imports: Vec<String> = tools
            .iter()
            .map(|t| format!("from {pkg_name}.agent import {fn_name}_{t}"))
            .collect();
        let handlers: Vec<String> = tools
            .iter()
            .map(|t| {
                format!(
                    r#"async def {t}(task: str, _context: dict | None = None) -> str:
    """{t_cap} phase for {name}."""
    return await {fn_name}_{t}(task, _context)"#,
                    t_cap = {
                        let mut c = t.chars();
                        match c.next() {
                            None => String::new(),
                            Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                        }
                    }
                )
            })
            .collect();

        format!(
            r#""""Top-level entry point for {name} agent."""

from __future__ import annotations

{imports}

{handlers}
"#,
            imports = imports.join("\n"),
            handlers = handlers.join("\n\n")
        )
    }
}

fn gen_skill_md(name: &str, description: &str, tools: &[&str]) -> String {
    let mut lines = vec![
        format!("# {name} -- {description}"),
        String::new(),
        "## Role".to_string(),
        String::new(),
        format!("You are a {name} agent. {description}"),
        String::new(),
        "## Capabilities".to_string(),
        String::new(),
    ];

    for t in tools {
        lines.push(format!("- **{t}**: TODO - describe what this tool does"));
    }

    lines.extend_from_slice(&[
        String::new(),
        "## Error Handling".to_string(),
        String::new(),
        "| Scenario | Handling |".to_string(),
        "|----------|----------|".to_string(),
        "| Invalid input | Return error message |".to_string(),
        "| Processing failure | Raise with context |".to_string(),
        String::new(),
        "## Example Usage".to_string(),
        String::new(),
    ]);

    if tools == ["run"] {
        lines.extend_from_slice(&[
            "```json".to_string(),
            r#"{ "task": "example task description" }"#.to_string(),
            "```".to_string(),
        ]);
    } else {
        for t in tools {
            lines.extend_from_slice(&[
                format!("### {t}"),
                "```json".to_string(),
                format!("{{ \"task\": \"example {t} input\" }}"),
                "```".to_string(),
                String::new(),
            ]);
        }
    }

    lines.join("\n") + "\n"
}

fn gen_pyproject(name: &str, pkg_name: &str) -> String {
    format!(
        r#"[project]
name = "agent-{name}"
version = "0.1.0"
description = "TODO: agent description"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
]

[project.optional-dependencies]
full = [
    "fastmcp>=2.0",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["{pkg_name}"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM"]
"#
    )
}

fn gen_pkg_init(name: &str, pkg_name: &str) -> String {
    // Convert kebab-case to PascalCase for class name
    let class_name = name
        .split('-')
        .map(|part| {
            let mut c = part.chars();
            match c.next() {
                None => String::new(),
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
            }
        })
        .collect::<String>();

    format!(
        r#""""agent-{name} -- Atomic Agent."""

from {pkg_name}.agent import {class_name}Agent

__all__ = [
    "{class_name}Agent",
]
"#
    )
}

fn gen_pkg_agent(name: &str, _pkg_name: &str, tools: &[&str]) -> String {
    let class_name = name
        .split('-')
        .map(|part| {
            let mut c = part.chars();
            match c.next() {
                None => String::new(),
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
            }
        })
        .collect::<String>();
    let fn_name = name.replace('-', "_");

    if tools == ["run"] {
        format!(
            r#""""{class_name}Agent implementation."""

from __future__ import annotations


class {class_name}Agent:
    """{name} agent."""

    async def run(self, task: str, context: dict | None = None) -> str:
        """Execute the agent task.

        Args:
            task: Task description.
            context: Optional context dictionary.

        Returns:
            Task result as string.
        """
        # TODO: Implement agent logic
        return f"Agent {{name!r}} executed: {{task}}"


async def {fn_name}_run(task: str, context: dict | None = None) -> str:
    """Module-level entry point for MCP adapter."""
    agent = {class_name}Agent()
    return await agent.run(task, context)
"#
        )
    } else {
        let methods: Vec<String> = tools
            .iter()
            .map(|t| {
                let t_cap = {
                    let mut c = t.chars();
                    match c.next() {
                        None => String::new(),
                        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                    }
                };
                format!(
                    r#"    async def {t}(self, task: str, context: dict | None = None) -> str:
        """{t_cap} phase.

        Args:
            task: Task description.
            context: Optional context dictionary.

        Returns:
            Result as string.
        """
        # TODO: Implement {t} logic
        return f"Agent {{name!r}} {t}: {{task}}""#
                )
            })
            .collect();

        let entry_points: Vec<String> = tools
            .iter()
            .map(|t| {
                format!(
                    r#"async def {fn_name}_{t}(task: str, context: dict | None = None) -> str:
    """Module-level entry point for {t}."""
    agent = {class_name}Agent()
    return await agent.{t}(task, context)"#
                )
            })
            .collect();

        format!(
            r#""""{class_name}Agent implementation."""

from __future__ import annotations


class {class_name}Agent:
    """{name} agent with pipeline tools."""

{methods}

{entry_points}
"#,
            methods = methods.join("\n\n"),
            entry_points = entry_points.join("\n\n")
        )
    }
}

fn gen_mcp_adapter(name: &str, pkg_name: &str, tools: &[&str]) -> String {
    let fn_name = name.replace('-', "_");

    if tools == ["run"] {
        format!(
            r#""""MCP adapter -- expose {name} as an MCP Server.

Requires the ``fastmcp`` package.
"""

from __future__ import annotations


def create_mcp_server() -> object:
    """Create and return a FastMCP server for {name}."""
    from fastmcp import FastMCP

    mcp = FastMCP("{name}")

    @mcp.tool()
    async def run(task: str, context: dict | None = None) -> str:
        """Execute the {name} agent task."""
        from {pkg_name}.agent import {fn_name}_run
        return await {fn_name}_run(task, context)

    return mcp
"#
        )
    } else {
        let handlers: Vec<String> = tools
            .iter()
            .map(|t| {
                let t_cap = {
                    let mut c = t.chars();
                    match c.next() {
                        None => String::new(),
                        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                    }
                };
                format!(
                    r#"    @mcp.tool()
    async def {t}(task: str, context: dict | None = None) -> str:
        """{t_cap} phase for {name}."""
        from {pkg_name}.agent import {fn_name}_{t}
        return await {fn_name}_{t}(task, context)"#
                )
            })
            .collect();

        format!(
            r#""""MCP adapter -- expose {name} as an MCP Server.

Requires the ``fastmcp`` package.
"""

from __future__ import annotations


def create_mcp_server() -> object:
    """Create and return a FastMCP server for {name}."""
    from fastmcp import FastMCP

    mcp = FastMCP("{name}")

{handlers}

    return mcp
"#,
            handlers = handlers.join("\n\n")
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_agent_scaffold() {
        let dir = tempfile::tempdir().unwrap();
        let output = OutputFormatter::new(true, false);
        create_scaffold(
            "test-agent",
            "A test agent",
            &["run"],
            "standard",
            "economy",
            Some(dir.path().to_str().unwrap()),
            &output,
        )
        .unwrap();

        let target = dir.path().join("test-agent");
        assert!(target.join("SKILL.md").exists());
        assert!(target.join("pyproject.toml").exists());
        assert!(target.join("agent-manifest.yaml").exists());
        assert!(target.join("agent.py").exists());
        assert!(target.join("test_agent").join("__init__.py").exists());
        assert!(target.join("test_agent").join("agent.py").exists());
        assert!(target.join("test_agent").join("mcp_adapter.py").exists());

        let skill = std::fs::read_to_string(target.join("SKILL.md")).unwrap();
        assert!(skill.contains("test-agent"));
    }

    #[test]
    fn create_agent_pipeline_tools() {
        let dir = tempfile::tempdir().unwrap();
        let output = OutputFormatter::new(true, false);
        create_scaffold(
            "pipeline-agent",
            "A pipeline agent",
            &["analyze", "execute", "report"],
            "standard",
            "economy",
            Some(dir.path().to_str().unwrap()),
            &output,
        )
        .unwrap();

        let target = dir.path().join("pipeline-agent");
        let agent_py = std::fs::read_to_string(target.join("agent.py")).unwrap();
        assert!(agent_py.contains("analyze"));
        assert!(agent_py.contains("execute"));
        assert!(agent_py.contains("report"));
    }

    #[test]
    fn reject_existing_directory() {
        let dir = tempfile::tempdir().unwrap();
        let existing = dir.path().join("duplicate");
        std::fs::create_dir_all(&existing).unwrap();

        let output = OutputFormatter::new(true, false);
        let result = create_scaffold(
            "duplicate",
            "A test",
            &["run"],
            "standard",
            "economy",
            Some(dir.path().to_str().unwrap()),
            &output,
        );
        assert!(result.is_err());
    }

    #[test]
    fn reject_path_traversal_in_name() {
        let _output = OutputFormatter::new(true, false);
        let result = commands::validate_fs_name("../../evil");
        assert!(result.is_err());
    }
}
