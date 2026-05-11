# P0-1: Peer-to-Peer Multi-Agent 协作

> 优先级：P0 🔴 | 预估工期：W1-6 | 独立路径，无强依赖

## 需求

- **来源**：Qwen-code #1815 Feature Request，行业趋势 Hub-Spoke → P2P Mesh
- **目标**：在 TaskGraph blocked_by 基础上增加 A2A 消息传递，从 hub-spoke 演进到 mesh 协作
- **需求强度**：⭐⭐⭐⭐⭐ | **差异化**：⭐⭐⭐⭐⭐ | **实现复杂度**：中

## 当前状态

| 组件 | 状态 | 文件 |
|------|------|------|
| TaskGraph (blocked_by + cycle detection) | ✅ 成熟 | `platform/orchestration/task_graph.py` |
| IPC (JSON-lines, 4MB, heartbeat) | ✅ 成熟 | `platform/orchestration/ipc.py` |
| IPC 消息类型 (仅 Platform↔Agent 各 3 种) | ⚠️ 有限 | `models/ipc.py` |
| ProcessManager (subprocess + health check) | ✅ 成熟 | `platform/orchestration/process_manager.py` |
| OrchestrationDSL (TOML DAG) | ✅ 成熟 | `platform/orchestration/dsl.py` |
| PlatformRouter (4-phase hub-spoke) | ✅ 成熟 | `platform/router/router.py` |
| **Agent 发现** | ❌ | — |
| **A2A 消息类型** | ❌ | — |
| **消息路由/广播** | ❌ | — |
| **团队生命周期** | ❌ | — |

### 关键约束

- IPC 是点到点 stream（platform↔agent），非 mesh
- ProcessManager 持有所有 agent handle，agent 间无直接通道
- TaskGraph 是平台作用域 SQLite，agent 无法直接修改任务状态
- DSL 是声明式 TOML，不支持命令式消息操作
- ClawTeam 的 file-based mailbox 被刻意替换过，不应回退

## 设计方案

### 核心决策：Platform-as-Broker

Agent 不直连，Platform Router 作为消息代理转发 A2A 消息。不改变 IPC stream 的点到点本质。

### A2A 消息模型

```
Agent A ──send_chat──> Platform ──receive_chat──> Agent B
Agent A ──request───> Platform ──receive_request──> Agent B
                                      Agent B ──reply──> Platform ──deliver──> Agent A
Agent A ──broadcast─> Platform ──receive──> [Agent B, C, D, ...]
```

**新增 IPC 消息类型**（8 种）：

| 方向 | 类型 | 说明 |
|------|------|------|
| Agent→Platform | `SEND_MESSAGE` | 发送消息给指定 agent |
| Agent→Platform | `SEND_REQUEST` | 发送请求（需回复） |
| Agent→Platform | `BROADCAST` | 广播给同组 agent |
| Agent→Platform | `REPLY` | 回复请求 |
| Platform→Agent | `RECEIVE_MESSAGE` | 收到消息 |
| Platform→Agent | `RECEIVE_REQUEST` | 收到请求（需回复） |
| Platform→Agent | `RECEIVE_BROADCAST` | 收到广播 |
| Platform→Agent | `RECEIVE_REPLY` | 收到回复 |

**新增数据模型** (`models/ipc.py`)：

```python
class AgentAddress(BaseModel):
    agent_id: str
    role: str | None = None

class A2AMessage(BaseModel):
    message_id: str         # UUID
    from_agent: str
    to_agent: str | None    # broadcast 时为 None
    msg_type: Literal["chat", "request", "broadcast", "reply"]
    in_reply_to: str | None = None
    content: str
    metadata: dict = {}
    timestamp: float
```

### MessageBroker

新增 `platform/orchestration/message_broker.py`：

```python
class MessageBroker:
    def __init__(self, process_manager: ProcessManager):
        self._pm = process_manager
        self._pending_replies: dict[str, asyncio.Future] = {}

    async def send_message(self, from_id: str, to_id: str, content: str) -> None
    async def send_request(self, from_id: str, to_id: str, content: str, timeout: float = 30.0) -> str
    async def broadcast(self, from_id: str, content: str, group: str | None = None) -> list[str]
    async def deliver_reply(self, request_id: str, content: str) -> None
    async def route(self, from_id: str, message: A2AMessage) -> None
```

### AgentDirectory

新增 `platform/orchestration/agent_directory.py`：

```python
class AgentDirectory:
    def register(self, agent_id: str, capabilities: list[str], role: str) -> None
    def deregister(self, agent_id: str) -> None
    def resolve(self, agent_id: str) -> AgentAddress | None
    def find_by_capability(self, capability: str) -> list[AgentAddress]
    def find_by_role(self, role: str) -> list[AgentAddress]
    def list_active(self) -> list[AgentAddress]
```

### DSL 扩展

```toml
[messaging]
enabled = true
max_message_size = 1048576   # 1MB (超过走 DATA_REFERENCE)
request_timeout = 30.0       # seconds
allowed_channels = ["chat", "request", "broadcast"]
```

## 已确认决策

| # | 决策 | 理由 |
|---|------|------|
| D1 | 消息持久化：内存队列 | 消息瞬态，进程挂了靠 TaskGraph 重新调度 |
| D2 | 请求超时：抛异常让调用方决定 | 平台不替 agent 做业务决策 |
| D3 | 广播组：按 DSL role | 零新增概念，复用现有字段 |
| D4 | 路由权限：仅同一 composition 内 | 安全默认值，天然信任关系 |
| D5 | 大消息：复用 4MB 限制 + DATA_REFERENCE | 复用现有机制 |
| D6 | 死锁：超时 + 禁止跨请求嵌套 | 超时打破 + 禁令从根源防 |
| D7 | 消息与 TaskGraph 状态分离 | 关注点分离，agent 自行决定操作 TaskGraph |

## 实施阶段

| Phase | 内容 | 工期 | 验证标准 |
|-------|------|------|----------|
| 1 | A2A 消息模型 + MessageBroker + IPC 类型 | W1-2 | 单元测试 4 种消息类型全通过 |
| 2 | AgentDirectory + 发现协议 | W3 | 集成测试：动态注册/查找/注销 |
| 3 | DSL messaging 配置 + 路由规则 | W4 | E2E：两 agent 通过 broker 完成请求-回复 |
| 4 | Rust 同步 | W5-6 | Rust 端 A2A 测试通过 |

## 依赖

- ProcessManager agent handle 注册（已存在）
- IPC protocol 消息类型扩展
- 无外部依赖

## 风险

| 风险 | 缓解 |
|------|------|
| 消息路由延迟 | 异步投递 + asyncio.Queue |
| 请求超时死锁 | 全局超时 + 嵌套禁令 |
| 广播滥用 | 速率限制 + 消息配额 |
| Rust 同步成本 | Python 先行，Phase 4 再同步 |

## ClawTeam 参考

| 特性 | ClawTeam | 本设计 |
|------|----------|--------|
| 消息传输 | File-based mailbox | Platform-as-Broker (IPC relay) |
| 消息类型 | 12 种 | 8 种 |
| Agent 发现 | MailboxManager.resolve_inbox | AgentDirectory |
| 团队管理 | TeamManager lifecycle | OrchestrationDSL |
