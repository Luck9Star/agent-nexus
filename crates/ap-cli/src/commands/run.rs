//! `agent-nexus run <agent>` — run an agent in the specified mode.

use anyhow::{bail, Result};

use crate::output::OutputFormatter;

/// Run `run <agent> [--mode <mode>] [--transport <transport>] [extra...]` command.
///
/// Supports three modes matching the Python CLI:
/// - `mcp` (default): MCP stdio standalone mode
/// - `router`: Platform Router mode via MCP Gateway
/// - `cli`: CLI standalone mode with forwarded arguments
///
/// Full implementation requires `PlatformRouter` integration which is not yet
/// migrated from the Python codebase. Currently returns a descriptive error.
pub fn run(
    agent: &str,
    mode: &str,
    transport: &str,
    extra: &[String],
    _output: &OutputFormatter,
) -> Result<()> {
    let _ = (agent, extra);

    match mode {
        "mcp" | "router" | "cli" => {}
        other => {
            bail!("Unknown mode '{other}'. Use: mcp, router, cli.");
        }
    }

    match transport {
        "stdio" | "sse" => {}
        other => {
            bail!("Unknown transport '{other}'. Use: stdio, sse.");
        }
    }

    bail!(
        "Agent execution requires PlatformRouter integration (not yet migrated from Python). \
         Mode: {mode}, Transport: {transport}. \
         Use the Python CLI for full agent execution: agent-nexus run {agent} --mode {mode}"
    );
}
