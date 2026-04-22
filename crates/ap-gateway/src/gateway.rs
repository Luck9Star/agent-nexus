//! MCP Gateway: axum HTTP server that aggregates agent tools.

use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::State;
use axum::routing::{get, post};
use axum::{Json, Router};
use thiserror::Error;
use tokio::sync::Mutex;

use crate::deferred_registry::{DeferredAgentRegistry, RegistryError};
use crate::tool_adapter::McpToolAdapter;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors from the MCP Gateway.
#[derive(Debug, Error)]
pub enum GatewayError {
    #[error("Bind error: {0}")]
    Bind(#[from] std::io::Error),

    #[error("Registry error: {0}")]
    Registry(#[from] RegistryError),

    #[error("Tool call failed: {0}")]
    ToolCall(String),
}

impl axum::response::IntoResponse for GatewayError {
    fn into_response(self) -> axum::response::Response {
        let (status, message) = match &self {
            GatewayError::Bind(_) => (axum::http::StatusCode::INTERNAL_SERVER_ERROR, self.to_string()),
            GatewayError::Registry(RegistryError::NotFound(_)) => {
                (axum::http::StatusCode::NOT_FOUND, self.to_string())
            }
            GatewayError::Registry(RegistryError::NotActive(_)) => {
                (axum::http::StatusCode::SERVICE_UNAVAILABLE, self.to_string())
            }
            GatewayError::Registry(RegistryError::ActivationFailed(_)) => {
                (axum::http::StatusCode::INTERNAL_SERVER_ERROR, self.to_string())
            }
            GatewayError::ToolCall(_) => {
                (axum::http::StatusCode::INTERNAL_SERVER_ERROR, self.to_string())
            }
        };
        (status, Json(serde_json::json!({ "error": message }))).into_response()
    }
}

// ---------------------------------------------------------------------------
// Config & Gateway
// ---------------------------------------------------------------------------

/// Configuration for the MCP Gateway.
pub struct GatewayConfig {
    pub listen_addr: String,
    pub idle_timeout_secs: u64,
}

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            listen_addr: "127.0.0.1:0".to_string(),
            idle_timeout_secs: 300,
        }
    }
}

/// The main MCP Gateway: an axum HTTP server that aggregates tools from
/// multiple agents behind a unified namespace.
pub struct McpGateway {
    config: GatewayConfig,
    registry: Arc<Mutex<DeferredAgentRegistry>>,
    #[allow(dead_code)] // Used for direct namespace lookups in production
    adapter: McpToolAdapter,
}

impl McpGateway {
    /// Create a new gateway with the given configuration.
    pub fn new(config: GatewayConfig) -> Self {
        let registry = Arc::new(Mutex::new(DeferredAgentRegistry::with_idle_timeout(
            std::time::Duration::from_secs(config.idle_timeout_secs),
        )));
        Self {
            config,
            registry,
            adapter: McpToolAdapter::new(),
        }
    }

    /// Get a reference to the inner registry for external registration.
    pub fn registry(&self) -> Arc<Mutex<DeferredAgentRegistry>> {
        Arc::clone(&self.registry)
    }

    /// Start the HTTP server. Returns the bound address.
    ///
    /// The server runs on a background tokio task.
    pub async fn start(self: Arc<Self>) -> Result<SocketAddr, GatewayError> {
        let app = Router::new()
            .route("/tools", get(Self::list_tools_handler))
            .route("/tools/call", post(Self::call_tool_handler))
            .with_state(Arc::clone(&self));

        let listener = tokio::net::TcpListener::bind(&self.config.listen_addr).await?;
        let addr = listener.local_addr()?;
        tokio::spawn(async move {
            axum::serve(listener, app).await.ok();
        });
        Ok(addr)
    }

    /// Handler for GET /tools: list all tools from all registered agents.
    async fn list_tools_handler(
        State(gw): State<Arc<Self>>,
    ) -> Json<Vec<serde_json::Value>> {
        let registry = gw.registry.lock().await;
        let agent_names = registry.list_agents().await;

        let mut all_tools = Vec::new();
        for name in agent_names {
            // Try to get cached tools; skip inactive agents
            if let Ok(tools) = registry.get_tools(&name).await {
                let schemas = crate::schema::merge_tool_schemas(&name, &tools);
                all_tools.extend(schemas);
            }
        }
        Json(all_tools)
    }

    /// Handler for POST /tools/call: invoke a namespaced tool.
    async fn call_tool_handler(
        State(gw): State<Arc<Self>>,
        Json(req): Json<serde_json::Value>,
    ) -> Result<Json<serde_json::Value>, GatewayError> {
        // Parse the namespaced tool name.
        let (agent, tool, arguments) = crate::schema::extract_tool_call(&req)
            .ok_or_else(|| {
                GatewayError::ToolCall("Invalid tool call request: missing or malformed 'name'".to_string())
            })?;

        // Ensure agent is active (attempt activation with NoopMcpClient if not).
        // In production, a real client factory would be injected. For now we
        // just try to call if already active.
        let registry = gw.registry.lock().await;
        let result = registry.call_tool(&agent, &tool, arguments).await?;
        Ok(Json(result))
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use ap_core::models::agent::{AgentManifest, AgentType};
    use ap_runtime::mcp_client::ToolInfo;
    use std::future::Future;
    use std::pin::Pin;

    /// Mock MCP client for gateway integration tests.
    struct MockMcpClient {
        tools: Vec<ToolInfo>,
    }

    impl MockMcpClient {
        fn new(tools: Vec<ToolInfo>) -> Self {
            Self { tools }
        }
    }

    impl ap_runtime::mcp_client::McpClient for MockMcpClient {
        fn list_tools(
            &self,
        ) -> Pin<
            Box<
                dyn Future<
                        Output = Result<Vec<ToolInfo>, ap_runtime::mcp_client::McpError>,
                    > + Send
                    + '_,
            >,
        > {
            let tools = self.tools.clone();
            Box::pin(async move { Ok(tools) })
        }

        fn call_tool(
            &self,
            name: &str,
            arguments: serde_json::Value,
        ) -> Pin<
            Box<
                dyn Future<
                        Output = Result<serde_json::Value, ap_runtime::mcp_client::McpError>,
                    > + Send
                    + '_,
            >,
        > {
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

    fn test_manifest(name: &str) -> AgentManifest {
        AgentManifest {
            name: name.to_string(),
            version: "0.1.0".to_string(),
            agent_type: AgentType::Atomic,
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
            isolation: None,
            color: None,
            background: false,
            initial_prompt: None,
        }
    }

    fn sample_tools() -> Vec<ToolInfo> {
        vec![ToolInfo {
            name: "review".to_string(),
            description: Some("Review code".to_string()),
            input_schema: Some(serde_json::json!({"type": "object"})),
        }]
    }

    #[tokio::test]
    async fn start_gateway_binds_to_address() {
        let config = GatewayConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            idle_timeout_secs: 300,
        };
        let gw = Arc::new(McpGateway::new(config));
        let addr = gw.start().await.unwrap();
        assert!(addr.port() > 0);
    }

    #[tokio::test]
    async fn tools_endpoint_returns_empty_when_no_agents() {
        let config = GatewayConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            idle_timeout_secs: 300,
        };
        let gw = Arc::new(McpGateway::new(config));
        let addr = gw.start().await.unwrap();

        let client = reqwest::Client::new();
        let resp = client
            .get(format!("http://{addr}/tools"))
            .send()
            .await
            .unwrap();
        assert_eq!(resp.status(), axum::http::StatusCode::OK);

        let body: Vec<serde_json::Value> = resp.json().await.unwrap();
        assert!(body.is_empty());
    }

    #[tokio::test]
    async fn tools_endpoint_lists_active_agent_tools() {
        let config = GatewayConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            idle_timeout_secs: 300,
        };
        let gw = Arc::new(McpGateway::new(config));
        let registry = gw.registry();

        // Register and activate an agent
        registry.lock().await.register_manifest(test_manifest("code-reviewer")).await;
        let tools = sample_tools();
        registry
            .lock()
            .await
            .activate(
                "code-reviewer",
                Box::new(move || Box::new(MockMcpClient::new(tools))),
            )
            .await
            .unwrap();

        let addr = gw.start().await.unwrap();

        let client = reqwest::Client::new();
        let resp = client
            .get(format!("http://{addr}/tools"))
            .send()
            .await
            .unwrap();
        assert_eq!(resp.status(), axum::http::StatusCode::OK);

        let body: Vec<serde_json::Value> = resp.json().await.unwrap();
        assert_eq!(body.len(), 1);
        assert_eq!(body[0]["name"], "code-reviewer___review");
    }

    #[tokio::test]
    async fn call_tool_endpoint_forwards_to_agent() {
        let config = GatewayConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            idle_timeout_secs: 300,
        };
        let gw = Arc::new(McpGateway::new(config));
        let registry = gw.registry();

        // Register and activate an agent
        registry.lock().await.register_manifest(test_manifest("code-reviewer")).await;
        let tools = sample_tools();
        registry
            .lock()
            .await
            .activate(
                "code-reviewer",
                Box::new(move || Box::new(MockMcpClient::new(tools))),
            )
            .await
            .unwrap();

        let addr = gw.start().await.unwrap();

        let client = reqwest::Client::new();
        let resp = client
            .post(format!("http://{addr}/tools/call"))
            .json(&serde_json::json!({
                "name": "code-reviewer___review",
                "arguments": {"path": "/src/main.rs"}
            }))
            .send()
            .await
            .unwrap();
        assert_eq!(resp.status(), axum::http::StatusCode::OK);

        let body: serde_json::Value = resp.json().await.unwrap();
        assert_eq!(body["tool"], "review");
        assert_eq!(body["arguments"]["path"], "/src/main.rs");
        assert_eq!(body["status"], "ok");
    }

    #[tokio::test]
    async fn call_tool_inactive_agent_returns_error() {
        let config = GatewayConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            idle_timeout_secs: 300,
        };
        let gw = Arc::new(McpGateway::new(config));
        let registry = gw.registry();

        // Register but do NOT activate
        registry.lock().await.register_manifest(test_manifest("reviewer")).await;

        let addr = gw.start().await.unwrap();

        let client = reqwest::Client::new();
        let resp = client
            .post(format!("http://{addr}/tools/call"))
            .json(&serde_json::json!({
                "name": "reviewer___some_tool",
                "arguments": {}
            }))
            .send()
            .await
            .unwrap();
        // Agent is registered but not active -> 503 Service Unavailable
        assert_eq!(resp.status(), axum::http::StatusCode::SERVICE_UNAVAILABLE);
    }

    #[tokio::test]
    async fn call_tool_malformed_name_returns_error() {
        let config = GatewayConfig {
            listen_addr: "127.0.0.1:0".to_string(),
            idle_timeout_secs: 300,
        };
        let gw = Arc::new(McpGateway::new(config));
        let addr = gw.start().await.unwrap();

        let client = reqwest::Client::new();
        let resp = client
            .post(format!("http://{addr}/tools/call"))
            .json(&serde_json::json!({
                "name": "no_separator",
                "arguments": {}
            }))
            .send()
            .await
            .unwrap();
        assert_eq!(resp.status(), axum::http::StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[tokio::test]
    async fn default_config_works() {
        let config = GatewayConfig::default();
        assert_eq!(config.listen_addr, "127.0.0.1:0");
        assert_eq!(config.idle_timeout_secs, 300);
    }
}
