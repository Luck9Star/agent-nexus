//! `agent-nexus runtime` — runtime commands for managing agent processes.
//!
//! ## Design Decision: Sync Process Management
//!
//! This module uses `std::process::Command` instead of `ap_runtime::AgentProcess` (which wraps
//! `tokio::process::Command`). This is intentional: ap-cli is a synchronous CLI tool and avoids
//! pulling in the tokio async runtime overhead for process lifecycle management. The trade-off is
//! two independent process-management codepaths, but ap-cli's use case (fire-and-forget daemon
//! spawning with PID-file tracking) doesn't benefit from tokio's async process model.

use std::path::PathBuf;

use anyhow::{bail, Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

/// Default timeout for waiting on an agent response.
const RESPONSE_TIMEOUT_SECS: u64 = 120;

/// Maximum PID value on Linux (PID_MAX_LIMIT = 4194304).
/// PIDs exceeding this are invalid and should not be signaled.
const PID_MAX_LIMIT: i32 = 4_194_304;

/// Validate a PID is within the acceptable range for signaling.
/// Returns `true` if the PID is valid and safe to use with `libc::kill`.
fn is_valid_pid(pid: i32) -> bool {
    pid > 0 && pid <= PID_MAX_LIMIT
}

/// Resolve the Python interpreter from `AGENT_NEXUS_PYTHON` or fall back to `python3`.
pub(super) fn resolve_python() -> String {
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

/// Check if the agent has a local venv with a python binary.
/// Looks in `<install_dir>/.venv/bin/python3`.
pub(super) fn resolve_venv_python(install_dir: &std::path::Path) -> Option<String> {
    let venv_python = install_dir.join(".venv").join("bin").join("python3");
    if venv_python.exists() {
        Some(venv_python.to_string_lossy().to_string())
    } else {
        None
    }
}

/// Resolve the agent entrypoint by checking multiple known files.
/// Returns `(Some(path), false)` if a direct script is found,
/// or `(None, true)` if python -m fallback should be used.
pub(super) fn resolve_entrypoint(install_dir: &std::path::Path, agent_name: &str) -> (Option<PathBuf>, bool) {
    let candidates = ["main.py", "agent.py"];
    for name in &candidates {
        let path = install_dir.join(name);
        if path.exists() {
            return (Some(path), false);
        }
    }
    // Check for mcp_adapter.py inside the package directory
    let module_dir = install_dir.join(agent_name.replace('-', "_"));
    if module_dir.join("mcp_adapter.py").exists() || module_dir.join("__init__.py").exists() {
        return (None, true); // use python -m fallback
    }
    (None, false) // no entrypoint found
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
            .load().map_or_else(|e| {
                output.info(&format!("Note: could not read lockfile: {e}"));
                Vec::new()
            }, |lf| lf.agents.into_keys().collect())
    } else {
        let name = agent.ok_or_else(|| anyhow::anyhow!("Agent name required when --all is not set"))?;
        if lockfile_mgr.get(name)?.is_none() {
            anyhow::bail!("Agent '{name}' is not installed.");
        }
        vec![name.to_string()]
    };

    // Assign SSE ports from a base range. Scan existing .port files to find
    // the next available port. This ensures `--all` doesn't collide.
    let base_port: u16 = 9100;
    let agents_dir = root.join(".agents");
    let used_ports: std::collections::HashSet<u16> = std::fs::read_dir(&agents_dir)
        .ok()
        .map(|entries| {
            entries
                .filter_map(std::result::Result::ok)
                .filter(|e| {
                    e.file_name().to_string_lossy().ends_with(".port")
                })
                .filter_map(|e| std::fs::read_to_string(e.path()).ok())
                .filter_map(|s| s.trim().parse::<u16>().ok())
                .collect()
        })
        .unwrap_or_default();

    let mut reaper_handles: Vec<std::thread::JoinHandle<()>> = Vec::new();
    let mut next_port = base_port;

    for agent_name in &agents {
        let install_dir = root.join(".agents").join(agent_name);
        if !install_dir.exists() {
            output.error(&format!("Agent '{agent_name}' install directory not found, skipping."));
            continue;
        }

        let pid_file = root.join(".agents").join(format!("{agent_name}.pid"));
        if pid_file.exists() {
            // Check if the PID is actually alive — stale PID files from unclean
            // shutdown should not prevent restarts.
            let pid_str = std::fs::read_to_string(&pid_file).unwrap_or_default();
            let is_alive = pid_str.split(':').next()
                .and_then(|p| p.trim().parse::<i32>().ok())
                .filter(|&pid| is_valid_pid(pid))
                .is_some_and(|pid| {
                    // Signal 0 doesn't kill the process — just checks existence.
                    // Returns 0 if the process exists, -1 with ESRCH otherwise.
                    // SAFETY: pid is validated > 0 and <= PID_MAX_LIMIT.
                    unsafe { libc::kill(pid, 0) == 0 }
                });

            if is_alive {
                // Process is still running — skip to avoid duplicate start.
                output.info(&format!("Agent '{agent_name}' is already running (PID: {pid_str})."));
                continue;
            }
            // Process is dead — clean stale PID/port files and restart.
            {
                let port_file = root.join(".agents").join(format!("{agent_name}.port"));
                let _ = std::fs::remove_file(&pid_file);
                let _ = std::fs::remove_file(&port_file);
                output.info(&format!("Cleaned stale PID file for '{agent_name}' (process no longer running)."));
                // Continue to restart the agent below.
            }
        }

        let (entrypoint, use_module) = resolve_entrypoint(&install_dir, agent_name);
        if entrypoint.is_none() && !use_module {
            output.error(&format!(
                "Agent '{agent_name}' has no entrypoint (tried main.py, agent.py, mcp_adapter.py), skipping."
            ));
            continue;
        }

        // Resolve python: prefer venv python if available, otherwise system python3
        let python = resolve_venv_python(&install_dir).unwrap_or_else(resolve_python);

        // Build command args from entrypoint or module fallback.
        let cmd_args: Vec<String> = if let Some(ref ep) = entrypoint {
            vec![ep.to_string_lossy().to_string()]
        } else {
            vec!["-m".to_string(), agent_name.replace('-', "_")]
        };

        // Assign a port for SSE transport (skip ports already in use).
        while used_ports.contains(&next_port) {
            next_port = next_port.checked_add(1)
                .ok_or_else(|| anyhow::anyhow!("No available ports in u16 range"))?;
        }
        let agent_port = next_port;
        next_port = next_port.checked_add(1)
            .ok_or_else(|| anyhow::anyhow!("No available ports in u16 range"))?;

        // Redirect stdout/stderr to a log file (readable via `runtime logs`).
        let log_path = install_dir.join("agent.log");
        let log_file = std::fs::File::create(&log_path)
            .with_context(|| format!("Failed to create log file at {}", log_path.display()))?;
        let log_stdout = log_file.try_clone()
            .context("Failed to clone log file handle for stdout")?;
        let log_stderr = log_file.try_clone()
            .context("Failed to clone log file handle for stderr")?;
        drop(log_file); // Release original FD — child only needs the two clones

        // Daemon mode: use SSE transport so the agent runs as an HTTP server.
        // Stdio transport doesn't work for daemons because the CLI exits and
        // drops stdin → agent sees EOF → exits. SSE lets external clients
        // (Platform Router, MCP clients) connect via HTTP.
        let null_stdin = std::fs::File::open("/dev/null")
            .context("Failed to open /dev/null for stdin")?;

        let mut child = std::process::Command::new(&python)
            .args(&cmd_args)
            .current_dir(&install_dir)
            .env("AGENT_NEXUS_ROOT", &root)
            .env("MCP_TRANSPORT", "sse")
            .env("MCP_PORT", agent_port.to_string())
            .env("MCP_HOST", "127.0.0.1")
            .stdin(std::process::Stdio::from(null_stdin))
            .stdout(std::process::Stdio::from(log_stdout))
            .stderr(std::process::Stdio::from(log_stderr))
            .spawn()
            .with_context(|| format!("Failed to start agent '{agent_name}'"))?;

        let pid = child.id();
        let start_time = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        // Use create_new(true) to prevent symlink attacks — fails if file already
        // exists (including symlinks). Stale PID files were cleaned above.
        {
            let f = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&pid_file)
                .with_context(|| format!("Failed to create PID file at {}", pid_file.display()))?;
            use std::io::Write;
            write!(&f, "{pid}:{start_time}")?;
        }

        // Store the SSE port for this agent.
        let port_file = root.join(".agents").join(format!("{agent_name}.port"));
        std::fs::write(&port_file, agent_port.to_string())?;

        // Spawn a background reaper thread to call wait() so the OS can
        // reap the child when it exits — prevents zombie processes.
        let agent_label = agent_name.clone();
        let handle = std::thread::spawn(move || {
            if let Err(e) = child.wait() {
                tracing::warn!("Reaper for agent '{agent_label}' failed: {e}");
            }
        });
        reaper_handles.push(handle);
        output.success(&format!(
            "Agent '{agent_name}' started (PID: {pid}, SSE: http://127.0.0.1:{agent_port}/sse)."
        ));
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
        if !agents_dir.exists() {
            output.info("No agents directory found — nothing to stop.");
            return Ok(());
        }
        std::fs::read_dir(&agents_dir)?
            .filter_map(std::result::Result::ok)
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
        let name = agent.ok_or_else(|| anyhow::anyhow!("agent name is required when --all is not set"))?.to_string();
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
        if let Some(pid) = pid_parts[0].parse::<i32>().ok().filter(|&p| p > 0) {
            #[cfg(unix)]
            {
                // Validate PID range before signaling to prevent misuse.
                if !is_valid_pid(pid) {
                    // Out-of-range PID — skip signaling, just clean up PID file below.
                } else {
                    // Verify the process exists before signaling (PID recycling protection)
                    // SAFETY: pid comes from a PID file written by this tool at start time.
                    // The start_time component in the PID file provides recycling protection.
                    // kill(pid, 0) is a standard permission/existence check — no signal sent.
                    // pid is validated > 0 and <= PID_MAX_LIMIT to avoid signaling process groups
                    // or absurdly large PID values.
                    let signal_ret = unsafe { libc::kill(pid, 0) };
                    if signal_ret == 0 {
                        // Process exists — check start_time if available for PID recycling protection
                        let should_kill = if pid_parts.len() > 1 {
                            if let Ok(expected_start) = pid_parts[1].parse::<u64>() {
                                // PID recycling protection: verify the process started
                                // at approximately the same time we recorded.
                                validate_pid_start_time(pid, expected_start)
                            } else {
                                true // Can't parse start_time, proceed
                            }
                        } else {
                            true // No start_time recorded (old format), assume it's ours
                        };

                        if should_kill {
                            // SAFETY: pid was validated above — signal_ret confirmed process exists.
                            // start_time in PID file guards against PID recycling.
                            // SIGTERM is the standard graceful termination signal.
                            let ret = unsafe { libc::kill(pid, libc::SIGTERM) };
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
            }
            #[cfg(not(unix))]
            {
                output.info(&format!("Stop not supported on this platform for PID: {pid}"));
            }
        }
        let _ = std::fs::remove_file(pid_path);
        // Clean up the SSE port file if it exists.
        let port_file = pid_path.with_extension("port");
        let _ = std::fs::remove_file(port_file);
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
/// Validate that a process with the given PID started at approximately
/// the expected start time (epoch seconds). Returns `false` if the process
/// start time doesn't match (PID recycling detected) or if we can't determine it.
fn validate_pid_start_time(pid: i32, expected_start_epoch: u64) -> bool {
    use std::time::{SystemTime, UNIX_EPOCH};

    // Get elapsed time from `ps -o etime`
    let output = match std::process::Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "etime="])
        .output()
    {
        Ok(o) => o,
        Err(_) => return false, // ps not available — can't validate, fail closed
    };

    let elapsed_str = String::from_utf8_lossy(&output.stdout);
    if elapsed_str.trim().is_empty() {
        return false; // Process doesn't exist or ps failed
    }

    let elapsed_secs = parse_ps_elapsed(&elapsed_str);
    let now_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    // Allow 3-second tolerance for timing differences between our start
    // recording and ps's elapsed time calculation.
    let actual_start = now_secs.saturating_sub(elapsed_secs);
    actual_start.abs_diff(expected_start_epoch) <= 3
}

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

    let mut running: Vec<(String, i32, bool, Option<u16>)> = Vec::new();

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
        let raw_pid: i32 = parts.next()
            .and_then(|s| s.parse().ok())
            .filter(|&p| p > 0)
            .unwrap_or(0);
        let stored_start_time: u64 = parts.next()
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        // Read SSE port if available
        let port_file = agents_dir.join(format!("{agent_name}.port"));
        let sse_port: Option<u16> = std::fs::read_to_string(&port_file)
            .ok()
            .and_then(|s| s.trim().parse().ok());
        if raw_pid > 0 {
            #[cfg(unix)]
            {
                // Reject out-of-range PIDs before signaling to prevent misuse.
                // Still report the agent as "dead" rather than silently skipping it.
                if !is_valid_pid(raw_pid) {
                    // Out-of-range PID — treat as dead, no signal attempted.
                    running.push((agent_name, raw_pid, false, sse_port));
                } else {
                    // SAFETY: raw_pid is validated > 0 and <= PID_MAX_LIMIT.
                    let alive = unsafe { libc::kill(raw_pid, 0) } == 0;
                    // PID recycling protection: verify the process at this PID
                    // is the same one we started by comparing start times.
                    // Use `ps -o etime=` to get elapsed seconds and compare.
                    let alive = if alive && stored_start_time > 0 {
                        let current_time = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs();
                        let output = std::process::Command::new("ps")
                            .args(["-o", "etime=", "-p", &raw_pid.to_string()])
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
                    running.push((agent_name, raw_pid, alive, sse_port));
                }
            }
            #[cfg(not(unix))]
            {
                running.push((agent_name, raw_pid, true, sse_port));
            }
        }
    }

    if output.is_json() {
        let arr: Vec<_> = running
            .iter()
            .map(|(name, pid, alive, port)| {
                let mut obj = serde_json::json!({
                    "name": name,
                    "pid": pid,
                    "status": if *alive { "running" } else { "dead" }
                });
                if let Some(p) = port {
                    obj["port"] = serde_json::json!(p);
                    obj["sse_url"] = serde_json::json!(format!("http://127.0.0.1:{p}/sse"));
                }
                obj
            })
            .collect();
        output.data(&arr);
    } else if running.is_empty() {
        output.info("No agents running.");
    } else {
        println!("{:<25} {:<10} {:<10} Status", "Agent", "PID", "Port");
        println!("{}", "-".repeat(60));
        for (name, pid, alive, port) in &running {
            let status = if *alive { "running" } else { "dead" };
            let port_str = port.map_or("-".to_string(), |p| p.to_string());
            println!("{name:<25} {pid:<10} {port_str:<10} {status}");
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
/// When `follow` is true, polls for new lines until Ctrl-C.
fn tail_log(path: &std::path::Path, lines: usize, follow: bool) -> Result<()> {
    use std::collections::VecDeque;

    let content = std::fs::read_to_string(path)?;
    let all_lines: Vec<&str> = content.lines().collect();
    let start = all_lines.len().saturating_sub(lines);
    for line in &all_lines[start..] {
        println!("{line}");
    }

    if !follow {
        return Ok(());
    }

    // Follow mode: poll for new lines every 500ms until Ctrl-C.
    let mut last_pos = std::fs::metadata(path)?.len();
    let mut tail_buf = VecDeque::with_capacity(64);

    // Ignore SIGINT in the loop so we can break cleanly.
    let running = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
    let r = running.clone();
    ctrlc::set_handler(move || {
        r.store(false, std::sync::atomic::Ordering::Relaxed);
    })?;

    while running.load(std::sync::atomic::Ordering::Relaxed) {
        std::thread::sleep(std::time::Duration::from_millis(500));

        let Ok(meta) = std::fs::metadata(path) else {
            break; // file disappeared
        };
        let current_size = meta.len();
        if current_size < last_pos {
            // File was truncated / rotated — reset.
            last_pos = 0;
        }
        if current_size <= last_pos {
            continue;
        }

        // Read just the new bytes.
        let mut f = std::fs::File::open(path)?;
        std::io::Seek::seek(&mut f, std::io::SeekFrom::Start(last_pos))?;
        let mut new_content = String::new();
        std::io::Read::read_to_string(&mut f, &mut new_content)?;
        last_pos += new_content.len() as u64;

        // Print new lines. Buffer partial last line (no trailing newline yet).
        for line in new_content.split('\n') {
            if let Some(partial) = tail_buf.pop_front() {
                let full = format!("{partial}{line}");
                if full.ends_with('\n') || full.ends_with('\r') {
                    print!("{full}");
                } else {
                    tail_buf.push_back(full);
                }
            } else if line.ends_with('\n') || line.ends_with('\r') {
                print!("{line}");
            } else {
                tail_buf.push_back(line.to_string());
            }
        }
        use std::io::Write;
        std::io::stdout().flush()?;
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

    let (entrypoint, use_module) = resolve_entrypoint(&install_dir, agent);
    let python = resolve_venv_python(&install_dir).unwrap_or_else(resolve_python);

    // Task content comes from the user's CLI args (everything after the agent name).
    // The MCP JSON-RPC protocol carries the task — NOT the process command line.
    let task = if args.is_empty() {
        "run".to_string()
    } else if args.len() == 1 {
        args[0].clone()
    } else {
        args.join(" ")
    };

    // Build process command — entrypoint only, no user args.
    let (cmd, cmd_args) = if let Some(ref ep) = entrypoint {
        (python, vec![ep.to_string_lossy().to_string()])
    } else if use_module {
        (python, vec!["-m".to_string(), agent.replace('-', "_")])
    } else {
        bail!("Agent '{agent}' has no entrypoint (tried main.py, agent.py, mcp_adapter.py).");
    };

    output.info(&format!("Spawning agent '{agent}' via {cmd} ..."));

    // Reuse existing tokio runtime if available, otherwise create one.
    // Avoids panic from Runtime::new() inside an existing runtime (M16).
    if let Ok(handle) = tokio::runtime::Handle::try_current() {
        handle.block_on(async_exec(agent, &cmd, &cmd_args, &task, entry.source.as_str(), output))?;
    } else {
        let rt = tokio::runtime::Runtime::new().context("Failed to create tokio runtime")?;
        rt.block_on(async_exec(agent, &cmd, &cmd_args, &task, entry.source.as_str(), output))?;
    }

    Ok(())
}

/// Send a JSON-RPC message (newline-delimited) to the agent's stdin.
async fn mcp_send(
    writer: &mut tokio::process::ChildStdin,
    msg: &serde_json::Value,
) -> Result<()> {
    use tokio::io::AsyncWriteExt;
    let mut payload = serde_json::to_string(msg)?;
    payload.push('\n');
    writer.write_all(payload.as_bytes()).await?;
    writer.flush().await?;
    Ok(())
}

/// Read one JSON-RPC response from the agent's stdout, skipping non-JSON lines.
async fn mcp_read(
    reader: &mut tokio::io::BufReader<tokio::process::ChildStdout>,
) -> Result<serde_json::Value> {
    use tokio::io::AsyncBufReadExt;
    let mut line = String::new();
    loop {
        line.clear();
        let n = reader.read_line(&mut line).await?;
        if n == 0 {
            bail!("Agent process closed stdout (EOF)");
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(trimmed) {
            // Skip server-initiated notifications (no "id" field per MCP JSON-RPC spec).
            // Notifications include progress updates, log messages, etc.
            if val.get("id").is_none() {
                tracing::debug!("Skipping MCP notification: {}", trimmed);
                continue;
            }
            return Ok(val);
        }
        // Non-JSON line (FastMCP banner, debug output) — skip.
    }
}

/// Async inner: spawn agent process, communicate via MCP JSON-RPC, display result.
///
/// Protocol flow:
///   1. `initialize` request → response
///   2. `notifications/initialized` notification
///   3. `tools/call` with `"run"` tool → response with task result
async fn async_exec(
    agent_id: &str,
    cmd: &str,
    args: &[String],
    task: &str,
    _source: &str,
    output: &OutputFormatter,
) -> Result<()> {
    use std::process::Stdio;

    // Spawn the agent as a subprocess with piped stdin/stdout for MCP stdio transport.
    let mut child = tokio::process::Command::new(cmd)
        .args(args.iter().map(String::as_str))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .with_context(|| format!("Failed to spawn agent process: {} {}", cmd, args.join(" ")))?;

    let pid = child.id().unwrap_or(0);
    output.info(&format!("Agent process started (pid={pid})"));

    let stdin = child.stdin.take().context("Failed to acquire agent stdin")?;
    let stdout = child.stdout.take().context("Failed to acquire agent stdout")?;

    let mut reader = tokio::io::BufReader::new(stdout);
    let mut writer = stdin;

    // ---- Step 1: MCP initialize ----
    let init_req = serde_json::json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "agent-nexus-cli",
                "version": env!("CARGO_PKG_VERSION")
            }
        }
    });
    mcp_send(&mut writer, &init_req).await?;
    output.info("MCP initialize sent, waiting for response...");

    let init_resp = mcp_read(&mut reader).await?;
    if let Some(err) = init_resp.get("error") {
        let msg = err["message"].as_str().unwrap_or("unknown");
        let _ = child.kill().await;
        bail!("MCP initialize error from agent: {msg}");
    }

    // ---- Step 2: notifications/initialized (no id → notification) ----
    let initialized_notif = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    });
    mcp_send(&mut writer, &initialized_notif).await?;

    // ---- Step 3: Discover available tools via tools/list ----
    let list_req = serde_json::json!({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    });
    mcp_send(&mut writer, &list_req).await?;

    let list_resp = mcp_read(&mut reader).await?;
    let tool_name = if let Some(tools) = list_resp
        .get("result")
        .and_then(|r| r.get("tools"))
        .and_then(|t| t.as_array())
    {
        // Prefer "run", then "analyze", then first available tool.
        let names: Vec<&str> = tools
            .iter()
            .filter_map(|t| t.get("name").and_then(|n| n.as_str()))
            .collect();
        if names.is_empty() {
            let _ = child.kill().await;
            bail!("Agent '{agent_id}' exposes no MCP tools");
        }
        if names.contains(&"run") {
            "run"
        } else if names.contains(&"analyze") {
            "analyze"
        } else {
            names[0]
        }
    } else {
        // tools/list failed — fall back to "run".
        "run"
    };
    output.info(&format!("Using MCP tool '{tool_name}' for agent '{agent_id}'"));

    // ---- Step 4: tools/call with the discovered tool name ----
    let call_req = serde_json::json!({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {
                "task": task
            }
        }
    });
    mcp_send(&mut writer, &call_req).await?;
    output.info(&format!("Task sent to '{agent_id}' via tools/call '{tool_name}', waiting for result..."));

    // Read tool call response with timeout. Kill child on timeout to prevent leaks.
    let tool_resp = if let Ok(result) = tokio::time::timeout(
        std::time::Duration::from_secs(RESPONSE_TIMEOUT_SECS),
        mcp_read(&mut reader),
    )
    .await { result.context("Failed to read agent response")? } else {
        let _ = child.kill().await;
        bail!("Timed out after {RESPONSE_TIMEOUT_SECS}s waiting for agent response");
    };

    // Extract text content from MCP tool result.
    // Format: { "result": { "content": [ { "type": "text", "text": "..." } ] } }
    if let Some(err) = tool_resp.get("error") {
        let msg = err["message"].as_str().unwrap_or("unknown error");
        output.error(&format!("Agent returned MCP error: {msg}"));
    } else if let Some(result) = tool_resp.get("result") {
        let text = result
            .get("content")
            .and_then(|c| c.as_array())
            .map(|arr| {
                arr.iter()
                    .filter(|item| {
                        item.get("type").and_then(|t| t.as_str()) == Some("text")
                    })
                    .map(|item| item.get("text").and_then(|t| t.as_str()).unwrap_or(""))
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .unwrap_or_default();

        if text.is_empty() {
            output.info("Agent returned empty result.");
        } else {
            output.success("Agent completed successfully:");
            output.info(&text);
        }
    } else {
        // Unexpected response shape — dump it for debugging.
        output.info(&format!("Unexpected response: {tool_resp}"));
    }

    let _ = child.kill().await;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Stdio;

    /// Mutex to serialize tests that modify the `AGENT_NEXUS_PYTHON` env var.
    static PYTHON_ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

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
        let _ = child.wait();
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
        let _ = child.wait();
    }

    #[test]
    fn resolve_python_env_set_and_fallback() {
        let _guard = PYTHON_ENV_LOCK.lock().unwrap();
        // Clean slate
        std::env::remove_var("AGENT_NEXUS_PYTHON");

        // Test env-var path takes priority
        std::env::set_var("AGENT_NEXUS_PYTHON", "/custom/python");
        let python = resolve_python();
        assert_eq!(python, "/custom/python");

        // Test fallback when env-var is removed
        std::env::remove_var("AGENT_NEXUS_PYTHON");
        let python = resolve_python();
        assert_eq!(python, "python3");
    }

    #[test]
    fn resolve_python_rejects_suspicious_path() {
        let _guard = PYTHON_ENV_LOCK.lock().unwrap();
        // Clean slate
        std::env::remove_var("AGENT_NEXUS_PYTHON");

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
        assert_eq!(parse_ps_elapsed("1:23:45"), 3600 + 23 * 60 + 45);
    }

    #[test]
    fn parse_ps_elapsed_days() {
        assert_eq!(parse_ps_elapsed("1-03:45:22"), 86400 + 3 * 3600 + 45 * 60 + 22);
    }
}
