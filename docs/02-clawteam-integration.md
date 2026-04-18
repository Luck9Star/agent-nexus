# 自建编排层

> Agent Nexus POC v5 — §4 自建编排层：自建组件设计（参考 ClawTeam 实现）、TaskGraph、IPC、ProcessManager、OrchestrationDSL、与 ClawTeam 的对应关系

## §4 自建编排层

> **参考项目**: [ClawTeam](https://github.com/hkuds-lab/clawteam) — MIT License, HKUDS
>
> **本地源码**: `/Users/yangyitian/Documents/dev/Agents/ClawTeam/`

### 4.1 设计决策：自建而非依赖

**为什么自建？**

| 维度 | ClawTeam 依赖 | 自建 |
|------|-------------|------|
| API 稳定性 | 中风险（风险矩阵原 R1） | 完全控制 |
| 实际使用率 | TaskStore + Mailbox + Spawn 约 30% 被使用 | 100% 按需 |
| 编排智能 | Leader 是 LLM agent，非程序化控制流 | Platform Router 自建 4-Phase Workflow |
| 外部依赖 | pip 依赖 + 版本锁定 | 零外部编排依赖 |
| Rust 重构 | 需先脱钩再重构 | 直接用 Rust 实现 |

**自建原则**：
- 参考 ClawTeam 经过验证的实现，不闭门造车
- 必要时直接搬运 ClawTeam 代码（MIT License，保留版权声明）
- 只建需要的能力，不做过度设计
- 自建组件的接口完全由本项目控制，便于后续 Rust 重构

### 4.2 组件对应关系

| ClawTeam 模块 | 自建组件 | 说明 | 参考源码 |
|---|---|---|---|
| `clawteam/store/` TaskStore | **TaskGraph** | 任务依赖图 + 状态机（SQLite） | `store/file.py` blocked_by + 环检测 |
| `clawteam/team/mailbox.py` | **IPC** | 进程间通信（stdin/stdout JSON-lines） | 简化：不需要文件邮箱，用管道通信 |
| `clawteam/spawn/` SpawnBackend | **ProcessManager** | 子进程管理 + 健康检查 + 自动重启 | `spawn/subprocess_backend.py` |
| `clawteam/templates/` Team Template | **OrchestrationDSL** | TOML DAG 编排定义 | 模板格式 + blocked_by |
| `clawteam/transport/` Transport | **暂不实现** | 初期仅本地通信 | P2P ZeroMQ 按需扩展 |
| `clawteam/board/` Board | **暂不实现** | 初期不需要可视化 | 按需加 |
| `clawteam/harness/` Harness | **不实现** | Platform Router 4-Phase Workflow 替代 | — |

### 4.3 TaskGraph（任务依赖图）

> **参考模块**: ClawTeam `clawteam/store/file.py` — `FileTaskStore` 实现 (blocked_by 依赖解析 + DFS 环检测 + 完成时自动解锁下游)

自建任务管理，核心能力参考 ClawTeam TaskStore：

```python
class TaskGraph:
    """
    任务依赖图：支持顺序 + 并行混合编排。
    状态机：pending → in_progress → completed / failed / blocked
    """

    def __init__(self, db_path: str = ":memory:"):
        # SQLite 存储，支持持久化和并发安全
        self._db = sqlite3.connect(db_path)
        self._init_schema()

    def add_task(self, task_id: str, agent: str, description: str,
                 blocked_by: list[str] = None) -> Task:
        """创建任务，自动设置 blocked 状态"""
        ...

    def complete_task(self, task_id: str, result: Any = None) -> list[str]:
        """完成任务，返回新解锁的任务 ID 列表"""
        ...

    def get_ready_tasks(self) -> list[Task]:
        """获取所有可执行的任务（pending 且无阻塞）"""
        ...

    def detect_cycles(self) -> bool:
        """DFS 环检测（参考 ClawTeam store/file.py:291-319）"""
        ...

    def get_parallel_groups(self) -> list[list[str]]:
        """
        按依赖拓扑排序，返回可并行执行的分组。
        同一组内可并行，组间顺序执行（参考 ClawTeam wave 模式）。
        """
        ...
```

**与 ClawTeam TaskStore 的差异**：
- 存储：SQLite（并发安全）vs 文件 JSON（fcntl.flock）
- 通信：内嵌在 ProcessManager 中，无需独立 CLI 命令
- 查询：内存缓存 + SQLite 持久化，性能更优

### 4.4 IPC（进程间通信）

> **参考模块**: ClawTeam `clawteam/team/mailbox.py` — `MailboxManager` (send/broadcast/peek)

简化 ClawTeam 的文件邮箱模式，采用 stdin/stdout JSON-lines 管道通信：

```python
# Platform → Agent 消息格式
{"type": "chat", "content": "...", "conversation_id": "..."}
{"type": "task", "task_id": "...", "description": "...", "blocked_by": []}
{"type": "data_reference", "ref_id": "var://...", "summary": "..."}

# Agent → Platform 消息格式
{"type": "result", "task_id": "...", "output": "...", "status": "completed"}
{"type": "progress", "task_id": "...", "message": "..."}
{"type": "error", "task_id": "...", "error": "..."}
```

**与 ClawTeam MailboxManager 的差异**：
- 传输：stdin/stdout 管道（零文件 IO） vs 文件邮箱（.tmp → rename）
- 无需 broadcast：Platform Router 是唯一协调者，直接向各 Agent 发送
- 无需 peek：管道是流式的，Platform Router 按序读取

### 4.5 ProcessManager（进程管理）

> **参考模块**: ClawTeam `clawteam/spawn/subprocess_backend.py` — `SubprocessBackend` (asyncio subprocess), `clawteam/spawn/tmux_backend.py` — `TmuxBackend`

```python
class ProcessManager:
    """
    Agent 子进程管理。
    默认 asyncio.subprocess（参考 ClawTeam SubprocessBackend）。
    支持：启动、停止、健康检查、自动重启。
    """

    async def start_agent(self, name: str, config: AgentConfig) -> AgentHandle:
        """启动 Agent 子进程，返回 Handle（含 stdin/stdout pipe）"""
        ...

    async def stop_agent(self, name: str, *, force: bool = False) -> None:
        """停止 Agent（graceful SIGTERM → force SIGKILL）"""
        ...

    async def health_check(self, name: str) -> HealthStatus:
        """健康检查（进程存活 + 心跳响应）"""
        ...

    async def restart_agent(self, name: str) -> AgentHandle:
        """自动重启（保留 TaskGraph 状态，恢复未完成任务）"""
        ...

    def list_running(self) -> list[AgentInfo]:
        """列出所有运行中的 Agent"""
        ...

@dataclass
class AgentHandle:
    """Agent 子进程句柄"""
    name: str
    process: asyncio.subprocess.Process
    stdin: asyncio.StreamWriter
    stdout: asyncio.StreamReader
    started_at: datetime
    last_heartbeat: datetime
```

**与 ClawTeam SpawnBackend 的差异**：
- 不实现 tmux 后端（开发调试直接用终端）
- 不实现 wsh 后端（不需要 Wave 终端）
- 增加：健康检查、自动重启、心跳机制
- BackendRegistry 保留：支持 InProcess（测试）+ Subprocess（生产）两种模式

### 4.6 OrchestrationDSL（编排 DSL）

> **参考模块**: ClawTeam `clawteam/templates/__init__.py` — `TemplateDef` 模型, `clawteam/harness/contract_executor.py` — Wave-based 并行执行

TOML 格式定义 Composite Agent 的编排 DAG：

```toml
[goal]
description = "需求驱动并行生成 API 文档、测试套件和代码审查"

[agent_name]
value = "feature-delivery-pipeline"

[[agents]]
name = "requirements-analyzer"
description = "需求分析专家"
role = "coordinator"

[[agents]]
name = "api-doc-generator"
description = "API 文档生成专家"

[[agents]]
name = "test-suite-generator"
description = "测试生成专家"

[[agents]]
name = "code-reviewer"
description = "代码审查专家"

[[tasks]]
id = "analyze-requirements"
description = "分析需求，生成结构化需求说明书"
agent = "requirements-analyzer"
blocked_by = []

[[tasks]]
id = "generate-api-doc"
description = "根据需求生成 API 文档"
agent = "api-doc-generator"
blocked_by = ["analyze-requirements"]

[[tasks]]
id = "generate-tests"
description = "根据需求生成测试套件"
agent = "test-suite-generator"
blocked_by = ["analyze-requirements"]

[[tasks]]
id = "review-code"
description = "代码审查"
agent = "code-reviewer"
blocked_by = ["analyze-requirements"]

[tool_loading]
strategy = "lazy"
preload_agents = ["requirements-analyzer"]
```

**与 ClawTeam Team Template 的差异**：
- `[[workers]]` → `[[agents]]`：术语统一
- `worker` → `agent`：任务分配字段名
- 新增 `[tool_loading]`：Agent 级 Deferred Loading 策略
- 新增 `blocked_by` 在模板层直接声明依赖（ClawTeam 的 TaskDef 无此字段）
- 模板变量替换保留：`{goal}`, `{team_name}`, `{agent_name}`

### 4.7 自建 vs 依赖决策总结

| 自建组件 | 代码量估算 | 参考 ClawTeam 源码 | 复杂度 |
|----------|-----------|-------------------|--------|
| TaskGraph | ~200 行 | `store/file.py` (blocked_by + 环检测) | 中 |
| IPC 协议 | ~100 行 | `team/mailbox.py` (消息格式参考) | 低 |
| ProcessManager | ~300 行 | `spawn/subprocess_backend.py` + 健康检查 | 中 |
| OrchestrationDSL | ~150 行 | `templates/__init__.py` + TOML 解析 | 低 |
| **总计** | **~750 行** | | |

> MIT License 义务：如果直接搬运 ClawTeam 代码，需保留原始版权声明和许可声明。

---
