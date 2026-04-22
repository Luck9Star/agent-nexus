//! `agent-nexus run <agent> <task>` — run an agent with a task (placeholder).

use anyhow::Result;

use crate::output::OutputFormatter;

/// Run `run <agent> [task...]` command.
///
/// This is a placeholder — full implementation requires PlatformRouter which
/// is not yet a separate crate. For now, prints the agent name and task.
pub fn run(agent: &str, task: &[String], model: Option<&str>, output: &OutputFormatter) -> Result<()> {
    let task_str = task.join(" ");
    let model_info = model.unwrap_or("default");

    output.info(&format!(
        "Run agent: {} with task: '{}' (model: {})",
        agent, task_str, model_info
    ));

    output.info("[placeholder] Full agent execution requires PlatformRouter (not yet implemented)");
    Ok(())
}
