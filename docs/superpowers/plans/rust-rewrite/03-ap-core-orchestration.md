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
                content TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                blocked_by TEXT DEFAULT '',
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);"
        )?;
        Ok(())
    }

    pub fn add_task(&self, task: &TaskItem) -> Result<(), TaskGraphError> {
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks (task_id, agent_name, content, state, blocked_by, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![task.task_id, task.agent_name, task.content,
                    task.state.to_string(), task.blocked_by, task.created_at, task.updated_at],
        )?;
        Ok(())
    }

    pub fn get_task(&self, task_id: &str) -> Result<Option<TaskItem>, TaskGraphError> {
        // query row, map to TaskItem
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
        let (client, server) = duplex(8 * 1024}}],
            _ => panic!("Expected Chat message"),
        }
    }

    #[tokio::test]
    async fn oversized_message_rejected() {
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

- [ ] **Step 2: Implement DSL parser + verify + commit**

```bash
cargo test -p ap-core -- dsl
git add crates/ap-core/src/orchestration/dsl.rs
git commit -m "feat(ap-core): OrchestrationDSL TOML DAG parser with validation"
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
