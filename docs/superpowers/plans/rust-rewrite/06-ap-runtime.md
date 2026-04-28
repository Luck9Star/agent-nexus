# Phase 6: ap-runtime — Python Subprocess Bridge

> **Goal:** Build the IPC + MCP bridge layer that communicates with Python Agent subprocesses.

**Python source:** `src/agent_nexus/platform/orchestration/ipc.py` + `src/agent_nexus/platform/runtime/runtime.py`
**Rust target:** `crates/ap-runtime/src/`
**Depends on:** Phase 1 (ap-core models), Phase 3 (ap-core orchestration IPC types)

**Files:**
- Create: `crates/ap-runtime/src/lib.rs` (overwrite skeleton)
- Create: `crates/ap-runtime/src/process.rs`
- Create: `crates/ap-runtime/src/ipc/codec.rs`
- Create: `crates/ap-runtime/src/ipc/mod.rs`
- Create: `crates/ap-runtime/src/ipc/stream.rs`
- Create: `crates/ap-runtime/src/ipc/protocol.rs`
- Create: `crates/ap-runtime/src/mcp_client.rs`
- Create: `crates/ap-runtime/src/lock.rs`

---

## Task 6.1: Process wrapper

**Rust target:** `crates/ap-runtime/src/process.rs`

Wraps `tokio::process::Command` with typed IPC and lifecycle management for Python agents.

- [ ] **Step 1: Write process tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn spawn_python_agent() {
        // Use `cat` as mock agent (echoes stdin to stdout)
        let agent = AgentProcess::spawn("test-agent", "cat", &[]).await.unwrap();
        assert!(agent.is_alive());
        agent.kill().await.unwrap();
    }

    #[tokio::test]
    async fn spawn_nonexistent_command_fails() {
        let result = AgentProcess::spawn("test", "nonexistent_binary_xyz", &[]).await;
        assert!(result.is_err());
    }
}
```

- [ ] **Step 2: Implement AgentProcess**

```rust
use std::process::Stdio;
use tokio::process::{Child, Command};
use tokio::io::{AsyncRead, AsyncWrite};

pub struct AgentProcess {
    id: String,
    child: Child,
    stdin_handle: Box<dyn AsyncWrite + Unpin + Send>,
    stdout_handle: Box<dyn AsyncRead + Unpin + Send>,
}

impl AgentProcess {
    pub async fn spawn(id: &str, cmd: &str, args: &[&str]) -> Result<Self, ProcessError> {
        let mut child = Command::new(cmd)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(ProcessError::Spawn)?;

        let stdin = Box::new(child.stdin.take().ok_or(ProcessError::NoStdin)?);
        let stdout = Box::new(child.stdout.take().ok_or(ProcessError::NoStdout)?);

        Ok(Self {
            id: id.to_string(),
            child,
            stdin_handle: stdin,
            stdout_handle: stdout,
        })
    }

    pub fn id(&self) -> &str { &self.id }

    pub fn is_alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    pub async fn kill(&mut self) -> Result<(), ProcessError> {
        self.child.kill().await.map_err(ProcessError::Kill)
    }

    pub fn split(self) -> (String, Box<dyn AsyncWrite + Unpin + Send>, Box<dyn AsyncRead + Unpin + Send>, Child) {
        (self.id, self.stdin_handle, self.stdout_handle, self.child)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ProcessError {
    #[error("Spawn failed: {0}")]
    Spawn(std::io::Error),
    #[error("No stdin available")]
    NoStdin,
    #[error("No stdout available")]
    NoStdout,
    #[error("Kill failed: {0}")]
    Kill(std::io::Error),
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-runtime -- process
git add crates/ap-runtime/src/process.rs
git commit -m "feat(ap-runtime): AgentProcess with spawn, lifecycle, and typed I/O"
```

---

## Task 6.2: IPC codec and stream

**Rust target:** `crates/ap-runtime/src/ipc/`

Re-exports and extends ap-core's IPC types with agent-specific functionality:
- `codec.rs` — `tokio::codec::LinesCodec` adapter
- `stream.rs` — `IpcStream` with timeout and error recovery
- `protocol.rs` — High-level `send_chat`, `send_task`, `receive_result`, `heartbeat`

This layer wraps ap-core's `IpcStream` with agent-specific concerns:
- Heartbeat ping-pong (10s timeout)
- Message peek buffer for progress → result multiplexing
- `receive_until_result()` that calls a progress callback

- [ ] **Step 1: Write IPC tests using tokio::io::duplex**

```rust
#[cfg(test)]
mod tests {
    #[tokio::test]
    async fn full_chat_result_roundtrip() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto_client = IpcProtocol::new(cr, cw);
        let mut stream_server = IpcStream::new(sr, sw);

        tokio::spawn(async move {
            // Read chat from client
            let msg: PlatformToAgent = stream_server.receive().await.unwrap();
            // Send result back
            stream_server.send(&AgentToPlatform::Result {
                content: "analyzed".into(),
                task_id: None,
                success: true,
            }).await.unwrap();
        });

        proto_client.send_chat("analyze this", None).await.unwrap();
        let result = proto_client.receive_until_result(|progress| {
            // progress callback
        }).await.unwrap();
        assert_eq!(result.content, "analyzed");
    }
}
```

- [ ] **Step 2: Implement IPC layer + verify + commit**

```bash
cargo test -p ap-runtime -- ipc
git add crates/ap-runtime/src/ipc/
git commit -m "feat(ap-runtime): IPC codec, stream, and protocol with heartbeat"
```

---

## Task 6.3: Lock Registry

**Rust target:** `crates/ap-runtime/src/lock.rs`

Per-agent `Mutex` registry with FIFO eviction. Wraps `DashMap<String, Arc<Mutex<()>>>`.

- [ ] **Write tests + implement + verify + commit**

```bash
cargo test -p ap-runtime -- lock
git add crates/ap-runtime/src/lock.rs
git commit -m "feat(ap-runtime): per-agent lock registry with FIFO eviction"
```

---

## Task 6.4: MCP Client wrapper

**Rust target:** `crates/ap-runtime/src/mcp_client.rs`

Wraps `rmcp` client for MCP stdio transport to Python Agent MCP servers.

**Note:** rmcp crate maturity is a risk. If `rmcp` API is unstable, wrap behind a trait so the gateway can fall back to raw IPC.

- [ ] **Step 1: Define MCP client trait**

```rust
use async_trait::async_trait;
use serde_json::Value;

#[async_trait]
pub trait McpClient: Send + Sync {
    async fn list_tools(&self) -> Result<Vec<ToolInfo>, McpError>;
    async fn call_tool(&self, name: &str, arguments: Value) -> Result<Value, McpError>;
    async fn close(&self) -> Result<(), McpError>;
}

pub struct ToolInfo {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

#[derive(Debug, thiserror::Error)]
pub enum McpError {
    #[error("Connection error: {0}")]
    Connection(String),
    #[error("Tool not found: {0}")]
    ToolNotFound(String),
    #[error("Tool call failed: {0}")]
    ToolCall(String),
}
```

- [ ] **Step 2: Implement rmcp-backed client**

Wrap rmcp's client behind the trait. If rmcp is not available, provide a `RawIpcClient` fallback.

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-runtime -- mcp
git add crates/ap-runtime/src/mcp_client.rs
git commit -m "feat(ap-runtime): MCP client trait with rmcp implementation"
```

---

## Task 6.5: Update lib.rs

- Update `crates/ap-runtime/src/lib.rs` to export all modules:

```rust
pub mod process;
pub mod ipc;
pub mod lock;
pub mod mcp_client;
```

- Verify: `cargo build -p ap-runtime`

```bash
git add crates/ap-runtime/src/lib.rs
git commit -m "feat(ap-runtime): module exports"
```

---

## Final Verification

- [ ] `cargo test -p ap-runtime`
- [ ] `cargo clippy -p ap-runtime -- -D warnings`
