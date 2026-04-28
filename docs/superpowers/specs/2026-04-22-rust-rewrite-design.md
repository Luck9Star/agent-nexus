# Rust 重构设计方案 — Agent Nexus Platform 层

> **Status**: Draft
> **Date**: 2026-04-22
> **Scope**: 全量 Rust 重写 Platform 层（除 Python Agent Runtime 外）
> **Migration**: 一次性替换（非 Strangler Fig）
> **Dependencies**: rmcp, axum, git2, rusqlite, clap, tokio, serde

---

## 1. 设计目标

将 Agent Nexus Platform 层从 Python 重写为 Rust，覆盖以下模块（17,076 行 Python → Rust）：

| 模块 | Python 行数 | Rust Crate |
|------|-----------|-----------|
| orchestration/ | 2,499 | ap-core |
| router/ | 992 | ap-core |
| config/ | 673 | ap-core |
| models/ | 1,577 | ap-core |
| gateway/ | 1,320 | ap-gateway |
| local/ (CLI + Installer + Supervisor) | 4,577 | ap-fetcher + ap-cli |
| evolution/ | 3,835 | ap-evolution |
| runtime/ (桥接层) | 2,010 | ap-runtime |
| hooks/ | 492 | ap-core |
| skills/ | 433 | ap-core |
| **Total** | **17,076** | **6 crates** |

### 不重写的部分

- `agents/` — Agent 内部代码（Python），通过 MCP/IPC 与 Rust 平台交互
- Python IPythonRuntime — 仅 Agent 内部使用
- Agent MCP Server (FastMCP) — Agent 的对外接口
- uv 工具链 — venv/dep 管理，Rust 通过 shell 调用

---

## 2. 决策记录

| # | 决策 | 选项 | 选择 | 理由 |
|---|------|------|------|------|
| 1 | 重写范围 | A(窄)/B(中)/C(宽) | **C** | 尽可能把稳定逻辑下沉到 Rust |
| 2 | 迁移策略 | Strangler Fig / 分批 / 全量 | **全量替换** | 无并行维护负担，一步到位 |
| 3 | Crate 结构 | 1:1映射 / 分层合并 / Monolith | **分层合并** | 与四层架构对齐 |
| 4 | Runtime 桥接 | 纯MCP / 双通道 / 全MCP | **双通道(MCP+raw IPC)** | MCP 做标准接口，IPC 保留编排灵活性 |
| 5 | Evolution Engine | 重写 / 留Python / 拆分混合 | **重写** | evolver 的 Runtime 耦合通过 IPC 解耦，全量替换闭环最简洁 |
| 6 | Evolution 评估标准 | — | 变更频率=性能 > 耦合度=团队能力 > 一致性 | 用户确认的优先级 |

---

## 3. Crate 依赖图与模块划分

### 3.1 Crate 结构

```
┌─────────────────────────────────────────────────────────────┐
│  ap-cli (binary crate)                                      │
│  CLI 入口 — clap，组合所有 crate                              │
├─────────────────────────────────────────────────────────────┤
│  ap-gateway       │  ap-fetcher      │  ap-evolution        │
│  MCP Gateway       │  Git 分发层       │  自进化引擎           │
│  + tool adapter    │  + sources       │  + store(SQLite)     │
│  + deferred reg    │  + lockfile      │  + evolver           │
│                    │  + supervisor    │  + promotion          │
├────────────────────┴─────────────────┴──────────────────────┤
│  ap-runtime                                                 │
│  Python 子进程桥接 — MCP client + raw IPC 协议               │
├─────────────────────────────────────────────────────────────┤
│  ap-core                                                    │
│  models + config + orchestration + router                   │
│  (TaskGraph/ProcessManager/IPC/DSL/4-Phase Router)          │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 依赖方向（严格向下，无循环）

```
ap-cli → ap-gateway, ap-fetcher, ap-evolution, ap-runtime
ap-gateway → ap-core, ap-runtime
ap-fetcher → ap-core
ap-evolution → ap-core, ap-runtime
ap-runtime → ap-core
ap-core → (无内部依赖)
```

### 3.3 Rust 依赖选型

| 领域 | Crate | 版本 | 用途 |
|------|-------|------|------|
| 异步运行时 | tokio | 1.x | 替代 asyncio |
| MCP 协议 | rmcp | 0.1+ | 官方 Rust SDK，stdio/SSE（版本以实际 crates.io 为准）|
| HTTP | axum | 0.8+ | Gateway HTTP 服务 |
| Git 操作 | git2 | 0.19+ | clone, tag, sparse checkout |
| SQLite | rusqlite | 0.32+ | WAL 模式，与 Python sqlite3 兼容 |
| CLI | clap | 4.x | 替代 Typer |
| 序列化 | serde + serde_json | 1.x | JSON/YAML/TOML |
| YAML | serde_yaml | 0.9+ | config/manifest 解析 |
| TOML | toml | 0.8+ | OrchestrationDSL + config.toml |
| 版本解析 | semver | 1.x | 与 Python packaging 版本规范兼容 |
| 错误处理 | thiserror + anyhow | — | 库精确 + 应用兜底 |
| 异步并发 | dashmap | 6.x | per-agent lock registry |
| 时间 | chrono | 0.4+ | 时间戳 |
| 测试 | rstest, assert_cmd, tokio-test | — | 单元/集成/CLI 测试 |

---

## 4. 数据模型与 IPC 协议

### 4.1 Python → Rust 类型映射

| Python 类型 | Rust 类型 |
|------------|----------|
| `str` | `String` |
| `str \| None` | `Option<String>` |
| `dict` | `serde_json::Value` |
| `list[X]` | `Vec<X>` |
| `datetime` | `chrono::DateTime<Utc>` |
| `Path` / `PathBuf` | `std::path::PathBuf` |
| `enum` (str) | `#[serde(rename_all = "snake_case")] enum` |
| Pydantic `BaseModel` | `#[derive(Serialize, Deserialize)] struct` |

### 4.2 IPC 双通道协议

#### 通道 1 — MCP stdio（标准化接口）

用途：工具发现、tool call 请求/响应、Agent 注册/注销。

```
[Rust ap-gateway] ←→ rmcp MCP stdio ←→ [Python Agent MCP Server]
```

#### 通道 2 — Raw JSON-lines IPC（编排控制）

用途：任务分配、进度上报、心跳/健康检查、数据引用传递。

```
[Rust ap-core::ipc] ←→ tokio stdin/stdout ←→ [Python Agent stdin/stdout]
```

消息格式与 Python 版完全兼容：

```rust
// ap-core/src/models/ipc.rs

#[derive(Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum PlatformToAgent {
    #[serde(rename = "chat")]
    Chat { content: String, conversation_id: Option<String> },
    #[serde(rename = "task")]
    Task { content: String, task_id: String },
    #[serde(rename = "data_reference")]
    DataReference { content: String, ref_id: String, summary: String },
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum AgentToPlatform {
    #[serde(rename = "result")]
    Result { content: String, task_id: Option<String>, success: bool },
    #[serde(rename = "progress")]
    Progress { content: String, task_id: Option<String>, percentage: Option<u8> },
    #[serde(rename = "error")]
    Error { error: String, error_type: String, task_id: Option<String> },
}
```

### 4.3 接口格式兼容保证

Rust 版必须能读取 Python 版写入的以下文件（向后兼容读取）：

| 文件 | 格式 | Python 写入者 | Rust 读取者 |
|------|------|-------------|-----------|
| `config.toml` | TOML | 用户手写 | `ap-core::config::loader` |
| `lockfile.json` | JSON | Python installer | `ap-fetcher::lockfile` |
| `sources.yaml` | YAML | 用户手写 | `ap-fetcher::sources` |
| `agent-manifest.yaml` | YAML | Agent 作者 | `ap-core::models::agent` |
| `*.toml` (DSL) | TOML | Agent 作者 | `ap-core::orchestration::dsl` |
| evolution SQLite | SQLite | Python store | `ap-evolution::store` |

---

## 5. 各 Crate 详细设计

### 5.1 ap-core — 平台内核

```
ap-core/
├── models/              # 共享数据模型
│   ├── agent.rs             AgentManifest, AgentType
│   ├── config.rs            ModelConfig, ProviderConfig
│   ├── ipc.rs               PlatformToAgent, AgentToPlatform
│   ├── task.rs              TaskItem, TaskStatus
│   ├── composition.rs       WorkflowPhase, WorkflowResult
│   ├── evolution.rs         SkillRecord, EvolutionSuggestion
│   ├── permission.rs        PermissionMode, PermissionCheck
│   ├── runtime.rs           SecurityViolation, ExecutionResult
│   ├── hooks.rs             HookEvent, HookAction
│   └── context.rs           ContextWindow, ContextBudget
├── config/              # 配置管理
│   ├── model_config.rs      ModelConfigManager: 模型字符串 + provider API key
│   ├── loader.rs            ConfigLoader: config.toml + sources.yaml
│   └── defaults.rs          默认值
├── orchestration/       # 编排核心
│   ├── task_graph.rs        TaskGraph: SQLite 任务依赖 + 环检测
│   ├── process_manager.rs   ProcessManager: tokio::process 管理
│   ├── ipc.rs               IPCStream: JSON-lines framing (tokio codec)
│   ├── ipc_protocol.rs      IPCProtocol: send_chat/send_task/heartbeat
│   ├── ipc_lock.rs          per-agent Mutex registry (DashMap)
│   └── dsl.rs               OrchestrationDSL: TOML DAG parser
├── router/              # 4-Phase 路由
│   ├── router.rs            PlatformRouter: route_chat/route_composite
│   ├── subtask.rs           SubtaskController: timeout/retry/parallel
│   └── workflow.rs          WorkflowPhase/WorkflowContext/WorkflowResult
├── hooks/               # 生命周期钩子
│   └── executor.rs          HookExecutor
├── skills/              # Skill 加载
│   ├── loader.rs            SkillLoader: SKILL.md 解析
│   └── models.rs            Skill data models
└── utils.rs             # 共享工具函数
```

#### 关键实现细节

**TaskGraph (SQLite)**：
- 使用 `rusqlite` + WAL 模式
- schema 与 Python 版 `task_graph.py` 完全一致
- `blocked_by` 依赖关系 + 拓扑排序 + 环检测
- 支持内存模式 (`:memory:`) 用于测试

**ProcessManager (tokio::process)**：
- 每个 Agent 一个 `tokio::process::Command`
- stdin/stdout 通过 `tokio::codec::LinesCodec` 做 JSON-lines framing
- 健康检查：周期性心跳（`tokio::time::interval`）
- 进程清理：`_cleanup_dead` 在检测到退出后关闭 FD + 清理资源
- 最大并发子进程数可配置（默认 10）

**OrchestrationDSL (TOML)**：
- 解析 TOML DAG 定义
- 验证：无环、所有依赖引用有效、阶段内无跨阶段依赖
- 支持 `depends_on`、`phase`、`agent`、`timeout` 字段

**PlatformRouter (4-Phase)**：
- 与 Python 版相同的 4 阶段流程：Research → Synthesis → Implementation → Verification
- 每阶段创建独立 TaskGraph
- 并行 worker 用 `tokio::JoinSet` 管理
- 总超时 = phases × per-call timeout

### 5.2 ap-runtime — Python 子进程桥接

```
ap-runtime/
├── lib.rs
├── process.rs           // ProcessManager: tokio::process 管理
├── ipc/
│   ├── codec.rs         // tokio::codec::LinesCodec for JSON-lines
│   ├── stream.rs        // IPCStream: send/receive with timeout
│   └── protocol.rs      // IPCProtocol: 高层语义方法
├── mcp_client.rs        // rmcp MCP client wrapper
└── lock.rs              // per-agent tokio::sync::Mutex registry
```

#### 关键实现细节

**IPCStream**：
- `send()`: 序列化 → `stdin.write_all()` → `stdin.flush()`
- `receive()`: `stdout.readline()` + `serde_json::from_slice()` + timeout
- 最大消息 4MB，超出拒绝
- 错误恢复：`ConnectionClosed` → 清理 FD → 上报

**IPCProtocol**：
- `send_chat()`, `send_task()`, `send_data_reference()`
- `receive_result()`: 从 peek buffer 或 stream 读取
- `receive_until_result()`: 循环接收直到 result/error，中间 progress 转 callback
- `send_heartbeat()`: ping-pong 模式，10s 超时

**Lock Registry**：
- `DashMap<String, Arc<Mutex<()>>>` 替代 Python 的 `dict[str, asyncio.Lock]`
- 最多 1000 个 agent lock，FIFO 驱逐
- 无 event loop 绑定问题（Rust 的 Mutex 不依赖运行时）

**MCP Client**：
- `rmcp` client 连接 Agent MCP Server（stdio transport）
- 工具发现：`list_tools()` → schema
- 工具调用：`call_tool(name, arguments)` → result
- 连接池：per-agent MCP 连接复用

### 5.3 ap-gateway — MCP Gateway

```
ap-gateway/
├── lib.rs
├── gateway.rs           // MCPGateway: axum + rmcp 聚合
├── tool_adapter.rs      // McpToolAdapter: Agent tool → MCP tool
├── deferred_registry.rs // DeferredAgentRegistry: 按需激活
└── schema.rs            // JSON Schema → rmcp Schema 转换
```

#### Deferred Loading 流程

1. Gateway 启动：只加载 Agent manifest（名称 + 描述），不启动子进程
2. LLM 调用 `search_and_activate(agent_name)`
3. Registry 通过 `ap-runtime` 启动子进程 + 建立 MCP 连接 + 拉取 tool schema
4. 后续 tool call 走已建立的 MCP 连接
5. 空闲超时自动关闭子进程释放资源

### 5.4 ap-fetcher — Git 分发层

```
ap-fetcher/
├── lib.rs
├── installer.rs         // GitInstaller: git2 clone + sparse checkout
├── sources.rs           // SourceManager: sources.yaml 管理
├── lockfile.rs          // LockfileManager: lockfile.json 原子读写
├── supervisor.rs        // AgentSupervisor: 进程生命周期管理
└── uv_bridge.rs         // uv 命令调用
```

#### 关键实现细节

**GitInstaller**：
- `git2::Repository::clone()` 替代 `git.Repo.clone_from()`
- 支持 sparse checkout（只 checkout agent 目录）
- 支持 tag/version pinning（`semver` 版本解析）
- 原子安装：先 clone 到 temp dir，完成后 rename

**LockfileManager**：
- 读：`serde_json::from_reader()` 直接映射到 struct
- 写：tempfile + write + rename 保证原子性
- 向后兼容：忽略未知字段（`#[serde(deny_unknown_fields)]` 不使用）

**uv_bridge**：
- `tokio::process::Command::new("uv")` 调用
- `uv venv`、`uv pip install` 封装
- 前置检查：`which uv`，不存在时给出安装提示

### 5.5 ap-evolution — 自进化引擎

```
ap-evolution/
├── lib.rs
├── store/
│   ├── mod.rs           // EvolutionStore facade
│   ├── schema.rs        // SQLite DDL (与 Python 版兼容)
│   └── queries.rs       // 查询方法
├── analyzer.rs          // ExecutionAnalyzer
├── evolver.rs           // SkillEvolver: FIX/DERIVED/CAPTURED via IPC
├── compaction.rs        // ContextCompactor
├── promotion.rs         // AgentPromoter: skill → agent
├── health.rs            // HealthTracker
├── thresholds.rs        // 阈值常量 + 配置
├── context_describer.rs // 进化上下文描述
└── engine.rs            // EvolutionEngine: 统一 facade
```

#### 关键实现细节

**EvolutionStore (rusqlite)**：
- SQLite schema 与 Python 版完全兼容（同一 DB 文件可读写）
- WAL 模式 + foreign keys
- `_chunked_in_fetchall()` 用 Rust iterator chunks 实现
- 内存模式 (`:memory:`) 用于测试

**SkillEvolver (IPC 解耦)**：
- Python 版：evolver 通过 IPythonRuntime in-process 执行代码
- Rust 版：evolver 通过 `ap-runtime` IPC 调 Agent 子进程执行
- 从 in-process → IPC 的转变让架构更干净，agent 安全边界更强

**AgentPromoter**：
- 文件生成（manifest/YAML/pyproject.toml/SKILL.md）用 `std::fs` + `serde_yaml`
- 原子写入保证：tempfile + rename
- 失败回滚：track written files，partial failure 时清理

### 5.6 ap-cli — CLI 入口

```
ap-cli/
├── main.rs
├── commands/
│   ├── mod.rs
│   ├── init.rs          // agent-nexus init
│   ├── sources.rs       // agent-nexus sources add/list/remove
│   ├── install.rs       // agent-nexus install <agent>
│   ├── run.rs           // agent-nexus run <agent> <task>
│   ├── create.rs        // agent-nexus create agent
│   ├── check.rs         // agent-nexus check
│   ├── config.rs        // agent-nexus config get/set
│   ├── evolution.rs     // agent-nexus evolution status/promote
│   └── runtime.rs       // agent-nexus runtime exec
└── output.rs            // 格式化输出 (--json, --follow, colored)
```

#### CLI 命令映射

| Python CLI | Rust CLI | 实现文件 |
|-----------|---------|---------|
| `agent-nexus init` | `agent-nexus init` | init.rs |
| `agent-nexus sources add/list/remove` | `agent-nexus sources add/list/remove` | sources.rs |
| `agent-nexus install` | `agent-nexus install <agent>` | install.rs |
| `agent-nexus run` | `agent-nexus run <agent> <task>` | run.rs |
| `agent-nexus create agent` | `agent-nexus create agent` | create.rs |
| `agent-nexus check` | `agent-nexus check` | check.rs |
| `agent-nexus config get/set` | `agent-nexus config get/set` | config.rs |
| `agent-nexus evolution status/promote` | `agent-nexus evolution status/promote` | evolution.rs |
| `agent-nexus runtime exec` | `agent-nexus runtime exec` | runtime.rs |

#### 输出格式
- 默认：colored text (owo-colors)
- `--json`：JSON 输出
- `--follow`：流式输出（tail -f 模式）

---

## 6. 错误处理体系

### 分层策略

```rust
// 库 crate (ap-core, ap-runtime, etc.) — thiserror 精确定义
#[derive(Debug, thiserror::Error)]
pub enum IpcError {
    #[error("Agent stdout closed (EOF)")]
    ConnectionClosed,
    #[error("Timed out after {timeout:.1}s")]
    Timeout { timeout: f64 },
    #[error("Message too large: {size} bytes (max {max})")]
    Oversized { size: usize, max: usize },
    #[error("Invalid JSON from agent: {0}")]
    InvalidJson(#[from] serde_json::Error),
}

// CLI 入口 — anyhow 兜底
// binary entrypoints use anyhow::Result, thiserror errors propagate naturally
```

### 原则
- 库 crate 用 `thiserror` 精确定义
- CLI 入口用 `anyhow` 兜底，`.context()` 添加链路信息
- 禁止 `unwrap()` — 所有 `must_use` 结果强制处理
- IPC 错误恢复：`ConnectionClosed` → `_cleanup_dead` → 重试或报告

---

## 7. 测试策略

| 层级 | Rust 工具 | 说明 |
|------|----------|------|
| 单元测试 | `#[test]` + `rstest` | 纯逻辑验证，无 I/O |
| 集成测试 | `tests/` + `tokio::test` | SQLite/文件系统/mock 子进程 |
| IPC 测试 | `tokio::io::duplex()` mock | 不启动真实 Python 进程 |
| CLI 测试 | `assert_cmd` | 命令行参数 + 输出验证 |
| 兼容性测试 | Python fixture 文件 | Rust 读取 Python 写的 lockfile/SQLite/config |
| 端到端测试 | 真实 Python Agent 子进程 | 复用现有 Agent，验证 MCP + IPC 双通道 |

### 兼容性测试（关键）

```
tests/compat/
├── fixtures/
│   ├── lockfile_python.json     → Rust LockfileManager 读取验证
│   ├── evolution_python.db      → Rust EvolutionStore 读取验证
│   ├── config_python.toml       → Rust ConfigLoader 读取验证
│   ├── sources_python.yaml      → Rust SourceManager 读取验证
│   └── manifest_python.yaml     → Rust AgentManifest 解析验证
```

---

## 8. Python Agent 不动的保证

| 保证项 | 机制 |
|--------|------|
| Agent Package 格式 | `pyproject.toml + agent-manifest.yaml + src/` 不变 |
| Agent 通信协议 | stdin/stdout JSON-lines 消息格式不变 |
| MCP 工具 schema | Agent 侧 MCP Server (FastMCP) 不变 |
| Agent 分发格式 | Git repo + agent-manifest.yaml 结构不变 |
| venv 管理 | 仍然用 `uv`，Rust 通过 shell 调用 |
| config.toml | 格式不变，Rust 读取兼容 |
| lockfile.json | 格式不变，Rust 读取兼容 |
| evolution SQLite | schema 不变，Rust 读写兼容 |

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| rmcp crate 不成熟 | Gateway 功能受限 | 双通道设计：MCP 不行时 fallback raw IPC |
| Evolution SQLite schema 漂移 | 数据丢失 | 兼容性测试 + schema version 字段 |
| IPC 消息格式不兼容 | Agent 无法通信 | fixture-based 兼容性测试 + 集成验证 |
| Rust 编译时间 | 开发体验 | workspace 分 crate，增量编译只重编译改动的 crate |
| uv 不在 PATH | 安装失败 | `check` 命令前置检查 + 安装提示 |

---

## 10. Workspace Cargo.toml 结构

```toml
[workspace]
members = [
    "crates/ap-core",
    "crates/ap-runtime",
    "crates/ap-gateway",
    "crates/ap-fetcher",
    "crates/ap-evolution",
    "crates/ap-cli",
]
resolver = "2"

[workspace.dependencies]
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
thiserror = "2"
anyhow = "1"
rmcp = "0.1"
axum = "0.8"
git2 = "0.19"
rusqlite = { version = "0.32", features = ["bundled"] }
clap = { version = "4", features = ["derive"] }
toml = "0.8"
serde_yaml = "0.9"
semver = "1"
chrono = { version = "0.4", features = ["serde"] }
dashmap = "6"
owo-colors = "4"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
```
