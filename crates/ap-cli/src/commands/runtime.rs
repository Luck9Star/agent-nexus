//! `agent-nexus runtime` — runtime commands (placeholder).

use anyhow::Result;

use crate::output::OutputFormatter;

/// Run `runtime exec <agent>` command.
///
/// Placeholder — full implementation requires the Platform Router.
pub fn run_exec(agent: &str, args: &[String], output: &OutputFormatter) -> Result<()> {
    let args_str = args.join(" ");
    output.info(&format!(
        "[placeholder] Execute agent '{}' with args: '{}' — requires PlatformRouter (not yet implemented)",
        agent, args_str
    ));
    Ok(())
}
