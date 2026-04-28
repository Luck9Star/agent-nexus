# Phase 3: ap-core/orchestration — TaskGraph, ProcessManager, IPC, DSL

> **Goal:** Port the orchestration core (SQLite TaskGraph, async ProcessManager, JSON-lines IPC, TOML DSL parser).

**Python source:** `src/agent_nexus/platform/orchestration/` (2,499 lines)
**Rust target:** `crates/ap-core/src/orchestration/`
**Depends on:** Phase 1 (models), Phase 2 (config)

**Files:**
- Create: `crates/ap-core/src/orchestration/mod.rs`
- Create: `crates/ap-core/src/orchestration/task_graph.rs`
- Create: `crates/ap-core/src/orchestration/process_manager.rs`
- Create: `crates/ap-core/src/orchestration/ipc.rs`
- Create: `crates/ap-core/src/orchestration/ipc_protocol.rs`
- Create: `crates/ap-core/src/orchestration/ipc_lock.rs`
- Create: `crates/ap-core/src/orchestration/dsl.rs`

---

## Task 3.1: TaskGraph (SQLite)

**Python source:** `src/agent_nexus/platform/orchestration/task_graph.py` (~600 lines)
**Rust target:** `crates/ap-core/src/orchestration/task_graph.rs`

Key features:
- SQLite with WAL mode
- `blocked_by` dependency tracking
- Topological sort + cycle detection
- `:memory:` mode for testing

- [ ] **Step 1: Write TaskGraph tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_in_memory() {
        let tg = TaskGraph::new_in_memory().unwrap();
        assert!(tg.is_empty());
    }

    #[test]
    fn add_and_get_task() {
        let tg = TaskGraph::new_in_memory().unwrap();
        let task = TaskItem::new("t1".into(), "agent-a".into(), "Do something".into());
        tg.add_task(&task).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.task_id, "t1");
        assert_eq!(got.agent_name, "agent-a");
    }

    #[test]
    fn detect_cycle() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&TaskItem::new_simple("t1", "a", "t2")).unwrap();
        tg.add_task(&TaskItem::new_simple("t2", "a", "t3")).unwrap();
        tg.add_task(&TaskItem::new_simple("t3", "a", "t1")).unwrap();
        assert!(tg.detect_cycle());
    }

    #[test]
    fn no_cycle_in_linear_chain() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&TaskItem::new_simple("t1", "a", "")).unwrap();
        tg.add_task(&TaskItem::new_simple("t2", "a", "t1")).unwrap();
        tg.add_task(&TaskItem::new_simple("t3", "a", "t2")).unwrap();
        assert!(!tg.detect_cycle());
    }

    #[test]
    fn topological_sort_order() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&TaskItem::new_simple("t1", "a", "")).unwrap();
        tg.add_task(&TaskItem::new_simple("t2", "b", "t1")).unwrap();
        tg.add_task(&TaskItem::new_simple("t3", "c", "t2")).unwrap();
        let order = tg.topological_sort().unwrap();
        assert!(order.iter().position(|t| t == "t1").unwrap() < order.iter().position(|t| t == "t2").unwrap());
        assert!(order.iter().position(|t| t == "t2").unwrap() < order.iter().position(|t| t == "t3").unwrap());
    }

    #[test]
    fn update_task_state() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&TaskItem::new_simple("t1", "a", "")).unwrap();
        tg.set_state("t1", TaskState::Running).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.state, TaskState::Running);
    }

    #[test]
    fn get_ready_tasks() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&TaskItem::new_simple("t1", "a", "")).unwrap();
        tg.add_task(&TaskItem::new_simple("t2", "b", "t1")).unwrap();
        tg.add_task(&TaskItem::new_simple("t3", "c", "")).unwrap();
        let ready = tg.get_ready_tasks().unwrap();
        assert_eq!(ready.len(), 2); // t1 and t3 have no dependencies
    }
}
```

- [ ] **Step 2: Implement TaskGraph**

```rust
use rusqlite::{Connection, params};
use crate::models::task::{TaskItem, TaskState};

#[derive(Debug, thiserror::Error)]
pub enum TaskGraphError {
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("Task not found: {0}")]
    NotFound(String),
    #[error("Cycle detected in task dependencies")]
    CycleDetected,
}

pub struct TaskGraph {
    conn: Connection,
}

impl TaskGraph {
    pub fn new_in_memory() -> Result<Self, TaskGraphError> {
        let conn = Connection::open_in_memory()?;
        let tg = Self { conn };
        tg.init_schema()?;
        Ok(tg)
    }

    pub fn new(path: &std::path::Path) -> Result<Self, TaskGraphError> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;
        let tg = Self { conn };
        tg.init_schema()?;
        Ok(tg)
    }

    fn init_schema(&self) -> Result<(), TaskGraphError> {
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                description TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                blocked_by TEXT DEFAULT '[]',
                vars TEXT,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);"
        )?;
        Ok(())
    }

    /// F-06 fix: TaskItem fields are `id` (not `task_id`), `agent` (not `agent_name`),
    /// `description` (not `content`). `blocked_by` is `Vec<String>` and needs JSON
    /// serialization for the TEXT column.
    pub fn add_task(&self, task: &TaskItem) -> Result<(), TaskGraphError> {
        let blocked_json = serde_json::to_string(&task.blocked_by)
            .map_err(|e| TaskGraphError::Serialization(e.to_string()))?;
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks (task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![task.id, task.agent, task.description,
                    task.state.to_string(), blocked_json,
                    task.vars.to_string(),
                    task.result.as_ref().map(|v| v.to_string()),
                    task.created_at.to_rfc3339(), task.updated_at.to_rfc3339()],
        )?;
        Ok(())
    }

    /// F-06 fix: Map SQL columns to TaskItem fields correctly.
    /// SQL `task_id` → model `id`, SQL `agent_name` → model `agent`,
    /// SQL `description` → model `description`, SQL `blocked_by` TEXT → `Vec<String>` via JSON.
    pub fn get_task(&self, task_id: &str) -> Result<Option<TaskItem>, TaskGraphError> {
        let mut stmt = self.conn.prepare(
            "SELECT task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at
             FROM tasks WHERE task_id = ?1"
        )?;
        // query row, map to TaskItem with correct field names
        todo!()
    }

    pub fn set_state(&self, task_id: &str, state: TaskState) -> Result<(), TaskGraphError> {
        self.conn.execute(
            "UPDATE tasks SET state = ?1, updated_at = ?2 WHERE task_id = ?3",
            params![state.to_string(), chrono::Utc::now().to_rfc3339(), task_id],
        )?;
        Ok(())
    }

    /// Detect cycles via DFS.
    pub fn detect_cycle(&self) -> bool {
        // Build adjacency list from blocked_by, then DFS with coloring
        todo!()
    }

    /// Return tasks in topological order.
    pub fn topological_sort(&self) -> Result<Vec<String>, TaskGraphError> {
        if self.detect_cycle() { return Err(TaskGraphError::CycleDetected); }
        // Kahn's algorithm
        todo!()
    }

    /// Get tasks with state=Pending and all dependencies completed.
    pub fn get_ready_tasks(&self) -> Result<Vec<TaskItem>, TaskGraphError> {
        todo!()
    }

    pub fn is_empty(&self) -> bool {
        self.conn.query_row("SELECT COUNT(*) FROM tasks", [], |row| row.get::<_, i64>(0)).unwrap_or(1) == 0
    }
}
```

- [ ] **Step 3: Verify tests pass**

Run: `cargo test -p ap-core -- task_graph`

- [ ] **Step 4: Commit**

```bash
git add crates/ap-core/src/orchestration/task_graph.rs
git commit -m "feat(ap-core): TaskGraph with SQLite, cycle detection, topological sort"
```

---

## Task 3.2: IPC Stream (JSON-lines codec)

**Python source:** `src/agent_nexus/platform/orchestration/ipc.py` (446 lines)
**Rust target:** `crates/ap-core/src/orchestration/ipc.rs`

- [ ] **Step 1: Write IPC tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{duplex, AsyncWriteExt};

    #[tokio::test]
    async fn send_and_receive_message() {
        let (client, server) = duplex(4096);
        let (read, write) = tokio::io::split(server);
        let (cread, cwrite) = tokio::io::split(client);

        let mut ipc_server = IpcStream::new(read, write);
        let mut ipc_client = IpcStream::new(cread, cwrite);

        let msg = PlatformToAgent::Chat {
            content: "hello".into(),
            conversation_id: None,
        };

        tokio::spawn(async move {
            ipc_client.send(&msg).await.unwrap();
        });

        let received = ipc_server.receive::<PlatformToAgent>().await.unwrap();
        match received {
            PlatformToAgent::Chat { content, .. } => assert_eq!(content, "hello"),
            _ => panic!("Expected Chat"),
        }
    }

    #[tokio::test]
    async fn oversized_message_rejected() {
        // F-15 fix: removed duplicate test + malformed `}]]` from copy-paste
        let (client, server) = duplex(1024 * 1024 * 8);
        let (read, write) = tokio::io::split(server);
        let (cread, cwrite) = tokio::io::split(client);

        let mut ipc = IpcStream::new(cread, cwrite);
        let big_content = "x".repeat(5 * 1024 * 1024); // 5MB, exceeds 4MB limit
        let msg = PlatformToAgent::Chat { content: big_content, conversation_id: None };
        let result = ipc.send(&msg).await;
        assert!(result.is_err());
    }
}
```

- [ ] **Step 2: Implement IpcStream**

```rust
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use serde::{Serialize, de::DeserializeOwned};

const MAX_MESSAGE_SIZE: usize = 4 * 1024 * 1024; // 4MB

#[derive(Debug, thiserror::Error)]
pub enum IpcError {
    #[error("Connection closed (EOF)")]
    ConnectionClosed,
    #[error("Timed out after {timeout:.1}s")]
    Timeout { timeout: f64 },
    #[error("Message too large: {size} bytes (max {max})")]
    Oversized { size: usize, max: usize },
    #[error("Invalid JSON: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}

pub struct IpcStream<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    reader: BufReader<R>,
    writer: W,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> IpcStream<R, W> {
    pub fn new(reader: R, writer: W) -> Self {
        Self { reader: BufReader::new(reader), writer }
    }

    pub async fn send<T: Serialize>(&mut self, msg: &T) -> Result<(), IpcError> {
        let json = serde_json::to_vec(msg)?;
        if json.len() > MAX_MESSAGE_SIZE {
            return Err(IpcError::Oversized { size: json.len(), max: MAX_MESSAGE_SIZE });
        }
        self.writer.write_all(&json).await?;
        self.writer.write_all(b"\n").await?;
        self.writer.flush().await?;
        Ok(())
    }

    pub async fn receive<T: DeserializeOwned>(&mut self) -> Result<T, IpcError> {
        let mut line = Vec::new();
        let n = self.reader.read_until(b'\n', &mut line).await?;
        if n == 0 {
            return Err(IpcError::ConnectionClosed);
        }
        if line.len() > MAX_MESSAGE_SIZE {
            return Err(IpcError::Oversized { size: line.len(), max: MAX_MESSAGE_SIZE });
        }
        let msg: T = serde_json::from_slice(&line)?;
        Ok(msg)
    }
}
```

- [ ] **Step 3: Verify tests pass**

Run: `cargo test -p ap-core -- ipc::tests`

- [ ] **Step 4: Commit**

```bash
git add crates/ap-core/src/orchestration/ipc.rs
git commit -m "feat(ap-core): IpcStream with JSON-lines framing and size limits"
```

---

## Task 3.3: IPC Protocol (high-level semantics)

**Python source:** `src/agent_nexus/platform/orchestration/ipc.py` — `send_chat`, `send_task`, `heartbeat`
**Rust target:** `crates/ap-core/src/orchestration/ipc_protocol.rs`

- [ ] **Step 1: Write protocol tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn send_chat_and_receive_result() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = IpcProtocol::new(cr, cw);
        let (mut sr, mut sw) = (sr, sw);

        // Server side: read chat, send result
        tokio::spawn(async move {
            let mut line = String::new();
            use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();
            let response = r#"{"type":"result","content":"done","success":true}"#;
            sw.write_all(response.as_bytes()).await.unwrap();
            sw.write_all(b"\n").await.unwrap();
            sw.flush().await.unwrap();
        });

        proto.send_chat("hello", None).await.unwrap();
        let result = proto.receive_result(None).await.unwrap();
        assert_eq!(result.content, "done");
        assert!(result.success);
    }
}
```

- [ ] **Step 2: Implement IpcProtocol**

```rust
use crate::models::ipc::{PlatformToAgent, AgentToPlatform};
use crate::orchestration::ipc::IpcStream;
use tokio::io::{AsyncRead, AsyncWrite};

pub struct IpcProtocol<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    stream: IpcStream<R, W>,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> IpcProtocol<R, W> {
    pub fn new(reader: R, writer: W) -> Self {
        Self { stream: IpcStream::new(reader, writer) }
    }

    pub async fn send_chat(&mut self, content: &str, conversation_id: Option<&str>) -> Result<(), crate::orchestration::ipc::IpcError> {
        self.stream.send(&PlatformToAgent::Chat {
            content: content.into(),
            conversation_id: conversation_id.map(|s| s.into()),
        }).await
    }

    pub async fn send_task(&mut self, content: &str, task_id: &str) -> Result<(), crate::orchestration::ipc::IpcError> {
        self.stream.send(&PlatformToAgent::Task {
            content: content.into(),
            task_id: task_id.into(),
        }).await
    }

    pub async fn receive_result(&mut self, _timeout: Option<f64>) -> Result<AgentResult, crate::orchestration::ipc::IpcError> {
        let msg: AgentToPlatform = self.stream.receive().await?;
        match msg {
            AgentToPlatform::Result { content, success, .. } => Ok(AgentResult { content, success }),
            AgentToPlatform::Error { error, error_type, .. } => {
                Err(crate::orchestration::ipc::IpcError::Io(std::io::Error::new(
                    std::io::ErrorKind::Other, format!("{error_type}: {error}")
                )))
            }
            AgentToPlatform::Progress { content, .. } => {
                // Should use receive_until_result for progress handling
                Ok(AgentResult { content, success: true })
            }
        }
    }
}

pub struct AgentResult {
    pub content: String,
    pub success: bool,
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-core -- ipc_protocol
git add crates/ap-core/src/orchestration/ipc_protocol.rs
git commit -m "feat(ap-core): IpcProtocol with semantic methods"
```

---

## Task 3.4: IPC Lock Registry

**Python source:** Python uses `dict[str, asyncio.Lock]` per agent
**Rust target:** `crates/ap-core/src/orchestration/ipc_lock.rs`

- [ ] **Step 1: Implement lock registry**

```rust
use dashmap::DashMap;
use std::sync::{Arc, Mutex};
use std::collections::VecDeque;

const MAX_LOCKS: usize = 1000;

pub struct IpcLockRegistry {
    locks: DashMap<String, Arc<Mutex<()>>>,
    order: std::sync::Mutex<VecDeque<String>>,
}

impl IpcLockRegistry {
    pub fn new() -> Self {
        Self {
            locks: DashMap::new(),
            order: std::sync::Mutex::new(VecDeque::new()),
        }
    }

    pub fn get_or_create(&self, agent_id: &str) -> Arc<Mutex<()>> {
        if let Some(lock) = self.locks.get(agent_id) {
            return Arc::clone(lock.value());
        }
        let lock = Arc::new(Mutex::new(()));
        self.locks.insert(agent_id.to_string(), Arc::clone(&lock));

        // Evict oldest if over limit
        let mut order = self.order.lock().unwrap();
        order.push_back(agent_id.to_string());
        if order.len() > MAX_LOCKS {
            if let Some(old_id) = order.pop_front() {
                self.locks.remove(&old_id);
            }
        }
        lock
    }
}
```

- [ ] **Step 2: Write tests + verify + commit**

```bash
cargo test -p ap-core -- ipc_lock
git add crates/ap-core/src/orchestration/ipc_lock.rs
git commit -m "feat(ap-core): IPC lock registry with FIFO eviction"
```

---

## Task 3.5: ProcessManager (tokio::process)

**Python source:** `src/agent_nexus/platform/orchestration/process_manager.py` (~550 lines)
**Rust target:** `crates/ap-core/src/orchestration/process_manager.rs`

- [ ] **Step 1: Write ProcessManager tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn spawn_echo_process() {
        let mut pm = ProcessManager::new();
        // Use `cat` as a simple echo subprocess that reads stdin and writes to stdout
        let id = pm.spawn("echo-test", "cat", &[]).await.unwrap();
        assert!(pm.is_running("echo-test"));
        pm.kill("echo-test").await.unwrap();
    }

    #[tokio::test]
    async fn list_running_processes() {
        let mut pm = ProcessManager::new();
        pm.spawn("p1", "sleep", &["10"]).await.unwrap();
        pm.spawn("p2", "sleep", &["10"]).await.unwrap();
        let running = pm.list_running();
        assert_eq!(running.len(), 2);
        pm.kill_all().await.unwrap();
    }

    // F-10: I/O accessor tests
    #[tokio::test]
    async fn take_io_extracts_handles() {
        let mut pm = ProcessManager::new();
        pm.spawn("io-test", "cat", &[]).await.unwrap();
        let (stdin, stdout) = pm.take_io("io-test").unwrap();
        // Process is still tracked for lifecycle management
        assert!(pm.is_running("io-test"));
        // But I/O is now owned by caller — stdin/stdout are moved out
        pm.kill("io-test").await.unwrap();
    }

    #[tokio::test]
    async fn stdin_stdout_borrow_for_inline_ipc() {
        let mut pm = ProcessManager::new();
        pm.spawn("borrow-test", "cat", &[]).await.unwrap();
        let stdin = pm.stdin_mut("borrow-test").unwrap();
        // Can write to borrowed stdin without taking ownership
        use tokio::io::AsyncWriteExt;
        // Note: actual write would go here; just testing the accessor compiles
        drop(stdin);
        pm.kill("borrow-test").await.unwrap();
    }

    #[tokio::test]
    async fn take_io_not_found() {
        let mut pm = ProcessManager::new();
        let result = pm.take_io("nonexistent");
        assert!(result.is_err());
    }
}
```

- [ ] **Step 2: Implement ProcessManager**

```rust
use std::collections::HashMap;
use std::process::Stdio;
use tokio::process::{Child, Command};
use tokio::io::{AsyncRead, AsyncWrite};

pub struct ManagedProcess {
    child: Child,
    stdin: Box<dyn AsyncWrite + Unpin + Send>,
    stdout: Box<dyn AsyncRead + Unpin + Send>,
}

pub struct ProcessManager {
    processes: HashMap<String, ManagedProcess>,
    max_concurrent: usize,
}

impl ProcessManager {
    pub fn new() -> Self {
        Self { processes: HashMap::new(), max_concurrent: 10 }
    }

    pub fn with_max_concurrent(mut self, max: usize) -> Self {
        self.max_concurrent = max;
        self
    }

    pub async fn spawn(&mut self, id: &str, cmd: &str, args: &[&str]) -> Result<(), ProcessError> {
        if self.processes.len() >= self.max_concurrent {
            return Err(ProcessError::MaxConcurrent(self.max_concurrent));
        }
        let mut child = Command::new(cmd)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(ProcessError::Spawn)?;

        let stdin = Box::new(child.stdin.take().unwrap());
        let stdout = Box::new(child.stdout.take().unwrap());

        self.processes.insert(id.to_string(), ManagedProcess { child, stdin, stdout });
        Ok(())
    }

    pub fn is_running(&mut self, id: &str) -> bool {
        if let Some(proc) = self.processes.get_mut(id) {
            match proc.child.try_wait() {
                Ok(None) => true,
                _ => false,
            }
        } else {
            false
        }
    }

    pub fn list_running(&mut self) -> Vec<String> {
        self.processes.iter_mut()
            .filter(|(_, p)| matches!(p.child.try_wait(), Ok(None)))
            .map(|(id, _)| id.clone())
            .collect()
    }

    pub async fn kill(&mut self, id: &str) -> Result<(), ProcessError> {
        if let Some(mut proc) = self.processes.remove(id) {
            proc.child.kill().await.map_err(ProcessError::Kill)?;
        }
        Ok(())
    }

    pub async fn kill_all(&mut self) -> Result<(), ProcessError> {
        let ids: Vec<String> = self.processes.keys().cloned().collect();
        for id in ids {
            self.kill(&id).await?;
        }
        Ok(())
    }

    /// F-10 fix: IPC needs stdin/stdout accessors.
    /// `IpcProtocol` takes ownership of I/O handles; this method extracts
    /// them from `ManagedProcess` and returns a pair the caller can pass
    /// to `IpcProtocol::new()`.
    ///
    /// After calling this, the process entry is removed from the map
    /// (handles are moved into IpcProtocol). The child handle is preserved
    /// so `is_running` / `kill` still work via a separate tracking struct.
    pub fn take_io(
        &mut self,
        id: &str,
    ) -> Result<
        (
            Box<dyn AsyncWrite + Unpin + Send>,
            Box<dyn AsyncRead + Unpin + Send>,
        ),
        ProcessError,
    > {
        let proc = self.processes.get_mut(id)
            .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;
        let stdin = std::mem::replace(
            &mut proc.stdin,
            Box::new(tokio::io::sink()), // placeholder — not used after take
        );
        let stdout = std::mem::replace(
            &mut proc.stdout,
            Box::new(tokio::io::empty()), // placeholder
        );
        Ok((stdin, stdout))
    }

    /// Alternative: borrow I/O for a single send/receive without taking ownership.
    /// Useful when IPC is managed inline rather than via IpcProtocol.
    pub fn stdin_mut(&mut self, id: &str) -> Result<&mut Box<dyn AsyncWrite + Unpin + Send>, ProcessError> {
        let proc = self.processes.get_mut(id)
            .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;
        Ok(&mut proc.stdin)
    }

    pub fn stdout_mut(&mut self, id: &str) -> Result<&mut Box<dyn AsyncRead + Unpin + Send>, ProcessError> {
        let proc = self.processes.get_mut(id)
            .ok_or_else(|| ProcessError::NotFound(id.to_string()))?;
        Ok(&mut proc.stdout)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ProcessError {
    #[error("Failed to spawn process: {0}")]
    Spawn(std::io::Error),
    #[error("Max concurrent processes reached: {0}")]
    MaxConcurrent(usize),
    #[error("Failed to kill process: {0}")]
    Kill(std::io::Error),
    #[error("Process not found: {0}")]
    NotFound(String),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-core -- process_manager
git add crates/ap-core/src/orchestration/process_manager.rs
git commit -m "feat(ap-core): ProcessManager with tokio::process and max concurrency"
```

---

## Task 3.6: OrchestrationDSL (TOML DAG parser)

**Python source:** `src/agent_nexus/platform/orchestration/dsl.py` (~350 lines)
**Rust target:** `crates/ap-core/src/orchestration/dsl.rs`

- [ ] **Step 1: Write DSL tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_simple_dag() {
        let toml = r#"
[[tasks]]
name = "research"
agent = "code-reviewer"
phase = 1

[[tasks]]
name = "implement"
agent = "code-writer"
phase = 2
depends_on = ["research"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        assert_eq!(dag.tasks.len(), 2);
        assert_eq!(dag.tasks[1].depends_on, vec!["research"]);
    }

    #[test]
    fn reject_cycle_in_dag() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
depends_on = ["b"]

[[tasks]]
name = "b"
agent = "y"
depends_on = ["a"]
"#;
        let result = OrchestrationDsl::parse(toml);
        assert!(result.is_err());
    }

    #[test]
    fn reject_missing_dependency() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
depends_on = ["nonexistent"]
"#;
        let result = OrchestrationDsl::parse(toml);
        assert!(result.is_err());
    }
}
```

- [ ] **Step 2: Implement DSL parser + composition logic**

```rust
use serde::{Serialize, Deserialize};
use std::collections::{HashMap, HashSet, VecDeque};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DslTask {
    pub name: String,
    pub agent: String,
    #[serde(default)]
    pub phase: u32,
    #[serde(default)]
    pub depends_on: Vec<String>,
    #[serde(default)]
    pub variables: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Clone)]
pub struct OrchestrationDsl {
    pub tasks: Vec<DslTask>,
    /// Index: task name → position in tasks vec
    name_index: HashMap<String, usize>,
}

impl OrchestrationDsl {
    /// Parse TOML string into a validated DAG.
    /// Rejects cycles, missing dependencies, and duplicate task names.
    pub fn parse(toml: &str) -> Result<Self, DslError> {
        let wrapper: DslToml = toml::from_str(toml)?;
        Self::from_tasks(wrapper.tasks)
    }

    /// Build from a task list (shared logic for parse and from_toml).
    fn from_tasks(tasks: Vec<DslTask>) -> Result<Self, DslError> {
        if tasks.is_empty() {
            return Err(DslError::EmptyDag);
        }

        // Build name index, check for duplicates
        let mut name_index = HashMap::new();
        for (i, task) in tasks.iter().enumerate() {
            if name_index.contains_key(&task.name) {
                return Err(DslError::DuplicateTask(task.name.clone()));
            }
            name_index.insert(task.name.clone(), i);
        }

        // Validate dependencies exist
        let all_names: HashSet<&str> = tasks.iter().map(|t| t.name.as_str()).collect();
        for task in &tasks {
            for dep in &task.depends_on {
                if !all_names.contains(dep.as_str()) {
                    return Err(DslError::MissingDependency {
                        task: task.name.clone(),
                        dep: dep.clone(),
                    });
                }
            }
        }

        // Cycle detection
        if let Some(cycle) = Self::detect_cycle(&tasks, &name_index) {
            return Err(DslError::CycleDetected(cycle));
        }

        Ok(Self { tasks, name_index })
    }

    /// Load from a TOML file on disk.
    pub fn from_toml(path: &std::path::Path) -> Result<Self, DslError> {
        let content = std::fs::read_to_string(path)?;
        Self::parse(&content)
    }

    /// Return tasks with no dependencies (entry points).
    pub fn get_root_tasks(&self) -> Vec<&DslTask> {
        self.tasks.iter()
            .filter(|t| t.depends_on.is_empty())
            .collect()
    }

    /// Return tasks that depend on the given task.
    pub fn get_dependents(&self, task_name: &str) -> Vec<&DslTask> {
        self.tasks.iter()
            .filter(|t| t.depends_on.contains(&task_name.to_string()))
            .collect()
    }

    /// Topological execution order (BFS/Kahn's algorithm).
    /// Respects phase ordering for ties.
    pub fn get_execution_order(&self) -> Vec<&DslTask> {
        let mut in_degree: HashMap<&str, usize> = HashMap::new();
        let mut adjacency: HashMap<&str, Vec<&str>> = HashMap::new();

        for task in &self.tasks {
            in_degree.entry(&task.name).or_insert(0);
            for dep in &task.depends_on {
                adjacency.entry(dep.as_str()).or_default().push(&task.name);
                *in_degree.entry(&task.name).or_insert(0) += 1;
            }
        }

        // Start with root tasks, sorted by phase
        let mut queue: VecDeque<&DslTask> = self.get_root_tasks()
            .into_iter()
            .collect();
        // Sort by phase for deterministic order
        queue.make_contiguous().sort_by_key(|t| t.phase);

        let mut result = Vec::with_capacity(self.tasks.len());
        while let Some(task) = queue.pop_front() {
            result.push(task);
            if let Some(deps) = adjacency.get(task.name.as_str()) {
                for &dep_name in deps {
                    let degree = in_degree.get_mut(dep_name).unwrap();
                    *degree -= 1;
                    if *degree == 0 {
                        if let Some(t) = self.get_task(dep_name) {
                            queue.push_back(t);
                        }
                    }
                }
            }
        }

        result
    }

    fn get_task(&self, name: &str) -> Option<&DslTask> {
        self.name_index.get(name).map(|&i| &self.tasks[i])
    }

    /// Detect cycles using DFS. Returns a cycle path if found.
    fn detect_cycle(
        tasks: &[DslTask],
        name_index: &HashMap<String, usize>,
    ) -> Option<Vec<String>> {
        let mut white: HashSet<&str> = tasks.iter().map(|t| t.name.as_str()).collect();
        let mut gray: HashSet<&str> = HashSet::new();
        let mut black: HashSet<&str> = HashSet::new();

        fn dfs<'a>(
            name: &'a str,
            tasks: &[DslTask],
            name_index: &HashMap<String, usize>,
            white: &mut HashSet<&'a str>,
            gray: &mut HashSet<&'a str>,
            black: &mut HashSet<&'a str>,
        ) -> Option<Vec<String>> {
            white.remove(name);
            gray.insert(name);

            let idx = *name_index.get(name)?;
            for dep in &tasks[idx].depends_on {
                if gray.contains(dep.as_str()) {
                    // Found cycle
                    return Some(vec![name.to_string(), dep.clone()]);
                }
                if !black.contains(dep.as_str()) {
                    if let Some(cycle) = dfs(dep.as_str(), tasks, name_index, white, gray, black) {
                        return Some(cycle);
                    }
                }
            }

            gray.remove(name);
            black.insert(name);
            None
        }

        let names: Vec<&str> = tasks.iter().map(|t| t.name.as_str()).collect();
        for name in names {
            if !black.contains(name) {
                if let Some(cycle) = dfs(name, tasks, name_index, &mut white, &mut gray, &mut black) {
                    return Some(cycle);
                }
            }
        }
        None
    }
}

/// TOML wire format — `[[tasks]]` array.
#[derive(Debug, Deserialize)]
struct DslToml {
    tasks: Vec<DslTask>,
}

#[derive(Debug, thiserror::Error)]
pub enum DslError {
    #[error("TOML parse error: {0}")]
    Toml(#[from] toml::de::Error),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("DAG has no tasks")]
    EmptyDag,
    #[error("Duplicate task name: {0}")]
    DuplicateTask(String),
    #[error("Missing dependency: task '{task}' depends on '{dep}' which does not exist")]
    MissingDependency { task: String, dep: String },
    #[error("Cycle detected: {0:?}")]
    CycleDetected(Vec<String>),
}
```

- [ ] **Step 3: Add composition logic tests**

```rust
    #[test]
    fn get_root_tasks_returns_entry_points() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
phase = 1

[[tasks]]
name = "b"
agent = "y"
phase = 2
depends_on = ["a"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        let roots = dag.get_root_tasks();
        assert_eq!(roots.len(), 1);
        assert_eq!(roots[0].name, "a");
    }

    #[test]
    fn get_dependents_finds_downstream() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"

[[tasks]]
name = "b"
agent = "y"
depends_on = ["a"]

[[tasks]]
name = "c"
agent = "z"
depends_on = ["a"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        let deps = dag.get_dependents("a");
        assert_eq!(deps.len(), 2);
    }

    #[test]
    fn execution_order_is_topological() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
phase = 1

[[tasks]]
name = "b"
agent = "y"
phase = 1

[[tasks]]
name = "c"
agent = "z"
phase = 2
depends_on = ["a", "b"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        let order: Vec<&str> = dag.get_execution_order().iter().map(|t| t.name.as_str()).collect();
        assert!(order.iter().position(|&n| n == "a").unwrap()
             < order.iter().position(|&n| n == "c").unwrap());
        assert!(order.iter().position(|&n| n == "b").unwrap()
             < order.iter().position(|&n| n == "c").unwrap());
    }

    #[test]
    fn from_toml_reads_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pipeline.toml");
        std::fs::write(&path, "[[tasks]]\nname = \"a\"\nagent = \"x\"\n").unwrap();
        let dag = OrchestrationDsl::from_toml(&path).unwrap();
        assert_eq!(dag.tasks.len(), 1);
    }

    #[test]
    fn reject_empty_dag() {
        let toml = "";
        let result = OrchestrationDsl::parse(toml);
        assert!(matches!(result, Err(DslError::EmptyDag)));
    }

    #[test]
    fn reject_duplicate_task_name() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"

[[tasks]]
name = "a"
agent = "y"
"#;
        let result = OrchestrationDsl::parse(toml);
        assert!(matches!(result, Err(DslError::DuplicateTask(_))));
    }
```

- [ ] **Step 4: Verify and commit**

```bash
cargo test -p ap-core -- dsl
git add crates/ap-core/src/orchestration/dsl.rs
git commit -m "feat(ap-core): OrchestrationDSL with TOML parsing, topological sort, cycle detection"
```

---

## Task 3.7: Module glue

- Create: `crates/ap-core/src/orchestration/mod.rs`
- Update: `crates/ap-core/src/lib.rs` — add `pub mod orchestration;`
- Verify: `cargo build -p ap-core`

```bash
git add crates/ap-core/src/orchestration/mod.rs crates/ap-core/src/lib.rs
git commit -m "feat(ap-core): orchestration module glue"
```

---

## Final Verification

- [ ] `cargo test -p ap-core`
- [ ] `cargo clippy -p ap-core -- -D warnings`
