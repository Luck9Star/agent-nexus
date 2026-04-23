//! `ExecutionAnalyzer` — post-task analysis to identify evolution opportunities.
//!
//! This is a stateless analysis pass: given a `TaskResult`, produce a list of
//! `EvolutionSuggestion`s. The analyzer does not touch the store itself.

/// Broad category of evolution action.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EvolutionType {
    /// Fix a bug / error in an existing skill.
    Fix,
    /// Derive a new skill from an existing one.
    Derived,
    /// Capture a one-off solution as a reusable skill.
    Captured,
}

impl std::fmt::Display for EvolutionType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EvolutionType::Fix => write!(f, "FIX"),
            EvolutionType::Derived => write!(f, "DERIVED"),
            EvolutionType::Captured => write!(f, "CAPTURED"),
        }
    }
}

/// A single suggestion produced by the analyzer.
#[derive(Debug, Clone)]
pub struct EvolutionSuggestion {
    pub evolution_type: EvolutionType,
    pub skill_name: String,
    pub reason: String,
}

/// Summary of a completed (or failed) task.
#[derive(Debug, Clone)]
pub struct TaskResult {
    pub success: bool,
    pub error: Option<String>,
    pub agent_name: String,
    pub task_id: String,
}

/// Stateless analyzer — no internal state, safe to share across threads.
pub struct Analyzer;

impl Analyzer {
    /// Create a new analyzer instance.
    #[must_use] 
    pub fn new() -> Self {
        Self
    }

    /// Analyze a task result and produce evolution suggestions.
    ///
    /// Current logic:
    /// - Failed task with an error message produces a `Fix` suggestion.
    /// - Successful tasks produce no suggestions (for now).
    #[must_use] 
    pub fn analyze(&self, task_result: &TaskResult) -> Vec<EvolutionSuggestion> {
        let mut suggestions = Vec::new();

        if !task_result.success {
            let error_msg = task_result
                .error
                .as_deref()
                .unwrap_or("unknown error");
            suggestions.push(EvolutionSuggestion {
                evolution_type: EvolutionType::Fix,
                skill_name: task_result.agent_name.clone(),
                reason: format!("Task failed: {error_msg}"),
            });
        }

        suggestions
    }
}

impl Default for Analyzer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn success_result() -> TaskResult {
        TaskResult {
            success: true,
            error: None,
            agent_name: "code-reviewer".to_string(),
            task_id: "t-001".to_string(),
        }
    }

    fn failed_result(error: &str) -> TaskResult {
        TaskResult {
            success: false,
            error: Some(error.to_string()),
            agent_name: "code-reviewer".to_string(),
            task_id: "t-002".to_string(),
        }
    }

    #[test]
    fn analyze_success_returns_empty() {
        let analyzer = Analyzer::new();
        let result = success_result();
        let suggestions = analyzer.analyze(&result);
        assert!(suggestions.is_empty());
    }

    #[test]
    fn analyze_failure_returns_fix_suggestion() {
        let analyzer = Analyzer::new();
        let result = failed_result("SyntaxError: invalid syntax");
        let suggestions = analyzer.analyze(&result);
        assert_eq!(suggestions.len(), 1);
        assert_eq!(suggestions[0].evolution_type, EvolutionType::Fix);
        assert_eq!(suggestions[0].skill_name, "code-reviewer");
        assert!(suggestions[0].reason.contains("SyntaxError"));
    }

    #[test]
    fn analyze_failure_without_error_message() {
        let analyzer = Analyzer::new();
        let result = TaskResult {
            success: false,
            error: None,
            agent_name: "doc-filler".to_string(),
            task_id: "t-003".to_string(),
        };
        let suggestions = analyzer.analyze(&result);
        assert_eq!(suggestions.len(), 1);
        assert!(suggestions[0].reason.contains("unknown error"));
    }

    #[test]
    fn evolution_type_display() {
        assert_eq!(format!("{}", EvolutionType::Fix), "FIX");
        assert_eq!(format!("{}", EvolutionType::Derived), "DERIVED");
        assert_eq!(format!("{}", EvolutionType::Captured), "CAPTURED");
    }

    #[test]
    fn analyzer_default_trait() {
        let analyzer = Analyzer;
        let result = success_result();
        let suggestions = analyzer.analyze(&result);
        assert!(suggestions.is_empty());
    }
}
