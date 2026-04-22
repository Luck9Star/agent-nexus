//! UV bridge — subprocess interface to the `uv` Python package manager.

use std::path::Path;
use std::process::Stdio;

use thiserror::Error;
use tokio::process::Command;
use tracing::{debug, warn};

/// Errors from UV bridge operations.
#[derive(Debug, Error)]
pub enum UvError {
    #[error("command failed: {0}")]
    CommandFailed(String),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

/// Bridges to the `uv` Python package manager via subprocess calls.
#[derive(Debug, Clone)]
pub struct UvBridge {
    uv_path: String,
}

impl UvBridge {
    /// Create a new UvBridge using the default `uv` binary name.
    pub fn new() -> Self {
        Self {
            uv_path: "uv".to_string(),
        }
    }

    /// Builder: specify a custom path to the `uv` binary.
    pub fn with_path(mut self, path: impl Into<String>) -> Self {
        self.uv_path = path.into();
        self
    }

    /// Check if `uv` is available by running `uv --version`.
    pub async fn check_available(&self) -> bool {
        self.detect_uv().await
    }

    /// Detect whether `uv` is available on the system.
    ///
    /// Checks `self.uv_path` first, then falls back to common names like
    /// `uv3`, `uv3.12`, etc.
    pub async fn detect_uv(&self) -> bool {
        let candidates = self.candidates();
        for candidate in &candidates {
            debug!("Checking for uv at: {}", candidate);
            match Command::new(candidate)
                .arg("--version")
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .status()
                .await
            {
                Ok(status) if status.success() => {
                    debug!("Found uv at: {}", candidate);
                    return true;
                }
                Ok(status) => {
                    debug!("uv at {} exited with: {}", candidate, status);
                }
                Err(e) => {
                    debug!("uv at {} not found: {}", candidate, e);
                }
            }
        }
        warn!("uv not found in any of: {:?}", candidates);
        false
    }

    /// Create a virtual environment at the given path using `uv venv`.
    pub async fn create_venv(&self, path: &Path) -> Result<(), UvError> {
        let uv = self.resolved_path().await?;
        let output = Command::new(&uv)
            .arg("venv")
            .arg(path)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .map_err(|e| UvError::CommandFailed(format!("failed to run uv venv: {}", e)))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(UvError::CommandFailed(format!(
                "uv venv failed (exit {}): {}",
                output.status.code().unwrap_or(-1),
                stderr.trim()
            )));
        }

        debug!("Created venv at {:?}", path);
        Ok(())
    }

    /// Install Python packages into a virtual environment.
    ///
    /// `venv_python` is the path to the Python binary in the venv.
    /// `requirements` is a list of package specifiers (e.g. `["requests>=2.0", "flask"]`).
    pub async fn pip_install(&self, venv_python: &Path, requirements: &[&str]) -> Result<(), UvError> {
        let uv = self.resolved_path().await?;
        let mut cmd = Command::new(&uv);
        cmd.arg("pip")
            .arg("install")
            .arg("--python")
            .arg(venv_python);

        for req in requirements {
            if req.starts_with('-') {
                return Err(UvError::CommandFailed(
                    format!("Invalid requirement (starts with '-'): {}", req)
                ));
            }
            cmd.arg(req);
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        let output = cmd.output().await.map_err(|e| {
            UvError::CommandFailed(format!("failed to run uv pip install: {}", e))
        })?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(UvError::CommandFailed(format!(
                "uv pip install failed (exit {}): {}",
                output.status.code().unwrap_or(-1),
                stderr.trim()
            )));
        }

        debug!(
            "Installed {} packages into {:?}",
            requirements.len(),
            venv_python
        );
        Ok(())
    }

    /// Returns the list of candidate binary names to search for.
    fn candidates(&self) -> Vec<String> {
        let mut names = vec![self.uv_path.clone()];
        if self.uv_path == "uv" {
            // Add common variant names
            for ver in &["3", "3.12", "3.11", "3.10"] {
                names.push(format!("uv{}", ver));
            }
        }
        names
    }

    /// Resolve the path to a working `uv` binary.
    ///
    /// Returns the first candidate that is available, or the configured path
    /// if none is found (the command will fail at execution time).
    async fn resolved_path(&self) -> Result<String, UvError> {
        let candidates = self.candidates();
        for candidate in &candidates {
            if let Ok(status) = Command::new(candidate)
                .arg("--version")
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .status()
                .await
            {
                if status.success() {
                    return Ok(candidate.clone());
                }
            }
        }
        // Fall back to configured path; the actual command will produce a clear error
        Ok(self.uv_path.clone())
    }
}

impl Default for UvBridge {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn check_available_if_uv_installed() {
        let bridge = UvBridge::new();
        // This test passes whether uv is installed or not — we just verify no panic
        let _available = bridge.check_available().await;
    }

    #[tokio::test]
    async fn check_available_with_nonexistent_path() {
        let bridge = UvBridge::new().with_path("/nonexistent/path/to/uv");
        let available = bridge.check_available().await;
        assert!(!available);
    }

    #[tokio::test]
    async fn create_venv_with_tempdir() {
        let bridge = UvBridge::new();
        if !bridge.check_available().await {
            eprintln!("Skipping create_venv test: uv not available");
            return;
        }

        let dir = tempfile::tempdir().unwrap();
        let venv_path = dir.path().join(".venv");
        bridge.create_venv(&venv_path).await.unwrap();

        // Verify the venv directory was created
        assert!(venv_path.exists());

        // Verify the Python binary exists (platform-specific)
        #[cfg(target_os = "macos")]
        {
            let python = venv_path.join("bin").join("python");
            assert!(python.exists());
        }
        #[cfg(target_os = "linux")]
        {
            let python = venv_path.join("bin").join("python");
            assert!(python.exists());
        }
    }

    #[tokio::test]
    async fn pip_install_with_tempdir() {
        let bridge = UvBridge::new();
        if !bridge.check_available().await {
            eprintln!("Skipping pip_install test: uv not available");
            return;
        }

        let dir = tempfile::tempdir().unwrap();
        let venv_path = dir.path().join(".venv");
        bridge.create_venv(&venv_path).await.unwrap();

        #[cfg(target_os = "macos")]
        let python = venv_path.join("bin").join("python");
        #[cfg(target_os = "linux")]
        let python = venv_path.join("bin").join("python");
        #[cfg(target_os = "windows")]
        let python = venv_path.join("Scripts").join("python.exe");

        // Install a tiny, well-known package
        bridge
            .pip_install(&python, &["pip"])
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn create_venv_nonexistent_uv_errors() {
        let bridge = UvBridge::new().with_path("/nonexistent/uv");
        let dir = tempfile::tempdir().unwrap();
        let venv_path = dir.path().join(".venv");

        let result = bridge.create_venv(&venv_path).await;
        assert!(result.is_err());
    }

    #[test]
    fn candidates_includes_variants() {
        let bridge = UvBridge::new();
        let cands = bridge.candidates();
        assert!(cands.contains(&"uv".to_string()));
        assert!(cands.contains(&"uv3".to_string()));
        assert!(cands.contains(&"uv3.12".to_string()));
    }

    #[test]
    fn candidates_custom_path_no_variants() {
        let bridge = UvBridge::new().with_path("/custom/uv");
        let cands = bridge.candidates();
        assert_eq!(cands.len(), 1);
        assert_eq!(cands[0], "/custom/uv");
    }

    #[test]
    fn default_is_uv() {
        let bridge = UvBridge::default();
        assert_eq!(bridge.uv_path, "uv");
    }

    #[tokio::test]
    async fn pip_install_rejects_dash_prefixed_requirement() {
        let bridge = UvBridge::new().with_path("/nonexistent/uv");
        let dir = tempfile::tempdir().unwrap();
        let python = dir.path().join("python");

        let result = bridge.pip_install(&python, &["--inject", "malicious"]).await;
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(
            err_msg.contains("Invalid requirement"),
            "Expected validation error, got: {err_msg}"
        );
    }
}
