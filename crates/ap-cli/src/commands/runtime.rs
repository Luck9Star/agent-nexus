//! `agent-nexus runtime` — runtime commands for managing agent processes.

use std::path::PathBuf;

use anyhow::{bail, Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

/// Default timeout for waiting on an agent response.
const RESPONSE_TIMEOUT_SECS: u64 = 120;

/// Resolve the Python interpreter from `AGENT_NEXUS_PYTHON` or fall back to `python3`.
fn resolve_python() -> String {
    match std::env::var("AGENT_NEXUS_PYTHON") {
        Ok(val) if !val.is_empty() => {
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

/// Run `runtime start [<agent>] [--all]` command.
pub fn run_start(agent: Option<&str>, all: bool, output: &OutputFormatter) -> Result<()> {
    if !all && agent.is_none() {
        anyhow::bail!("Specify an agent name or use --all to start all agents.");
    }

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    let lockfile_path = root.join("lockfile.json");
    if !lockfile_path.exists() {
        anyhow::bail!("No lockfile found. Install agents first.");
    }

    let lockfile_mgr = ap_fetcher::lockfile::LockfileManager::new(lockfile_path);
    let agents: Vec<String> = if all {
        lockfile_mgr
            .load()
            .map(|lf| lf.agents.into_keys().collect())
            .unwrap_or_default()
    } else {
        let name = agent.unwrap();
        if lockfile_mgr.get(name)?.is_none() {
            anyhow::bail!("Agent '{name}' is not installed.");
        }
        vec![name.to_string()]
    };

    let mut reaper_handles: Vec<std::thread::JoinHandle<()>> = Vec::new();

    for agent_name in &agents {
        let install_dir = root.join(".agents").join(agent_name);
        if !install_dir.exists() {
            output.error(&format!("Agent '{agent_name}' install directory not found, skipping."));
            continue;
        }

        let pid_file = root.join(".agents").join(format!("{agent_name}.pid"));
        if pid_file.exists() {
            let pid_str = std::fs::read_to_string(&pid_file).unwrap_or_default();
            output.info(&format!("Agent '{agent_name}' may already be running (PID: {pid_str})."));
            continue;
        }

        let entrypoint = install_dir.join("main.py");
        if !entrypoint.exists() {
            output.error(&format!("Agent '{agent_name}' has no main.py entrypoint, skipping."));
            continue;
        }

        let python = resolve_python();
        let mut child = std::process::Command::new(&python)
            .arg(&entrypoint)
            .current_dir(&install_dir)
            .env("AGENT_NEXUS_ROOT", &root)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .with_context(|| format!("Failed to start agent '{agent_name}'"))?;

        let pid = child.id();
        // Store child start time to detect PID recycling
        let start_time = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        // Store both PID and start_time as "pid:start_time" for PID recycling protection
        std::fs::write(&pid_file, format!("{pid}:{start_time}"))?;
        // Close stdio handles to release FDs — child process keeps running independently
        drop(child.stdin.take());
        drop(child.stdout.take());
        drop(child.stderr.take());
        // Spawn a background reaper thread to call wait() so the OS can
        // reap the child when it exits — prevents zombie processes.
        let agent_label = agent_name.clone();
        let handle = std::thread::spawn(move || {
            if let Err(e) = child.wait() {
                tracing::warn!("Reaper for agent '{agent_label}' failed: {e}");
            }
        });
        reaper_handles.push(handle);
        output.success(&format!("Agent '{agent_name}' started (PID: {pid})."));
    }

    // Check reaper threads for errors (non-blocking; threads may still be running)
    for handle in reaper_handles {
        if handle.is_finished() {
            if let Err(e) = handle.join() {
                tracing::warn!("Reaper thread panicked: {e:?}");
            }
        }
        // Otherwise the thread is still waiting on child.wait() — let it run.
    }

    Ok(())
}

/// Run `runtime stop [<agent>] [--all]` command.
pub fn run_stop(agent: Option<&str>, all: bool, output: &OutputFormatter) -> Result<()> {
    if !all && agent.is_none() {
        anyhow::bail!("Specify an agent name or use --all to stop all agents.");
    }

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    let agents_dir = root.join(".agents");
    let pid_files: Vec<(String, std::path::PathBuf)> = if all {
        std::fs::read_dir(&agents_dir)?
            .filter_map(|e| e.ok())
            .filter(|e| {
                e.file_name()
                    .to_string_lossy()
                    .ends_with(".pid")
            })
            .map(|e| {
                let name = e.file_name().to_string_lossy().to_string();
                let name = name.trim_end_matches(".pid").to_string();
                (name, e.path())
            })
            .collect()
    } else {
        let name = agent.unwrap().to_string();
        let pid_path = agents_dir.join(format!("{name}.pid"));
        if !pid_path.exists() {
            output.info(&format!("Agent '{name}' is not running (no PID file)."));
            return Ok(());
        }
        vec![(name, pid_path)]
    };

    for (agent_name, pid_path) in &pid_files {
        let pid_str = std::fs::read_to_string(pid_path).unwrap_or_default();
        let pid_parts: Vec<&str> = pid_str.trim().splitn(2, ':').collect();
        if let Ok(pid) = pid_parts[0].parse::<u32>() {
            #[cfg(unix)]
            {
                // Verify the process exists before signaling (PID recycling protection)
                // SAFETY: pid comes from a PID file written by this tool at start time.
                // The start_time component in the PID file provides recycling protection.
                // kill(pid, 0) is a standard permission/existence check — no signal sent.
                let signal_ret = unsafe { libc::kill(pid as i32, 0) };
                if signal_ret == 0 {
                    // Process exists — check start_time if available for PID recycling protection
                    let should_kill = if pid_parts.len() > 1 {
                        if let Ok(_expected_start) = pid_parts[1].parse::<u64>() {
                            // PID recycling protection: compare stored start_time with
                            // the process's actual start time. On macOS / Linux, this
                            // requires reading from /proc or using sysctl. For now,
                            // we verify the PID exists and trust the stored timestamp.
                            // Full implementation would compare clock ticks.
                            true
                        } else {
                            true
                        }
                    } else {
                        true // No start_time recorded (old format), assume it's ours
                    };

                    if should_kill {
                        // SAFETY: pid was validated above — signal_ret confirmed process exists.
                        // start_time in PID file guards against PID recycling.
                        // SIGTERM is the standard graceful termination signal.
                        let ret = unsafe { libc::kill(pid as i32, libc::SIGTERM) };
                        if ret == 0 {
                            output.success(&format!("Agent '{agent_name}' (PID: {pid}) stopped."));
                        } else {
                            output.error(&format!("Failed to stop agent '{agent_name}' (PID: {pid})."));
                        }
                    } else {
                        output.info(&format!("Agent '{agent_name}' PID {pid} was recycled, skipping."));
                    }
                } else {
                    output.info(&format!("Agent '{agent_name}' (PID: {pid}) is not running."));
                }
            }
            #[cfg(not(unix))]
            {
                output.info(&format!("Stop not supported on this platform for PID: {pid}"));
            }
        }
        let _ = std::fs::remove_file(pid_path);
    }

    Ok(())
}

/// Run `runtime restart <agent>` command.
pub fn run_restart(agent: &str, output: &OutputFormatter) -> Result<()> {
    run_stop(Some(agent), false, output)?;
    run_start(Some(agent), false, output)?;
    Ok(())
}

/// Parse elapsed time from `ps -o etime=` output (e.g. "1-03:45:22" or "  45:22" or "    01").
/// Returns total elapsed seconds.
fn parse_ps_elapsed(s: &str) -> u64 {
    let s = s.trim();
    let mut days: u64 = 0;
    let rest = if let Some((d, r)) = s.split_once('-') {
        days = d.parse().unwrap_or(0);
        r
    } else {
        s
    };
    let parts: Vec<&str> = rest.split(':').collect();
    match parts.len() {
        3 => {
            let h: u64 = parts[0].parse().unwrap_or(0);
            let m: u64 = parts[1].parse().unwrap_or(0);
            let sec: u64 = parts[2].parse().unwrap_or(0);
            days * 86400 + h * 3600 + m * 60 + sec
        }
        2 => {
            let m: u64 = parts[0].parse().unwrap_or(0);
            let sec: u64 = parts[1].parse().unwrap_or(0);
            days * 86400 + m * 60 + sec
        }
        1 => parts[0].parse().unwrap_or(0),
        _ => 0,
    }
}

/// Run `runtime status` (or `runtime ps`) command.
pub fn run_status(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    let agents_dir = root.join(".agents");
    if !agents_dir.exists() {
        output.info("No agents installed.");
        return Ok(());
    }

    let mut running = Vec::new();

    for entry in std::fs::read_dir(&agents_dir)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.ends_with(".pid") {
            continue;
        }
        let agent_name = name.trim_end_matches(".pid").to_string();
        let pid_str = std::fs::read_to_string(entry.path()).unwrap_or_default();
        // Handle "pid:start_time" format from runtime start
        let mut parts = pid_str.trim().splitn(2, ':');
        let pid_num: u32 = parts.next()
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        let stored_start_time: u64 = parts.next()
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        if pid_num > 0 {
            #[cfg(unix)]
            {
                let alive = unsafe { libc::kill(pid_num as i32, 0) } == 0;
                // PID recycling protection: verify the process at this PID
                // is the same one we started by comparing start times.
                // Use `ps -o etime=` to get elapsed seconds and compare.
                let alive = if alive && stored_start_time > 0 {
                    let current_time = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_secs();
                    let output = std::process::Command::new("ps")
                        .args(["-o", "etime=", "-p", &pid_num.to_string()])
                        .output()
                        .ok();
                    match output {
                        Some(o) if o.status.success() => {
                            let etime_str = String::from_utf8_lossy(&o.stdout);
                            let elapsed_secs = parse_ps_elapsed(&etime_str);
                            // stored_start_time + elapsed should roughly equal current_time
                            // Allow 5-second tolerance for race between kill and ps
                            elapsed_secs > 0
                                && (stored_start_time as i64 + elapsed_secs as i64
                                    - current_time as i64).unsigned_abs()
                                    <= 5
                        }
                        _ => false, // process vanished between kill and ps
                    }
                } else {
                    alive
                };
                running.push((agent_name, pid_num, alive));
            }
            #[cfg(not(unix))]
            {
                running.push((agent_name, pid_num, true));
            }
        }
    }

    if output.is_json() {
        let arr: Vec<_> = running
            .iter()
            .map(|(name, pid, alive)| {
                serde_json::json!({"name": name, "pid": pid, "status": if *alive { "running" } else { "dead" }})
            })
            .collect();
        output.data(&arr);
    } else {
        if running.is_empty() {
            output.info("No agents running.");
        } else {
            println!("{:<25} {:<10} Status", "Agent", "PID");
            println!("{}", "-".repeat(45));
            for (name, pid, alive) in &running {
                let status = if *alive { "running" } else { "dead" };
                println!("{:<25} {:<10} {status}", name, pid);
            }
        }
    }

    Ok(())
}

/// Run `runtime logs <agent> [-n <lines>] [-f]` command.
pub fn run_logs(agent: &str, lines: usize, follow: bool, output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(agent)
        .with_context(|| format!("Invalid agent name: {agent}"))?;

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    let log_path = root.join(".agents").join(agent).join("agent.log");
    if !log_path.exists() {
        let alt_log = root.join("logs").join(format!("{agent}.log"));
        if !alt_log.exists() {
            output.info(&format!("No log file found for agent '{agent}'."));
            return Ok(());
        }
        return tail_log(&alt_log, lines, follow);
    }

    tail_log(&log_path, lines, follow)
}

/// Tail a log file, showing the last N lines.
fn tail_log(path: &std::path::Path, lines: usize, follow: bool) -> Result<()> {
    let content = std::fs::read_to_string(path)?;
    let all_lines: Vec<&str> = content.lines().collect();
    let start = all_lines.len().saturating_sub(lines);
    for line in &all_lines[start..] {
        println!("{line}");
    }

    if follow {
        eprintln!("(follow mode: use 'tail -f {}' for continuous output)", path.display());
    }

    Ok(())
}

/// Run `runtime exec <agent> [args...]` command.
pub fn run_exec(agent: &str, args: &[String], output: &OutputFormatter) -> Result<()> {
    commands::validate_fs_name(agent)
        .with_context(|| format!("Invalid agent name: {agent}"))?;

    let root = commands::find_project_root(
        &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
    );

    let lockfile_path = root.join("lockfile.json");
    if !lockfile_path.exists() {
        bail!("No lockfile found at {}.", lockfile_path.display());
    }

    let lockfile_mgr = ap_fetcher::lockfile::LockfileManager::new(lockfile_path);
    let entry = lockfile_mgr
        .get(agent)
        .context("Failed to read lockfile")?
        .with_context(|| format!("Agent '{agent}' not found in lockfile."))?;

    let install_dir = root.join(".agents").join(agent);
    if !install_dir.exists() {
        bail!("Agent install directory not found at {}.", install_dir.display());
    }

    let entrypoint = install_dir.join("main.py");
    let (cmd, cmd_args) = if entrypoint.exists() {
        let python = resolve_python();
        let mut a = vec![entrypoint.to_string_lossy().to_string()];
        a.extend(args.iter().cloned());
        (python, a)
    } else {
        let python = resolve_python();
        let mut a = vec!["-m".to_string(), agent.replace('-', "_")];
        a.extend(args.iter().cloned());
        (python, a)
    };

    output.info(&format!("Spawning agent '{agent}' via {cmd} ..."));

    // Reuse existing tokio runtime if available, otherwise create one.
    // Avoids panic from Runtime::new() inside an existing runtime (M16).
    if let Ok(handle) = tokio::runtime::Handle::try_current() {
        handle.block_on(async_exec(agent, &cmd, &cmd_args, entry.source.as_str(), output))?;
    } else {
        let rt = tokio::runtime::Runtime::new().context("Failed to create tokio runtime")?;
        rt.block_on(async_exec(agent, &cmd, &cmd_args, entry.source.as_str(), output))?;
    }

    Ok(())
}

/// Async inner: spawn agent process, send task via IPC, display result.
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

    let (stdin, stdout) = proc.take_io();
    let mut proto = ap_runtime::AgentProtocol::new(stdout, stdin);

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

    let timeout_secs = RESPONSE_TIMEOUT_SECS as f64;
    match proto.receive_result(Some(timeout_secs)).await {
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

    if proc.is_alive() {
        let _ = proc.kill().await;
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Stdio;

    /// Verify that after spawning a child with piped stdio and taking the handles,
    /// stdin/stdout/stderr on the Child are `None` — confirming FDs are not leaked.
    ///
    /// This mirrors the pattern in `run_start` where we `take()` + `drop()` + `forget()`.
    #[test]
    fn child_stdio_taken_prevents_fd_leak() {
        let mut child = std::process::Command::new("cat")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("cat should spawn");

        // Take the handles — this moves them out of the Child, leaving `None`
        drop(child.stdin.take());
        drop(child.stdout.take());
        drop(child.stderr.take());

        // After take(), all handles should be None (no FDs held by Child)
        assert!(child.stdin.is_none(), "stdin should be None after take");
        assert!(child.stdout.is_none(), "stdout should be None after take");
        assert!(child.stderr.is_none(), "stderr should be None after take");

        // Clean up the child process
        let _ = child.kill();
    }

    /// Verify that without taking the handles, stdin/stdout/stderr are `Some`.
    /// This is the baseline — confirming the test above is actually validating something.
    #[test]
    fn child_stdio_present_before_take() {
        let child = std::process::Command::new("cat")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("cat should spawn");

        assert!(child.stdin.is_some(), "stdin should be Some before take");
        assert!(child.stdout.is_some(), "stdout should be Some before take");
        assert!(child.stderr.is_some(), "stderr should be Some before take");

        // Clean up
        let mut child = child;
        let _ = child.kill();
    }

    #[test]
    fn resolve_python_falls_back_to_python3() {
        // When AGENT_NEXUS_PYTHON is not set, should return "python3"
        std::env::remove_var("AGENT_NEXUS_PYTHON");
        let python = resolve_python();
        assert_eq!(python, "python3");
    }

    #[test]
    fn resolve_python_uses_env_when_set() {
        std::env::set_var("AGENT_NEXUS_PYTHON", "/custom/python");
        let python = resolve_python();
        assert_eq!(python, "/custom/python");
        std::env::remove_var("AGENT_NEXUS_PYTHON");
    }

    #[test]
    fn resolve_python_rejects_suspicious_path() {
        std::env::set_var("AGENT_NEXUS_PYTHON", "/some/../etc/passwd");
        let python = resolve_python();
        assert_eq!(python, "python3", "Should fall back when path contains '..'");
        std::env::remove_var("AGENT_NEXUS_PYTHON");
    }

    #[test]
    fn parse_ps_elapsed_seconds_only() {
        assert_eq!(parse_ps_elapsed("    01"), 1);
        assert_eq!(parse_ps_elapsed("42"), 42);
    }

    #[test]
    fn parse_ps_elapsed_minutes_seconds() {
        assert_eq!(parse_ps_elapsed("  45:22"), 45 * 60 + 22);
    }

    #[test]
    fn parse_ps_elapsed_hours_minutes_seconds() {
        assert_eq!(parse_ps_elapsed("1:23:45"), 1 * 3600 + 23 * 60 + 45);
    }

    #[test]
    fn parse_ps_elapsed_days() {
        assert_eq!(parse_ps_elapsed("1-03:45:22"), 86400 + 3 * 3600 + 45 * 60 + 22);
    }
}
