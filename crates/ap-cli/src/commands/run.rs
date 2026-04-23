//! `agent-nexus run <agent> <task>` — run an agent with a task (placeholder).

use anyhow::{bail, Result};

use crate::output::OutputFormatter;

/// Run `run <agent> [task...]` command.
///
/// This is a placeholder — full implementation requires `PlatformRouter` which
/// is not yet migrated from the Python codebase.
pub fn run(agent: &str, task: &[String], model: Option<&str>, _output: &OutputFormatter) -> Result<()> {
    let _ = (agent, task, model);
    bail!(
        "Agent execution requires PlatformRouter integration (not yet migrated from Python). \
         Use `agent-nexus run <agent> --mode local` for local execution via the Python runtime."
    );
}
