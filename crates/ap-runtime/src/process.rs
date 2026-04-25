//! `AgentProcess`: wraps a tokio subprocess with piped stdin/stdout for IPC.
//!
//! This is a higher-level, single-process wrapper compared to ap-core's
//! `ProcessManager` (which manages a map of processes). `AgentProcess` owns
//! exactly one child process and provides lifecycle + I/O extraction.

use std::collections::HashMap;
use std::mem::ManuallyDrop;
use std::process::Stdio;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::process::{Child, Command};

// ---------------------------------------------------------------------------
// DetachedProcess — kill-on-drop wrapper for detached child processes
// ---------------------------------------------------------------------------

/// A child process that was intentionally detached from the [`AgentProcess`]
/// lifecycle but still gets killed on drop to prevent zombies.
///
/// Returned by [`AgentProcess::split`]. Callers who need to keep the process
/// running beyond the lifetime of this handle should call
/// [`DetachedProcess::forget`].
pub struct DetachedProcess {
    child: Option<Child>,
}

impl DetachedProcess {
    /// Delegates to [`Child::try_wait`].
    pub fn try_wait(&mut self) -> std::io::Result<Option<std::process::ExitStatus>> {
        self.child.as_mut().map(|c| c.try_wait()).unwrap_or(Ok(None))
    }

    /// Delegates to [`Child::kill`].
    pub async fn kill(&mut self) -> std::io::Result<()> {
        if let Some(c) = self.child.as_mut() {
            c.kill().await
        } else {
            Ok(())
        }
    }

    /// Returns the OS-assigned process ID, if available.
    pub fn id(&self) -> Option<u32> {
        self.child.as_ref().and_then(|c| c.id())
    }

    /// Consume this handle **without** killing the child process.
    ///
    /// Use this when the child is meant to outlive the current scope (e.g.
    /// a long-running agent daemon).
    pub fn forget(mut self) {
        // Prevent Drop from killing the child.
        // Must mem::forget the Child because the Command was created with
        // kill_on_drop(true) — a normal drop would kill the process.
        if let Some(child) = self.child.take() {
            std::mem::forget(child);
        }
    }
}

impl Drop for DetachedProcess {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            // Best-effort kill + reap to prevent zombies.
            if let Err(e) = child.start_kill() {
                tracing::warn!("DetachedProcess: failed to kill child on drop: {}", e);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum ProcessError {
    #[error("Failed to spawn process: {0}")]
    Spawn(std::io::Error),
    #[error("stdin was already taken")]
    NoStdin,
    #[error("stdout was already taken")]
    NoStdout,
    #[error("Failed to kill process: {0}")]
    Kill(std::io::Error),
}

impl From<ap_core::orchestration::process_manager::ProcessError> for ProcessError {
    fn from(err: ap_core::orchestration::process_manager::ProcessError) -> Self {
        use ap_core::orchestration::process_manager::ProcessError as Core;
        match err {
            Core::Spawn(e) => ProcessError::Spawn(e),
            Core::Kill(e) => ProcessError::Kill(e),
            other => ProcessError::Spawn(std::io::Error::other(other.to_string())),
        }
    }
}

// ---------------------------------------------------------------------------
// AgentProcess
// ---------------------------------------------------------------------------

pub struct AgentProcess {
    id: String,
    child: ManuallyDrop<Child>,
    stdin: Option<Box<dyn AsyncWrite + Unpin + Send>>,
    stdout: Option<Box<dyn AsyncRead + Unpin + Send>>,
}

impl AgentProcess {
    /// Spawn a new subprocess with piped stdin/stdout and inherited stderr.
    ///
    /// Marked `async` for API consistency; the underlying `Command::spawn` is
    /// synchronous but callers may use this in async contexts where a future
    /// `async` implementation is anticipated.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    #[allow(clippy::unused_async)]
    pub async fn spawn(id: &str, cmd: &str, args: &[&str]) -> Result<Self, ProcessError> {
        Self::spawn_with_env(id, cmd, args, None).await
    }

    /// Spawn a subprocess with optional environment variable overrides.
    ///
    /// When `env` is `Some`, the child process receives only the specified
    /// environment variables (not the parent's). This prevents accidental
    /// leakage of secrets like `OPENAI_API_KEY` into agent subprocesses.
    ///
    /// When `env` is `None`, inherits the full parent environment (legacy behavior).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    #[allow(clippy::unused_async)]
    pub async fn spawn_with_env(
        id: &str,
        cmd: &str,
        args: &[&str],
        env: Option<HashMap<String, String>>,
    ) -> Result<Self, ProcessError> {
        let mut command = Command::new(cmd);
        command
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);

        if let Some(env_map) = env {
            command.env_clear().envs(&env_map);
        }

        let mut child = command.spawn().map_err(ProcessError::Spawn)?;

        let stdin = child
            .stdin
            .take()
            .map(|s| -> Box<dyn AsyncWrite + Unpin + Send> { Box::new(s) })
            .ok_or(ProcessError::NoStdin)?;

        let stdout = child
            .stdout
            .take()
            .map(|s| -> Box<dyn AsyncRead + Unpin + Send> { Box::new(s) })
            .ok_or(ProcessError::NoStdout)?;

        Ok(Self {
            id: id.to_string(),
            child: ManuallyDrop::new(child),
            stdin: Some(stdin),
            stdout: Some(stdout),
        })
    }

    /// Returns the agent ID.
    #[must_use] 
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Checks if the process is still running.
    pub fn is_alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    /// Kill the process.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn kill(&mut self) -> Result<(), ProcessError> {
        self.child.kill().await.map_err(ProcessError::Kill)
    }

    /// Extract stdin and stdout as boxed trait objects, replacing them with
    /// sink/empty stubs so the process struct remains valid for lifecycle ops.
    pub fn take_io(
        &mut self,
    ) -> (
        Box<dyn AsyncWrite + Unpin + Send>,
        Box<dyn AsyncRead + Unpin + Send>,
    ) {
        let stdin = self
            .stdin
            .take()
            .unwrap_or_else(|| Box::new(tokio::io::sink()));
        let stdout = self
            .stdout
            .take()
            .unwrap_or_else(|| Box::new(tokio::io::empty()));
        (stdin, stdout)
    }

    /// Consume self, returning (id, stdin, stdout, child).
    /// stdin/stdout are sink/empty if already taken via `take_io()`.
    ///
    /// # Safety Contract
    ///
    /// The returned `Child` is NOT killed on drop. The caller is responsible for
    /// calling `child.kill()` or awaiting the child's exit to prevent zombie processes.
    /// Failure to do so will leak OS process resources.
    ///
    /// After calling `split()`, the `AgentProcess` is consumed and its `Drop` impl
    /// will NOT run — the child will NOT be killed automatically.
    #[must_use]
    pub fn split(
        mut self,
    ) -> (
        String,
        Box<dyn AsyncWrite + Unpin + Send>,
        Box<dyn AsyncRead + Unpin + Send>,
        DetachedProcess,
    ) {
        let id = std::mem::take(&mut self.id);
        let stdin = self
            .stdin
            .take()
            .unwrap_or_else(|| Box::new(tokio::io::sink()));
        let stdout = self
            .stdout
            .take()
            .unwrap_or_else(|| Box::new(tokio::io::empty()));
        // SAFETY: We immediately forget(self) after this, preventing Drop
        // from running on the now-empty ManuallyDrop slot.
        let child = unsafe { ManuallyDrop::take(&mut self.child) };
        std::mem::forget(self);
        (id, stdin, stdout, DetachedProcess { child: Some(child) })
    }
}

impl Drop for AgentProcess {
    fn drop(&mut self) {
        // Best-effort kill child process to prevent zombies.
        // start_kill() is non-async and sends SIGKILL immediately.
        // When split() was called, ManuallyDrop::take extracted the child
        // and std::mem::forget prevented this Drop from ever running.
        // So if Drop *does* run, the child is still owned and safe to kill.
        if let Err(e) = self.child.start_kill() {
            tracing::warn!("Failed to kill child process during drop: {}", e);
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn spawn_and_check_alive() {
        // `cat` with no arguments echoes stdin to stdout indefinitely.
        let mut proc = AgentProcess::spawn("test-agent", "cat", &[])
            .await
            .expect("cat should spawn");
        assert!(proc.is_alive(), "cat process should be alive immediately after spawn");
        proc.kill().await.expect("kill should succeed");
    }

    #[tokio::test]
    async fn id_returns_correct_value() {
        let mut proc = AgentProcess::spawn("my-agent-42", "cat", &[])
            .await
            .unwrap();
        assert_eq!(proc.id(), "my-agent-42");
        proc.kill().await.unwrap();
    }

    #[tokio::test]
    async fn kill_terminates_process() {
        let mut proc = AgentProcess::spawn("kill-test", "cat", &[])
            .await
            .unwrap();
        proc.kill().await.unwrap();
        // After killing, is_alive should return false.
        // Give a small moment for the signal to propagate.
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        assert!(!proc.is_alive(), "process should not be alive after kill");
    }

    #[tokio::test]
    async fn take_io_extracts_handles() {
        let mut proc = AgentProcess::spawn("io-test", "cat", &[])
            .await
            .unwrap();
        let (stdin, stdout) = proc.take_io();
        // Handles should be usable (not sink/empty defaults)
        drop(stdin);
        drop(stdout);
        // Process should still be alive and killable after take_io
        assert!(proc.is_alive());
        proc.kill().await.unwrap();
    }

    #[tokio::test]
    async fn split_consumes_self() {
        let proc = AgentProcess::spawn("split-test", "cat", &[])
            .await
            .unwrap();
        let (id, _stdin, _stdout, mut detached) = proc.split();
        assert_eq!(id, "split-test");
        // DetachedProcess should still be alive
        assert!(matches!(detached.try_wait(), Ok(None)));
        detached.kill().await.unwrap();
    }

    #[tokio::test]
    async fn take_io_then_split_uses_defaults() {
        let mut proc = AgentProcess::spawn("double-take", "cat", &[])
            .await
            .unwrap();
        let (_stdin, _stdout) = proc.take_io();
        let (id, _stdin2, _stdout2, mut detached) = proc.split();
        assert_eq!(id, "double-take");
        detached.kill().await.unwrap();
    }

    #[tokio::test]
    async fn spawn_nonexistent_command_fails() {
        let result = AgentProcess::spawn("bad", "/nonexistent/command/that/does/not/exist", &[]).await;
        assert!(result.is_err());
        let err = result.err().unwrap();
        match err {
            ProcessError::Spawn(_) => {} // expected
            other => panic!("expected Spawn error, got: {other}"),
        }
    }

    #[tokio::test]
    async fn spawn_with_env_clears_parent_env() {
        // Spawn `env` (macOS) / `printenv` with a custom env to verify isolation.
        let env_cmd = if std::path::Path::new("/usr/bin/env").exists() {
            "/usr/bin/env"
        } else {
            "/usr/bin/printenv"
        };
        let mut env = HashMap::new();
        env.insert("AGENT_ISOLATED".to_string(), "yes".to_string());
        env.insert("PATH".to_string(), std::env::var("PATH").unwrap_or_default());

        let mut proc = AgentProcess::spawn_with_env("env-test", env_cmd, &[], Some(env))
            .await
            .unwrap();

        let (stdin, stdout) = proc.take_io();
        drop(stdin);
        let mut output = Vec::new();
        use tokio::io::AsyncReadExt;
        let _ = stdout.take(4096).read_to_end(&mut output).await;
        let output_str = String::from_utf8_lossy(&output);
        assert!(output_str.contains("AGENT_ISOLATED=yes"), "Should see our env var");
        // HOME should NOT be present since we cleared parent env
        assert!(!output_str.contains("HOME="), "Parent HOME should not leak through");
        proc.kill().await.unwrap();
    }

    // --- ProcessError conversion tests ---

    use ap_core::orchestration::process_manager::ProcessError as CoreProcessError;

    #[test]
    fn from_core_spawn_maps_to_spawn() {
        let io_err = std::io::Error::new(std::io::ErrorKind::PermissionDenied, "spawn failed");
        let core = CoreProcessError::Spawn(io_err);
        let runtime: ProcessError = core.into();
        match runtime {
            ProcessError::Spawn(e) => assert_eq!(e.kind(), std::io::ErrorKind::PermissionDenied),
            other => panic!("expected Spawn, got: {other}"),
        }
    }

    #[test]
    fn from_core_kill_maps_to_kill() {
        let io_err = std::io::Error::new(std::io::ErrorKind::BrokenPipe, "kill failed");
        let core = CoreProcessError::Kill(io_err);
        let runtime: ProcessError = core.into();
        match runtime {
            ProcessError::Kill(e) => assert_eq!(e.kind(), std::io::ErrorKind::BrokenPipe),
            other => panic!("expected Kill, got: {other}"),
        }
    }

    #[test]
    fn from_core_max_concurrent_maps_to_spawn_with_message() {
        let core = CoreProcessError::MaxConcurrent(42);
        let runtime: ProcessError = core.into();
        match runtime {
            ProcessError::Spawn(e) => {
                let msg = e.to_string();
                assert!(
                    msg.contains("Max concurrent"),
                    "Expected MaxConcurrent error message, got: {msg}"
                );
            }
            other => panic!("expected Spawn (from MaxConcurrent), got: {other}"),
        }
    }

    #[test]
    fn from_core_not_found_maps_to_spawn_with_message() {
        let core = CoreProcessError::NotFound("agent-xyz".to_string());
        let runtime: ProcessError = core.into();
        match runtime {
            ProcessError::Spawn(e) => {
                let msg = e.to_string();
                assert!(
                    msg.contains("agent-xyz"),
                    "Expected NotFound error message containing 'agent-xyz', got: {msg}"
                );
            }
            other => panic!("expected Spawn (from NotFound), got: {other}"),
        }
    }

    #[test]
    fn from_core_shutdown_timeout_maps_to_spawn_with_message() {
        let core = CoreProcessError::ShutdownTimeout;
        let runtime: ProcessError = core.into();
        match runtime {
            ProcessError::Spawn(e) => {
                let msg = e.to_string();
                assert!(
                    msg.contains("Graceful shutdown timed out"),
                    "Expected ShutdownTimeout error message, got: {msg}"
                );
            }
            other => panic!("expected Spawn (from ShutdownTimeout), got: {other}"),
        }
    }
}
