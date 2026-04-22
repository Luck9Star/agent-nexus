//! MCP tool name namespacing: `agent_name__tool_name` format.

/// Namespaces MCP tools using the pattern `agent_name__tool_name`.
///
/// Hyphens in agent names are replaced with underscores so that the
/// namespaced identifier is a valid simple identifier.
pub struct McpToolAdapter;

impl McpToolAdapter {
    pub fn new() -> Self {
        Self
    }

    /// Create namespaced tool name: "code-reviewer" + "review" -> "code_reviewer__review"
    /// Replaces hyphens with underscores in agent name.
    pub fn namespace_tool(&self, agent: &str, tool: &str) -> String {
        format!("{}__{}", agent.replace('-', "_"), tool)
    }

    /// Parse namespaced name: "code_reviewer__review" -> ("code-reviewer", "review")
    /// Returns None if format doesn't match.
    pub fn parse_namespaced(&self, name: &str) -> Option<(String, String)> {
        let (agent, tool) = name.split_once("__")?;
        Some((agent.replace('_', "-"), tool.to_string()))
    }
}

impl Default for McpToolAdapter {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn namespace_tool_basic() {
        let adapter = McpToolAdapter::new();
        assert_eq!(
            adapter.namespace_tool("code-reviewer", "review"),
            "code_reviewer__review"
        );
    }

    #[test]
    fn namespace_tool_no_hyphens() {
        let adapter = McpToolAdapter::new();
        assert_eq!(
            adapter.namespace_tool("codereviewer", "review"),
            "codereviewer__review"
        );
    }

    #[test]
    fn parse_namespaced_basic() {
        let adapter = McpToolAdapter::new();
        let result = adapter.parse_namespaced("code_reviewer__review");
        assert_eq!(result, Some(("code-reviewer".to_string(), "review".to_string())));
    }

    #[test]
    fn parse_namespaced_no_separator_returns_none() {
        let adapter = McpToolAdapter::new();
        assert_eq!(adapter.parse_namespaced("noseparator"), None);
    }

    #[test]
    fn roundtrip() {
        let adapter = McpToolAdapter::new();
        let namespaced = adapter.namespace_tool("my-agent", "my-tool");
        let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
        assert_eq!(agent, "my-agent");
        assert_eq!(tool, "my-tool");
    }

    #[test]
    fn roundtrip_multiple_words() {
        let adapter = McpToolAdapter::new();
        let namespaced = adapter.namespace_tool("code-review-v2", "run-checks");
        let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
        assert_eq!(agent, "code-review-v2");
        assert_eq!(tool, "run-checks");
    }

    #[test]
    fn parse_multiple_separators_takes_first() {
        let adapter = McpToolAdapter::new();
        // "agent__tool__extra" -> agent="agent", tool="tool__extra"
        let result = adapter.parse_namespaced("agent__tool__extra");
        assert_eq!(
            result,
            Some(("agent".to_string(), "tool__extra".to_string()))
        );
    }

    #[test]
    fn default_trait_works() {
        let adapter = McpToolAdapter::default();
        assert_eq!(adapter.namespace_tool("foo", "bar"), "foo__bar");
    }
}
