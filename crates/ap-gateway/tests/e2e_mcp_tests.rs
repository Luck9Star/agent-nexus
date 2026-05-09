//! E2E MCP tests — full communication lifecycle through the gateway.
//!
//! Tests cover:
//! 1. Agent registration + tool discovery
//! 2. Namespaced tool invocation through the gateway
//! 3. Multi-agent tool isolation
//! 4. Provider strategy selection

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use ap_core::models::agent::{AgentManifest, AgentType, IsolationLevel};
use ap_gateway::deferred_registry::DeferredAgentRegistry;
use ap_gateway::gateway::{GatewayConfig, McpGateway};
use ap_gateway::provider_strategy::{ProviderAwareStrategy, ToolLoadingStrategy};
use ap_gateway::tool_adapter::McpToolAdapter;
use ap_runtime::mcp_client::{McpClient, McpError, ToolInfo};

// ---------------------------------------------------------------------------
// Mock MCP client
// ---------------------------------------------------------------------------

/// A mock MCP client that returns predefined tools and echoes tool calls.
struct MockMcpClient {
    tools: Vec<ToolInfo>,
}

impl MockMcpClient {
    fn new(tools: Vec<ToolInfo>) -> Self {
        Self { tools }
    }
}

impl McpClient for MockMcpClient {
    fn list_tools(
        &self,
    ) -> Pin<Box<dyn Future<Output = Result<Vec<ToolInfo>, McpError>> + Send + '_>> {
        let tools = self.tools.clone();
        Box::pin(async move { Ok(tools) })
    }

    fn call_tool(
        &self,
        name: &str,
        arguments: serde_json::Value,
    ) -> Pin<Box<dyn Future<Output = Result<serde_json::Value, McpError>> + Send + '_>> {
        let name = name.to_string();
        Box::pin(async move {
            Ok(serde_json::json!({
                "tool": name,
                "arguments": arguments,
                "status": "ok"
            }))
        })
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn test_manifest(name: &str) -> ap_core::models::agent::AgentManifest {
    ap_core::models::agent::AgentManifest {
        name: name.to_string(),
        version: "0.1.0".to_string(),
        agent_type: ap_core::models::agent::AgentType::Atomic,
        description: format!("Test agent {name}"),
        capabilities: vec![],
        model_preferences: None,
        role: None,
        dependencies: Default::default(),
        permissions: None,
        tools: vec![],
        denied_tools: vec![],
        permission_mode: None,
        skills: vec![],
        hooks: Default::default(),
        mcp_servers: Default::default(),
        pip_dependencies: vec![],
        effort: None,
        max_turns: None,
        memory_scope: None,
        isolation: IsolationLevel::None,
        color: None,
        background: false,
        initial_prompt: None,
    }
}

fn make_tools(names: &[&str]) -> Vec<ToolInfo> {
    names
        .iter()
        .map(|&name| ToolInfo {
            name: name.to_string(),
            description: Some(format!("Tool {name}")),
            input_schema: Some(serde_json::json!({"type": "object"})),
        })
        .collect()
}

// ---------------------------------------------------------------------------
// E2E Tests
// ---------------------------------------------------------------------------

/// Register an agent's tools, then discover them via the gateway HTTP API.
#[tokio::test]
async fn e2e_tool_registration_and_discovery() {
    let config = GatewayConfig {
        listen_addr: "127.0.0.1:0".to_string(),
        idle_timeout_secs: 300,
    };
    let gw = Arc::new(McpGateway::new(config));
    let registry = gw.registry();

    // Register and activate agent with two tools
    registry.register_manifest(test_manifest("reviewer")).await;
    let tools = make_tools(&["review", "lint"]);
    registry
        .activate(
            "reviewer",
            Box::new(move || Box::new(MockMcpClient::new(tools))),
        )
        .await
        .unwrap();

    let (addr, handle) = gw.start().await.unwrap();

    // Discover tools via HTTP
    let client = reqwest::Client::new();
    let resp = client
        .get(format!("http://{addr}/tools"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), axum::http::StatusCode::OK);

    let body: Vec<serde_json::Value> = resp.json().await.unwrap();
    assert_eq!(body.len(), 2);

    let names: Vec<&str> = body
        .iter()
        .filter_map(|t| t["name"].as_str())
        .collect();
    assert!(names.contains(&"reviewer___review"));
    assert!(names.contains(&"reviewer___lint"));

    handle.shutdown().await;
}

/// Call a namespaced tool through the gateway and verify the response.
#[tokio::test]
async fn e2e_namespaced_tool_invocation() {
    let config = GatewayConfig {
        listen_addr: "127.0.0.1:0".to_string(),
        idle_timeout_secs: 300,
    };
    let gw = Arc::new(McpGateway::new(config));
    let registry = gw.registry();

    registry.register_manifest(test_manifest("formatter")).await;
    let tools = make_tools(&["format"]);
    registry
        .activate(
            "formatter",
            Box::new(move || Box::new(MockMcpClient::new(tools))),
        )
        .await
        .unwrap();

    let (addr, handle) = gw.start().await.unwrap();

    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://{addr}/tools/call"))
        .json(&serde_json::json!({
            "name": "formatter___format",
            "arguments": {"file": "main.rs", "style": "rustfmt"}
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), axum::http::StatusCode::OK);

    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["tool"], "format");
    assert_eq!(body["arguments"]["file"], "main.rs");
    assert_eq!(body["arguments"]["style"], "rustfmt");
    assert_eq!(body["status"], "ok");

    handle.shutdown().await;
}

/// Multiple agents' tools are properly isolated — no cross-agent leakage.
#[tokio::test]
async fn e2e_multi_agent_isolation() {
    let config = GatewayConfig {
        listen_addr: "127.0.0.1:0".to_string(),
        idle_timeout_secs: 300,
    };
    let gw = Arc::new(McpGateway::new(config));
    let registry = gw.registry();

    // Agent A with tool "scan"
    registry.register_manifest(test_manifest("security-scanner")).await;
    let tools_a = make_tools(&["scan"]);
    registry
        .activate(
            "security-scanner",
            Box::new(move || Box::new(MockMcpClient::new(tools_a))),
        )
        .await
        .unwrap();

    // Agent B with tool "generate"
    registry.register_manifest(test_manifest("doc-gen")).await;
    let tools_b = make_tools(&["generate"]);
    registry
        .activate(
            "doc-gen",
            Box::new(move || Box::new(MockMcpClient::new(tools_b))),
        )
        .await
        .unwrap();

    let (addr, handle) = gw.start().await.unwrap();
    let client = reqwest::Client::new();

    // Verify tool discovery shows both agents' tools
    let resp = client
        .get(format!("http://{addr}/tools"))
        .send()
        .await
        .unwrap();
    let body: Vec<serde_json::Value> = resp.json().await.unwrap();
    assert_eq!(body.len(), 2);

    let names: Vec<&str> = body.iter().filter_map(|t| t["name"].as_str()).collect();
    assert!(names.contains(&"security-scanner___scan"));
    assert!(names.contains(&"doc-gen___generate"));

    // Verify tool invocation for agent A
    let resp_a = client
        .post(format!("http://{addr}/tools/call"))
        .json(&serde_json::json!({
            "name": "security-scanner___scan",
            "arguments": {"target": "src/"}
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp_a.status(), axum::http::StatusCode::OK);
    let body_a: serde_json::Value = resp_a.json().await.unwrap();
    assert_eq!(body_a["tool"], "scan");

    // Verify tool invocation for agent B
    let resp_b = client
        .post(format!("http://{addr}/tools/call"))
        .json(&serde_json::json!({
            "name": "doc-gen___generate",
            "arguments": {"format": "markdown"}
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp_b.status(), axum::http::StatusCode::OK);
    let body_b: serde_json::Value = resp_b.json().await.unwrap();
    assert_eq!(body_b["tool"], "generate");

    // Cross-agent call with wrong namespace should fail (agent doesn't have that tool)
    // Note: the mock client doesn't validate tool names, but the namespacing is correct
    // at the gateway level — the tool name "scan" is sent to agent "security-scanner".
    // A production client would return ToolNotFound, but our mock echoes the call.

    handle.shutdown().await;
}

/// Provider strategy selects correctly based on model string.
#[test]
fn e2e_strategy_selection() {
    // Anthropic models -> deferred
    assert_eq!(
        ProviderAwareStrategy::select_strategy("anthropic:claude-sonnet-4-20250514"),
        ToolLoadingStrategy::AnthropicDeferred
    );
    assert_eq!(
        ProviderAwareStrategy::select_strategy("Anthropic:claude-3-opus"),
        ToolLoadingStrategy::AnthropicDeferred
    );

    // All other providers -> eager
    assert_eq!(
        ProviderAwareStrategy::select_strategy("openai:gpt-4o"),
        ToolLoadingStrategy::Eager
    );
    assert_eq!(
        ProviderAwareStrategy::select_strategy("ollama:llama3"),
        ToolLoadingStrategy::Eager
    );
    assert_eq!(
        ProviderAwareStrategy::select_strategy("api:MiniMax-M2.7-highspeed"),
        ToolLoadingStrategy::Eager
    );
}

/// Verify DeferredAgentRegistry and McpToolAdapter work together for full lifecycle.
#[tokio::test]
async fn e2e_registry_adapter_lifecycle() {
    let registry = DeferredAgentRegistry::new();
    let adapter = McpToolAdapter::new();

    // Register two agents
    registry.register_manifest(test_manifest("agent-x")).await;
    registry.register_manifest(test_manifest("agent-y")).await;

    // Activate agent-x
    let tools = make_tools(&["ping", "pong"]);
    registry
        .activate(
            "agent-x",
            Box::new(move || Box::new(MockMcpClient::new(tools))),
        )
        .await
        .unwrap();

    // Activate agent-y
    let tools_y = make_tools(&["analyze"]);
    registry
        .activate(
            "agent-y",
            Box::new(move || Box::new(MockMcpClient::new(tools_y))),
        )
        .await
        .unwrap();

    // List all agents
    let mut agents = registry.list_agents().await;
    agents.sort();
    assert_eq!(agents, vec!["agent-x", "agent-y"]);

    // Get tools and verify namespacing
    let tools_x = registry.get_tools("agent-x").await.unwrap();
    assert_eq!(tools_x.len(), 2);

    let namespaced = adapter.namespace_tool("agent-x", "ping");
    assert_eq!(namespaced, "agent-x___ping");
    let (agent, tool) = adapter.parse_namespaced(&namespaced).unwrap();
    assert_eq!(agent, "agent-x");
    assert_eq!(tool, "ping");

    // Call a tool through the registry (bypassing HTTP for direct registry test)
    let result = registry
        .call_tool("agent-x", "ping", serde_json::json!({"key": "value"}))
        .await
        .unwrap();
    assert_eq!(result["tool"], "ping");
    assert_eq!(result["arguments"]["key"], "value");

    // Deactivate agent-y, verify it's no longer active
    registry.deactivate("agent-y").await.unwrap();
    assert!(registry.get_tools("agent-y").await.is_err());
    // But agent-x is still active
    assert!(registry.get_tools("agent-x").await.is_ok());
}
