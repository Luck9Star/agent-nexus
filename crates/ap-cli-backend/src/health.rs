//! Health check — binary existence and version verification.

use crate::types::{BackendConfig, CLIBackendError};
use std::process::Command;

pub struct HealthCheck;

impl HealthCheck {
    pub fn check_installed(config: &BackendConfig) -> bool {
        which::which(&config.command).is_ok()
    }

    pub fn check_version(config: &BackendConfig) -> Result<String, CLIBackendError> {
        let output = Command::new(&config.command)
            .arg("--version")
            .output()
            .map_err(CLIBackendError::Io)?;

        if !output.status.success() {
            return Err(CLIBackendError::ExitError {
                command: config.command.clone(),
                code: output.status.code().unwrap_or(-1),
                stderr: String::from_utf8_lossy(&output.stderr).to_string(),
            });
        }

        let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
        Ok(version)
    }
}
