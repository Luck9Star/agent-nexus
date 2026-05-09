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

#[cfg(test)]
mod tests {
    use super::*;

    fn echo_config() -> BackendConfig {
        BackendConfig {
            command: "echo".into(),
            ..Default::default()
        }
    }

    fn nonexistent_config() -> BackendConfig {
        BackendConfig {
            command: "definitely_not_installed_abcxyz".into(),
            ..Default::default()
        }
    }

    #[test]
    fn check_installed_echo() {
        assert!(HealthCheck::check_installed(&echo_config()));
    }

    #[test]
    fn check_not_installed() {
        assert!(!HealthCheck::check_installed(&nonexistent_config()));
    }

    #[test]
    fn check_version_echo() {
        let result = HealthCheck::check_version(&echo_config());
        // `echo --version` may succeed or fail depending on platform
        // On macOS, echo is a builtin but also /bin/echo exists
        // Just verify it doesn't panic
        let _ = result;
    }

    #[test]
    fn check_version_nonexistent() {
        let result = HealthCheck::check_version(&nonexistent_config());
        assert!(result.is_err());
    }
}
