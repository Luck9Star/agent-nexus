# Agent Nexus 功能路线图设计文档 (P0/P1/P2)

> **已拆分**：本文档内容已按模块拆分至 `docs/roadmap/` 目录，便于实现时按需读取。
> - 总控文档 + TODO 清单：[docs/roadmap/README.md](roadmap/README.md)
> - 各模块独立文档：`roadmap/p0-1-*.md` / `p0-2-*.md` / `p1-3-*.md` / `p1-4-*.md` / `p2-5-*.md`
>
> 本文件保留作为历史参考，不再更新。

---

~~> 基于《Agent Nexus 需求分析最终报告》驱动，结合代码库深度审计。~~
~~> 生成日期：2026-05-10~~

---

## 目录

1. [总览](#1-总览)
2. [P0-1: Peer-to-Peer Multi-Agent 协作](#2-p0-1-peer-to-peer-multi-agent-协作)
3. [P0-2: MCP Gateway 安全增强](#3-p0-2-mcp-gateway-安全增强)
4. [P1-3: Agent Marketplace + Quality Gate](#4-p1-3-agent-marketplace--quality-gate)
5. [P1-4: Domain Atomic Agent 扩展](#5-p1-4-domain-atomic-agent-扩展)
6. [P2-5: Self-Evolution 产品化](#6-p2-5-self-evolution-产品化)
7. [依赖关系与实施顺序](#7-依赖关系与实施顺序)
8. [风险矩阵](#8-风险矩阵)

---

## 1. 总览

### 需求来源

| 优先级 | 功能 | 需求强度 | 差异化 | 实现复杂度 |
|--------|------|----------|--------|-----------|
| P0-1 | P2P Multi-Agent 协作 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 中 |
| P0-2 | MCP Gateway 安全增强 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 中 |
| P1-3 | Agent Marketplace + QG | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 高 |
| P1-4 | Domain Atomic Agent 扩展 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 低 |
| P2-5 | Self-Evolution 产品化 | ⭐⭐⭐ | ⭐⭐⭐⭐ | 高 |

### 行业趋势锚定

- **A2A/ACP 协议兴起**：行业从 Hub-Spoke 走向 P2P Mesh（P0-1 直接受益）
- **ClawHavoc 供应链攻击**：企业急需 MCP 安全产品（P0-2 是最强竞争力）
- **Git-based Agent 分发**：Homebrew Tap 模型是行业缺口 = 差异化机会（P1-3）
- **泛化→专精趋势**：市场从通用 Skills 转向专精 Agent（P1-4）
- **Self-Evolution 学术爆发**：EvoAgentX、MASLab 等多篇顶会论文（P2-5）

### 核心原则

1. **不破坏现有架构**：所有新功能以增量方式添加，不重构已有模块
2. **双语言一致**：Python + Rust 平台保持接口兼容
3. **渐进式交付**：每个功能分阶段实施，每阶段可独立验证

---

## 2. P0-1: Peer-to-Peer Multi-Agent 协作

### 2.1 需求分析

**目标**：在现有 TaskGraph `blocked_by` 依赖调度基础上，增加 Agent-to-Agent (A2A) 直接消息传递能力，实现从 Hub-Spoke 到 Mesh 协作的演进。

**来源**：Qwen-code #1815 Feature Request，多论坛热议，行业趋势从 Hub-Spoke → P2P Mesh。

**核心场景**：
- Agent A 在执行中需要向 Agent B 请求中间结果（非阻塞式）
- Agent A 需要向 Agent B 请求审批（阻塞式，等待回复）
- Agent 广播通知（如"我已完成数据准备，需要的人可以开始了"）
- 动态子任务委派（Agent A 发现需要 Agent C 的能力，动态请求协助）

### 2.2 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| TaskGraph | ✅ 成熟 | SQLite DAG，blocked_by + cycle detection + parallel groups |
| IPC 协议 | ✅ 成熟 | JSON-lines stdin/stdout，4MB 限制，heartbeat |
| IPC 消息类型 | ⚠️ 有限 | 仅 Platform↔Agent 各 3 种，无 A2A 类型 |
| ProcessManager | ✅ 成熟 | subprocess 管理，健康检查，优雅关闭 |
| OrchestrationDSL | ✅ 成熟 | TOML DAG，仅支持静态任务依赖 |
| Agent 发现 | ❌ 缺失 | Agent 不知道彼此的存在 |
| 消息路由 | ❌ 缺失 | 无 relay/broadcast/pub-sub 机制 |
| 团队生命周期 | ❌ 缺失 | 无 TeamManager 等价物 |

**关键约束**：
- IPC 是点到点 stream（platform↔agent），非 mesh
- ProcessManager 持有所有 agent handle，agent 间无直接通道
- TaskGraph 是平台作用域的 SQLite，agent 无法直接修改任务状态
- DSL 是声明式 TOML，不支持命令式消息操作

### 2.3 设计方案

**核心决策：Platform-as-Broker 模式**

> 不引入新的 agent 间直连通道（避免复杂性爆炸），而是让 Platform Router 作为消息代理（Message Broker），在 agent 间转发消息。

**理由**：
1. 不改变 IPC stream 的点到点本质 — agent 仍然只有一条连接
2. Platform 已持有所有 agent handle，天然具备路由能力
3. 安全审计集中化 — 所有 A2A 消息经过平台，可统一检查
4. 与 TaskGraph 状态管理天然协同

#### 2.3.1 A2A 消息模型

```
Agent A ──send_chat──> Platform Router ──receive_chat──> Agent B
Agent A ──request───> Platform Router ──receive_request──> Agent B
                                            Agent B ──reply──> Platform ──deliver_reply──> Agent A
Agent A ──broadcast─> Platform Router ──receive──> [Agent B, C, D, ...]
```

**新增 IPC 消息类型**：

| 方向 | 类型 | 说明 |
|------|------|------|
| Agent→Platform | `SEND_MESSAGE` | 发送消息给指定 agent |
| Agent→Platform | `SEND_REQUEST` | 发送请求（需回复）给指定 agent |
| Agent→Platform | `BROADCAST` | 广播给同组/全部 agent |
| Agent→Platform | `REPLY` | 回复收到的请求 |
| Platform→Agent | `RECEIVE_MESSAGE` | 收到来自其他 agent 的消息 |
| Platform→Agent | `RECEIVE_REQUEST` | 收到来自其他 agent 的请求（需回复） |
| Platform→Agent | `RECEIVE_BROADCAST` | 收到广播 |
| Platform→Agent | `RECEIVE_REPLY` | 收到请求的回复 |

**新增数据模型** (`models/ipc.py`)：

```python
class AgentAddress(BaseModel):
    """Agent 寻址模型"""
    agent_id: str          # agent 在 composition 中的名称
    role: str | None = None  # 可选：按角色寻址（如 "所有 worker"）

class A2AMessage(BaseModel):
    """Agent-to-Agent 消息"""
    message_id: str         # UUID
    from_agent: str         # 发送者 agent_id
    to_agent: str | None    # 接收者（broadcast 时为 None）
    msg_type: Literal["chat", "request", "broadcast", "reply"]
    in_reply_to: str | None = None  # 回复的消息 ID
    content: str            # 消息内容
    metadata: dict = {}     # 扩展字段
    timestamp: float        # 发送时间
```

#### 2.3.2 MessageBroker 模块

新增 `platform/orchestration/message_broker.py`：

```python
class MessageBroker:
    """平台级消息代理，负责 A2A 消息路由"""

    def __init__(self, process_manager: ProcessManager):
        self._pm = process_manager
        self._pending_replies: dict[str, asyncio.Future] = {}  # request_id -> Future

    async def send_message(self, from_id: str, to_id: str, content: str) -> None
    async def send_request(self, from_id: str, to_id: str, content: str, timeout: float = 30.0) -> str
    async def broadcast(self, from_id: str, content: str, group: str | None = None) -> list[str]
    async def deliver_reply(self, request_id: str, content: str) -> None
    async def route(self, from_id: str, message: A2AMessage) -> None
```

**核心行为**：
- `send_message`：异步投递，不等待回复
- `send_request`：阻塞等待回复（`asyncio.Future` + timeout），超时抛 `A2ATimeoutError`
- `broadcast`：可选 group 过滤（按 role），返回成功投递的 agent 列表
- 所有消息经过 Platform → 可加审计日志、权限检查、速率限制

#### 2.3.3 AgentDirectory

新增 `platform/orchestration/agent_directory.py`：

```python
class AgentDirectory:
    """Agent 发现与能力注册"""

    def register(self, agent_id: str, capabilities: list[str], role: str) -> None
    def deregister(self, agent_id: str) -> None
    def resolve(self, agent_id: str) -> AgentAddress | None
    def find_by_capability(self, capability: str) -> list[AgentAddress]
    def find_by_role(self, role: str) -> list[AgentAddress]
    def list_active(self) -> list[AgentAddress]
```

- Agent 在启动时通过 IPC 注册自己的能力和角色
- Platform 维护活跃 agent 目录
- 支持"按能力查找"（Agent A 想找能做 code review 的 agent）

#### 2.3.4 DSL 扩展

在 OrchestrationDSL 中增加可选的 `[messaging]` 段：

```toml
[messaging]
enabled = true
max_message_size = 1048576  # 1MB
request_timeout = 30.0      # seconds
allowed_channels = ["chat", "request", "broadcast"]
```

不引入命令式消息操作到 TOML（保持声明式），消息行为在运行时由 agent 代码发起。

### 2.4 实施阶段

| 阶段 | 内容 | 预估工期 | 验证标准 |
|------|------|----------|----------|
| **Phase 1** | A2A 消息模型 + MessageBroker + IPC 类型扩展 | W1-2 | 单元测试：send/request/broadcast/reply 四种消息类型全部通过 |
| **Phase 2** | AgentDirectory + agent 发现协议 | W3 | 集成测试：agent 动态注册、按能力查找、自动注销 |
| **Phase 3** | DSL messaging 配置 + 路由规则 + 权限控制 | W4 | E2E 测试：两个 agent 通过 platform broker 完成一次请求-回复 |
| **Phase 4** | Rust 平台同步实现 | W5-6 | Rust 端 A2A 消息测试通过 |

### 2.5 依赖与风险

**依赖**：
- ProcessManager 的 agent handle 注册机制（已存在）
- IPC protocol 的消息类型扩展（需修改 `models/ipc.py`）
- 无外部依赖

**风险**：

| 风险 | 影响 | 缓解 |
|------|------|------|
| 消息路由延迟（platform 瓶颈） | 中 | 异步投递 + 消息队列（asyncio.Queue） |
| 请求超时导致死锁 | 高 | 设置全局超时 + 死锁检测（类似 TaskGraph cycle detection） |
| 消息爆炸（broadcast 滥用） | 中 | 速率限制 + 消息配额 |
| 与 Rust 平台同步成本 | 中 | 先实现 Python 端，Rust 端在 Phase 4 同步 |

---

## 3. P0-2: MCP Gateway 安全增强

### 3.1 需求分析

**目标**：在现有 SecurityChecker + PermissionChecker 基础上，增加 MCP 级别的认证、访问控制和审计日志，形成完整的纵深防御体系。

**来源**：Reddit cybersecurity 社区热议，ClawHavoc 供应链攻击教训。竞品无安全产品，这是最强差异化竞争力。

**核心场景**：
- 企业部署需要控制哪些 MCP 客户端可以连接 Gateway
- 审计追踪：谁在什么时间调用了什么工具，传了什么参数
- 外部 MCP server 连接需要认证（API key、bearer token）
- 敏感操作需要权限确认

### 3.2 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| SecurityChecker | ✅ 成熟 | AST 级代码安全分析，30+ 规则 |
| PermissionChecker | ✅ 成熟 | 运行时权限评估，3 种模式 |
| Gateway (Python) | ⚠️ 无认证 | FastMCP server，任何客户端可连接 |
| Gateway (Rust) | ⚠️ 无认证 | axum server，任何客户端可连接 |
| 外部 Server 连接 | ⚠️ 无认证 | ExternalServerConfig 无 auth 字段 |
| 审计日志 | ❌ 缺失 | 仅标准 logging，无结构化审计 |
| 调用记录 | ❌ 缺失 | 工具调用无持久化记录 |
| 输入/输出过滤 | ⚠️ 有限 | Schema 验证存在，但无内容过滤 |

**关键约束**：
- FastMCP 控制传输层，auth 需要中间件支持
- 双语言网关（Python + Rust）需同步实现
- PermissionChecker 是 agent-centric（agent 能做什么），不是 client-centric（谁可以调用）
- IPC 信任域内设计，Gateway 认证引入新的信任边界

### 3.3 设计方案

**核心决策：三层安全模型**

```
Layer 1: Gateway Authentication — 谁可以连接？
Layer 2: Tool Authorization — 连接者可以调用什么工具？
Layer 3: Audit Trail — 记录一切，不可篡改
```

#### 3.3.1 Gateway Authentication (Layer 1)

**方案：API Key + Bearer Token 双模式**

新增 `platform/gateway/auth.py`：

```python
class GatewayAuthConfig(BaseModel):
    """Gateway 认证配置"""
    enabled: bool = True
    method: Literal["api_key", "bearer_token", "mtls"] = "api_key"
    keys: list[str] = []           # API key 白名单（SHA256 hashed）
    token_issuer: str | None = None  # JWT issuer（bearer_token 模式）
    token_audience: str | None = None
    mtls_ca_cert: str | None = None  # mTLS CA 证书路径

class AuthenticatedClient(BaseModel):
    """已认证的客户端身份"""
    client_id: str
    roles: list[str] = ["default"]
    permissions: list[str] = []
    authenticated_at: float
```

**实现路径**：
- Python 端：在 FastMCP server 初始化前注入 auth middleware（检查 HTTP header 或 MCP session metadata）
- Rust 端：axum tower layer，在路由前插入 auth guard
- 认证失败返回标准 MCP error code `-32001` (Unauthorized)

#### 3.3.2 Tool Authorization (Layer 2)

**扩展现有 Permission 模型**，从 agent-centric 扩展为 agent + client 双维度：

```python
class ToolAccessPolicy(BaseModel):
    """工具访问策略 — 基于 client identity + tool name 的细粒度控制"""
    client_roles: list[str]         # 哪些 client 角色可访问
    tools_allowed: list[str]        # 允许的工具 glob 模式
    tools_denied: list[str]         # 拒绝的工具 glob 模式（优先级更高）
    rate_limit: int | None = None   # 每分钟调用上限
    require_confirmation: list[str] # 需要确认的工具列表
```

**组合逻辑**：
1. Gateway auth 验证 client identity → 解析 roles
2. Tool access policy 匹配 client roles → 确定可访问工具集
3. Agent permission policy 确定工具执行权限
4. 两者取交集 = 最终可执行操作

#### 3.3.3 Audit Trail (Layer 3)

新增 `platform/gateway/audit.py`：

```python
class AuditEvent(BaseModel):
    """结构化审计事件"""
    event_id: str              # UUID
    timestamp: float           # 事件时间
    event_type: Literal[
        "auth_success", "auth_failure",
        "tool_call", "tool_result",
        "agent_activation", "agent_error",
        "external_server_call", "config_change"
    ]
    client_id: str | None      # 调用者
    agent_id: str | None       # 目标 agent
    tool_name: str | None      # 工具名称
    request_summary: str | None  # 请求摘要（不含完整参数，防敏感泄露）
    response_status: str | None  # success/error/denied
    duration_ms: float | None
    metadata: dict = {}

class AuditLogger:
    """结构化审计日志 — SQLite WAL + 可选外部 sink"""

    def __init__(self, db_path: str, sinks: list[AuditSink] | None = None):
        self._db = ...      # SQLite WAL mode
        self._sinks = sinks  # 可扩展：文件、syslog、HTTP webhook

    async def log(self, event: AuditEvent) -> None
    async def query(self, filter: AuditFilter) -> list[AuditEvent]
    async def export(self, format: Literal["json", "csv"], since: float) -> str
```

**设计要点**：
- SQLite WAL 存储，异步写入，不阻塞工具调用
- 请求参数只存摘要（前 200 字符），避免敏感数据入库
- 不可篡改：写入后只有 append，无 update/delete API
- 可扩展 sink 机制：支持推送到外部 SIEM/syslog

#### 3.3.4 外部 Server 认证

扩展 `ExternalServerConfig`：

```python
class ExternalServerAuth(BaseModel):
    """外部 MCP server 认证配置"""
    method: Literal["none", "api_key", "bearer", "mtls"] = "none"
    api_key: str | None = None       # 从环境变量读取
    bearer_token: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = None

class ExternalServerConfig(BaseModel):
    # ... 现有字段 ...
    auth: ExternalServerAuth = ExternalServerAuth()  # 新增
    tls_verify: bool = True                           # 新增
    allowed_tools: list[str] | None = None            # 新增：工具白名单
```

### 3.4 实施阶段

| 阶段 | 内容 | 预估工期 | 验证标准 |
|------|------|----------|----------|
| **Phase 1** | AuditLogger + 结构化审计事件 | W1-2 | 所有工具调用产生审计记录，可查询导出 |
| **Phase 2** | GatewayAuth API Key 模式 | W3 | 未认证客户端被拒绝，认证客户端可正常调用 |
| **Phase 3** | Tool Access Policy + 角色模型 | W4 | 不同角色看到不同工具集，denied 工具不可调用 |
| **Phase 4** | 外部 Server 认证 + TLS | W5 | 外部 server 连接支持 bearer/api_key 认证 |
| **Phase 5** | Rust 平台同步实现 | W6-7 | Rust gateway 的 auth + audit 测试通过 |

### 3.5 依赖与风险

**依赖**：
- FastMCP 的 session/middleware 能力（需确认版本支持）
- axum tower layer 生态（成熟，无风险）

**风险**：

| 风险 | 影响 | 缓解 |
|------|------|------|
| FastMCP auth middleware 限制 | 高 | Phase 2 前做 FastMCP 可行性验证，必要时用反向代理 |
| 审计日志性能开销 | 中 | 异步批量写入 + SQLite WAL |
| 密钥管理安全 | 高 | API key 仅存 SHA256 hash，支持环境变量/secret manager |
| 双语言同步成本 | 中 | Python 先行，Rust 在 Phase 5 同步 |

---

## 4. P1-3: Agent Marketplace + Quality Gate

### 4.1 需求分析

**目标**：将 Git-based 分发升级为可信 Agent 市场，包含质量评分、签名验证、依赖解析和搜索发现。

**来源**：竞品无原生市场，Homebrew Tap 模型唯一性。

**核心场景**：
- 开发者提交 Agent 到官方仓库，自动通过质量检查
- 用户搜索/浏览/安装 Agent，看到质量评分和下载量
- 企业部署只允许经过签名验证的 Agent
- Composite Agent 自动安装缺失的 Atomic Agent 依赖

### 4.2 当前状态

| 组件 | Python | Rust | 说明 |
|------|--------|------|------|
| Git 安装器 | ✅ GitInstaller | ✅ Installer trait | sparse-checkout 高效克隆 |
| Source 管理 | ✅ SourceManager | ✅ | 三种 source + index.yaml |
| Lockfile | ✅ LockfileManager | ✅ | flock + atomic write |
| 安全审计 | ✅ QualityGate.SecurityCheck | ✅ security_audit.rs | Python 通过 QualityGate 集成 |
| Manifest 检查 | ✅ QualityGate.ManifestCheck | ✅ manifest_checker.rs | Python 已有完整 manifest 检查 |
| SKILL 检查 | ✅ QualityGate.SkillFileCheck | ✅ skill_checker.rs | Python 通过 QualityGate 集成 |
| 签名验证 | ❌ | ❌ | 无任何签名机制 |
| 依赖解析 | ✅ DependencyResolver | ❌ | Python 有版本冲突检测 |
| 搜索 API | ⚠️ 本地 | ❌ | 仅按名称搜索 |
| 评分系统 | ✅ ScoreManager | ❌ | Python 有 JSON 持久化评分 |

**关键约束**：
- Python 用 `agent-manifest.yaml`，Rust 用 `agent.toml` — 双格式待统一
- Rust 端已有安全审计/manifest/skill 检查，但未桥接到 Python
- Lockfile 是扁平结构，无依赖图

### 4.3 设计方案

#### 4.3.1 统一 Manifest 格式

**决策：迁移到 TOML**

理由：Rust 生态天然 TOML 友好，Python `tomllib` (3.11+) 原生支持。

```toml
# agent.toml（统一格式）
[agent]
name = "code-reviewer"
version = "1.2.0"
type = "atomic"        # atomic | composite
description = "Code review with SOLID/security analysis"
entry = "code_reviewer.mcp:serve"

[agent.model_config]
recommended = "anthropic:claude-sonnet-4-20250514"
fallback = "ollama:qwen2.5-coder:7b"

[agent.capabilities]
tags = ["code-review", "security", "solid"]
domains = ["software-engineering"]

[agent.permissions]
mode = "default"
allowed_tools = ["read_file", "search_files"]
denied_tools = ["execute_shell"]

[agent.dependencies]   # Composite only
atomic_agents = ["doc-filler", "test-suite-generator"]

[agent.quality]        # 新增：Quality Gate 声明
min_coverage = 0.8
required_checks = ["security_audit", "skill_validation"]
```

**迁移策略**：双读兼容（同时支持 YAML 和 TOML），写入只用 TOML。

#### 4.3.2 Quality Gate Pipeline

新增 `platform/local/quality_gate.py`：

```python
class QualityGateResult(BaseModel):
    agent_name: str
    version: str
    passed: bool
    checks: list[CheckResult]
    overall_score: float        # 0.0 - 1.0
    severity_summary: dict      # {"critical": 0, "warning": 2, "info": 5}

class CheckResult(BaseModel):
    check_name: str
    status: Literal["pass", "fail", "warn", "skip"]
    message: str
    severity: Literal["critical", "warning", "info"]
    details: dict = {}

class QualityGate:
    """安装前质量检查流水线"""

    def __init__(self):
        self._checks: list[QualityCheck] = [
            ManifestCheck(),       # agent.toml 格式和必填字段
            SkillFileCheck(),      # SKILL.md 存在性和必需段落
            SecurityAuditCheck(),  # 危险函数/硬编码密钥/网络访问
            DependencyCheck(),     # 依赖存在性和版本兼容性
            TestCoverageCheck(),   # 测试覆盖率和通过率（可选）
        ]

    async def evaluate(self, agent_path: Path) -> QualityGateResult
```

**评分算法**：
- Critical failure = 直接 fail（不通过）
- Warning = 扣分（-0.1 per warning）
- 最终分数 = 1.0 - (warnings * 0.1)
- 最低通过线 = 0.6

#### 4.3.3 Agent 签名

```python
class AgentSigner:
    """基于 Sigstore 的 Agent 包签名"""

    async def sign(self, agent_path: Path, identity_token: str) -> SignatureBundle
    async def verify(self, agent_path: Path, signature: SignatureBundle) -> bool

class SignatureBundle(BaseModel):
    """签名包"""
    agent_name: str
    version: str
    commit_sha: str
    signature: str          # Base64 签名
    certificate: str        # 签名证书
    bundle_format: Literal["sigstore", "gpg"] = "sigstore"
```

**方案选择**：优先 Sigstore（免密钥管理，基于 OIDC identity），备选 GPG。

#### 4.3.4 依赖解析

```python
class DependencyResolver:
    """Agent 依赖解析器"""

    async def resolve(self, agent: AgentManifest) -> ResolutionResult
    async def check_conflicts(self, agents: list[AgentManifest]) -> ConflictReport

class ResolutionResult(BaseModel):
    required: list[str]        # 需要安装的 agent 列表
    already_installed: list[str]
    conflicts: list[Conflict]  # 版本冲突

class Conflict(BaseModel):
    agent_name: str
    required_by: list[str]     # 哪些 composite 需要
    versions: list[str]        # 冲突的版本列表
```

### 4.4 实施阶段

| 阶段 | 内容 | 预估工期 | 验证标准 |
|------|------|----------|----------|
| **Phase 1** | 统一 Manifest TOML + 双读兼容 | W1-2 | Python/Rust 都能读 TOML + YAML |
| **Phase 2** | Quality Gate Pipeline（Python） | W3-4 | 5 项检查全部通过/失败可测 |
| **Phase 3** | 依赖解析 + 冲突检测 | W5 | Composite 安装自动拉取 Atomic 依赖 |
| **Phase 4** | Sigstore 签名验证 | W6-7 | 签名/验签流程端到端 |
| **Phase 5** | 搜索 API 增强 + Rust 同步 | W8-9 | 按能力/领域搜索可用 |

### 4.5 依赖与风险

**依赖**：
- Sigstore Python/Rust SDK
- Python `tomllib` (3.11+，项目已要求 3.12)

**风险**：

| 风险 | 影响 | 缓解 |
|------|------|------|
| YAML→TOML 迁移影响现有 agent | 高 | 双读兼容期 + 自动迁移脚本 |
| Quality Gate 误报 | 中 | 可配置检查级别 + skip 机制 |
| Sigstore OIDC 依赖 | 中 | 备选 GPG 签名 |
| 依赖解析复杂度 | 中 | 先支持扁平依赖，后续支持嵌套 |

---

## 5. P1-4: Domain Atomic Agent 扩展

### 5.1 需求分析

**目标**：将现有 20 个 Atomic Agent 扩展到 30-50 个，覆盖主流开发领域。

**来源**：市场趋势从泛化→专精，Agent Nexus Atomic/Composite 分层优势明显。

**核心场景**：
- 新领域开发者能快速找到对应领域的专精 Agent
- Agent 之间可组合，覆盖更复杂的工作流
- 社区贡献者能按标准模板创建新 Agent

### 5.2 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| Atomic Agent 模式 | ✅ 成熟 | 20 个 agent，统一结构 |
| Composite Agent 组合 | ✅ 成熟 | 5 个 composite，DAG 编排 |
| Agent Manifest | ✅ 成熟 | 标准化字段 |
| SKILL.md 规范 | ✅ 成熟 | 三段式渐进加载 |
| Agent 脚手架 | ✅ 已实现 | `create-agent` CLI + `AgentCreator` 类，支持 atomic/composite 类型 |
| 能力分类体系 | ❌ 缺失 | 无受控词汇表 |
| Agent 模板系统 | ❌ 缺失 | 无 cookiecutter 模板 |
| 测试基础设施 | ⚠️ 基础 | pyproject.toml 有 [dev]，但多数 agent 无测试文件 |

### 5.3 设计方案

#### 5.3.1 Agent 能力分类体系

```toml
# capabilities.toml — 受控能力词汇表
[categories]
"software-engineering" = ["code-review", "testing", "refactoring", "debugging"]
"documentation" = ["api-docs", "technical-writing", "localization", "compliance"]
"devops" = ["ci-cd", "deployment", "monitoring", "infrastructure"]
"data-engineering" = ["etl", "data-quality", "schema-design", "analytics"]
"security" = ["vulnerability-scan", "compliance-check", "dependency-audit"]
"ai-ml" = ["model-review", "data-validation", "experiment-tracking"]
"product" = ["requirements", "market-analysis", "competitive-intelligence"]
```

#### 5.3.2 Agent 脚手架工具

新增 CLI 命令 `agent-nexus create-agent`：

```bash
# 交互式创建
agent-nexus create-agent --name "db-migration-reviewer" --category "software-engineering" --interactive

# 模板创建
agent-nexus create-agent --template code-reviewer --name "custom-reviewer"
```

**生成文件**：
- `agent.toml`（预填分类和能力标签）
- `SKILL.md`（三段式模板，带 TODO 占位符）
- `pyproject.toml`（标准依赖和 entry point）
- `src/{agent_name}/__init__.py`
- `src/{agent_name}/agent.py`（最小实现骨架）
- `tests/test_{agent_name}.py`（测试模板）

#### 5.3.3 扩展路线图（30-50 agents）

**第一批（+8，达 20 个）** — 高需求、低实现成本：

| Agent | 领域 | 核心能力 | 复杂度 |
|-------|------|----------|--------|
| db-schema-analyzer | data | 数据库 schema 设计审查 | 低 |
| api-contract-tester | qa | API 契约测试生成 | 中 |
| performance-profiler | perf | 性能瓶颈分析 | 中 |
| dependency-auditor | security | 依赖漏洞扫描 | 低 |
| i18n-validator | i18n | 国际化完整性检查 | 低 |
| config-linter | devops | 配置文件规范检查 | 低 |
| error-analyzer | debugging | 错误模式分析与建议 | 低 |
| data-pipeline-validator | data | ETL pipeline 验证 | 中 |

**第二批（+10，达 30 个）** — 中等需求、中实现成本：

| Agent | 领域 |
|-------|------|
| terraform-reviewer | devops |
| dockerfile-optimizer | devops |
| graphql-schema-designer | api |
| ml-model-reviewer | ai-ml |
| prompt-engineer | ai-ml |
| architecture-reviewer | software |
| migration-planner | data |
| incident-analyzer | sre |
| cost-optimizer | cloud |
| compliance-checker | governance |

**第三批（+10-20，达 40-50 个）** — 长尾领域、社区驱动：

按社区贡献为主，平台提供模板和质量检查。方向包括：mobile、game-dev、embedded、blockchain 等。

### 5.4 实施阶段

| 阶段 | 内容 | 预估工期 | 验证标准 |
|------|------|----------|----------|
| **Phase 1** | 能力分类体系 + 脚手架 CLI | W1-2 | `create-agent` 命令生成可运行的 agent 骨架 |
| **Phase 2** | 第一批 8 个 agent 实现 | W3-5 | 每个 agent 有 SKILL.md + 测试 + 可通过 MCP 调用 |
| **Phase 3** | 第二批 10 个 agent 实现 | W6-8 | 同上 |
| **Phase 4** | 社区贡献流程 + 模板 | W9-10 | 外部贡献者能创建 PR 并通过 Quality Gate |

### 5.5 依赖与风险

**依赖**：
- P1-3 的 Quality Gate Pipeline（agent 质量检查）
- P1-3 的统一 TOML 格式

**风险**：

| 风险 | 影响 | 缓解 |
|------|------|------|
| Agent 质量参差不齐 | 中 | Quality Gate + 最低覆盖率要求 |
| 维护 50 个 agent 成本 | 高 | 社区贡献 + 自动化测试 + 脚手架 |
| LLM 提示词调优 | 中 | 每个 agent 遵循 SKILL.md 标准，减少调优量 |

---

## 6. P2-5: Self-Evolution 产品化

### 6.1 需求分析

**目标**：将现有三层进化引擎（Skill → Orchestration → Agent Promotion）从研究原型升级为生产可用系统。

**来源**：Self-Evolution 学术爆发（EvoAgentX EMNLP'25、MASLab、SPIRAL），OpenSpace 云端能力可复用。

**核心场景**：
- Agent 在使用中自动发现并修复低效 skill
- 成熟的 skill 自动被提升为独立 agent
- 进化过程可视化，运营可观测
- A/B 测试验证进化效果

### 6.2 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| SkillEvolver | ⚠️ 半成品 | 记录元数据但不修改 SKILL.md 内容 |
| ExecutionAnalyzer | ✅ 成熟 | Levenshtein 模糊匹配 + 判断 + 建议 |
| HealthChecker | ✅ 成熟 | 三条阈值规则 |
| AgentPromoter | ✅ 成熟 | 完整 agent 包生成 |
| EvolutionStore | ✅ 成熟 | SQLite WAL，6 张表 |
| CompactionGuard | ✅ 成熟 | 分层上下文管理 |
| LLM 集成 | ❌ 缺失 | 无 LLM 调用生成改进内容 |
| Agency 集成 | ❌ 缺失 | 不在 pipeline 生命周期内 |
| 可观测性 | ❌ 缺失 | 无 dashboard/metrics |
| A/B 测试 | ❌ 缺失 | 进化即替换，无并行验证 |
| 回滚机制 | ❌ 缺失 | 无 rollback API |
| 配置化 | ❌ 缺失 | 阈值全部硬编码 |

**关键发现**：最核心的差距是 **SkillEvolver 不修改 skill 内容**。OpenSpace 有 `patch.py` (33KB) 做 LLM 驱动的 skill 内容修改，agent-nexus 只做元数据记录。这不是"进化"，是"记账"。

### 6.3 设计方案

#### 6.3.1 LLM 驱动的 Skill 内容进化

**核心：引入 LLM Patch 循环**

```
HealthChecker 发现问题 → SkillEvolver 生成进化策略 → LLM 生成改进内容
→ 验证改进内容（语法/安全/测试）→ 写入新版本 SKILL.md → 记录 lineage
```

新增 `platform/evolution/skill_patch.py`：

```python
class SkillPatcher:
    """LLM 驱动的 Skill 内容修改"""

    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def generate_fix(self, skill: SkillRecord, diagnosis: HealthDiagnosis) -> PatchResult
    async def generate_derived(self, skill: SkillRecord, insights: list[str]) -> PatchResult
    async def validate_patch(self, original: str, patched: str) -> ValidationResult

class PatchResult(BaseModel):
    original_content: str
    patched_content: str
    diff: str                  # unified diff
    patch_type: EvolutionType
    confidence: float          # LLM 置信度 0-1
    validation: ValidationResult

class ValidationResult(BaseModel):
    syntax_valid: bool         # Markdown/代码语法检查
    security_pass: bool        # SecurityChecker 通过
    test_pass: bool | None     # 测试通过（如有测试）
    regression_risk: float     # 回归风险 0-1
```

**LLM Prompt 模板**：

```python
FIX_PROMPT = """
你是 Agent Skill 修复专家。以下 Skill 存在问题：

诊断信息：
{diagnosis}

当前 Skill 内容：
{skill_content}

请生成修复后的 Skill 内容。要求：
1. 保持原有结构（三段式）
2. 只修改与诊断问题相关的部分
3. 保持向后兼容
4. 输出完整的修复后内容
"""
```

#### 6.3.2 Agency Pipeline 集成

在 agency pipeline 的 `LLMQualityGate` 之后插入 evolution hook：

```python
# agency/pipeline.py 中的集成点
class AgencyPipeline:
    async def run(self, task: str, ...):
        # ... 现有 4 阶段 ...

        # 新增：任务完成后触发进化分析
        if self.evolution_engine:
            await self.evolution_engine.post_analysis(EvolutionContext(
                agent_id=self.agent_id,
                task_id=task_id,
                task_description=task,
                task_completed=result.success,
                skill_ids_used=result.skills_used,
                skills_applied=result.skills_applied,
                skills_fell_back=result.skills_fell_back,
            ))
```

#### 6.3.3 A/B 测试与回滚

```python
class EvolutionExperimenter:
    """Skill 进化 A/B 测试"""

    async def create_experiment(self, parent: SkillRecord, evolved: SkillRecord) -> Experiment
    async def assign(self, experiment: Experiment) -> SkillRecord  # 随机分配版本
    async def record_outcome(self, experiment_id: str, skill_id: str, success: bool) -> None
    async def evaluate(self, experiment_id: str) -> ExperimentResult

class ExperimentResult(BaseModel):
    parent_performance: float   # 父版本成功率
    evolved_performance: float  # 进化版本成功率
    confidence: float           # 统计显著性
    recommendation: Literal["promote", "revert", "continue"]
```

#### 6.3.4 可观测性

```python
class EvolutionMetrics:
    """进化引擎指标导出"""

    # Prometheus 格式指标
    evolution_total: Counter      # 进化总次数（按类型分）
    evolution_success: Counter    # 成功次数
    skill_active_count: Gauge     # 活跃 skill 数量
    promotion_total: Counter      # 提升总次数
    experiment_running: Gauge     # 进行中的 A/B 测试

class EvolutionDashboard:
    """进化引擎 CLI/HTTP dashboard"""
    async def get_summary(self) -> EvolutionSummary
    async def get_skill_lineage(self, skill_id: str) -> LineageTree
    async def get_health_report(self) -> HealthReport
```

#### 6.3.5 配置化

新增 `evolution.toml` 配置文件：

```toml
[evolution]
enabled = true
auto_promote = false           # 自动提升（生产环境建议关闭）
max_evolution_per_day = 10     # 每日最大进化次数

[evolution.thresholds]
fix_fallback_rate = 0.4
fix_applied_rate = 0.4
fix_completion_rate = 0.35
derived_effective_rate = 0.55
derived_applied_rate = 0.25
promotion_effective_rate = 0.8
promotion_min_selections = 50

[evolution.llm]
model = "anthropic:claude-sonnet-4-20250514"
temperature = 0.3
max_tokens = 4096

[evolution.experiment]
min_samples = 30               # A/B 测试最小样本量
confidence_level = 0.95        # 统计显著性水平
max_duration_days = 7          # 最长实验周期
```

### 6.4 实施阶段

| 阶段 | 内容 | 预估工期 | 验证标准 |
|------|------|----------|----------|
| **Phase 1** | SkillPatcher LLM 集成 | W1-3 | 能自动修复一个已知问题的 skill |
| **Phase 2** | Agency Pipeline 集成 | W4 | 任务完成后自动触发进化分析 |
| **Phase 3** | 配置化 + 可观测性 | W5-6 | evolution.toml 可配置，CLI 可查看状态 |
| **Phase 4** | A/B 测试 + 回滚 | W7-8 | 进化版本与父版本并行测试，可回滚 |
| **Phase 5** | Rust 同步 + 性能优化 | W9-10 | Rust 端进化引擎测试通过 |

### 6.5 依赖与风险

**依赖**：
- LLMClient（已有，需确保进化用 model 可配置）
- Agency Pipeline 的 execution context
- OpenSpace patch.py 的设计参考（不直接引入代码）

**风险**：

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM 生成低质量修改 | 高 | validation pipeline + confidence 阈值 + 人工审核 |
| 进化回回归（越改越差） | 高 | A/B 测试 + 自动回滚 + 每日进化上限 |
| LLM 调用成本 | 中 | 限制进化频率 + 使用低成本 model |
| 与 Rust 进化引擎不一致 | 中 | Python 先行，Rust 后续同步 |

---

## 7. 依赖关系与实施顺序

### 7.1 功能依赖图

```
P0-2 (Gateway Security)
  ↓ (auth 机制被 Marketplace 签名验证复用)
P1-3 (Marketplace + QG)
  ↓ (QG 被 Agent 扩展使用)
P1-4 (Atomic Agent 扩展)
  ↓ (更多 agent 为 Evolution 提供数据)
P2-5 (Self-Evolution 产品化)

P0-1 (P2P Collaboration) — 独立路径，与其他功能无强依赖
```

### 7.2 推荐时间线

```
W1-2  ┃ P0-1 Phase 1 (A2A 消息模型)  ┃ P0-2 Phase 1 (AuditLogger)
W3-4  ┃ P0-1 Phase 2-3 (Directory+DSL) ┃ P0-2 Phase 2-3 (Auth+Policy)
W5-6  ┃ P0-1 Phase 4 (Rust sync)     ┃ P0-2 Phase 4-5 (External+Rust)
       ┃                              ┃ P1-3 Phase 1-2 (TOML+QG)
W7-8  ┃ P1-3 Phase 3-4 (Deps+Sign)   ┃ P1-4 Phase 1-2 (Taxonomy+CLI+8 agents)
W9-10 ┃ P1-3 Phase 5 (Rust+Search)   ┃ P1-4 Phase 3 (10 agents)
       ┃                              ┃ P2-5 Phase 1-2 (SkillPatcher+Agency)
W11-12┃ P1-4 Phase 4 (Community)     ┃ P2-5 Phase 3-4 (Config+A/B)
W13-14┃                               ┃ P2-5 Phase 5 (Rust sync)
```

**总预估工期**：14 周（约 3.5 个月）

### 7.3 里程碑

| 里程碑 | 时间点 | 交付物 |
|--------|--------|--------|
| **M1: A2A MVP** | W4 | Agent 间可通过 platform broker 互相发送消息 |
| **M2: Secure Gateway** | W6 | Gateway 认证 + 审计日志上线 |
| **M3: Marketplace Alpha** | W8 | Quality Gate + 依赖解析 + 签名验证 |
| **M4: 30 Agents** | W10 | 达到 30 个 atomic agent |
| **M5: Evolution Live** | W14 | LLM 驱动的 skill 进化上线 |

---

## 8. 风险矩阵

| 风险 | 概率 | 影响 | 总评 | 缓解策略 |
|------|------|------|------|----------|
| FastMCP auth 中间件不支持 | 中 | 高 | 🔴 | 提前验证，必要时用反向代理方案 |
| P2P 消息死锁 | 中 | 高 | 🔴 | 全局超时 + 死锁检测 |
| YAML→TOML 迁移破坏现有 agent | 中 | 高 | 🔴 | 双读兼容 + 自动迁移脚本 |
| LLM 进化质量不稳定 | 高 | 中 | 🟠 | validation + confidence 阈值 + 人工审核 |
| 50 agent 维护成本爆炸 | 中 | 中 | 🟠 | 脚手架 + 自动化测试 + 社区贡献 |
| 双语言同步延迟 | 高 | 低 | 🟡 | Python 先行，Rust 滞后 1-2 phase |
| 审计日志性能影响 | 低 | 中 | 🟡 | 异步写入 + 批量提交 |

---

## 附录

### A. 参考文档

| 文档 | 路径 |
|------|------|
| 竞品分析（需求来源） | 飞书文档 `UzXEdpCizofVD9xuUQWcLwi3n3b` |
| 原子 Agent 改进计划 | `docs/12-atomic-agents-improvement-plan.md` |
| MCP 通信矩阵 | `docs/06-mcp-communication.md` |
| Agent 系统 | `docs/05-agent-system.md` |
| Self-Evolution | `docs/04-self-evolution.md` |
| 约束与决策 | `docs/08-constraints-decisions.md` |
| 实施计划 | `docs/09-implementation-plan.md` |

### B. ClawTeam A2A 参考

| 特性 | ClawTeam | Agent Nexus 设计 |
|------|----------|-----------------|
| 消息传输 | File-based mailbox | Platform-as-Broker (IPC relay) |
| 消息类型 | 12 种（含 join/approval/shutdown） | 8 种（chat/request/broadcast/reply + 方向变体） |
| Agent 发现 | MailboxManager.resolve_inbox | AgentDirectory.register/resolve |
| 团队管理 | TeamManager lifecycle | 由 OrchestrationDSL 定义 |
| 认证 | AgentIdentity + P2P transport | Gateway auth + client identity |

### C. OpenSpace Evolution 参考

| 特性 | OpenSpace | Agent Nexus 差距 |
|------|-----------|-----------------|
| Skill 内容修改 | `patch.py` (33KB) LLM 驱动 | SkillPatcher（本设计新增） |
| Skill 排名 | `skill_ranker.py` (14KB) | 缺失（Phase 3 可加） |
| 对话格式化 | `conversation_formatter.py` (13KB) | 简化版 context_describer 已有 |
| Fuzzy 匹配 | `fuzzy_match.py` (10KB) 独立 | 内联在 analyzer.py |
| Skill 注册 | `registry.py` (29KB) 丰富索引 | 简化版 store-based lookup |

### D. 已确认设计决策（28 项）

> 以下决策经评审确认，作为实施阶段的强制性约束。

#### P0-1: P2P 协作

| # | 决策项 | 确认方案 | 理由 |
|---|--------|---------|------|
| D1 | 消息持久化 | **内存队列**（进程存活期间不丢，进程挂了靠 TaskGraph 重新调度） | 消息是瞬态的，SQLite 持久化是过度设计 |
| D2 | 请求超时行为 | **抛异常让调用方决定** | 平台不替 agent 做业务决策 |
| D3 | 广播组定义 | **按 DSL 中的 role**（explore/worker/plan/verification） | 零新增概念，复用现有字段 |
| D4 | 消息路由权限 | **受限路由，仅同一 composition 内** | 安全默认值，composition 内天然有信任关系 |
| D5 | 大消息处理 | **复用 4MB 限制，大内容走 DATA_REFERENCE** | 复用现有 IPC 机制，不新增传输层 |
| D6 | 死锁检测 | **仅靠超时 + 禁止跨请求嵌套** | 超时打破死锁，嵌套禁令从根源防死锁 |
| D7 | 消息与 TaskGraph 状态 | **分离**（消息只传信息，状态变更走 ProcessManager API） | 关注点分离，agent 自行决定是否操作 TaskGraph |

#### P0-2: Gateway 安全

| # | 决策项 | 确认方案 | 理由 |
|---|--------|---------|------|
| D8 | FastMCP middleware | **先验证 FastMCP middleware 能力，不行用反向代理** | Phase 1 前必须完成技术验证 |
| D9 | API Key 存储 | **环境变量 + SHA256 hash 校验** | 零新依赖，单机部署不需要 vault |
| D10 | 审计日志保留 | **按大小轮转 500MB** | 比按时间更可预测，超限自动归档 |
| D11 | 未认证模式兼容 | **默认关闭 auth，显式启用** | 向后兼容，不锁出现有用户 |
| D12 | 速率限制粒度 | **按 client_id 全局限制**（默认 100/min） | 先解决有无，per-tool 限流等真实瓶颈再加 |
| D13 | Credential 轮转 | **热重载配置**（SIGHUP / CLI reload） | 停机 <1 秒，不需要多 key 并行 |

#### P1-3: Marketplace

| # | 决策项 | 确认方案 | 理由 |
|---|--------|---------|------|
| D14 | Manifest 迁移截止线 | **3 个月过渡 + 1 个月 deprecation warning** | 足够迁移，不会无限拖延 |
| D15 | Quality Gate 评分 | **完全可配置，默认 0.6** | 不同领域应有不同标准 |
| D16 | 签名方案 | **Sigstore 优先**（免密钥管理，GitHub OIDC 一键签名） | 分发模型本身是 Git-based（需联网），OIDC 不是障碍 |
| D17 | 依赖解析范围 | **仅 direct deps**（composite → atomic） | 当前无 composite-on-composite 需求，YAGNI |
| D18 | 搜索排序 | **按下载量**（最客观，不可伪造） | 质量评分可被刷，relevance 需要算法，下载量 = 真实使用 |

#### P1-4: Agent 扩展

| # | 决策项 | 确认方案 | 理由 |
|---|--------|---------|------|
| D19 | 第一批优先级 | **dependency-auditor → config-linter → error-analyzer** | 需求频率 × 实现简单度排序 |
| D20 | SKILL.md 编写 | **AI 生成初稿 + 用户审核** | 结构固定可自动生成，领域准确性靠人工 |
| D21 | Agent 并发加载 | **延续现有三层**（core/activated/dormant） | 已验证的模型，50 agent 不需要第四层 |

#### P2-5: Self-Evolution

| # | 决策项 | 确认方案 | 理由 |
|---|--------|---------|------|
| D22 | 进化 LLM model | **可配置，默认 sonnet** | 需要足够 reasoning，haiku 可能质量不足 |
| D23 | 进化生效方式 | **可配置，默认 A/B 测试** | 生产强制 A/B，开发阶段可跳过 |
| D24 | 回滚粒度 | **仅上一版本** | 任意版本需维护所有快照，成本高 |
| D25 | 进化质量判定 | **综合加权（0.5 effective + 0.3 fallback + 0.2 usage）** | 防止单一指标被 gaming |

#### 跨功能

| # | 决策项 | 确认方案 | 理由 |
|---|--------|---------|------|
| D26 | 配置文件路径 | **统一放在 `config/` 目录** | 符合现有约定 |
| D27 | Rust 同步策略 | **Python 全部完成后，再启动 Rust 同步** | 避免 context switch，Python 是 source of truth |
| D28 | 测试覆盖率要求 | **维持 80% 基线，新增模块不低于 85%** | 新代码无历史包袱，应高于平均线 |
