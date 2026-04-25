//! `agent-nexus run <agent>` — run an agent in the specified mode.
//!
//! ## Modes
//!
//! - **mcp** (default): Replace current process with the agent's MCP server.
//!   The agent owns stdin/stdout directly, so external MCP clients can connect.
//! - **cli**: Replace current process with the agent for direct interactive use.
//!   The agent gets raw terminal I/O without pipe intermediaries.
//! - **router**: Orchestrate via PlatformRouter. Spawns the agent as a managed
//!   process, routes a chat message through the 4-phase composite workflow,
//!   then shuts down. Use `--message` to provide the chat input.
//!
//! Both `mcp` and `cli` use `CommandExt::process_replace()` (Unix process
//! replacement), matching the Python CLI's `os.execvpe` behavior.

use std::collections::HashMap;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};

use crate::commands;
use crate::commands::runtime::{resolve_entrypoint, resolve_python, resolve_venv_python};
use crate::output::OutputFormatter;

/// Run `run <agent> [--mode <mode>] [--transport <transport>] [--message <msg>] [extra...]` command.
pub fn run(
    agent: &str,
    mode: &str,
    transport: &str,
    message: Option<&str>,
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
        let msg = match message {
            Some(m) => m.to_string(),
            None => read_message_from_stdin()?,
        };
        return run_router_mode(agent, &msg, output);
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
    cmd.env("MCP_TRANSPORT", transport);
    cmd.env("AGENT_NEXUS_ROOT", root.to_string_lossy().to_string());
    cmd.env("AGENT_DIR", install_dir.to_string_lossy().to_string());

    let err = cmd.exec();
    // .exec() only returns on error — if we reach here, it failed.
    bail!("Failed to replace process with agent: {err}");
}

/// Read a chat message from stdin (piped input).
fn read_message_from_stdin() -> Result<String> {
    use std::io::{self, Read};
    let mut buf = String::new();
    io::stdin().read_to_string(&mut buf)?;
    let msg = buf.trim().to_string();
    if msg.is_empty() {
        bail!("No message provided. Use --message <msg> or pipe input to stdin.");
    }
    Ok(msg)
}

/// Router mode: spawn the agent as a managed process, route a chat message
/// through the PlatformRouter, then shut down.
fn run_router_mode(agent: &str, message: &str, output: &OutputFormatter) -> Result<()> {
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

    let cmd_args: Vec<String> = if let Some(ref ep) = entrypoint {
        vec![ep.to_string_lossy().to_string()]
    } else if use_module {
        vec!["-m".to_string(), agent.replace('-', "_")]
    } else {
        bail!("Agent '{agent}' has no entrypoint (tried main.py, agent.py, mcp_adapter.py).");
    };

    output.info(&format!("Starting agent '{agent}' in router mode..."));

    // Run the async router orchestration in a tokio runtime.
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async {
        use ap_core::orchestration::{
            PlatformRouter, ProcessManager, SubtaskConfig,
        };

        // 1. Create ProcessManager and spawn the agent.
        let pm = ProcessManager::new();
        let handle = ap_core::orchestration::ProcessManagerHandle::new(pm);

        let mut env = HashMap::new();
        env.insert("AGENT_MODE".to_string(), "router".to_string());
        env.insert("AGENT_NAME".to_string(), agent.to_string());
        env.insert("MCP_TRANSPORT".to_string(), "stdio".to_string());
        env.insert("AGENT_NEXUS_ROOT".to_string(), root.to_string_lossy().to_string());
        env.insert("AGENT_DIR".to_string(), install_dir.to_string_lossy().to_string());

        let args_str: Vec<&str> = cmd_args.iter().map(|s| s.as_str()).collect();
        handle
            .spawn(agent, &python, &args_str, Some(env))
            .await
            .context("Failed to spawn agent process")?;

        // 2. Create PlatformRouter.
        let mut router = PlatformRouter::new(handle.clone_handle(), SubtaskConfig::default());

        // Check if this agent is a composite — look for a composite definition
        // in the install dir (e.g., composite.toml or workflow.toml).
        let composite_path = install_dir.join("composite.toml");
        if composite_path.exists() {
            let def = load_composite_definition(&composite_path)?;
            router.register_composite(agent.to_string(), def);
        }
        // 3. Route the chat message + output results.
        let chat_result: Result<(), anyhow::Error> = async {
            let conversation_id = uuid_or_fallback();
            let result = router
                .route_chat(agent, message, &conversation_id)
                .await
                .context("Router failed")?;

            if result.success {
                output.info("Router workflow completed successfully.");
                println!("{}", result.final_output);
            } else {
                output.error(&format!(
                    "Router workflow failed: {}",
                    result.error.as_deref().unwrap_or("unknown error")
                ));
            }

            if result.total_phases > 1 {
                eprintln!(
                    "\nPhase summary: {}/{} completed",
                    result.completed_phases, result.total_phases
                );
                for phase in ap_core::orchestration::WorkflowPhase::ordered() {
                    if let Some(pr) = result.phase_results.get(&phase) {
                        let status = if pr.success { "OK" } else { "FAIL" };
                        eprintln!("  {} [{}]", phase, status);
                    }
                }
            }
            Ok(())
        }.await;

        // 4. Always shut down the agent process — even if routing failed.
        if let Err(e) = handle
            .graceful_shutdown_all(std::time::Duration::from_secs(5))
            .await
        {
            tracing::warn!("Graceful shutdown failed: {e}, force-killing");
            let _ = handle.kill_all().await;
        }

        chat_result // propagate the original error
    })
}

/// Load a composite agent definition from a TOML file.
fn load_composite_definition(
    path: &std::path::Path,
) -> Result<ap_core::orchestration::CompositeDefinition> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read {}", path.display()))?;
    let value: toml::Value = toml::from_str(&content)
        .with_context(|| format!("Failed to parse {}", path.display()))?;

    use ap_core::orchestration::WorkflowPhase;

    let mut phase_agents = HashMap::new();
    let table = value
        .get("phases")
        .and_then(|v| v.as_table())
        .ok_or_else(|| anyhow::anyhow!("Missing [phases] section in {}", path.display()))?;

    for phase in WorkflowPhase::ordered() {
        let key = phase.to_string();
        if let Some(agents) = table.get(&key).and_then(|v| v.as_array()) {
            let names: Vec<String> = agents
                .iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect();
            if !names.is_empty() {
                phase_agents.insert(phase, names);
            }
        }
    }

    Ok(ap_core::orchestration::CompositeDefinition { phase_agents })
}

/// Generate a conversation ID (UUID or random fallback).
fn uuid_or_fallback() -> String {
    // Try uuid crate first, fallback to timestamp-based.
    use std::time::{SystemTime, UNIX_EPOCH};
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("conv-{ts:x}")
}
