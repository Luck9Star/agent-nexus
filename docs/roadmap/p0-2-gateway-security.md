# P0-2: MCP Gateway 安全增强

> 优先级：P0 🔴 | 预估工期：W1-7 | 被 P1-3 依赖（auth 机制复用）

## 需求

- **来源**：Reddit cybersecurity 社区，ClawHavoc 供应链攻击教训
- **目标**：在 SecurityChecker + PermissionChecker 基础上增加 MCP 级认证、访问控制、审计日志
- **需求强度**：⭐⭐⭐⭐⭐ | **差异化**：⭐⭐⭐⭐⭐（竞品无安全，最强竞争力） | **实现复杂度**：中

## 当前状态

| 组件 | 状态 | 文件 |
|------|------|------|
| SecurityChecker (AST 级，30+ 规则) | ✅ 成熟 | `platform/runtime/security_checker.py` |
| PermissionChecker (3 模式，agent-centric) | ✅ 成熟 | `platform/runtime/permission_checker.py` |
| Python Gateway (FastMCP) | ⚠️ 无认证 | `platform/gateway/gateway.py` |
| Rust Gateway (axum) | ⚠️ 无认证 | `crates/ap-gateway/src/gateway.rs` |
| ExternalMcpAdapter | ⚠️ 无认证 | `platform/gateway/external_mcp_adapter.py` |
| SchemaTransformer (JSON Schema → Pydantic) | ✅ 成熟 | `platform/gateway/schema_transformer.py` |
| DeferredAgentRegistry | ✅ 成熟 | `platform/gateway/deferred_registry.py` |
| **Gateway 认证** | ❌ | — |
| **Client identity** | ❌ | — |
| **结构化审计日志** | ❌ | — |
| **速率限制** | ❌ | — |

### 关键约束

- FastMCP 控制传输层，auth 需要 middleware 或反向代理
- 双语言网关（Python + Rust）需同步实现
- PermissionChecker 是 agent-centric（agent 能做什么），不是 client-centric（谁可以调用）
- IPC 信任域内设计，Gateway auth 引入新的信任边界

## 设计方案

### 三层安全模型

```
Layer 1: Gateway Authentication — 谁可以连接？
Layer 2: Tool Authorization — 连接者可以调用什么工具？
Layer 3: Audit Trail — 记录一切，不可篡改
```

### Layer 1: Gateway Authentication

新增 `platform/gateway/auth.py`：

```python
class GatewayAuthConfig(BaseModel):
    enabled: bool = False                           # 默认关闭
    method: Literal["api_key", "bearer_token", "mtls"] = "api_key"
    keys: list[str] = []                            # SHA256 hashed
    token_issuer: str | None = None
    token_audience: str | None = None
    mtls_ca_cert: str | None = None

class AuthenticatedClient(BaseModel):
    client_id: str
    roles: list[str] = ["default"]
    permissions: list[str] = []
    authenticated_at: float
```

### Layer 2: Tool Authorization

```python
class ToolAccessPolicy(BaseModel):
    client_roles: list[str]
    tools_allowed: list[str]        # glob 模式
    tools_denied: list[str]         # 优先级更高
    rate_limit: int | None = None   # 每分钟调用上限
    require_confirmation: list[str]
```

组合逻辑：Gateway auth → roles → ToolAccessPolicy → Agent permissions → 交集

### Layer 3: Audit Trail

新增 `platform/gateway/audit.py`：

```python
class AuditEvent(BaseModel):
    event_id: str
    timestamp: float
    event_type: Literal[
        "auth_success", "auth_failure",
        "tool_call", "tool_result",
        "agent_activation", "agent_error",
        "external_server_call", "config_change"
    ]
    client_id: str | None
    agent_id: str | None
    tool_name: str | None
    request_summary: str | None    # 前 200 字符，防敏感泄露
    response_status: str | None
    duration_ms: float | None
    metadata: dict = {}

class AuditLogger:
    def __init__(self, db_path: str, sinks: list[AuditSink] | None = None): ...
    async def log(self, event: AuditEvent) -> None
    async def query(self, filter: AuditFilter) -> list[AuditEvent]
    async def export(self, format: Literal["json", "csv"], since: float) -> str
```

### 外部 Server 认证

扩展 `ExternalServerConfig`：

```python
class ExternalServerAuth(BaseModel):
    method: Literal["none", "api_key", "bearer", "mtls"] = "none"
    api_key: str | None = None
    bearer_token: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = None

class ExternalServerConfig(BaseModel):
    # ... 现有字段 ...
    auth: ExternalServerAuth = ExternalServerAuth()   # 新增
    tls_verify: bool = True                            # 新增
    allowed_tools: list[str] | None = None             # 新增
```

## 已确认决策

| # | 决策 | 理由 |
|---|------|------|
| D8 | Phase 0 先验证 FastMCP middleware，不行用反向代理 | 技术事实，需提前验证 |
| D9 | API Key 存储：环境变量 + SHA256 hash | 零新依赖，单机不需要 vault |
| D10 | 审计日志：按大小轮转 500MB | 比按时间可预测 |
| D11 | 未认证兼容：默认关闭 auth，显式启用 | 向后兼容，不锁现有用户 |
| D12 | 速率限制：按 client_id 全局 100/min | 先解决有无 |
| D13 | Credential 轮转：热重载配置 | 停机 <1 秒 |

## 实施阶段

| Phase | 内容 | 工期 | 验证标准 |
|-------|------|------|----------|
| 0 | FastMCP middleware 可行性验证 | 半天 | 确认实现路径 |
| 1 | AuditLogger + 结构化审计 | W1-2 | 工具调用产生审计记录 |
| 2 | API Key 认证 | W3 | 未认证被拒，认证可调用 |
| 3 | Tool Access Policy + 限流 | W4 | 不同角色不同工具集 |
| 4 | 外部 Server 认证 + TLS | W5 | 外部 server 支持 bearer/api_key |
| 5 | Rust 同步 | W6-7 | Rust auth + audit 测试通过 |

## 依赖

- FastMCP middleware 能力（Phase 0 验证）
- axum tower layer 生态（成熟）

## 风险

| 风险 | 缓解 |
|------|------|
| FastMCP 不支持 middleware | 反向代理方案（nginx/caddy） |
| 审计日志性能 | 异步批量写入 + SQLite WAL |
| 密钥安全 | 仅存 SHA256 hash，支持 env var |
| 双语言同步 | Python 先行，Phase 5 同步 |
