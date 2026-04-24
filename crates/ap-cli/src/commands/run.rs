//! `agent-nexus run <agent>` — run an agent in the specified mode.
//!
//! ## Modes
//!
//! - **mcp** (default): Replace current process with the agent's MCP server.
//!   The agent owns stdin/stdout directly, so external MCP clients can connect.
//! - **cli**: Replace current process with the agent for direct interactive use.
//!   The agent gets raw terminal I/O without pipe intermediaries.
//! - **router**: Orchestrate via PlatformRouter + MCPGateway. Requires full
//!   orchestration layer (not yet ported from Python).
//!
//! Both `mcp` and `cli` use `CommandExt::process_replace()` (Unix process
//! replacement), matching the Python CLI's `os.execvpe` behavior.

use std::os::unix::process::CommandExt;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};

use crate::commands;
use crate::commands::runtime::{resolve_entrypoint, resolve_python, resolve_venv_python};
use crate::output::OutputFormatter;

/// Run `run <agent> [--mode <mode>] [--transport <transport>] [extra...]` command.
pub fn run(
    agent: &str,
    mode: &str,
    transport: &str,
    extra: &[String],
    output: &OutputFormatter,
) -> Result<()> {
    commands::validate_fs_name(agent)
        .with_context(|| format!("Invalid agent name: {agent}"))?;

    match mode {
        "mcp" | "router" | "cli" => {}
        other => bail!("Unknown mode '{other}'. Use: mcp, router, cli."),
    }

    match transport {
        "stdio" | "sse" => {}
        other => bail!("Unknown transport '{other}'. Use: stdio, sse."),
    }

    if mode == "router" {
        bail!(
            "Router mode requires PlatformRouter + MCPGateway integration (not yet migrated). \
             Use 'mcp' mode instead: agent-nexus run {agent} --mode mcp"
        );
    }

    // Resolve agent from lockfile.
    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );
    let lockfile_path = root.join("lockfile.json");
    if !lockfile_path.exists() {
        bail!("No lockfile found at {}.", lockfile_path.display());
    }

    let lockfile_mgr = ap_fetcher::lockfile::LockfileManager::new(lockfile_path);
    let _entry = lockfile_mgr
        .get(agent)
        .context("Failed to read lockfile")?
        .with_context(|| {
            format!("Agent '{agent}' is not installed. Use 'agent-nexus install {agent}' first.")
        })?;

    let install_dir = root.join(".agents").join(agent);
    if !install_dir.exists() {
        bail!("Agent install directory not found at {}.", install_dir.display());
    }

    // Resolve entrypoint and Python interpreter.
    let (entrypoint, use_module) = resolve_entrypoint(&install_dir, agent);
    let python = resolve_venv_python(&install_dir).unwrap_or_else(resolve_python);

    let mut cmd_args: Vec<String> = if let Some(ref ep) = entrypoint {
        vec![ep.to_string_lossy().to_string()]
    } else if use_module {
        vec!["-m".to_string(), agent.replace('-', "_")]
    } else {
        bail!("Agent '{agent}' has no entrypoint (tried main.py, agent.py, mcp_adapter.py).");
    };

    // For cli mode, forward extra arguments to the agent process.
    if mode == "cli" {
        cmd_args.extend(extra.iter().cloned());
    }

    output.info(&format!("Running agent '{agent}' in {mode} mode..."));

    // Replace the current CLI process with the agent process.
    // This gives the agent direct stdin/stdout ownership — same as Python's os.execvpe.
    let mut cmd = std::process::Command::new(&python);
    cmd.args(&cmd_args);
    cmd.env("AGENT_MODE", mode);
    cmd.env("AGENT_NAME", agent);

    let err = cmd.exec();
    // .exec() only returns on error — if we reach here, it failed.
    bail!("Failed to replace process with agent: {err}");
}
