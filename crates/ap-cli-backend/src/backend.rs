//! GenericCLIBackend — config-driven CLI subprocess invocation.

use crate::parser::{parse_json_output, parse_text_output};
use crate::types::{BackendConfig, CLIBackendError, CLIResult};
use std::time::{Duration, Instant};

pub struct GenericCLIBackend {
    config: BackendConfig,
    available: bool,
}

impl GenericCLIBackend {
    pub fn new(config: BackendConfig) -> Self {
        let available = which::which(&config.command).is_ok();
        Self { config, available }
    }

    pub fn name(&self) -> &str {
        &self.config.command
    }

    pub fn is_available(&self) -> bool {
        self.available
    }

    pub fn refresh_availability(&mut self) {
        self.available = which::which(&self.config.command).is_ok();
    }

    pub fn config(&self) -> &BackendConfig {
        &self.config
    }

    pub fn build_args(
        &self,
        system_prompt: &str,
        user_message: &str,
        session_id: Option<&str>,
    ) -> Vec<String> {
        let mut args = self.config.args.clone();

        if !self.config.system_prompt_flag.is_empty() && !system_prompt.is_empty() {
            args.push(self.config.system_prompt_flag.clone());
            args.push(system_prompt.to_string());
        }

        if !self.config.output_format_flag.is_empty() && !self.config.output_format.is_empty() {
            args.push(self.config.output_format_flag.clone());
            args.push(self.config.output_format.clone());
        }

        if let Some(sid) = session_id {
            if !self.config.session_flag.is_empty() {
                args.push(self.config.session_flag.clone());
                args.push(sid.to_string());
            }
        }

        args.push(user_message.to_string());
        args
    }

    pub async fn call(
        &self,
        system_prompt: &str,
        user_message: &str,
        session_id: Option<&str>,
    ) -> Result<CLIResult, CLIBackendError> {
        let args = self.build_args(system_prompt, user_message, session_id);
        let start = Instant::now();

        let output = tokio::time::timeout(
            Duration::from_secs(self.config.timeout_secs),
            tokio::process::Command::new(&self.config.command)
                .args(&args)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .kill_on_drop(true)
                .output(),
        )
        .await
        .map_err(|_| CLIBackendError::Timeout {
            command: self.config.command.clone(),
            timeout_secs: self.config.timeout_secs,
        })??;

        let duration = start.elapsed();
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();

        if !output.status.success() {
            return Err(CLIBackendError::ExitError {
                command: self.config.command.clone(),
                code: output.status.code().unwrap_or(-1),
                stderr: stderr.chars().take(500).collect(),
            });
        }

        let mut result = if self.config.output_format == "json" {
            let r = parse_json_output(&stdout, &self.config);
            if r.parse_error {
                tracing::warn!("JSON parse failed for '{}', used raw text", self.config.command);
            }
            r
        } else {
            parse_text_output(&stdout, &stderr, &self.config)
        };

        result.duration = duration;
        result.raw_stdout = stdout;
        result.raw_stderr = stderr;
        result.returncode = 0;

        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_config() -> BackendConfig {
        BackendConfig {
            command: "echo".into(),
            args: vec![],
            system_prompt_flag: "--system".into(),
            session_flag: "--resume".into(),
            output_format: "text".into(),
            output_format_flag: String::new(),
            json_paths: Default::default(),
            text_patterns: Default::default(),
            model_map: Default::default(),
            timeout_secs: 10,
        }
    }

    #[test]
    fn build_args_basic() {
        let backend = GenericCLIBackend::new(test_config());
        let args = backend.build_args("sys prompt", "user msg", None);
        assert!(args.contains(&"--system".to_string()));
        assert!(args.contains(&"sys prompt".to_string()));
        assert!(args.contains(&"user msg".to_string()));
    }

    #[test]
    fn build_args_with_session() {
        let backend = GenericCLIBackend::new(test_config());
        let args = backend.build_args("sys", "user", Some("sess-123"));
        assert!(args.contains(&"--resume".to_string()));
        assert!(args.contains(&"sess-123".to_string()));
    }

    #[tokio::test]
    async fn call_echo_command() {
        let config = BackendConfig {
            command: "echo".into(),
            output_format: "text".into(),
            ..test_config()
        };
        let backend = GenericCLIBackend::new(config);
        let result = backend.call("sys", "hello world", None).await;
        assert!(result.is_ok());
        let r = result.unwrap();
        assert!(r.text.contains("hello world"));
        assert_eq!(r.returncode, 0);
    }

    #[tokio::test]
    async fn call_nonexistent_command() {
        let mut config = test_config();
        config.command = "definitely_not_a_real_command_xyz".into();
        config.timeout_secs = 2;
        let backend = GenericCLIBackend::new(config);
        let result = backend.call("sys", "msg", None).await;
        assert!(result.is_err());
    }
}
