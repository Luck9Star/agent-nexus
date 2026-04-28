//! Core types for CLI backend integration.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::Duration;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CLIResult {
    pub text: String,
    pub model: String,
    pub session_id: Option<String>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub raw_stdout: String,
    pub raw_stderr: String,
    pub returncode: i32,
    #[serde(skip)]
    pub duration: Duration,
    pub parse_error: bool,
}

impl Default for CLIResult {
    fn default() -> Self {
        Self {
            text: String::new(),
            model: String::new(),
            session_id: None,
            input_tokens: None,
            output_tokens: None,
            raw_stdout: String::new(),
            raw_stderr: String::new(),
            returncode: 0,
            duration: Duration::ZERO,
            parse_error: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct JsonPathConfig {
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub input_tokens: Option<String>,
    #[serde(default)]
    pub output_tokens: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct TextPatternConfig {
    pub session_id: Option<String>,
    pub model: Option<String>,
}

fn _default_args() -> Vec<String> {
    Vec::new()
}

fn default_system_prompt_flag() -> String {
    "--system-prompt".to_string()
}

fn default_session_flag() -> String {
    "--resume".to_string()
}

fn default_output_format() -> String {
    "json".to_string()
}

fn default_timeout() -> u64 {
    180
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BackendConfig {
    #[serde(default)]
    pub command: String,
    #[serde(default = "_default_args")]
    pub args: Vec<String>,
    #[serde(default = "default_system_prompt_flag")]
    pub system_prompt_flag: String,
    #[serde(default = "default_session_flag")]
    pub session_flag: String,
    #[serde(default = "default_output_format")]
    pub output_format: String,
    #[serde(default)]
    pub output_format_flag: String,
    #[serde(default)]
    pub json_paths: JsonPathConfig,
    #[serde(default)]
    pub text_patterns: TextPatternConfig,
    #[serde(default)]
    pub model_map: HashMap<String, String>,
    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,
}

impl Default for BackendConfig {
    fn default() -> Self {
        Self {
            command: String::new(),
            args: Vec::new(),
            system_prompt_flag: default_system_prompt_flag(),
            session_flag: default_session_flag(),
            output_format: default_output_format(),
            output_format_flag: String::new(),
            json_paths: JsonPathConfig::default(),
            text_patterns: TextPatternConfig::default(),
            model_map: HashMap::new(),
            timeout_secs: default_timeout(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RoutingConfig {
    pub default: String,
    #[serde(default)]
    pub fallback_chain: Vec<String>,
    #[serde(default = "default_true")]
    pub fallback_enabled: bool,
    #[serde(default)]
    pub model_rules: HashMap<String, String>,
}

fn default_true() -> bool {
    true
}

impl Default for RoutingConfig {
    fn default() -> Self {
        Self {
            default: String::new(),
            fallback_chain: vec![],
            fallback_enabled: true,
            model_rules: HashMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct DataLifecycleConfig {
    #[serde(default = "default_hot_days")]
    pub hot_days: u32,
    #[serde(default = "default_warm_days")]
    pub warm_days: u32,
    #[serde(default)]
    pub archive_dir: String,
    #[serde(default = "default_true")]
    pub auto_archive: bool,
}

fn default_hot_days() -> u32 {
    30
}

fn default_warm_days() -> u32 {
    90
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct CLISession {
    pub session_id: String,
    pub name: Option<String>,
    pub backend_name: String,
    pub model: Option<String>,
    pub task_id: Option<String>,
    pub created_at: String,
    pub last_used_at: String,
    pub turn_count: u32,
    pub metadata: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum CLIBackendError {
    #[error("Command '{command}' timed out after {timeout_secs}s")]
    Timeout { command: String, timeout_secs: u64 },

    #[error("Command '{command}' exited with code {code}: {stderr}")]
    ExitError { command: String, code: i32, stderr: String },

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Database error: {0}")]
    Database(#[from] rusqlite::Error),

    #[error("Backend not installed: {0}")]
    NotInstalled(String),

    #[error("No available backend found")]
    NoAvailableBackend,

    #[error("All backends in fallback chain are unavailable")]
    AllBackendsUnavailable,

    #[error("JSON parse error: {0}")]
    JsonParse(String),
}
