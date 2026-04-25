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

/// Validate a PEP 508 requirement string to prevent injection.
///
/// Rejects:
/// - Flags (starting with `-`)
/// - URL schemes (`http://`, `https://`, `git+`, `ftp://`, etc.)
/// - Whitespace/control characters
/// - Embedded pip options after `;`
fn validate_requirement(req: &str) -> Result<(), UvError> {
    if req.starts_with('-') {
        return Err(UvError::CommandFailed(
            format!("Invalid requirement (starts with '-'): {req}")
        ));
    }
    if req.contains(|c: char| c.is_control() || c == ' ' || c == '\t') {
        return Err(UvError::CommandFailed(
            format!("Invalid requirement (contains whitespace/control chars): {req:?}")
        ));
    }
    // Reject URL-based requirements (package @ git+https://..., direct URLs)
    let lower = req.to_ascii_lowercase();
    let url_schemes = ["http://", "https://", "git+", "ftp://", "file://"];
    if url_schemes.iter().any(|s| lower.contains(s)) {
        return Err(UvError::CommandFailed(
            format!("Invalid requirement (URL scheme not allowed): {req}")
        ));
    }
    // Reject embedded pip options after semicolon
    if let Some(idx) = req.find(';') {
        let after = &req[idx + 1..];
        // Allow environment markers (e.g. ; python_version >= "3.8") but reject flags
        if after.split(',').any(|part| part.trim().starts_with('-')) {
            return Err(UvError::CommandFailed(
                format!("Invalid requirement (embedded option after ';'): {req}")
            ));
        }
    }
    Ok(())
}

/// Bridges to the `uv` Python package manager via subprocess calls.
#[derive(Debug)]
pub struct UvBridge {
    uv_path: String,
    resolved: tokio::sync::OnceCell<String>,
}

impl UvBridge {
    /// Create a new `UvBridge` using the default `uv` binary name.
    #[must_use]
    pub fn new() -> Self {
        Self {
            uv_path: "uv".to_string(),
            resolved: tokio::sync::OnceCell::new(),
        }
    }

    /// Builder: specify a custom path to the `uv` binary.
    #[must_use]
    pub fn with_path(self, path: impl Into<String>) -> Self {
        Self {
            uv_path: path.into(),
            resolved: tokio::sync::OnceCell::new(),
        }
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
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn create_venv(&self, path: &Path) -> Result<(), UvError> {
        let uv = self.resolved_path().await?;
        let output = Command::new(&uv)
            .arg("venv")
            .arg(path)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .map_err(|e| UvError::CommandFailed(format!("failed to run uv venv: {e}")))?;

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
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn pip_install(&self, venv_python: &Path, requirements: &[&str]) -> Result<(), UvError> {
        // Validate requirements BEFORE resolving the uv binary so that input
        // validation errors are caught regardless of whether uv is installed.
        for req in requirements {
            validate_requirement(req)?;
        }

        let uv = self.resolved_path().await?;
        let mut cmd = Command::new(&uv);
        cmd.arg("pip")
            .arg("install")
            .arg("--python")
            .arg(venv_python);

        for req in requirements {
            cmd.arg(req);
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        let output = cmd.output().await.map_err(|e| {
            UvError::CommandFailed(format!("failed to run uv pip install: {e}"))
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
                names.push(format!("uv{ver}"));
            }
        }
        names
    }

    /// Resolve the path to a working `uv` binary.
    ///
    /// Uses `OnceCell` for exactly-once initialization — concurrent callers
    /// all share the same probe, preventing N redundant `uv --version` spawns.
    async fn resolved_path(&self) -> Result<String, UvError> {
        self.resolved
            .get_or_try_init(|| async {
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
                            debug!("Resolved uv binary: {}", candidate);
                            return Ok(candidate.clone());
                        }
                    }
                }
                Err(UvError::CommandFailed(
                    format!("uv binary not found on system (tried: {:?})", candidates),
                ))
            })
            .await
            .cloned()
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

    #[tokio::test]
    async fn pip_install_rejects_url_requirement() {
        let bridge = UvBridge::new().with_path("/nonexistent/uv");
        let dir = tempfile::tempdir().unwrap();
        let python = dir.path().join("python");

        // No whitespace so it passes whitespace check, but contains git+ URL
        let result = bridge
            .pip_install(&python, &["pkg@git+https://evil.com/repo"])
            .await;
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(
            err_msg.contains("URL scheme"),
            "Expected URL rejection, got: {err_msg}"
        );
    }

    #[tokio::test]
    async fn pip_install_rejects_whitespace_requirement() {
        let bridge = UvBridge::new().with_path("/nonexistent/uv");
        let dir = tempfile::tempdir().unwrap();
        let python = dir.path().join("python");

        let result = bridge
            .pip_install(&python, &["requests >= 1.0 --extra-index-url https://evil.com"])
            .await;
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(
            err_msg.contains("whitespace"),
            "Expected whitespace rejection, got: {err_msg}"
        );
    }

    #[tokio::test]
    async fn pip_install_accepts_valid_pep508() {
        // These should pass validation (though pip install itself will fail
        // since the uv binary doesn't exist).
        let bridge = UvBridge::new().with_path("/nonexistent/uv");
        let dir = tempfile::tempdir().unwrap();
        let python = dir.path().join("python");

        // Valid requirements should not be rejected by our validation
        let result = bridge.pip_install(&python, &["requests>=2.0"]).await;
        // Will fail because uv doesn't exist, but NOT because of validation
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(
            !err_msg.contains("Invalid requirement"),
            "Valid PEP 508 should pass validation, got: {err_msg}"
        );
    }
}
