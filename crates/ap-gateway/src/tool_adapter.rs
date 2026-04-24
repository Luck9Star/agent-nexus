//! MCP tool name namespacing: `agent_name___tool_name` format.
//!
//! Uses `___` (triple underscore) as separator so that agent names containing
//! hyphens (e.g. `code-reviewer`) round-trip correctly without any character
//! conversion. Agent/tool names with triple underscores will NOT round-trip
//! correctly — such names are considered invalid for this system.

/// Namespaces MCP tools using the pattern `agent_name___tool_name`.
///
/// No hyphen conversion is performed. The `___` separator is distinctive enough
/// that it will not appear in typical agent or tool names.
pub struct McpToolAdapter;

impl McpToolAdapter {
    #[must_use] 
    pub fn new() -> Self {
        Self
    }

    /// Create namespaced tool name: "code-reviewer" + "review" -> "code-reviewer___review"
    #[must_use] 
    pub fn namespace_tool(&self, agent: &str, tool: &str) -> String {
        format!("{agent}{SEPARATOR}{tool}")
    }

    /// Parse namespaced name: "code-reviewer___review" -> ("code-reviewer", "review")
    /// Returns None if format doesn't match.
    #[must_use] 
    pub fn parse_namespaced(&self, name: &str) -> Option<(String, String)> {
        let (agent, tool) = name.split_once(SEPARATOR)?;
        if agent.is_empty() || tool.is_empty() {
            return None;
        }
        Some((agent.to_string(), tool.to_string()))
    }
}

impl Default for McpToolAdapter {
    fn default() -> Self {
        Self::new()
    }
}

/// Separator used between agent name and tool name in the namespaced identifier.
/// Triple underscore is chosen to avoid collisions with hyphens or single underscores
/// in agent/tool names.
const SEPARATOR: &str = "___";

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
            "code-reviewer___review"
        );
    }

    #[test]
    fn namespace_tool_no_hyphens() {
        let adapter = McpToolAdapter::new();
        assert_eq!(
            adapter.namespace_tool("codereviewer", "review"),
            "codereviewer___review"
        );
    }

    #[test]
    fn parse_namespaced_basic() {
        let adapter = McpToolAdapter::new();
        let result = adapter.parse_namespaced("code-reviewer___review");
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
    fn parse_namespaced_empty_agent_returns_none() {
        let adapter = McpToolAdapter::new();
        assert_eq!(adapter.parse_namespaced("___tool"), None);
    }

    #[test]
    fn parse_namespaced_empty_tool_returns_none() {
        let adapter = McpToolAdapter::new();
        assert_eq!(adapter.parse_namespaced("agent___"), None);
    }

    #[test]
    fn parse_multiple_separators_takes_first() {
        let adapter = McpToolAdapter::new();
        // "agent___tool___extra" -> agent="agent", tool="tool___extra"
        let result = adapter.parse_namespaced("agent___tool___extra");
        assert_eq!(
            result,
            Some(("agent".to_string(), "tool___extra".to_string()))
        );
    }

    #[test]
    fn default_trait_works() {
        let adapter = McpToolAdapter;
        assert_eq!(adapter.namespace_tool("foo", "bar"), "foo___bar");
    }

    #[test]
    fn underscore_names_roundtrip_correctly() {
        // Agent names with underscores (not hyphens) round-trip correctly
        let adapter = McpToolAdapter::new();
        let namespaced = adapter.namespace_tool("my_agent", "my_tool");
        let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
        assert_eq!(agent, "my_agent");
        assert_eq!(tool, "my_tool");
    }

    #[test]
    fn double_underscore_in_name_not_confused_with_separator() {
        // "some__agent" should NOT be split on the "__" — only "___" is the separator
        let adapter = McpToolAdapter::new();
        let namespaced = adapter.namespace_tool("some__agent", "review");
        assert_eq!(namespaced, "some__agent___review");
        let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
        assert_eq!(agent, "some__agent");
        assert_eq!(tool, "review");
    }
}
