# Phase 9: ap-gateway — MCP Gateway

> **Goal:** Port the MCP Gateway that aggregates agent tools via MCP protocol, with deferred agent loading.

**Python source:** `src/agent_nexus/platform/gateway/` (1,320 lines)
**Rust target:** `crates/ap-gateway/src/`
**Depends on:** Phase 1 (ap-core models), Phase 6 (ap-runtime MCP client)

**Files:**
- Create: `crates/ap-gateway/src/lib.rs` (overwrite skeleton)
- Create: `crates/ap-gateway/src/gateway.rs`
- Create: `crates/ap-gateway/src/tool_adapter.rs`
- Create: `crates/ap-gateway/src/deferred_registry.rs`
- Create: `crates/ap-gateway/src/schema.rs`

---

## Task 9.1: DeferredAgentRegistry

**Python source:** `src/agent_nexus/platform/gateway/deferred_registry.py`
**Rust target:** `crates/ap-gateway/src/deferred_registry.rs`

Lazy loading: only start agent subprocesses when their tools are actually needed.

- [ ] **Step 1: Write registry tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_agent_manifest() {
        let registry = DeferredAgentRegistry::new();
        let manifest = AgentManifest {
            name: "code-reviewer".into(),
            ..Default::default()
        };
        registry.register_manifest(manifest);
        assert!(registry.has_agent("code-reviewer"));
    }

    #[test]
    fn list_available_agents() {
        let registry = DeferredAgentRegistry::new();
        registry.register_manifest(AgentManifest { name: "a".into(), ..Default::default() });
        registry.register_manifest(AgentManifest { name: "b".into(), ..Default::default() });
        assert_eq!(registry.list_agents().len(), 2);
    }

    #[tokio::test]
    async fn activate_agent_loads_tools() {
        // When activated, starts subprocess, connects MCP, loads tool schemas
        // For testing: mock the subprocess/MCP connection
    }

    #[tokio::test]
    async fn idle_timeout_deactivates() {
        // After idle period, agent subprocess is stopped
    }
}
```

- [ ] **Step 2: Implement DeferredAgentRegistry**

```rust
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;
use ap_core::models::agent::AgentManifest;
use ap_runtime::mcp_client::{McpClient, ToolInfo};

struct AgentState {
    manifest: AgentManifest,
    mcp_client: Option<Box<dyn McpClient>>,
    tools: Vec<ToolInfo>,
    last_used: std::time::Instant,
}

pub struct DeferredAgentRegistry {
    agents: Arc<Mutex<HashMap<String, AgentState>>>,
    idle_timeout: std::time::Duration,
}

impl DeferredAgentRegistry {
    pub fn new() -> Self {
        Self {
            agents: Arc::new(Mutex::new(HashMap::new())),
            idle_timeout: std::time::Duration::from_secs(300), // 5 min default
        }
    }

    pub fn register_manifest(&self, manifest: AgentManifest) {
        // Add to agents map without starting process
        todo!()
    }

    pub fn has_agent(&self, name: &str) -> bool {
        // Check if agent manifest is registered
        todo!()
    }

    pub fn list_agents(&self) -> Vec<String> {
        // Return all registered agent names
        todo!()
    }

    pub async fn activate(&self, agent_name: &str) -> Result<Vec<ToolInfo>, RegistryError> {
        // 1. Check if already active
        // 2. If not: start subprocess via ap-runtime
        // 3. Connect MCP client
        // 4. List tools from MCP server
        // 5. Cache tools
        todo!()
    }

    pub async fn deactivate_idle(&self) {
        // Check all agents, deactivate those idle > idle_timeout
        todo!()
    }
}

#[derive(Debug, thiserror::Error)]
pub enum RegistryError {
    #[error("Agent not found: {0}")]
    NotFound(String),
    #[error("Activation failed: {0}")]
    ActivationFailed(String),
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-gateway -- deferred_registry
git add crates/ap-gateway/src/deferred_registry.rs
git commit -m "feat(ap-gateway): DeferredAgentRegistry with lazy loading"
```

---

## Task 9.2: Tool Adapter

**Python source:** `src/agent_nexus/platform/gateway/tool_adapter.py`
**Rust target:** `crates/ap-gateway/src/tool_adapter.rs`

Adapts per-agent MCP tools into a unified tool namespace (e.g., `code_reviewer__review`).

- [ ] **Step 1: Write adapter tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn namespace_tool_name() {
        let adapter = McpToolAdapter::new();
        let name = adapter.namespace_tool("code-reviewer", "review");
        assert_eq!(name, "code_reviewer__review");
    }

    #[test]
    fn strip_namespace() {
        let adapter = McpToolAdapter::new();
        let (agent, tool) = adapter.parse_namespaced("code_reviewer__review").unwrap();
        assert_eq!(agent, "code-reviewer");
        assert_eq!(tool, "review");
    }

    #[test]
    fn adapt_tool_schema() {
        // Convert JSON Schema from MCP to gateway's expected format
    }
}
```

- [ ] **Step 2: Implement + verify + commit**

```bash
cargo test -p ap-gateway -- tool_adapter
git add crates/ap-gateway/src/tool_adapter.rs
git commit -m "feat(ap-gateway): McpToolAdapter with namespace translation"
```

---

## Task 9.3: JSON Schema conversion

**Rust target:** `crates/ap-gateway/src/schema.rs`

Converts between JSON Schema formats used by MCP and the gateway.

- [ ] **Write tests + implement + commit**

```bash
cargo test -p ap-gateway -- schema
git add crates/ap-gateway/src/schema.rs
git commit -m "feat(ap-gateway): JSON Schema conversion utilities"
```

---

## Task 9.4: MCPGateway (facade)

**Python source:** `src/agent_nexus/platform/gateway/gateway.py`
**Rust target:** `crates/ap-gateway/src/gateway.rs`

The main gateway that:
1. Starts an axum HTTP server for external tool calls
2. Manages the deferred registry
3. Routes tool calls to the appropriate agent via MCP

- [ ] **Step 1: Write gateway tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn gateway_starts_and_responds() {
        let gateway = McpGateway::new(GatewayConfig {
            listen_addr: "127.0.0.1:0".into(), // random port
            idle_timeout_secs: 300,
        });
        let addr = gateway.start().await.unwrap();
        // Verify HTTP endpoint responds
    }

    #[tokio::test]
    async fn list_tools_returns_namespaced() {
        // Register agent, activate, list tools
        // Tools should be namespaced (agent__tool)
    }

    #[tokio::test]
    async fn call_tool_routes_to_agent() {
        // Call namespaced tool, verify it routes to correct agent via MCP
    }
}
```

- [ ] **Step 2: Implement McpGateway**

```rust
use axum::{Router, routing::{get, post}, extract::State, Json};
use std::sync::Arc;

pub struct GatewayConfig {
    pub listen_addr: String,
    pub idle_timeout_secs: u64,
}

pub struct McpGateway {
    config: GatewayConfig,
    registry: Arc<DeferredAgentRegistry>,
    adapter: Arc<McpToolAdapter>,
}

impl McpGateway {
    pub fn new(config: GatewayConfig) -> Self {
        Self {
            config,
            registry: Arc::new(DeferredAgentRegistry::new()),
            adapter: Arc::new(McpToolAdapter::new()),
        }
    }

    pub async fn start(&self) -> Result<std::net::SocketAddr, GatewayError> {
        let app = Router::new()
            .route("/tools", get(Self::list_tools))
            .route("/tools/call", post(Self::call_tool))
            .with_state(Arc::clone(&self.registry));

        let listener = tokio::net::TcpListener::bind(&self.config.listen_addr).await?;
        let addr = listener.local_addr()?;
        // Spawn server
        Ok(addr)
    }

    async fn list_tools(State(registry): State<Arc<DeferredAgentRegistry>>) -> Json<Vec<serde_json::Value>> {
        // List all tools from all active agents
        todo!()
    }

    async fn call_tool(
        State(registry): State<Arc<DeferredAgentRegistry>>,
        Json(req): Json<ToolCallRequest>,
    ) -> Result<Json<serde_json::Value>, GatewayError> {
        // 1. Parse namespaced tool name
        // 2. Activate agent if needed
        // 3. Forward call via MCP client
        todo!()
    }
}

#[derive(Debug, thiserror::Error)]
pub enum GatewayError {
    #[error("Bind error: {0}")]
    Bind(#[from] std::io::Error),
    #[error("Registry error: {0}")]
    Registry(#[from] crate::deferred_registry::RegistryError),
}
```

- [ ] **Step 3: Update lib.rs + verify + commit**

```rust
// crates/ap-gateway/src/lib.rs
pub mod gateway;
pub mod tool_adapter;
pub mod deferred_registry;
pub mod schema;
```

```bash
cargo test -p ap-gateway
cargo clippy -p ap-gateway -- -D warnings
git add crates/ap-gateway/
git commit -m "feat(ap-gateway): MCPGateway with axum server and deferred registry"
```
