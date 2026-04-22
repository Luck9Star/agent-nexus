//! ProcessManager: async process spawning via tokio::process::Command.
//!
//! Python source: `src/agent_nexus/platform/orchestration/process_manager.py` (~550 lines)

use std::collections::HashMap;
use std::process::Stdio;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::process::{Child, Command};
use tracing::warn;

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
}

// ---------------------------------------------------------------------------
// ManagedProcess
// ---------------------------------------------------------------------------

pub struct ManagedProcess {
    child: Child,
    stdin: Box<dyn AsyncWrite + Unpin + Send>,
    stdout: Box<dyn AsyncRead + Unpin + Send>,
}

// ---------------------------------------------------------------------------
// ProcessManager
// ---------------------------------------------------------------------------

pub type IoPair = (Box<dyn AsyncWrite + Unpin + Send>, Box<dyn AsyncRead + Unpin + Send>);

pub struct ProcessManager {
    processes: HashMap<String, ManagedProcess>,
    max_concurrent: usize,
}

impl ProcessManager {
    pub fn new() -> Self {
        Self {
            processes: HashMap::new(),
            max_concurrent: 10,
        }
    }

    pub fn with_max_concurrent(mut self, max: usize) -> Self {
        self.max_concurrent = max;
        self
    }

    /// Spawn a new process, capturing stdin/stdout for IPC.
    ///
    /// If a process with the same `id` already exists, the old process is
    /// killed first to prevent resource leaks.
    pub async fn spawn(&mut self, id: &str, cmd: &str, args: &[&str]) -> Result<(), ProcessError> {
        if self.processes.len() >= self.max_concurrent {
            return Err(ProcessError::MaxConcurrent(self.max_concurrent));
        }

        // Kill existing process with the same ID to prevent resource leaks
        if self.processes.contains_key(id) {
            warn!(id, "spawn: replacing existing process");
            if let Some(mut old) = self.processes.remove(id) {
                let _ = old.child.kill().await;
            }
        }

        let mut child = Command::new(cmd)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(ProcessError::Spawn)?;

        let stdin = Box::new(child.stdin.take().unwrap());
        let stdout = Box::new(child.stdout.take().unwrap());

        self.processes
            .insert(id.to_string(), ManagedProcess { child, stdin, stdout });
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
    pub async fn kill(&mut self, id: &str) -> Result<(), ProcessError> {
        if let Some(mut proc) = self.processes.remove(id) {
            proc.child.kill().await.map_err(ProcessError::Kill)?;
        }
        Ok(())
    }

    /// Kill all tracked processes.
    ///
    /// Best-effort: continues killing remaining processes even if one fails.
    /// Returns the last error encountered, if any.
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

    /// Extract stdin/stdout handles from a tracked process.
    ///
    /// After calling this, the process entry keeps its child handle
    /// (so `is_running` / `kill` still work), but I/O is now owned by the caller.
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
}

impl Default for ProcessManager {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn spawn_echo_process() {
        let mut pm = ProcessManager::new();
        // Use `cat` as a simple subprocess that reads stdin and writes to stdout
        pm.spawn("echo-test", "cat", &[]).await.unwrap();
        assert!(pm.is_running("echo-test"));
        pm.kill("echo-test").await.unwrap();
    }

    #[tokio::test]
    async fn list_running_processes() {
        let mut pm = ProcessManager::new();
        pm.spawn("p1", "sleep", &["10"]).await.unwrap();
        pm.spawn("p2", "sleep", &["10"]).await.unwrap();
        let running = pm.list_running();
        assert_eq!(running.len(), 2);
        pm.kill_all().await.unwrap();
    }

    #[tokio::test]
    async fn take_io_extracts_handles() {
        let mut pm = ProcessManager::new();
        pm.spawn("io-test", "cat", &[]).await.unwrap();
        let (_stdin, _stdout) = pm.take_io("io-test").unwrap();
        // Process is still tracked for lifecycle management
        assert!(pm.is_running("io-test"));
        pm.kill("io-test").await.unwrap();
    }

    #[tokio::test]
    async fn stdin_stdout_borrow_for_inline_ipc() {
        let mut pm = ProcessManager::new();
        pm.spawn("borrow-test", "cat", &[]).await.unwrap();
        let _stdin = pm.stdin_mut("borrow-test").unwrap();
        // Can borrow stdin without taking ownership
        // (reference is implicitly dropped when `_stdin` goes out of scope)
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
        pm.spawn("p1", "sleep", &["10"]).await.unwrap();
        pm.spawn("p2", "sleep", &["10"]).await.unwrap();
        let result = pm.spawn("p3", "sleep", &["10"]).await;
        assert!(result.is_err());
        pm.kill_all().await.unwrap();
    }

    #[tokio::test]
    async fn spawn_replaces_existing_process() {
        let mut pm = ProcessManager::new();
        // Spawn first process
        pm.spawn("dup", "sleep", &["10"]).await.unwrap();
        assert!(pm.is_running("dup"));

        // Spawn again with same ID — old process should be killed
        pm.spawn("dup", "cat", &[]).await.unwrap();
        assert!(pm.is_running("dup"));

        // Clean up — should succeed (the replacement process)
        pm.kill("dup").await.unwrap();
    }

    /// F-1: kill_all must attempt to kill ALL processes even if one fails.
    #[tokio::test]
    async fn kill_all_continues_after_first_error() {
        let mut pm = ProcessManager::new();

        // Spawn 3 real processes
        pm.spawn("p1", "sleep", &["10"]).await.unwrap();
        pm.spawn("p2", "sleep", &["10"]).await.unwrap();
        pm.spawn("p3", "sleep", &["10"]).await.unwrap();

        // Kill p2 directly so kill_all will fail on it (already dead)
        pm.kill("p2").await.unwrap();

        // Re-insert a dead entry manually to simulate a process that fails to kill.
        // We spawn p2 again so it's tracked, then kill it externally.
        pm.spawn("p2", "sleep", &["10"]).await.unwrap();
        // Now manually kill the child behind ProcessManager's back
        {
            let proc = pm.processes.get_mut("p2").unwrap();
            proc.child.kill().await.unwrap();
        }

        // kill_all should still try to kill p1 and p3 (which are alive)
        // and encounter an error for p2 (already dead).
        // The important invariant: all 3 entries are removed from the manager.
        let _ = pm.kill_all().await;

        // All processes must be removed from the manager
        assert!(pm.processes.is_empty(), "kill_all must remove all entries, even if some kills failed");
    }
}
