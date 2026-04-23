//! Lazy-loading agent registry: only starts agent subprocesses when their tools are needed.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use ap_core::models::agent::AgentManifest;
use ap_runtime::mcp_client::{McpClient, ToolInfo};
use thiserror::Error;
use tokio::sync::{Mutex, OnceCell};

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors from the deferred agent registry.
#[derive(Debug, Error)]
pub enum RegistryError {
    #[error("Agent not found: {0}")]
    NotFound(String),

    #[error("Agent activation failed: {0}")]
    ActivationFailed(String),

    #[error("Agent not active: {0}")]
    NotActive(String),

    #[error("Tool execution failed: {0}")]
    ToolExecutionFailed(String),
}

// ---------------------------------------------------------------------------
// Internal slot
// ---------------------------------------------------------------------------

struct AgentSlot {
    #[allow(dead_code)] // Used for agent metadata lookups in production
    manifest: AgentManifest,
    /// `OnceCell` ensures only one caller creates the client, eliminating the
    /// TOCTOU race where multiple concurrent `activate()` calls each create
    /// redundant MCP clients (which may spawn expensive subprocesses).
    client: OnceCell<Arc<Mutex<Box<dyn McpClient>>>>,
    tools: OnceCell<Arc<Vec<ToolInfo>>>,
    last_used: std::time::Instant,
}

// ---------------------------------------------------------------------------
// DeferredAgentRegistry
// ---------------------------------------------------------------------------

/// Lazy-loading registry that only starts agent subprocesses when their tools
/// are needed.
///
/// Agents are registered with a manifest and remain inactive until `activate`
/// is called. Idle agents are stopped after a configurable timeout.
///
/// Uses `tokio::sync::OnceCell` for client initialization to guarantee exactly
/// one MCP client is created per agent, even under concurrent activation.
/// Slots are wrapped in `Arc` so `OnceCell` references can outlive the global lock.
pub struct DeferredAgentRegistry {
    agents: Arc<Mutex<HashMap<String, Arc<Mutex<AgentSlot>>>>>,
    idle_timeout: Duration,
}

impl DeferredAgentRegistry {
    const DEFAULT_IDLE_TIMEOUT: Duration = Duration::from_mins(5); // 5 minutes

    /// Create a new registry with the default 5-minute idle timeout.
    #[must_use] 
    pub fn new() -> Self {
        Self {
            agents: Arc::new(Mutex::new(HashMap::new())),
            idle_timeout: Self::DEFAULT_IDLE_TIMEOUT,
        }
    }

    /// Create a new registry with a custom idle timeout.
    #[must_use] 
    pub fn with_idle_timeout(timeout: Duration) -> Self {
        Self {
            agents: Arc::new(Mutex::new(HashMap::new())),
            idle_timeout: timeout,
        }
    }

    /// Register an agent manifest without starting the subprocess.
    pub async fn register_manifest(&self, manifest: AgentManifest) {
        let name = manifest.name.clone();
        let slot = Arc::new(Mutex::new(AgentSlot {
            manifest,
            client: OnceCell::new(),
            tools: OnceCell::new(),
            last_used: std::time::Instant::now(),
        }));
        self.agents.lock().await.insert(name, slot);
    }

    /// Check whether an agent with the given name is registered.
    pub async fn has_agent(&self, name: &str) -> bool {
        self.agents.lock().await.contains_key(name)
    }

    /// List the names of all registered agents (active or not).
    pub async fn list_agents(&self) -> Vec<String> {
        self.agents
            .lock()
            .await
            .keys()
            .cloned()
            .collect()
    }

    /// Activate an agent: start its subprocess, connect MCP, list tools.
    ///
    /// The `client_factory` closure is called at most once per agent thanks to
    /// `OnceCell`. Concurrent callers will await the same initialization
    /// instead of creating duplicate clients.
    ///
    /// Returns the list of tools discovered on the agent.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn activate(
        &self,
        name: &str,
        client_factory: Box<dyn FnOnce() -> Box<dyn McpClient> + Send>,
    ) -> Result<Vec<ToolInfo>, RegistryError> {
        // Brief lock to get an Arc reference to the slot.
        let slot_arc = {
            let agents = self.agents.lock().await;
            agents
                .get(name)
                .cloned()
                .ok_or_else(|| RegistryError::NotFound(name.to_string()))?
        }; // Lock released — all subsequent work uses per-slot synchronization.

        let mut slot = slot_arc.lock().await;

        // Update last_used first to avoid borrow conflicts with OnceCell.
        slot.last_used = std::time::Instant::now();

        // Initialize the client exactly once.
        let client_arc = slot
            .client
            .get_or_init(|| async {
                let client = client_factory();
                Arc::new(Mutex::new(client))
            })
            .await;

        // Initialize the tools list exactly once.
        let tools_arc = slot
            .tools
            .get_or_init(|| async {
                let client = client_arc.lock().await;
                let tools = client
                    .list_tools()
                    .await
                    .map_err(|e| RegistryError::ActivationFailed(e.to_string()))
                    .unwrap_or_default();
                Arc::new(tools)
            })
            .await;

        Ok(tools_arc.to_vec())
    }

    /// Get the cached tool list for an active agent.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn get_tools(&self, name: &str) -> Result<Arc<Vec<ToolInfo>>, RegistryError> {
        let agents = self.agents.lock().await;
        let slot_arc = agents
            .get(name)
            .ok_or_else(|| RegistryError::NotFound(name.to_string()))?;
        let slot = slot_arc.lock().await;

        slot.tools
            .get()
            .cloned()
            .ok_or_else(|| RegistryError::NotActive(name.to_string()))
    }

    /// Call a tool on an active agent via its MCP client.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn call_tool(
        &self,
        name: &str,
        tool_name: &str,
        args: serde_json::Value,
    ) -> Result<serde_json::Value, RegistryError> {
        // Phase 1: Get client Arc and update last_used (brief lock)
        let client_arc = {
            let agents = self.agents.lock().await;
            let slot_arc = agents
                .get(name)
                .ok_or_else(|| RegistryError::NotFound(name.to_string()))?;
            let mut slot = slot_arc.lock().await;
            slot.last_used = std::time::Instant::now();
            slot.client
                .get()
                .cloned()
                .ok_or_else(|| RegistryError::NotActive(name.to_string()))?
        };
        // Phase 2: Call tool outside global lock (only per-agent mutex held)
        let client = client_arc.lock().await;
        client
            .call_tool(tool_name, args)
            .await
            .map_err(|e| RegistryError::ToolExecutionFailed(e.to_string()))
    }

    /// Deactivate all agents that have been idle longer than the timeout.
    ///
    /// Returns the number of agents deactivated.
    ///
    /// Two-phase approach: collect idle clients under the lock, then shut them
    /// down outside the lock to avoid blocking all registry operations during I/O.
    pub async fn deactivate_idle(&self) -> usize {
        // Phase 1: Collect idle clients and remove from registry (under lock)
        let to_shutdown: Vec<Arc<Mutex<Box<dyn McpClient>>>> = {
            let agents = self.agents.lock().await;
            let timeout = self.idle_timeout;
            let mut idle_clients = Vec::new();
            for slot_arc in agents.values() {
                let mut slot = slot_arc.lock().await;
                if slot.client.get().is_some() && slot.last_used.elapsed() > timeout {
                    if let Some(client_arc) = slot.client.take() {
                        idle_clients.push(client_arc);
                    }
                    slot.tools.take();
                }
            }
            idle_clients
        };

        let count = to_shutdown.len();

        // Phase 2: Shutdown outside the lock
        for client_arc in to_shutdown {
            let mut client = client_arc.lock().await;
            client.shutdown().await;
        }

        count
    }

    /// Deactivate a specific agent by name.
    ///
    /// Two-phase approach: remove the agent under the lock, then shut it down
    /// outside the lock to avoid blocking all registry operations during I/O.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn deactivate(&self, name: &str) -> Result<(), RegistryError> {
        // Phase 1: Remove agent and take client Arc (under lock)
        let client_arc = {
            let agents = self.agents.lock().await;
            let slot_arc = agents
                .get(name)
                .ok_or_else(|| RegistryError::NotFound(name.to_string()))?;
            let mut slot = slot_arc.lock().await;
            slot.tools.take();
            slot.client.take()
        }; // Lock dropped here

        // Phase 2: Shutdown outside the lock
        if let Some(client_arc) = client_arc {
            let mut client = client_arc.lock().await;
            client.shutdown().await;
        }
        Ok(())
    }
}

impl Default for DeferredAgentRegistry {
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
    use ap_core::models::agent::{AgentType, AgentManifest};
    use ap_runtime::mcp_client::ToolInfo;
    use std::future::Future;
    use std::pin::Pin;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// A mock MCP client that returns predefined tools and echoes calls.
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
        ) -> Pin<Box<dyn Future<Output = Result<Vec<ToolInfo>, ap_runtime::mcp_client::McpError>> + Send + '_>>
        {
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
        vec![
            ToolInfo {
                name: "review".to_string(),
                description: Some("Review code".to_string()),
                input_schema: Some(serde_json::json!({"type": "object"})),
            },
            ToolInfo {
                name: "lint".to_string(),
                description: Some("Lint code".to_string()),
                input_schema: None,
            },
        ]
    }

    #[tokio::test]
    async fn register_and_list() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("agent-a")).await;
        registry.register_manifest(test_manifest("agent-b")).await;

        assert!(registry.has_agent("agent-a").await);
        assert!(registry.has_agent("agent-b").await);
        assert!(!registry.has_agent("agent-c").await);

        let mut names = registry.list_agents().await;
        names.sort();
        assert_eq!(names, vec!["agent-a", "agent-b"]);
    }

    #[tokio::test]
    async fn activate_with_mock_client() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("reviewer")).await;

        let tools = sample_tools();
        let expected = tools.clone();
        let result = registry
            .activate(
                "reviewer",
                Box::new(move || Box::new(MockMcpClient::new(tools))),
            )
            .await
            .unwrap();

        assert_eq!(result.len(), 2);
        assert_eq!(result, expected);
    }

    #[tokio::test]
    async fn activate_already_active_returns_cached() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("reviewer")).await;

        let tools = sample_tools();
        registry
            .activate(
                "reviewer",
                Box::new(move || Box::new(MockMcpClient::new(tools))),
            )
            .await
            .unwrap();

        // Second activation should succeed without needing a new factory
        // (but we must provide one that would panic if called).
        let result = registry
            .activate(
                "reviewer",
                Box::new(|| panic!("Factory should not be called")),
            )
            .await
            .unwrap();
        assert_eq!(result.len(), 2);
    }

    #[tokio::test]
    async fn activate_unknown_agent_returns_not_found() {
        let registry = DeferredAgentRegistry::new();
        let err = registry
            .activate(
                "nonexistent",
                Box::new(|| Box::new(MockMcpClient::new(vec![]))),
            )
            .await
            .unwrap_err();
        assert!(matches!(err, RegistryError::NotFound(_)));
    }

    #[tokio::test]
    async fn get_tools_before_activate_returns_not_active() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("reviewer")).await;

        let err = registry.get_tools("reviewer").await.unwrap_err();
        assert!(matches!(err, RegistryError::NotActive(_)));
    }

    #[tokio::test]
    async fn call_tool_delegates_to_client() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("reviewer")).await;

        let tools = sample_tools();
        registry
            .activate(
                "reviewer",
                Box::new(move || Box::new(MockMcpClient::new(tools))),
            )
            .await
            .unwrap();

        let result = registry
            .call_tool("reviewer", "review", serde_json::json!({"path": "/src/main.rs"}))
            .await
            .unwrap();

        assert_eq!(result["tool"], "review");
        assert_eq!(result["arguments"]["path"], "/src/main.rs");
        assert_eq!(result["status"], "ok");
    }

    #[tokio::test]
    async fn call_tool_inactive_returns_not_active() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("reviewer")).await;

        let err = registry
            .call_tool("reviewer", "review", serde_json::json!({}))
            .await
            .unwrap_err();
        assert!(matches!(err, RegistryError::NotActive(_)));
    }

    #[tokio::test]
    async fn deactivate_specific_agent() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("reviewer")).await;

        let tools = sample_tools();
        registry
            .activate(
                "reviewer",
                Box::new(move || Box::new(MockMcpClient::new(tools))),
            )
            .await
            .unwrap();

        registry.deactivate("reviewer").await.unwrap();

        // Tools should be empty now
        let err = registry.get_tools("reviewer").await.unwrap_err();
        assert!(matches!(err, RegistryError::NotActive(_)));
    }

    #[tokio::test]
    async fn deactivate_unknown_agent_returns_not_found() {
        let registry = DeferredAgentRegistry::new();
        let err = registry.deactivate("nonexistent").await.unwrap_err();
        assert!(matches!(err, RegistryError::NotFound(_)));
    }

    #[tokio::test]
    async fn deactivate_idle_removes_stale_agents() {
        let registry =
            DeferredAgentRegistry::with_idle_timeout(Duration::from_millis(50));

        // Activate "stale" first
        registry.register_manifest(test_manifest("stale")).await;
        let tools = sample_tools();
        registry
            .activate(
                "stale",
                Box::new(move || Box::new(MockMcpClient::new(tools))),
            )
            .await
            .unwrap();

        // Wait long enough for "stale" to exceed the idle timeout
        tokio::time::sleep(Duration::from_millis(80)).await;

        // Now activate "fresh" — its timer starts now
        registry.register_manifest(test_manifest("fresh")).await;
        let tools2 = sample_tools();
        registry
            .activate(
                "fresh",
                Box::new(move || Box::new(MockMcpClient::new(tools2))),
            )
            .await
            .unwrap();

        let deactivated = registry.deactivate_idle().await;
        assert_eq!(deactivated, 1);

        // "stale" should be inactive
        assert!(registry.get_tools("stale").await.is_err());
        // "fresh" should still be active
        assert!(registry.get_tools("fresh").await.is_ok());
    }

    #[tokio::test]
    async fn default_trait_works() {
        let registry = DeferredAgentRegistry::default();
        assert!(registry.list_agents().await.is_empty());
    }

    /// Verify that a second activation does not call the factory again.
    #[tokio::test]
    async fn concurrent_activate_calls_factory_once() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(test_manifest("concurrent")).await;

        // First activation with a counter factory.
        let counter = Arc::new(AtomicUsize::new(0));
        let counter_clone = counter.clone();
        registry
            .activate(
                "concurrent",
                Box::new(move || {
                    counter_clone.fetch_add(1, Ordering::SeqCst);
                    Box::new(MockMcpClient::new(sample_tools()))
                }),
            )
            .await
            .unwrap();

        assert_eq!(counter.load(Ordering::SeqCst), 1);

        // Second call must NOT invoke the factory.
        registry
            .activate(
                "concurrent",
                Box::new(|| panic!("Factory should not be called a second time")),
            )
            .await
            .unwrap();

        assert_eq!(counter.load(Ordering::SeqCst), 1);
    }
}
