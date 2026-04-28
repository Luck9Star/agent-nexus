//! Hook system models: `HookType`, `HookEvent`, `HookDefinition`, `HookExecution`.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::models::common::utc_now;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HookType {
    Command,
    Http,
    Prompt,
    Agent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HookEvent {
    PreExecution,
    PostExecution,
    PreToolUse,
    PostToolUse,
    OnError,
    OnEvolution,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HookDefinition {
    #[serde(rename = "type")]
    pub hook_type: HookType,
    pub event: HookEvent,
    #[serde(default)]
    pub config: serde_json::Value,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub block_on_failure: bool,
    #[serde(default = "default_hook_timeout")]
    pub timeout_seconds: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub matcher: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompt: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

fn default_true() -> bool { true }
fn default_hook_timeout() -> f64 { 10.0 }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HookExecution {
    pub hook: HookDefinition,
    pub passed: bool,
    #[serde(default)]
    pub blocked: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_type: Option<String>,
    #[serde(default)]
    pub duration_ms: f64,
    #[serde(default = "utc_now")]
    pub executed_at: DateTime<Utc>,
}

impl HookExecution {
    /// Validate: cannot be both passed and blocked.
    /// Python source: models/hooks.py `_validate_passed_blocked`
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn validate(&self) -> Result<(), String> {
        if self.passed && self.blocked {
            return Err("HookExecution cannot be both passed and blocked".into());
        }
        Ok(())
    }
}

/// Aggregated result of all hook executions for a single event.
///
/// Python source: models/hooks.py `AggregatedHookResult`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AggregatedHookResult {
    pub event: HookEvent,
    #[serde(default)]
    pub results: Vec<HookExecution>,
    #[serde(default)]
    pub blocked: bool,
    #[serde(default)]
    pub errors: Vec<String>,
}

impl HookDefinition {
    /// Validate the hook definition.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn validate(&self) -> Result<(), String> {
        if !self.enabled {
            return Ok(());
        }
        match self.hook_type {
            HookType::Command => {
                if self.command.as_ref().is_none_or(|c| c.trim().is_empty()) {
                    return Err("Command hook requires a non-empty 'command' field".into());
                }
            }
            HookType::Http => {
                if self.url.as_ref().is_none_or(|u| u.trim().is_empty()) {
                    return Err("HTTP hook requires a non-empty 'url' field".into());
                }
            }
            HookType::Prompt => {
                if self.prompt.as_ref().is_none_or(|p| p.trim().is_empty()) {
                    return Err("Prompt hook requires a non-empty 'prompt' field".into());
                }
            }
            HookType::Agent => {}
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hook_execution_cannot_be_passed_and_blocked() {
        let hook = HookDefinition {
            hook_type: HookType::Command,
            event: HookEvent::PreExecution,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: Some("echo hi".into()),
            url: None,
            prompt: None,
            model: None,
        };
        let exec = HookExecution {
            hook,
            passed: true,
            blocked: true,
            output: None,
            error: None,
            error_type: None,
            duration_ms: 0.0,
            executed_at: utc_now(),
        };
        assert!(exec.validate().is_err());
    }

    #[test]
    fn command_hook_requires_command() {
        let hook = HookDefinition {
            hook_type: HookType::Command,
            event: HookEvent::PreExecution,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: None,
            url: None,
            prompt: None,
            model: None,
        };
        assert!(hook.validate().is_err());
    }
}
