//! Gateway integration tests — tool adapter namespacing.

use ap_gateway::tool_adapter::McpToolAdapter;

// ── Test: Gateway tool name namespacing ──────────────────────────────

#[test]
fn gateway_tool_namespacing() {
    let adapter = McpToolAdapter::new();
    let namespaced = adapter.namespace_tool("code-reviewer", "review-code");
    assert_eq!(namespaced, "mcp__code_reviewer__review_code");

    let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
    assert_eq!(agent, "code_reviewer");
    assert_eq!(tool, "review_code");
}

#[test]
fn gateway_tool_roundtrip() {
    let adapter = McpToolAdapter::new();
    let namespaced = adapter.namespace_tool("my-agent", "my-tool");
    let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
    assert_eq!(agent, "my_agent");
    assert_eq!(tool, "my_tool");
}

#[test]
fn gateway_tool_no_separator_returns_none() {
    let adapter = McpToolAdapter::new();
    assert!(adapter.parse_namespaced("no-separator-here").is_none());
}
