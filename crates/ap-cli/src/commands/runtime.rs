//! `agent-nexus runtime` — runtime commands for executing agents via subprocess + IPC.

use std::path::PathBuf;
use std::time::Duration;

use anyhow::{bail, Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

/// Default timeout for waiting on an agent response.
const RESPONSE_TIMEOUT_SECS: u64 = 120;

/// Resolve the Python interpreter from `AGENT_NEXUS_PYTHON` or fall back to `python3`.
///
/// Validates that the value is a bare command name or an absolute path
/// (rejects relative paths containing `..` or directory traversal).
fn resolve_python() -> String {
    match std::env::var("AGENT_NEXUS_PYTHON") {
        Ok(val) if !val.is_empty() => {
            // Reject values with path traversal
            if val.contains("..") || val.contains('\0') {
                eprintln!(
                    "Warning: AGENT_NEXUS_PYTHON contains suspicious path '{val}', falling back to python3"
                );
                "python3".to_string()
            } else {
                val
            }
        }
        _ => "python3".to_string(),
    }
}

/// Run `runtime exec <agent>` command.
///
/// Looks up the agent in the lockfile, spawns it as a subprocess via
/// `AgentProcess`, communicates via IPC (JSON-lines), and displays the result.
pub fn run_exec(agent: &str, args: &[String], output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(agent)
        .with_context(|| format!("Invalid agent name: {agent}"))?;

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    // 1. Look up the agent in the lockfile
    let lockfile_path = root.join("lockfile.json");
    if !lockfile_path.exists() {
        bail!(
            "No lockfile found at {}. Install an agent first with `agent-nexus install <agent>`.",
            lockfile_path.display()
        );
    }

    let lockfile_mgr = ap_fetcher::lockfile::LockfileManager::new(lockfile_path);
    let entry = lockfile_mgr
        .get(agent)
        .context("Failed to read lockfile")?
        .with_context(|| {
            format!(
                "Agent '{agent}' not found in lockfile. Install it first with `agent-nexus install {agent}`."
            )
        })?;

    // 2. Find the agent's install directory
    let install_dir = root.join(".agents").join(agent);
    if !install_dir.exists() {
        bail!(
            "Agent install directory not found at {}. Try reinstalling with `agent-nexus install {}`.",
            install_dir.display(),
            agent
        );
    }

    // 3. Determine the command to run.
    //    Look for a `main.py` entrypoint; fall back to `python -m <agent>`.
    let entrypoint = install_dir.join("main.py");
    let (cmd, cmd_args) = if entrypoint.exists() {
        let python = resolve_python();
        let mut a = vec![entrypoint.to_string_lossy().to_string()];
        a.extend(args.iter().cloned());
        (python, a)
    } else {
        // Fall back: try running the agent module directly
        let python = resolve_python();
        let mut a = vec!["-m".to_string(), agent.replace('-', "_")];
        a.extend(args.iter().cloned());
        (python, a)
    };

    output.info(&format!("Spawning agent '{agent}' via {cmd} ..."));
    if !args.is_empty() {
        output.info(&format!("  Args: {}", args.join(" ")));
    }

    // 4. Spawn and communicate via tokio runtime
    let rt = tokio::runtime::Runtime::new().context("Failed to create tokio runtime")?;
    rt.block_on(async_exec(agent, &cmd, &cmd_args, entry.source.as_str(), output))?;

    Ok(())
}

/// Async inner: spawn `AgentProcess`, send task via IPC, display result.
async fn async_exec(
    agent_id: &str,
    cmd: &str,
    args: &[String],
    _source: &str,
    output: &OutputFormatter,
) -> Result<()> {
    let arg_refs: Vec<&str> = args.iter().map(std::string::String::as_str).collect();

    let mut proc = ap_runtime::AgentProcess::spawn(agent_id, cmd, &arg_refs)
        .await
        .with_context(|| format!("Failed to spawn agent process: {} {}", cmd, args.join(" ")))?;

    output.info(&format!("Agent process started (id={})", proc.id()));

    // Take I/O handles for IPC communication
    let (stdin, stdout) = proc.take_io();

    // Create IPC protocol layer
    let mut proto = ap_runtime::AgentProtocol::new(stdout, stdin);

    // Generate a task ID
    let task_id = format!("cli-{}-{}", std::process::id(), chrono::Utc::now().timestamp_millis());

    let task_content = if args.len() == 1 {
        args[0].clone()
    } else {
        args.join(" ")
    };
    proto
        .send_task(&task_content, &task_id)
        .await
        .context("Failed to send task to agent via IPC")?;

    output.info(&format!("Task sent (id={task_id}), waiting for response..."));

    // Wait for response with timeout
    let timeout = Duration::from_secs(RESPONSE_TIMEOUT_SECS);
    match proto.receive_result(Some(timeout)).await {
        Ok(result) => {
            if result.success {
                output.success("Agent completed successfully:");
                output.info(&result.content);
            } else {
                output.error(&format!("Agent returned failure: {}", result.content));
            }
        }
        Err(e) => {
            output.error(&format!("Agent communication error: {e}"));
        }
    }

    // Clean up: kill the process
    if proc.is_alive() {
        let _ = proc.kill().await;
    }

    Ok(())
}
