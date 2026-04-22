//! JSON Schema utilities for converting between MCP tool format and gateway format.

use ap_runtime::mcp_client::ToolInfo;

/// Merge tool schemas from multiple agents into a unified gateway format.
///
/// Each tool is namespaced via `McpToolAdapter` (`{agent}___{tool}`) with its
/// description and input schema preserved.
pub fn merge_tool_schemas(
    agent_name: &str,
    tools: &[ToolInfo],
) -> Vec<serde_json::Value> {
    let adapter = crate::tool_adapter::McpToolAdapter::new();
    tools
        .iter()
        .map(|tool| {
            serde_json::json!({
                "name": adapter.namespace_tool(agent_name, &tool.name),
                "description": tool.description,
                "inputSchema": tool.input_schema,
            })
        })
        .collect()
}

/// Extract the original agent name, tool name, and input from a gateway request.
///
/// Expects a JSON object with a "name" field (namespaced) and an optional
/// "arguments" field.
pub fn extract_tool_call(
    request: &serde_json::Value,
) -> Option<(String, String, serde_json::Value)> {
    let name = request.get("name")?.as_str()?;
    let adapter = crate::tool_adapter::McpToolAdapter::new();
    let (agent, tool) = adapter.parse_namespaced(name)?;
    let arguments = request
        .get("arguments")
        .cloned()
        .unwrap_or(serde_json::json!({}));
    Some((agent, tool, arguments))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_tool() -> ToolInfo {
        ToolInfo {
            name: "review".to_string(),
            description: Some("Review code".to_string()),
            input_schema: Some(serde_json::json!({"type": "object"})),
        }
    }

    #[test]
    fn merge_tool_schemas_basic() {
        let tools = vec![sample_tool()];
        let schemas = merge_tool_schemas("code-reviewer", &tools);
        assert_eq!(schemas.len(), 1);
        assert_eq!(schemas[0]["name"], "code-reviewer___review");
        assert_eq!(schemas[0]["description"], "Review code");
        assert_eq!(schemas[0]["inputSchema"]["type"], "object");
    }

    #[test]
    fn merge_tool_schemas_empty() {
        let schemas = merge_tool_schemas("agent", &[]);
        assert!(schemas.is_empty());
    }

    #[test]
    fn merge_tool_schemas_multiple() {
        let tools = vec![
            ToolInfo {
                name: "a".to_string(),
                description: None,
                input_schema: None,
            },
            ToolInfo {
                name: "b".to_string(),
                description: Some("Tool B".to_string()),
                input_schema: None,
            },
        ];
        let schemas = merge_tool_schemas("my-agent", &tools);
        assert_eq!(schemas.len(), 2);
        assert_eq!(schemas[0]["name"], "my-agent___a");
        assert_eq!(schemas[1]["name"], "my-agent___b");
    }

    #[test]
    fn extract_tool_call_basic() {
        let req = serde_json::json!({
            "name": "code-reviewer___review",
            "arguments": {"path": "/src/main.rs"}
        });
        let (agent, tool, args) = extract_tool_call(&req).unwrap();
        assert_eq!(agent, "code-reviewer");
        assert_eq!(tool, "review");
        assert_eq!(args["path"], "/src/main.rs");
    }

    #[test]
    fn extract_tool_call_no_arguments_defaults_empty_object() {
        let req = serde_json::json!({
            "name": "my-agent___ping"
        });
        let (agent, tool, args) = extract_tool_call(&req).unwrap();
        assert_eq!(agent, "my-agent");
        assert_eq!(tool, "ping");
        assert!(args.is_object());
        assert!(args.as_object().unwrap().is_empty());
    }

    #[test]
    fn extract_tool_call_no_name_returns_none() {
        let req = serde_json::json!({"arguments": {}});
        assert!(extract_tool_call(&req).is_none());
    }

    #[test]
    fn extract_tool_call_unparseable_name_returns_none() {
        let req = serde_json::json!({"name": "no_separator"});
        assert!(extract_tool_call(&req).is_none());
    }
}
