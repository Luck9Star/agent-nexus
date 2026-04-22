//! HookExecutor — registers and runs hooks, dispatching on HookType.

use std::collections::HashMap;
use std::time::Instant;

use crate::models::common::utc_now;
use crate::models::hooks::{
    AggregatedHookResult, HookDefinition, HookEvent, HookExecution, HookType,
};

/// Dispatches and executes hooks keyed by event.
#[derive(Debug, Clone)]
pub struct HookExecutor {
    hooks: HashMap<HookEvent, Vec<HookDefinition>>,
}

impl Default for HookExecutor {
    fn default() -> Self {
        Self::new()
    }
}

impl HookExecutor {
    /// Create an empty executor.
    pub fn new() -> Self {
        Self {
            hooks: HashMap::new(),
        }
    }

    /// Register a hook. Disabled hooks are silently skipped.
    pub fn register(&mut self, hook: HookDefinition) {
        if !hook.enabled {
            return;
        }
        self.hooks.entry(hook.event).or_default().push(hook);
    }

    /// Execute a single hook, dispatching on its hook_type.
    pub async fn execute(&self, hook: &HookDefinition) -> HookExecution {
        let start = Instant::now();
        let duration_ms = start.elapsed().as_secs_f64() * 1000.0;

        match hook.hook_type {
            HookType::Command => self.execute_command(hook, duration_ms).await,
            HookType::Http => self.execute_http_placeholder(hook, duration_ms),
            HookType::Prompt => self.execute_prompt_placeholder(hook, duration_ms),
            HookType::Agent => self.execute_agent_placeholder(hook, duration_ms),
        }
    }

    /// Run all hooks registered for `event`, returning an aggregated result.
    pub async fn run_all(&self, event: HookEvent) -> AggregatedHookResult {
        let hooks = self.hooks.get(&event).cloned().unwrap_or_default();
        let mut results = Vec::with_capacity(hooks.len());
        let mut errors = Vec::new();
        let mut blocked = false;

        for hook in &hooks {
            let exec = self.execute(hook).await;
            if exec.blocked {
                blocked = true;
            }
            if let Some(ref err) = exec.error {
                errors.push(err.clone());
            }
            results.push(exec);
        }

        AggregatedHookResult {
            event,
            results,
            blocked,
            errors,
        }
    }

    // ---- dispatch implementations ----

    async fn execute_command(&self, hook: &HookDefinition, start_elapsed: f64) -> HookExecution {
        let cmd_str = match hook.command.as_deref() {
            Some(c) if !c.trim().is_empty() => c,
            _ => {
                return HookExecution {
                    hook: hook.clone(),
                    passed: false,
                    blocked: hook.block_on_failure,
                    output: None,
                    error: Some("no command specified".into()),
                    error_type: Some("configuration".into()),
                    duration_ms: start_elapsed,
                    executed_at: utc_now(),
                };
            }
        };

        // Split command into program + args (simple shell-like split)
        let parts = shlex_split(cmd_str);
        let (program, args) = match parts.split_first() {
            Some((p, a)) => (p.as_str(), a),
            None => {
                return HookExecution {
                    hook: hook.clone(),
                    passed: false,
                    blocked: hook.block_on_failure,
                    output: None,
                    error: Some("empty command".into()),
                    error_type: Some("configuration".into()),
                    duration_ms: start_elapsed,
                    executed_at: utc_now(),
                };
            }
        };

        let start = Instant::now();
        let result = tokio::process::Command::new(program)
            .args(args)
            .output()
            .await;

        let duration_ms = start.elapsed().as_secs_f64() * 1000.0;

        match result {
            Ok(output) => {
                let passed = output.status.success();
                let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                let stderr = String::from_utf8_lossy(&output.stderr).to_string();

                let (output_str, error) = if passed {
                    (if stdout.is_empty() { None } else { Some(stdout) }, None)
                } else {
                    let err = if stderr.is_empty() {
                        format!("exit code: {}", output.status.code().unwrap_or(-1))
                    } else {
                        stderr
                    };
                    (if stdout.is_empty() { None } else { Some(stdout) }, Some(err))
                };

                HookExecution {
                    hook: hook.clone(),
                    passed,
                    blocked: !passed && hook.block_on_failure,
                    output: output_str,
                    error,
                    error_type: if !passed { Some("command_failed".into()) } else { None },
                    duration_ms,
                    executed_at: utc_now(),
                }
            }
            Err(e) => HookExecution {
                hook: hook.clone(),
                passed: false,
                blocked: hook.block_on_failure,
                output: None,
                error: Some(e.to_string()),
                error_type: Some("spawn_failed".into()),
                duration_ms,
                executed_at: utc_now(),
            },
        }
    }

    fn execute_http_placeholder(&self, hook: &HookDefinition, duration_ms: f64) -> HookExecution {
        // Placeholder: just verify URL exists
        let passed = hook.url.as_ref().is_some_and(|u| !u.trim().is_empty());
        HookExecution {
            hook: hook.clone(),
            passed,
            blocked: !passed && hook.block_on_failure,
            output: if passed { Some("http placeholder ok".into()) } else { None },
            error: if passed { None } else { Some("no url configured".into()) },
            error_type: if passed { None } else { Some("configuration".into()) },
            duration_ms,
            executed_at: utc_now(),
        }
    }

    fn execute_prompt_placeholder(&self, hook: &HookDefinition, duration_ms: f64) -> HookExecution {
        let passed = hook.prompt.as_ref().is_some_and(|p| !p.trim().is_empty());
        HookExecution {
            hook: hook.clone(),
            passed,
            blocked: !passed && hook.block_on_failure,
            output: if passed { Some("prompt placeholder ok".into()) } else { None },
            error: if passed { None } else { Some("no prompt configured".into()) },
            error_type: if passed { None } else { Some("configuration".into()) },
            duration_ms,
            executed_at: utc_now(),
        }
    }

    fn execute_agent_placeholder(&self, hook: &HookDefinition, duration_ms: f64) -> HookExecution {
        HookExecution {
            hook: hook.clone(),
            passed: true,
            blocked: false,
            output: Some("agent placeholder ok".into()),
            error: None,
            error_type: None,
            duration_ms,
            executed_at: utc_now(),
        }
    }
}

/// Minimal shlex-like split: splits on whitespace, respects double quotes.
fn shlex_split(s: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut in_quotes = false;
    let chars = s.chars();

    for ch in chars {
        match ch {
            '"' => in_quotes = !in_quotes,
            ' ' | '\t' if !in_quotes => {
                if !current.is_empty() {
                    tokens.push(std::mem::take(&mut current));
                }
            }
            _ => current.push(ch),
        }
    }
    if !current.is_empty() {
        tokens.push(current);
    }
    tokens
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cmd_hook(event: HookEvent, command: &str) -> HookDefinition {
        HookDefinition {
            hook_type: HookType::Command,
            event,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: Some(command.into()),
            url: None,
            prompt: None,
            model: None,
        }
    }

    fn cmd_hook_blocking(event: HookEvent, command: &str) -> HookDefinition {
        let mut h = cmd_hook(event, command);
        h.block_on_failure = true;
        h
    }

    fn http_hook(event: HookEvent, url: &str) -> HookDefinition {
        HookDefinition {
            hook_type: HookType::Http,
            event,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: None,
            url: Some(url.into()),
            prompt: None,
            model: None,
        }
    }

    fn prompt_hook(event: HookEvent, prompt: &str) -> HookDefinition {
        HookDefinition {
            hook_type: HookType::Prompt,
            event,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: None,
            url: None,
            prompt: Some(prompt.into()),
            model: None,
        }
    }

    fn agent_hook(event: HookEvent) -> HookDefinition {
        HookDefinition {
            hook_type: HookType::Agent,
            event,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: None,
            url: None,
            prompt: None,
            model: None,
        }
    }

    fn disabled_hook(event: HookEvent) -> HookDefinition {
        let mut h = cmd_hook(event, "echo disabled");
        h.enabled = false;
        h
    }

    // --- tests ---

    #[tokio::test]
    async fn register_enabled_hook() {
        let mut exec = HookExecutor::new();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo hello"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert!(!result.blocked);
        assert_eq!(result.results.len(), 1);
        assert!(result.results[0].passed);
    }

    #[tokio::test]
    async fn register_disabled_hook_is_skipped() {
        let mut exec = HookExecutor::new();
        exec.register(disabled_hook(HookEvent::PreExecution));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert!(result.results.is_empty());
    }

    #[tokio::test]
    async fn command_hook_captures_stdout() {
        let hook = cmd_hook(HookEvent::PreExecution, "echo hello_world");
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(result.passed);
        assert!(!result.blocked);
        assert_eq!(result.output.as_deref(), Some("hello_world\n"));
    }

    #[tokio::test]
    async fn command_hook_failure_sets_error() {
        let hook = cmd_hook(HookEvent::PreExecution, "false");
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
        assert!(!result.blocked); // block_on_failure is false
    }

    #[tokio::test]
    async fn command_hook_blocking_on_failure() {
        let hook = cmd_hook_blocking(HookEvent::PreExecution, "false");
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.blocked); // block_on_failure is true
    }

    #[tokio::test]
    async fn command_hook_no_command_returns_error() {
        let mut hook = cmd_hook(HookEvent::PreExecution, "");
        hook.command = None;
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
    }

    #[tokio::test]
    async fn command_hook_nonexistent_program() {
        let hook = cmd_hook(HookEvent::PreExecution, "no_such_program_xyz_12345");
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
        assert_eq!(result.error_type.as_deref(), Some("spawn_failed"));
    }

    #[tokio::test]
    async fn http_placeholder_passes_with_url() {
        let hook = http_hook(HookEvent::PostExecution, "https://example.com/webhook");
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(result.passed);
    }

    #[tokio::test]
    async fn http_placeholder_fails_without_url() {
        let hook = HookDefinition {
            url: None,
            ..http_hook(HookEvent::PostExecution, "")
        };
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
    }

    #[tokio::test]
    async fn prompt_placeholder_passes_with_prompt() {
        let hook = prompt_hook(HookEvent::PreToolUse, "Check safety");
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(result.passed);
    }

    #[tokio::test]
    async fn prompt_placeholder_fails_without_prompt() {
        let hook = HookDefinition {
            prompt: None,
            ..prompt_hook(HookEvent::PreToolUse, "")
        };
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
    }

    #[tokio::test]
    async fn agent_placeholder_always_passes() {
        let hook = agent_hook(HookEvent::OnEvolution);
        let exec = HookExecutor::new();
        let result = exec.execute(&hook).await;
        assert!(result.passed);
        assert!(!result.blocked);
    }

    #[tokio::test]
    async fn run_all_empty_event() {
        let exec = HookExecutor::new();
        let result = exec.run_all(HookEvent::OnError).await;
        assert!(result.results.is_empty());
        assert!(!result.blocked);
        assert!(result.errors.is_empty());
    }

    #[tokio::test]
    async fn run_all_multiple_hooks() {
        let mut exec = HookExecutor::new();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo first"));
        exec.register(cmd_hook(HookEvent::PreExecution, "echo second"));
        exec.register(cmd_hook(HookEvent::PostExecution, "echo other"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert_eq!(result.results.len(), 2);
        assert!(!result.blocked);
    }

    #[tokio::test]
    async fn run_all_aggregates_blocked() {
        let mut exec = HookExecutor::new();
        exec.register(cmd_hook_blocking(HookEvent::PreExecution, "false"));
        exec.register(cmd_hook(HookEvent::PreExecution, "echo ok"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert!(result.blocked);
        assert_eq!(result.results.len(), 2);
    }

    #[tokio::test]
    async fn run_all_collects_errors() {
        let mut exec = HookExecutor::new();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo ok"));
        exec.register(cmd_hook(HookEvent::PreExecution, "false"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert_eq!(result.errors.len(), 1);
    }

    #[tokio::test]
    async fn events_are_isolated() {
        let mut exec = HookExecutor::new();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo pre"));
        exec.register(cmd_hook(HookEvent::PostExecution, "echo post"));
        let pre = exec.run_all(HookEvent::PreExecution).await;
        let post = exec.run_all(HookEvent::PostExecution).await;
        assert_eq!(pre.results.len(), 1);
        assert_eq!(post.results.len(), 1);
    }

    // --- shlex_split tests ---

    #[test]
    fn shlex_split_simple() {
        assert_eq!(shlex_split("echo hello"), vec!["echo", "hello"]);
    }

    #[test]
    fn shlex_split_quoted() {
        assert_eq!(
            shlex_split("echo \"hello world\""),
            vec!["echo", "hello world"]
        );
    }

    #[test]
    fn shlex_split_empty() {
        assert!(shlex_split("").is_empty());
    }
}
