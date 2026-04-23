//! AgentProcess: wraps a tokio subprocess with piped stdin/stdout for IPC.
//!
//! This is a higher-level, single-process wrapper compared to ap-core's
//! ProcessManager (which manages a map of processes). AgentProcess owns
//! exactly one child process and provides lifecycle + I/O extraction.

use std::mem::ManuallyDrop;
use std::process::Stdio;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::process::{Child, Command};

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
    pub async fn spawn(id: &str, cmd: &str, args: &[&str]) -> Result<Self, ProcessError> {
        let mut child = Command::new(cmd)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(ProcessError::Spawn)?;

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
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Checks if the process is still running.
    pub fn is_alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    /// Kill the process.
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
    pub fn split(
        mut self,
    ) -> (
        String,
        Box<dyn AsyncWrite + Unpin + Send>,
        Box<dyn AsyncRead + Unpin + Send>,
        Child,
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
        (id, stdin, stdout, child)
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
        let (id, _stdin, _stdout, mut child) = proc.split();
        assert_eq!(id, "split-test");
        // Child is still alive, we own it now
        assert!(matches!(child.try_wait(), Ok(None)));
        child.kill().await.unwrap();
    }

    #[tokio::test]
    async fn take_io_then_split_uses_defaults() {
        let mut proc = AgentProcess::spawn("double-take", "cat", &[])
            .await
            .unwrap();
        let (_stdin, _stdout) = proc.take_io();
        let (id, _stdin2, _stdout2, mut child) = proc.split();
        assert_eq!(id, "double-take");
        child.kill().await.unwrap();
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
}
