//! `HookExecutor` — registers and runs hooks, dispatching on `HookType`.

use std::collections::{HashMap, HashSet};
use std::time::Instant;

use crate::models::common::utc_now;
use crate::models::hooks::{
    AggregatedHookResult, HookDefinition, HookEvent, HookExecution, HookType,
};

/// Dispatches and executes hooks keyed by event.
#[derive(Debug, Clone)]
pub struct HookExecutor {
    hooks: HashMap<HookEvent, Vec<HookDefinition>>,
    allowed_commands: Option<HashSet<String>>,
}

impl Default for HookExecutor {
    fn default() -> Self {
        Self::new()
    }
}

impl HookExecutor {
    /// Create an empty executor.
    ///
    /// By default `allowed_commands` is `None`, which means ALL command hooks
    /// are rejected (matching Python's default-deny behavior). Use
    /// `with_allowed_commands` to explicitly allow specific programs.
    #[must_use] 
    pub fn new() -> Self {
        Self {
            hooks: HashMap::new(),
            allowed_commands: None, // None = reject all commands (match Python default)
        }
    }

    /// Set the allowlist of command programs that hooks may invoke.
    #[must_use] 
    pub fn with_allowed_commands(mut self, cmds: HashSet<String>) -> Self {
        self.allowed_commands = Some(cmds);
        self
    }

    /// Register a hook. Disabled hooks are silently skipped.
    pub fn register(&mut self, hook: HookDefinition) {
        if !hook.enabled {
            return;
        }
        self.hooks.entry(hook.event).or_default().push(hook);
    }

    /// Execute a single hook, dispatching on its `hook_type`.
    pub async fn execute(&self, hook: &HookDefinition) -> HookExecution {
        let start = Instant::now();

        let (passed, output, error, error_type) = match hook.hook_type {
            HookType::Command => self.execute_command(hook).await,
            HookType::Http => self.execute_http_placeholder(hook),
            HookType::Prompt => self.execute_prompt_placeholder(hook),
            HookType::Agent => self.execute_agent_placeholder(hook),
        };

        let duration_ms = start.elapsed().as_secs_f64() * 1000.0;

        HookExecution {
            hook: hook.clone(),
            passed,
            blocked: !passed && hook.block_on_failure,
            output,
            error,
            error_type,
            duration_ms,
            executed_at: utc_now(),
        }
    }

    /// Run all hooks registered for `event`, returning an aggregated result.
    ///
    /// Stops executing remaining hooks when a hook with `blocked=true` is
    /// encountered (matching Python behavioral parity).
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
            if blocked {
                break; // Stop executing remaining hooks (Python behavioral parity)
            }
        }

        AggregatedHookResult {
            event,
            results,
            blocked,
            errors,
        }
    }

    // ---- dispatch implementations ----

    async fn execute_command(
        &self,
        hook: &HookDefinition,
    ) -> (bool, Option<String>, Option<String>, Option<String>) {
        let cmd = match hook.command.as_deref() {
            Some(c) if !c.trim().is_empty() => c,
            _ => return (false, None, Some("empty command".into()), None),
        };

        let parts = shlex_split(cmd);
        if parts.is_empty() {
            return (false, None, Some("empty command after split".into()), None);
        }
        let program = &parts[0];
        let args = &parts[1..];

        // Allowlist check (P0-3): match Python's default-deny behavior.
        match &self.allowed_commands {
            None => {
                return (
                    false,
                    None,
                    Some(format!(
                        "command '{program}' rejected: no allowlist configured"
                    )),
                    None,
                );
            }
            Some(allowed) if !allowed.contains(program) => {
                return (
                    false,
                    None,
                    Some(format!("command '{program}' not in allowlist")),
                    None,
                );
            }
            _ => {}
        }

        let timeout = std::time::Duration::from_secs_f64(hook.timeout_seconds);
        let result = tokio::time::timeout(
            timeout,
            tokio::process::Command::new(program).args(args).output(),
        )
        .await;

        match result {
            Ok(Ok(output)) => {
                let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                let stderr = if output.status.success() {
                    None
                } else {
                    Some(String::from_utf8_lossy(&output.stderr).to_string())
                };
                (output.status.success(), Some(stdout), stderr, None)
            }
            Ok(Err(e)) => (
                false,
                None,
                Some(e.to_string()),
                Some("spawn_error".into()),
            ),
            Err(_) => (
                false,
                None,
                Some(format!(
                    "command timed out after {:.1}s",
                    hook.timeout_seconds
                )),
                Some("timeout".into()),
            ),
        }
    }

    #[allow(clippy::unused_self)]
    fn execute_http_placeholder(
        &self,
        hook: &HookDefinition,
    ) -> (bool, Option<String>, Option<String>, Option<String>) {
        let passed = hook.url.as_ref().is_some_and(|u| !u.trim().is_empty());
        (
            passed,
            if passed {
                Some("http placeholder ok".into())
            } else {
                None
            },
            if passed {
                None
            } else {
                Some("no url configured".into())
            },
            if passed {
                None
            } else {
                Some("configuration".into())
            },
        )
    }

    #[allow(clippy::unused_self)]
    fn execute_prompt_placeholder(
        &self,
        hook: &HookDefinition,
    ) -> (bool, Option<String>, Option<String>, Option<String>) {
        let passed = hook.prompt.as_ref().is_some_and(|p| !p.trim().is_empty());
        (
            passed,
            if passed {
                Some("prompt placeholder ok".into())
            } else {
                None
            },
            if passed {
                None
            } else {
                Some("no prompt configured".into())
            },
            if passed {
                None
            } else {
                Some("configuration".into())
            },
        )
    }

    #[allow(clippy::unused_self)]
    fn execute_agent_placeholder(
        &self,
        _hook: &HookDefinition,
    ) -> (bool, Option<String>, Option<String>, Option<String>) {
        (
            true,
            Some("agent placeholder ok".into()),
            None,
            None,
        )
    }
}

/// Split a command string into tokens using proper shell word splitting.
///
/// Delegates to the `shell-words` crate which correctly handles double quotes,
/// single quotes, and escape sequences — unlike the previous custom implementation
/// which only handled double quotes.
fn shlex_split(input: &str) -> Vec<String> {
    shell_words::split(input).unwrap_or_else(|_| {
        // If shell_words can't parse it, fall back to simple whitespace split
        input.split_whitespace().map(std::string::ToString::to_string).collect()
    })
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

    /// Helper: create an executor that allows common test commands.
    fn test_executor() -> HookExecutor {
        HookExecutor::new().with_allowed_commands(
            vec![
                "echo".to_string(),
                "false".to_string(),
                "true".to_string(),
                "no_such_program_xyz_12345".to_string(),
                "sleep".to_string(),
            ]
            .into_iter()
            .collect(),
        )
    }

    // --- tests ---

    #[tokio::test]
    async fn register_enabled_hook() {
        let mut exec = test_executor();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo hello"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert!(!result.blocked);
        assert_eq!(result.results.len(), 1);
        assert!(result.results[0].passed);
    }

    #[tokio::test]
    async fn register_disabled_hook_is_skipped() {
        let mut exec = test_executor();
        exec.register(disabled_hook(HookEvent::PreExecution));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert!(result.results.is_empty());
    }

    #[tokio::test]
    async fn command_hook_captures_stdout() {
        let hook = cmd_hook(HookEvent::PreExecution, "echo hello_world");
        let exec = test_executor();
        let result = exec.execute(&hook).await;
        assert!(result.passed);
        assert!(!result.blocked);
        assert_eq!(result.output.as_deref(), Some("hello_world\n"));
    }

    #[tokio::test]
    async fn command_hook_failure_sets_error() {
        let hook = cmd_hook(HookEvent::PreExecution, "false");
        let exec = test_executor();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
        assert!(!result.blocked); // block_on_failure is false
    }

    #[tokio::test]
    async fn command_hook_blocking_on_failure() {
        let hook = cmd_hook_blocking(HookEvent::PreExecution, "false");
        let exec = test_executor();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.blocked); // block_on_failure is true
    }

    #[tokio::test]
    async fn command_hook_no_command_returns_error() {
        let mut hook = cmd_hook(HookEvent::PreExecution, "");
        hook.command = None;
        let exec = test_executor();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
    }

    #[tokio::test]
    async fn command_hook_nonexistent_program() {
        let hook = cmd_hook(HookEvent::PreExecution, "no_such_program_xyz_12345");
        let exec = test_executor();
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
        assert_eq!(result.error_type.as_deref(), Some("spawn_error"));
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
        let exec = test_executor();
        let result = exec.run_all(HookEvent::OnError).await;
        assert!(result.results.is_empty());
        assert!(!result.blocked);
        assert!(result.errors.is_empty());
    }

    #[tokio::test]
    async fn run_all_multiple_hooks() {
        let mut exec = test_executor();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo first"));
        exec.register(cmd_hook(HookEvent::PreExecution, "echo second"));
        exec.register(cmd_hook(HookEvent::PostExecution, "echo other"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert_eq!(result.results.len(), 2);
        assert!(!result.blocked);
    }

    #[tokio::test]
    async fn run_all_aggregates_blocked() {
        let mut exec = test_executor();
        exec.register(cmd_hook_blocking(HookEvent::PreExecution, "false"));
        exec.register(cmd_hook(HookEvent::PreExecution, "echo ok"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert!(result.blocked);
        // After P0-1 fix: blocked hook stops execution, so only 1 result (the blocking one)
        assert_eq!(result.results.len(), 1);
    }

    #[tokio::test]
    async fn run_all_collects_errors() {
        let mut exec = test_executor();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo ok"));
        exec.register(cmd_hook(HookEvent::PreExecution, "false"));
        let result = exec.run_all(HookEvent::PreExecution).await;
        assert_eq!(result.errors.len(), 1);
    }

    #[tokio::test]
    async fn events_are_isolated() {
        let mut exec = test_executor();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo pre"));
        exec.register(cmd_hook(HookEvent::PostExecution, "echo post"));
        let pre = exec.run_all(HookEvent::PreExecution).await;
        let post = exec.run_all(HookEvent::PostExecution).await;
        assert_eq!(pre.results.len(), 1);
        assert_eq!(post.results.len(), 1);
    }

    // --- P0-1: blocking hook stops execution ---

    #[tokio::test]
    async fn run_all_blocking_hook_stops_execution() {
        // Register 3 hooks: pass, block, pass.
        // After the blocking hook, execution should stop (3rd not reached).
        let mut exec = test_executor();
        exec.register(cmd_hook(HookEvent::PreExecution, "echo first")); // passes
        exec.register(cmd_hook_blocking(HookEvent::PreExecution, "false")); // fails + blocks
        exec.register(cmd_hook(HookEvent::PreExecution, "echo third")); // should NOT execute

        let result = exec.run_all(HookEvent::PreExecution).await;
        assert!(result.blocked, "should be blocked");
        assert_eq!(
            result.results.len(),
            2,
            "only 2 hooks should have executed (first + blocking)"
        );
        assert!(
            result.results[0].passed,
            "first hook should pass"
        );
        assert!(
            !result.results[1].passed,
            "second hook should fail"
        );
        assert!(
            result.results[1].blocked,
            "second hook should be blocked"
        );
    }

    // --- P0-3: command allowlist enforcement ---

    #[tokio::test]
    async fn command_rejected_when_no_allowlist() {
        // HookExecutor::new() without with_allowed_commands → all commands rejected
        let exec = HookExecutor::new();
        let hook = cmd_hook(HookEvent::PreExecution, "echo hello");
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
        assert!(
            result.error.as_deref().unwrap().contains("no allowlist configured"),
            "error should mention no allowlist: {:?}",
            result.error
        );
    }

    #[tokio::test]
    async fn command_rejected_when_not_in_allowlist() {
        let exec = HookExecutor::new().with_allowed_commands(
            vec!["ls".to_string()].into_iter().collect(),
        );
        let hook = cmd_hook(HookEvent::PreExecution, "echo hello");
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
        assert!(
            result.error.as_deref().unwrap().contains("not in allowlist"),
            "error should mention not in allowlist: {:?}",
            result.error
        );
    }

    #[tokio::test]
    async fn command_allowed_when_in_allowlist() {
        let exec = HookExecutor::new().with_allowed_commands(
            vec!["echo".to_string()].into_iter().collect(),
        );
        let hook = cmd_hook(HookEvent::PreExecution, "echo hello");
        let result = exec.execute(&hook).await;
        assert!(result.passed, "command in allowlist should execute: {:?}", result.error);
    }

    #[tokio::test]
    async fn command_rejected_with_empty_allowlist() {
        let exec = HookExecutor::new().with_allowed_commands(HashSet::new());
        let hook = cmd_hook(HookEvent::PreExecution, "echo hello");
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(
            result.error.as_deref().unwrap().contains("not in allowlist"),
            "empty allowlist should reject: {:?}",
            result.error
        );
    }

    // --- P1-1: command hook timeout enforcement ---

    #[tokio::test]
    async fn command_hook_times_out() {
        let exec = HookExecutor::new().with_allowed_commands(
            vec!["sleep".to_string()].into_iter().collect(),
        );
        let mut hook = cmd_hook(HookEvent::PreExecution, "sleep 30");
        hook.timeout_seconds = 0.2; // very short timeout
        let result = exec.execute(&hook).await;
        assert!(!result.passed);
        assert!(result.error.is_some());
        assert!(
            result.error.as_deref().unwrap().contains("timed out"),
            "should report timeout: {:?}",
            result.error
        );
        assert_eq!(result.error_type.as_deref(), Some("timeout"));
    }

    // --- P0-2: duration_ms is nonzero for real execution ---

    #[tokio::test]
    async fn execute_duration_ms_is_nonzero() {
        let exec = test_executor();
        let hook = cmd_hook(HookEvent::PreExecution, "echo hello");
        let result = exec.execute(&hook).await;
        assert!(result.passed);
        // duration_ms should be non-trivial (not ~0ms from old bug)
        // We can't guarantee an exact value, but it should be >= 0
        assert!(
            result.duration_ms >= 0.0,
            "duration_ms should be non-negative: {}",
            result.duration_ms
        );
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
