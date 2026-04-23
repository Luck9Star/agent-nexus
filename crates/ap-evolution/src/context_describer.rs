//! Context describer — generates a human-readable description of the
//! evolution context for a task result.

use crate::analyzer::TaskResult;

/// Generate a context description string for the given task result and skill.
///
/// This is used as a prompt prefix when asking an LLM to evolve a skill.
#[must_use] 
pub fn describe_context(task_result: &TaskResult, skill_name: &str) -> String {
    let status = if task_result.success { "SUCCESS" } else { "FAILURE" };
    let error_section = match &task_result.error {
        Some(e) => format!("\nError: {e}"),
        None => String::new(),
    };

    format!(
        "Evolution context for skill '{skill_name}':\n\
Task ID: {task_id}\n\
Agent: {agent}\n\
Status: {status}{error_section}",
        task_id = task_result.task_id,
        agent = task_result.agent_name,
        status = status,
        error_section = error_section,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn describe_success() {
        let result = TaskResult {
            success: true,
            error: None,
            agent_name: "code-reviewer".to_string(),
            task_id: "t-001".to_string(),
        };
        let desc = describe_context(&result, "review-skill");
        assert!(desc.contains("review-skill"));
        assert!(desc.contains("t-001"));
        assert!(desc.contains("code-reviewer"));
        assert!(desc.contains("SUCCESS"));
        assert!(!desc.contains("Error:"));
    }

    #[test]
    fn describe_failure() {
        let result = TaskResult {
            success: false,
            error: Some("SyntaxError: invalid syntax".to_string()),
            agent_name: "doc-filler".to_string(),
            task_id: "t-002".to_string(),
        };
        let desc = describe_context(&result, "fill-skill");
        assert!(desc.contains("FAILURE"));
        assert!(desc.contains("SyntaxError"));
    }

    #[test]
    fn describe_failure_no_error_msg() {
        let result = TaskResult {
            success: false,
            error: None,
            agent_name: "agent".to_string(),
            task_id: "t-003".to_string(),
        };
        let desc = describe_context(&result, "some-skill");
        assert!(desc.contains("FAILURE"));
        assert!(!desc.contains("Error:"));
    }
}
