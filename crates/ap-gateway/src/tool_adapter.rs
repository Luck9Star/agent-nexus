//! MCP tool name namespacing: `mcp__{sanitized_agent}__{tool}` format.
//!
//! Matches the Python gateway's naming convention: `mcp__` prefix + double
//! underscore separator + sanitized names (non-alphanumeric chars → `_`).

/// Namespaces MCP tools using the pattern `mcp__{agent}__{tool}`.
///
/// Names are sanitized: non-alphanumeric characters are replaced with `_`.
/// This matches the Python gateway's `_sanitize()` + `mcp__` naming convention.
pub struct McpToolAdapter;

impl McpToolAdapter {
    #[must_use]
    pub fn new() -> Self {
        Self
    }

    /// Create namespaced tool name: "code-reviewer" + "review" -> "mcp__code_reviewer__review"
    #[must_use]
    pub fn namespace_tool(&self, agent: &str, tool: &str) -> String {
        format!("mcp__{}{}{}", sanitize(agent), SEPARATOR, sanitize(tool))
    }

    /// Parse namespaced name: "mcp__code_reviewer__review" -> ("code_reviewer", "review")
    /// Returns None if format doesn't match.
    #[must_use]
    pub fn parse_namespaced(&self, name: &str) -> Option<(String, String)> {
        let stripped = name.strip_prefix("mcp__")?;
        let (agent, tool) = stripped.split_once(SEPARATOR)?;
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

/// Sanitize a name: replace non-alphanumeric characters with `_`.
///
/// # Collision warning
///
/// This is a lossy transformation — `code-reviewer` and `code_reviewer`
/// both produce `code_reviewer`.  The gateway resolves this via
/// `DeferredAgentRegistry::find_by_sanitized_name`, which iterates registered
/// agents to find the original name.  Registering two agents whose names
/// differ only by non-alphanumeric characters will cause a collision.
pub fn sanitize(name: &str) -> String {
    name.chars()
        .map(|c| if c.is_alphanumeric() { c } else { '_' })
        .collect()
}

/// Separator used between sanitized agent name and tool name.
const SEPARATOR: &str = "__";

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
            "mcp__code_reviewer__review"
        );
    }

    #[test]
    fn namespace_tool_no_hyphens() {
        let adapter = McpToolAdapter::new();
        assert_eq!(
            adapter.namespace_tool("codereviewer", "review"),
            "mcp__codereviewer__review"
        );
    }

    #[test]
    fn parse_namespaced_basic() {
        let adapter = McpToolAdapter::new();
        let result = adapter.parse_namespaced("mcp__code_reviewer__review");
        assert_eq!(result, Some(("code_reviewer".to_string(), "review".to_string())));
    }

    #[test]
    fn parse_namespaced_no_separator_returns_none() {
        let adapter = McpToolAdapter::new();
        assert_eq!(adapter.parse_namespaced("noseparator"), None);
    }

    #[test]
    fn parse_namespaced_no_prefix_returns_none() {
        let adapter = McpToolAdapter::new();
        assert_eq!(adapter.parse_namespaced("code_reviewer__review"), None);
    }

    #[test]
    fn roundtrip() {
        let adapter = McpToolAdapter::new();
        let namespaced = adapter.namespace_tool("my-agent", "my-tool");
        let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
        assert_eq!(agent, "my_agent");
        assert_eq!(tool, "my_tool");
    }

    #[test]
    fn roundtrip_multiple_words() {
        let adapter = McpToolAdapter::new();
        let namespaced = adapter.namespace_tool("code-review-v2", "run-checks");
        let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
        assert_eq!(agent, "code_review_v2");
        assert_eq!(tool, "run_checks");
    }

    #[test]
    fn parse_namespaced_empty_agent_returns_none() {
        let adapter = McpToolAdapter::new();
        assert_eq!(adapter.parse_namespaced("mcp____tool"), None);
    }

    #[test]
    fn parse_namespaced_empty_tool_returns_none() {
        let adapter = McpToolAdapter::new();
        assert_eq!(adapter.parse_namespaced("mcp__agent__"), None);
    }

    #[test]
    fn default_trait_works() {
        let adapter = McpToolAdapter;
        assert_eq!(adapter.namespace_tool("foo", "bar"), "mcp__foo__bar");
    }

    #[test]
    fn sanitize_replaces_hyphens() {
        assert_eq!(sanitize("code-reviewer"), "code_reviewer");
    }

    #[test]
    fn sanitize_replaces_dots() {
        assert_eq!(sanitize("agent.v2"), "agent_v2");
    }

    #[test]
    fn sanitize_leaves_alphanumeric() {
        assert_eq!(sanitize("abc123"), "abc123");
    }
}
