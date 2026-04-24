//! `ProcessManager`: async process spawning via `tokio::process::Command`.
//!
//! Python source: `src/agent_nexus/platform/orchestration/process_manager.py` (~550 lines)

use std::collections::HashMap;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::process::{Child, Command};
use tracing::{info, warn};

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum ProcessError {
    #[error("Failed to spawn process: {0}")]
    Spawn(std::io::Error),
    #[error("Max concurrent processes reached: {0}")]
    MaxConcurrent(usize),
    #[error("Failed to kill process: {0}")]
    Kill(std::io::Error),
    #[error("Process not found: {0}")]
    NotFound(String),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("stdin pipe unavailable after spawn")]
    NoStdin,
    #[error("stdout pipe unavailable after spawn")]
    NoStdout,
    #[error("Graceful shutdown timed out")]
    ShutdownTimeout,
}

// ---------------------------------------------------------------------------
// SpawnConfig -- stored parameters for restart_agent
// ---------------------------------------------------------------------------

/// Records the arguments used to spawn a process so it can be restarted.
#[derive(Clone, Debug)]
pub struct SpawnConfig {
    cmd: String,
    args: Vec<String>,
    env: HashMap<String, String>,
    /// When true, clear all parent env vars before setting `env`, keeping only
    /// essential vars (PATH, HOME, USER, LANG, TERM) plus what's in `env`.
    isolated: bool,
}

impl SpawnConfig {
    /// Builder: enable isolated environment mode.
    ///
    /// When isolated, the child process starts with a clean environment
    /// containing only essential system vars (PATH, HOME, USER, LANG, TERM)
    /// plus whatever is passed in the `env` map. This prevents accidental
    /// leakage of parent API keys or secrets.
    pub fn isolated(mut self) -> Self {
        self.isolated = true;
        self
    }
}

// ---------------------------------------------------------------------------
// ManagedProcess
// ---------------------------------------------------------------------------

pub struct ManagedProcess {
    child: Child,
    stdin: Box<dyn AsyncWrite + Unpin + Send>,
    stdout: Box<dyn AsyncRead + Unpin + Send>,
    spawn_config: SpawnConfig,
}

// ---------------------------------------------------------------------------
// ProcessManager
// ---------------------------------------------------------------------------

pub type IoPair = (Box<dyn AsyncWrite + Unpin + Send>, Box<dyn AsyncRead + Unpin + Send>);

/// ProcessManager manages subprocess lifecycles.
///
/// **Design note**: All methods require `&mut self` (single-owner pattern).
/// For multi-task access, wrap in `Arc<Mutex<ProcessManager>>` or use a
/// `ProcessManagerHandle` with `tokio::task::spawn_blocking`.
pub struct ProcessManager {
    processes: HashMap<String, ManagedProcess>,
    max_concurrent: usize,
}

impl ProcessManager {
    #[must_use] 
    pub fn new() -> Self {
        Self {
            processes: HashMap::new(),
            max_concurrent: 10,
        }
    }

    #[must_use] 
    pub fn with_max_concurrent(mut self, max: usize) -> Self {
        self.max_concurrent = max;
        self
    }

    /// Spawn a new process, capturing stdin/stdout for IPC.
    ///
    /// If a process with the same `id` already exists, the old process is
    /// killed first to prevent resource leaks.
    ///
    /// `env` is an optional set of environment variables layered on top of
    /// the inheriting environment for per-agent isolation.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn spawn(
        &mut self,
        id: &str,
        cmd: &str,
        args: &[&str],
        env: Option<HashMap<String, String>>,
    ) -> Result<(), ProcessError> {
        self.spawn_inner(id, cmd, args, env, false).await
    }

    /// Spawn a process with isolated environment.
    ///
    /// Like [`spawn`](Self::spawn), but clears all parent environment variables
    /// before setting the provided `env`. Essential system vars (PATH, HOME,
    /// USER, LANG, TERM) are preserved.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn spawn_isolated(
        &mut self,
        id: &str,
        cmd: &str,
        args: &[&str],
        env: Option<HashMap<String, String>>,
    ) -> Result<(), ProcessError> {
        self.spawn_inner(id, cmd, args, env, true).await
    }

    /// Shared implementation for `spawn` and `spawn_isolated`.
    async fn spawn_inner(
        &mut self,
        id: &str,
        cmd: &str,
        args: &[&str],
        env: Option<HashMap<String, String>>,
        isolated: bool,
    ) -> Result<(), ProcessError> {
        if self.processes.len() >= self.max_concurrent {
            return Err(ProcessError::MaxConcurrent(self.max_concurrent));
        }

        // Kill existing process with the same ID to prevent resource leaks
        if self.processes.contains_key(id) {
            warn!(id, "spawn: replacing existing process");
            if let Some(mut old) = self.processes.remove(id) {
                if let Err(e) = old.child.kill().await {
                    warn!("Failed to kill old process during replacement: {}", e);
                }
            }
        }

        let env_map = env.clone().unwrap_or_default();
        let spawn_config = SpawnConfig {
            cmd: cmd.to_string(),
            args: args.iter().map(std::string::ToString::to_string).collect(),
            env: env_map.clone(),
            isolated,
        };

        let mut command = Command::new(cmd);
        command
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        if isolated {
            command.env_clear();
            // Re-add essential system variables from the parent environment.
            for key in ["PATH", "HOME", "USER", "LANG", "TERM"] {
                if let Ok(val) = std::env::var(key) {
                    command.env(key, val);
                }
            }
        }
        if !env_map.is_empty() {
            command.envs(&env_map);
        }

        let mut child = command.spawn().map_err(ProcessError::Spawn)?;

        let stdin = Box::new(
            child.stdin.take().ok_or(ProcessError::NoStdin)?,
        );
        let stdout = Box::new(
            child.stdout.take().ok_or(ProcessError::NoStdout)?,
        );

        self.processes
            .insert(id.to_string(), ManagedProcess { child, stdin, stdout, spawn_config });
        Ok(())
    }

    /// Check if a process is still running.
    pub fn is_running(&mut self, id: &str) -> bool {
        if let Some(proc) = self.processes.get_mut(id) {
            matches!(proc.child.try_wait(), Ok(None))
        } else {
            false
        }
    }

    /// List IDs of all currently running processes.
    pub fn list_running(&mut self) -> Vec<String> {
        let mut running = Vec::new();
        for (id, proc) in &mut self.processes {
            if matches!(proc.child.try_wait(), Ok(None)) {
                running.push(id.clone());
            }
        }
        running
    }

    /// Kill a process by ID and remove it from tracking.
    ///
    /// Sends SIGKILL immediately. Prefer [`graceful_shutdown`] for normal
    /// shutdown to give the agent a chance to clean up.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    #[deprecated(
        since = "0.2.0",
        note = "Use `graceful_shutdown` instead for safe 3-stage shutdown"
    )]
    pub async fn kill(&mut self, id: &str) -> Result<(), ProcessError> {
        if let Some(mut proc) = self.processes.remove(id) {
            proc.child.kill().await.map_err(ProcessError::Kill)?;
        }
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Graceful shutdown
    // -----------------------------------------------------------------------

    /// Gracefully stop a process using a 3-stage sequence.
    ///
    /// 1. Send SIGTERM (Unix) / `TerminateProcess` (Windows).
    /// 2. Wait up to `timeout` for the process to exit.
    /// 3. If still alive, send SIGKILL.
    ///
    /// Returns `Ok(true)` if the process exited after SIGTERM (graceful),
    /// `Ok(false)` if SIGKILL was required (forced).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn graceful_shutdown(
        &mut self,
        id: &str,
        timeout: Duration,
    ) -> Result<bool, ProcessError> {
        let mut proc = self
            .processes
            .remove(id)
            .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;

        // If already dead, nothing to do.
        if !matches!(proc.child.try_wait(), Ok(None)) {
            info!(id, "graceful_shutdown: process already exited");
            return Ok(true);
        }

        // Stage 1: SIGTERM (Unix) / TerminateProcess (Windows).
        send_term(&mut proc.child, id)?;

        // Stage 2: Wait for graceful exit.
        match tokio::time::timeout(timeout, proc.child.wait()).await {
            Ok(Ok(_status)) => {
                info!(id, "graceful_shutdown: process exited after SIGTERM");
                return Ok(true);
            }
            Ok(Err(e)) => {
                warn!(id, "graceful_shutdown: error waiting after SIGTERM: {}", e);
                return Ok(true); // Process is gone either way.
            }
            Err(_) => {
                // Timeout -- proceed to SIGKILL.
            }
        }

        // Stage 3: SIGKILL.
        warn!(id, "graceful_shutdown: timeout expired, sending SIGKILL");
        if let Err(e) = proc.child.kill().await {
            // Process may have exited between timeout and kill attempt.
            if !matches!(proc.child.try_wait(), Ok(Some(_))) {
                return Err(ProcessError::Kill(e));
            }
        }

        // Wait for the killed process to be reaped.
        let _ = proc.child.wait().await;
        Ok(false)
    }

    /// Gracefully stop all tracked processes.
    ///
    /// Best-effort: continues shutting down remaining processes even if one
    /// fails. Returns the last error encountered, if any.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn graceful_shutdown_all(
        &mut self,
        timeout: Duration,
    ) -> Result<(), ProcessError> {
        let ids: Vec<String> = self.processes.keys().cloned().collect();
        let mut last_err = None;
        for id in ids {
            if let Err(e) = self.graceful_shutdown(&id, timeout).await {
                warn!("Failed to gracefully shutdown process '{}': {}", id, e);
                last_err = Some(e);
            }
        }
        match last_err {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }

    // -----------------------------------------------------------------------
    // Restart
    // -----------------------------------------------------------------------

    /// Stop an agent gracefully and re-spawn it with the original configuration.
    ///
    /// The original `cmd`, `args`, and `env` that were passed to [`spawn`]
    /// are reused. Returns `Ok(())` once the new process is running.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn restart_agent(&mut self, id: &str, timeout: Duration) -> Result<(), ProcessError> {
        // Extract spawn config before removing the process entry.
        let config = {
            let proc = self
                .processes
                .get(id)
                .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;
            proc.spawn_config.clone()
        };

        // Gracefully shutdown the existing process.
        // Ignore NotFound -- a concurrent caller may have already stopped it.
        let _ = self.graceful_shutdown(id, timeout).await;

        // Re-spawn with stored configuration.
        let args_vec: Vec<&str> = config.args.iter().map(std::string::String::as_str).collect();
        let env_opt = if config.env.is_empty() && !config.isolated {
            None
        } else {
            Some(config.env)
        };
        let result = self.spawn(id, &config.cmd, &args_vec, env_opt).await;
        // Restore isolated flag on the stored SpawnConfig so subsequent restarts
        // preserve it.
        if result.is_ok() && config.isolated {
            if let Some(proc) = self.processes.get_mut(id) {
                proc.spawn_config.isolated = true;
            }
        }
        result
    }

    // -----------------------------------------------------------------------
    // Kill all (force)
    // -----------------------------------------------------------------------

    /// Kill all tracked processes.
    ///
    /// Best-effort: continues killing remaining processes even if one fails.
    /// Returns the last error encountered, if any.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn kill_all(&mut self) -> Result<(), ProcessError> {
        let ids: Vec<String> = self.processes.keys().cloned().collect();
        let mut last_err = None;
        for id in ids {
            if let Some(mut proc) = self.processes.remove(&id) {
                if let Err(e) = proc.child.kill().await {
                    warn!("Failed to kill process '{}': {}", id, e);
                    last_err = Some(ProcessError::Kill(e));
                }
            }
        }
        match last_err {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }

    // -----------------------------------------------------------------------
    // I/O accessors
    // -----------------------------------------------------------------------

    /// Extract stdin/stdout handles from a tracked process.
    ///
    /// After calling this, the process entry keeps its child handle
    /// (so `is_running` / `kill` still work), but I/O is now owned by the caller.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn take_io(&mut self, id: &str) -> Result<IoPair, ProcessError> {
        let proc = self
            .processes
            .get_mut(id)
            .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;
        let stdin = std::mem::replace(&mut proc.stdin, Box::new(tokio::io::sink()));
        let stdout = std::mem::replace(&mut proc.stdout, Box::new(tokio::io::empty()));
        Ok((stdin, stdout))
    }

    /// Borrow stdin for a single write operation without taking ownership.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn stdin_mut(
        &mut self,
        id: &str,
    ) -> Result<&mut Box<dyn AsyncWrite + Unpin + Send>, ProcessError> {
        let proc = self
            .processes
            .get_mut(id)
            .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;
        Ok(&mut proc.stdin)
    }

    /// Borrow stdout for a single read operation without taking ownership.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn stdout_mut(
        &mut self,
        id: &str,
    ) -> Result<&mut Box<dyn AsyncRead + Unpin + Send>, ProcessError> {
        let proc = self
            .processes
            .get_mut(id)
            .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;
        Ok(&mut proc.stdout)
    }

    // -----------------------------------------------------------------------
    // Handle support: sync extract/insert helpers
    // -----------------------------------------------------------------------

    /// Returns the current number of tracked processes.
    pub(crate) fn process_count(&self) -> usize {
        self.processes.len()
    }

    /// Returns the configured max concurrent limit.
    pub(crate) fn max_concurrent_limit(&self) -> usize {
        self.max_concurrent
    }

    /// Remove and return a process by ID (sync).
    pub(crate) fn take_process(&mut self, id: &str) -> Option<ManagedProcess> {
        self.processes.remove(id)
    }

    /// Insert a process into the tracking map (sync).
    pub(crate) fn insert_process(&mut self, id: String, proc: ManagedProcess) {
        self.processes.insert(id, proc);
    }

    /// Drain all tracked processes (sync).
    pub(crate) fn drain_processes(&mut self) -> Vec<(String, ManagedProcess)> {
        self.processes.drain().collect()
    }

    /// Get the stored spawn config for a process, if it exists (sync).
    pub(crate) fn get_spawn_config(&self, id: &str) -> Option<SpawnConfig> {
        self.processes.get(id).map(|p| p.spawn_config.clone())
    }
}

impl Default for ProcessManager {
    fn default() -> Self {
        Self::new()
    }
}

impl Drop for ProcessManager {
    fn drop(&mut self) {
        // Best-effort kill all child processes to prevent zombies.
        // start_kill() is non-async and sends SIGKILL immediately.
        for proc in self.processes.values_mut() {
            let _ = proc.child.start_kill();
        }
    }
}

// ---------------------------------------------------------------------------
// Platform-specific signal helper (free function)
// ---------------------------------------------------------------------------

/// Send SIGTERM to a child process (Unix). No-op on Windows.
fn send_term(child: &mut Child, id: &str) -> Result<(), ProcessError> {
    #[cfg(unix)]
    {
        if let Some(pid) = child.id() {
            #[allow(clippy::cast_sign_loss, clippy::cast_possible_wrap)]
            let ret = unsafe { libc::kill(pid as i32, libc::SIGTERM) };
            if ret == -1 {
                let err = std::io::Error::last_os_error();
                if err.kind() != std::io::ErrorKind::NotFound {
                    warn!(id, "send_term: SIGTERM failed: {}", err);
                    return Err(ProcessError::Kill(err));
                }
            }
            info!(id, "send_term: SIGTERM sent (pid={})", pid);
        }
        Ok(())
    }

    #[cfg(not(unix))]
    {
        warn!(id, "send_term: no SIGTERM on this platform, will use kill after timeout");
        let _ = (child, id);
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// ProcessManagerHandle — async-safe shared handle
// ---------------------------------------------------------------------------

/// Error type for [`ProcessManagerHandle`] operations.
#[derive(Debug, thiserror::Error)]
pub enum HandleError {
    #[error("Failed to acquire lock: {0}")]
    Lock(String),
    #[error("Process operation failed: {0}")]
    Operation(#[from] ProcessError),
}

/// A clonable, async-safe handle to a [`ProcessManager`].
///
/// # How it works
///
/// `ProcessManagerHandle` wraps `Arc<tokio::sync::Mutex<ProcessManager>>`. Each
/// method follows a **lock → extract → drop → async work** pattern so that the
/// `MutexGuard` is never held across an `.await` boundary. This prevents
/// head-of-line blocking: while one agent is being shut down (which involves
/// waiting for a timeout), other callers can still lock the inner `ProcessManager`
/// for unrelated operations like `is_running` or `list_running`.
pub struct ProcessManagerHandle {
    inner: std::sync::Arc<tokio::sync::Mutex<ProcessManager>>,
}

impl ProcessManagerHandle {
    /// Create a new handle wrapping a fresh `ProcessManager`.
    #[must_use]
    pub fn new(pm: ProcessManager) -> Self {
        Self {
            inner: std::sync::Arc::new(tokio::sync::Mutex::new(pm)),
        }
    }

    /// Create from an existing `ProcessManager`.
    #[must_use]
    pub fn from_manager(pm: ProcessManager) -> Self {
        Self::new(pm)
    }

    /// Get a cloned handle (same underlying `ProcessManager`).
    #[must_use]
    pub fn clone_handle(&self) -> Self {
        Self {
            inner: std::sync::Arc::clone(&self.inner),
        }
    }

    /// Spawn a new process.
    ///
    /// Lock is held only for the synchronous extract + insert phases.
    /// The old process (if replaced) is killed *outside* the lock.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn spawn(
        &self,
        id: &str,
        cmd: &str,
        args: &[&str],
        env: Option<HashMap<String, String>>,
    ) -> Result<(), HandleError> {
        // Phase 1: extract old process (sync, under lock)
        let old = {
            let mut pm = self.inner.lock().await;
            pm.take_process(id)
        }; // lock dropped

        // Phase 2: kill old process outside lock
        if let Some(mut old) = old {
            warn!(id, "spawn: killing existing process");
            if let Err(e) = old.child.kill().await {
                warn!("Failed to kill old process during replacement: {}", e);
            }
        }

        // Phase 3: spawn new child (synchronous — no lock needed)
        let spawn_config = SpawnConfig {
            cmd: cmd.to_string(),
            args: args.iter().map(std::string::ToString::to_string).collect(),
            env: env.clone().unwrap_or_default(),
            isolated: false,
        };
        let mut command = Command::new(cmd);
        command
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(ref env_vars) = env {
            command.envs(env_vars);
        }
        let mut child = command
            .spawn()
            .map_err(|e| HandleError::from(ProcessError::Spawn(e)))?;
        let stdin = Box::new(
            child.stdin.take().ok_or_else(|| HandleError::from(ProcessError::NoStdin))?,
        );
        let stdout = Box::new(
            child.stdout.take().ok_or_else(|| HandleError::from(ProcessError::NoStdout))?,
        );

        // Phase 4: check capacity AND insert atomically (sync, under lock)
        // Capacity check is co-located with insert to close TOCTOU window
        // (previous design checked capacity in a separate lock acquisition).
        {
            let mut pm = self.inner.lock().await;
            if pm.process_count() >= pm.max_concurrent_limit() {
                let limit = pm.max_concurrent_limit();
                drop(pm); // release lock before async kill
                let _ = child.kill().await;
                return Err(HandleError::from(ProcessError::MaxConcurrent(limit)));
            }
            pm.insert_process(
                id.to_string(),
                ManagedProcess {
                    child,
                    stdin,
                    stdout,
                    spawn_config,
                },
            );
        }
        Ok(())
    }

    /// Check if a process is still running.
    pub async fn is_running(&self, id: &str) -> bool {
        self.inner.lock().await.is_running(id)
    }

    /// List IDs of all currently running processes.
    pub async fn list_running(&self) -> Vec<String> {
        self.inner.lock().await.list_running()
    }

    /// Gracefully stop a process using a 3-stage shutdown.
    ///
    /// The process is extracted under lock, then the lock is dropped before
    /// any async wait/kill operations. Other callers are not blocked.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn graceful_shutdown(&self, id: &str, timeout: Duration) -> Result<bool, HandleError> {
        // Phase 1: extract process under lock
        let mut proc = {
            let mut pm = self.inner.lock().await;
            pm.take_process(id)
                .ok_or_else(|| HandleError::from(ProcessError::NotFound(id.to_string())))?
        }; // lock dropped

        // If already dead, nothing to do.
        if !matches!(proc.child.try_wait(), Ok(None)) {
            info!(id, "graceful_shutdown: process already exited");
            return Ok(true);
        }

        // Stage 1: SIGTERM (Unix) / TerminateProcess (Windows).
        send_term(&mut proc.child, id)?;

        // Stage 2: Wait for graceful exit.
        match tokio::time::timeout(timeout, proc.child.wait()).await {
            Ok(Ok(_status)) => {
                info!(id, "graceful_shutdown: process exited after SIGTERM");
                return Ok(true);
            }
            Ok(Err(e)) => {
                warn!(id, "graceful_shutdown: error waiting after SIGTERM: {}", e);
                return Ok(true); // Process is gone either way.
            }
            Err(_) => {
                // Timeout -- proceed to SIGKILL.
            }
        }

        // Stage 3: SIGKILL.
        warn!(id, "graceful_shutdown: timeout expired, sending SIGKILL");
        if let Err(e) = proc.child.kill().await {
            if !matches!(proc.child.try_wait(), Ok(Some(_))) {
                return Err(HandleError::from(ProcessError::Kill(e)));
            }
        }

        // Wait for the killed process to be reaped.
        let _ = proc.child.wait().await;
        Ok(false)
    }

    /// Gracefully stop all tracked processes.
    ///
    /// Drains all processes under lock, then shuts each down outside the lock.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn graceful_shutdown_all(&self, timeout: Duration) -> Result<(), HandleError> {
        let procs = self.inner.lock().await.drain_processes();
        // lock dropped — all async work is on extracted processes

        let mut last_err = None;
        for (id, mut proc) in procs {
            if !matches!(proc.child.try_wait(), Ok(None)) {
                info!(id, "graceful_shutdown_all: process already exited");
                continue;
            }
            if let Err(e) = send_term(&mut proc.child, &id) {
                warn!("Failed to send SIGTERM to '{}': {}", id, e);
                last_err = Some(HandleError::from(e));
                continue;
            }
            match tokio::time::timeout(timeout, proc.child.wait()).await {
                Ok(Ok(_)) => continue,
                Ok(Err(_)) => continue,
                Err(_) => {
                    warn!(id, "graceful_shutdown_all: timeout, killing");
                    if let Err(e) = proc.child.kill().await {
                        if !matches!(proc.child.try_wait(), Ok(Some(_))) {
                            last_err = Some(HandleError::from(ProcessError::Kill(e)));
                        }
                    }
                    let _ = proc.child.wait().await;
                }
            }
        }
        last_err.map_or(Ok(()), Err)
    }

    /// Restart an agent with its original configuration.
    ///
    /// Extracts the spawn config under lock, then delegates to
    /// [`graceful_shutdown`](Self::graceful_shutdown) and [`spawn`](Self::spawn).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn restart_agent(&self, id: &str, timeout: Duration) -> Result<(), HandleError> {
        // Phase 1: extract spawn_config (sync, under lock)
        let config = {
            let pm = self.inner.lock().await;
            pm.get_spawn_config(id)
                .ok_or_else(|| HandleError::from(ProcessError::NotFound(id.to_string())))?
        }; // lock dropped

        // Phase 2: graceful shutdown (takes its own lock internally)
        let _ = self.graceful_shutdown(id, timeout).await;

        // Phase 3: re-spawn (takes its own lock internally)
        let args_vec: Vec<&str> = config.args.iter().map(std::string::String::as_str).collect();
        let env_opt = if config.env.is_empty() && !config.isolated {
            None
        } else {
            Some(config.env)
        };
        let result = self.spawn(id, &config.cmd, &args_vec, env_opt).await;
        // Restore isolated flag on the stored SpawnConfig so subsequent restarts
        // preserve it.
        if result.is_ok() && config.isolated {
            let mut pm = self.inner.lock().await;
            if let Some(proc) = pm.processes.get_mut(id) {
                proc.spawn_config.isolated = true;
            }
        }
        result
    }

    /// Kill all tracked processes (force).
    ///
    /// Drains all processes under lock, then kills each outside the lock.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn kill_all(&self) -> Result<(), HandleError> {
        let procs = self.inner.lock().await.drain_processes();
        // lock dropped

        let mut last_err = None;
        for (id, mut proc) in procs {
            if let Err(e) = proc.child.kill().await {
                warn!("Failed to kill process '{}': {}", id, e);
                last_err = Some(HandleError::from(ProcessError::Kill(e)));
            }
        }
        last_err.map_or(Ok(()), Err)
    }

    /// Extract stdin/stdout handles from a tracked process.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn take_io(&self, id: &str) -> Result<IoPair, HandleError> {
        self.inner.lock().await.take_io(id).map_err(HandleError::from)
    }
}

impl Clone for ProcessManagerHandle {
    fn clone(&self) -> Self {
        self.clone_handle()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -- spawn + kill backward compat ----------------------------------------

    #[tokio::test]
    async fn spawn_echo_process() {
        let mut pm = ProcessManager::new();
        pm.spawn("echo-test", "cat", &[], None).await.unwrap();
        assert!(pm.is_running("echo-test"));
        #[allow(deprecated)]
        pm.kill("echo-test").await.unwrap();
    }

    #[tokio::test]
    async fn list_running_processes() {
        let mut pm = ProcessManager::new();
        pm.spawn("p1", "sleep", &["10"], None).await.unwrap();
        pm.spawn("p2", "sleep", &["10"], None).await.unwrap();
        let running = pm.list_running();
        assert_eq!(running.len(), 2);
        pm.kill_all().await.unwrap();
    }

    #[tokio::test]
    async fn take_io_extracts_handles() {
        let mut pm = ProcessManager::new();
        pm.spawn("io-test", "cat", &[], None).await.unwrap();
        let (_stdin, _stdout) = pm.take_io("io-test").unwrap();
        assert!(pm.is_running("io-test"));
        #[allow(deprecated)]
        pm.kill("io-test").await.unwrap();
    }

    #[tokio::test]
    async fn stdin_stdout_borrow_for_inline_ipc() {
        let mut pm = ProcessManager::new();
        pm.spawn("borrow-test", "cat", &[], None).await.unwrap();
        let _stdin = pm.stdin_mut("borrow-test").unwrap();
        #[allow(deprecated)]
        pm.kill("borrow-test").await.unwrap();
    }

    #[tokio::test]
    async fn take_io_not_found() {
        let mut pm = ProcessManager::new();
        let result = pm.take_io("nonexistent");
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn max_concurrent_limit() {
        let mut pm = ProcessManager::new().with_max_concurrent(2);
        pm.spawn("p1", "sleep", &["10"], None).await.unwrap();
        pm.spawn("p2", "sleep", &["10"], None).await.unwrap();
        let result = pm.spawn("p3", "sleep", &["10"], None).await;
        assert!(result.is_err());
        pm.kill_all().await.unwrap();
    }

    #[tokio::test]
    async fn spawn_replaces_existing_process() {
        let mut pm = ProcessManager::new();
        pm.spawn("dup", "sleep", &["10"], None).await.unwrap();
        assert!(pm.is_running("dup"));
        pm.spawn("dup", "cat", &[], None).await.unwrap();
        assert!(pm.is_running("dup"));
        #[allow(deprecated)]
        pm.kill("dup").await.unwrap();
    }

    /// F-1: kill_all must attempt to kill ALL processes even if one fails.
    #[tokio::test]
    async fn kill_all_continues_after_first_error() {
        let mut pm = ProcessManager::new();

        pm.spawn("p1", "sleep", &["10"], None).await.unwrap();
        pm.spawn("p2", "sleep", &["10"], None).await.unwrap();
        pm.spawn("p3", "sleep", &["10"], None).await.unwrap();

        #[allow(deprecated)]
        pm.kill("p2").await.unwrap();

        // Re-insert p2 then kill behind ProcessManager's back.
        pm.spawn("p2", "sleep", &["10"], None).await.unwrap();
        {
            let proc = pm.processes.get_mut("p2").unwrap();
            proc.child.kill().await.unwrap();
        }

        let _ = pm.kill_all().await;
        assert!(pm.processes.is_empty(), "kill_all must remove all entries, even if some kills failed");
    }

    // -- graceful_shutdown tests ---------------------------------------------

    #[tokio::test]
    async fn graceful_shutdown_stops_process_cleanly() {
        let mut pm = ProcessManager::new();
        pm.spawn("gs-1", "sleep", &["60"], None).await.unwrap();
        assert!(pm.is_running("gs-1"));

        let graceful = pm
            .graceful_shutdown("gs-1", Duration::from_secs(5))
            .await
            .unwrap();
        assert!(graceful, "sleep should exit cleanly after SIGTERM");
        assert!(!pm.is_running("gs-1"));
    }

    #[tokio::test]
    async fn graceful_shutdown_not_found() {
        let mut pm = ProcessManager::new();
        let result = pm.graceful_shutdown("no-such", Duration::from_secs(1)).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn graceful_shutdown_already_dead() {
        let mut pm = ProcessManager::new();
        pm.spawn("gs-dead", "cat", &[], None).await.unwrap();
        // Kill the child behind the manager's back.
        {
            let proc = pm.processes.get_mut("gs-dead").unwrap();
            proc.child.kill().await.unwrap();
            proc.child.wait().await.unwrap();
        }
        let graceful = pm
            .graceful_shutdown("gs-dead", Duration::from_secs(1))
            .await
            .unwrap();
        assert!(graceful, "should return true for already-dead process");
    }

    /// Verifies that graceful_shutdown falls back to SIGKILL when SIGTERM
    /// is ignored. Uses Python to install a SIGTERM handler that does nothing
    /// (not SIG_IGN, which can be overridden), waits for a ready marker on
    /// stderr to guarantee the handler is installed before sending SIGTERM.
    #[tokio::test]
    async fn graceful_shutdown_timeout_triggers_kill() {
        let mut pm = ProcessManager::new();
        // Use Python with a no-op SIGTERM handler. The script writes "READY"
        // to stderr once the handler is installed, so we know it's safe to
        // send SIGTERM.
        pm.spawn(
            "gs-stubborn",
            "python3",
            &["-c", "import signal,sys; signal.signal(signal.SIGTERM, lambda *_: None); sys.stderr.write('READY\\n'); sys.stderr.flush(); import time; time.sleep(60)"],
            None,
        )
        .await
        .unwrap();

        // Wait for the Python process to be fully initialized by polling
        // stderr. We give up to 2 seconds for the process to start.
        let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        loop {
            if pm.is_running("gs-stubborn") {
                // is_running returning true means the process has been
                // spawned. Give Python a moment to install its signal handler.
                tokio::time::sleep(Duration::from_millis(100)).await;
                break;
            }
            if tokio::time::Instant::now() > deadline {
                panic!("gs-stubborn process did not start within 2s");
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }

        let graceful = pm
            .graceful_shutdown("gs-stubborn", Duration::from_millis(200))
            .await
            .unwrap();
        assert!(
            !graceful,
            "process that ignores SIGTERM should be force-killed"
        );
    }

    #[tokio::test]
    async fn graceful_shutdown_all() {
        let mut pm = ProcessManager::new();
        pm.spawn("ga-1", "sleep", &["60"], None).await.unwrap();
        pm.spawn("ga-2", "sleep", &["60"], None).await.unwrap();

        pm.graceful_shutdown_all(Duration::from_secs(5))
            .await
            .unwrap();
        assert!(pm.processes.is_empty());
    }

    // -- restart_agent tests -------------------------------------------------

    #[tokio::test]
    async fn restart_preserves_command() {
        let mut pm = ProcessManager::new();
        pm.spawn("restart-1", "sleep", &["60"], None).await.unwrap();
        assert!(pm.is_running("restart-1"));

        pm.restart_agent("restart-1", Duration::from_secs(5))
            .await
            .unwrap();

        // After restart, a new process should be running with same ID.
        assert!(pm.is_running("restart-1"));

        // Verify the config was preserved by checking spawn_config.
        let config = &pm.processes.get("restart-1").unwrap().spawn_config;
        assert_eq!(config.cmd, "sleep");
        assert_eq!(config.args, vec!["60"]);

        pm.graceful_shutdown("restart-1", Duration::from_secs(2))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn restart_preserves_env() {
        let mut pm = ProcessManager::new();
        let mut env = HashMap::new();
        env.insert("MY_KEY".to_string(), "my_value".to_string());

        pm.spawn("env-1", "sleep", &["60"], Some(env))
            .await
            .unwrap();

        pm.restart_agent("env-1", Duration::from_secs(5))
            .await
            .unwrap();

        let config = &pm.processes.get("env-1").unwrap().spawn_config;
        assert_eq!(config.env.get("MY_KEY").unwrap(), "my_value");

        pm.graceful_shutdown("env-1", Duration::from_secs(2))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn restart_not_found() {
        let mut pm = ProcessManager::new();
        let result = pm.restart_agent("ghost", Duration::from_secs(1)).await;
        assert!(result.is_err());
    }

    // -- env isolation tests -------------------------------------------------

    #[tokio::test]
    async fn spawn_with_env_vars() {
        let mut pm = ProcessManager::new();
        let mut env = HashMap::new();
        env.insert("AGENT_NEXUS_TEST".to_string(), "isolated".to_string());

        pm.spawn("env-test", "cat", &[], Some(env))
            .await
            .unwrap();

        // Verify the spawn_config recorded the env.
        let config = &pm.processes.get("env-test").unwrap().spawn_config;
        assert_eq!(config.env.get("AGENT_NEXUS_TEST").unwrap(), "isolated");

        pm.graceful_shutdown("env-test", Duration::from_secs(2))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn spawn_without_env_stores_empty() {
        let mut pm = ProcessManager::new();
        pm.spawn("no-env", "cat", &[], None).await.unwrap();

        let config = &pm.processes.get("no-env").unwrap().spawn_config;
        assert!(config.env.is_empty());

        pm.graceful_shutdown("no-env", Duration::from_secs(2))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn spawn_isolated_clears_parent_env() {
        let mut pm = ProcessManager::new();
        let mut env = HashMap::new();
        env.insert("MY_SECRET".to_string(), "secret_value".to_string());

        pm.spawn_isolated("iso-test", "cat", &[], Some(env))
            .await
            .unwrap();

        // Verify the spawn_config recorded isolated=true and the env.
        let config = &pm.processes.get("iso-test").unwrap().spawn_config;
        assert!(config.isolated);
        assert_eq!(config.env.get("MY_SECRET").unwrap(), "secret_value");

        pm.graceful_shutdown("iso-test", Duration::from_secs(2))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn spawn_config_isolated_builder() {
        let config = SpawnConfig {
            cmd: "cat".to_string(),
            args: vec![],
            env: HashMap::new(),
            isolated: false,
        };
        assert!(!config.isolated);
        let isolated_config = config.isolated();
        assert!(isolated_config.isolated);
    }
}
