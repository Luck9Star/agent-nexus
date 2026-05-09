# MCP 暴露与通信

> Agent Nexus Design Doc — §8 MCP 暴露与通信：FastMCP 双模式、MCP Gateway、通信矩阵、Platform Router、SKILL.md 规范、数据流场景

> **Status**: ✅ Implemented (core) | ⚠️ Partial (Provider Adaptation, AnthropicNativeStrategy, Deferred Loading)
> **Code**: `src/agent_nexus/platform/gateway/` (gateway.py, deferred_registry.py, tool_adapter.py), `src/agent_nexus/platform/router/` (router.py, workflow.py, subtask.py)
> **Tests**: `tests/unit/test_gateway_tool_adapter.py`, `tests/unit/test_router_subtask.py`, `tests/unit/test_router_workflow.py`, `tests/unit/test_gateway_module.py`

> ⚠️ **Note**: 以下功能标记为 "deferred" 或 "pending implementation"：
> - §8.8.5 Provider Adaptation
> - §8.9.2 Anthropic Native Strategy (defer_loading)
> - 端到端 MCP 测试

## §8 MCP 暴露与通信

### 8.1 FastMCP 双模式

> **参考模块**: nanobot `nanobot/agent/tools/mcp.py` — MCP 客户端连接与工具桥接, `nanobot/agent/tools/registry.py` — `ToolRegistry` 动态工具注册

每个 Agent 支持两种 MCP 暴露方式：

| 模式 | 说明 | 触发方式 |
|------|------|----------|
| **MCP Server (stdio)** | 直接作为 MCP Server 运行 | `uvx agent-name`（默认）|
| **MCP Server (SSE)** | 通过 HTTP SSE 暴露 | `AGENT_MCP_MODE=sse` |

```python
from fastmcp import FastMCP

def create_mcp_server(agent_name: str, agent: Agent) -> FastMCP:
    mcp = FastMCP(agent_name)

    @mcp.tool()
    def analyze_template(template_path: str) -> dict:
        """分析文档模板，识别可填充字段"""
        result = agent.run(f"分析模板: {template_path}")
        return {"fields": result.output.fields, "styles": result.output.styles}

    @mcp.tool()
    def fill_template(template_path: str, data: dict) -> dict:
        """填充文档模板"""
        result = agent.run(f"填充模板 {template_path}，数据: {data}")
        return {"output_path": result.output.output_path}

    return mcp
```

#### 8.1.1 McpToolAdapter（动态 MCP 工具桥接）

> **参考模块**: nanobot `nanobot/agent/tools/mcp.py` — `MCPToolWrapper`, `connect_mcp_servers()`; OpenHarness `src/openharness/mcp/client.py` — `McpClientManager`, `src/openharness/mcp/types.py` — MCP 配置类型

**Problem**

当 Agent 需要调用外部 MCP Server 的工具时，为每个工具手动编写适配代码不切实际。McpToolAdapter 通过从远程 MCP 工具 schema 动态创建 Pydantic 模型，自动将外部 MCP 工具桥接为本地 BaseTool。

**Architecture**

```python
class McpToolAdapter(BaseTool):
    """将远程 MCP 工具动态包装为本地 BaseTool"""

    def __init__(self, server_name: str, tool_schema: dict):
        self.name = f"mcp__{server_name}__{sanitize(tool_schema['name'])}"
        self.description = tool_schema.get("description", "")

        # 从远程 schema 动态创建 Pydantic 输入模型
        self.input_model = create_model(
            f"{self.name}_input",
            **self._parse_schema_fields(tool_schema["inputSchema"])
        )

    async def execute(self, arguments: dict, context: ToolExecutionContext) -> ToolResult:
        # 转发到远程 MCP Server 执行
        result = await self.mcp_client.call_tool(self.tool_name, arguments)
        return ToolResult(output=result.content, is_error=result.isError)
```

**Naming Convention**

| MCP Server | Tool | 本地工具名 |
|-----------|------|-----------|
| filesystem | read_file | `mcp__filesystem__read_file` |
| github | create_issue | `mcp__github__create_issue` |
| docx-processor | analyze | `mcp__docx_processor__analyze` |

Pattern: `mcp__{server_name}__{tool_name}`（segment 需做 sanitize：`/` → `_`，`-` → `_`）

**Dynamic Model Creation**

远程 MCP schema 通过 `create_model()` 动态转换为 Pydantic 输入模型：

```python
from pydantic import create_model

# Remote MCP schema → Pydantic model
remote_schema = {
    "name": "read_file",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "encoding": {"type": "string", "default": "utf-8"}
        },
        "required": ["path"]
    }
}

# Dynamically creates: ReadFileInput(path: str, encoding: str = "utf-8")
input_model = create_model("read_file_input", **parse_fields(remote_schema))
```

**Integration with Agent Package**

当 Agent Package 在 `agent-manifest.yaml` 中声明 `mcp_servers` 时，McpToolAdapter 自动生效。Platform Router 在 Agent 启动时发现并包装所有声明的 MCP Server 工具，将它们注册到本地工具注册表。

### 8.2 MCP Gateway

MCP Gateway 将 Platform 暴露为单个 MCP Server，聚合所有 Agent：

```python
def create_gateway(router: Router, model_config: ModelConfigManager) -> FastMCP:
    mcp = FastMCP("agent-platform")

    @mcp.tool()
    def list_agents() -> str:
        """列出所有注册的 Agent（Atomic + Composite），显示名称、类型、状态和工具数"""
        lines = ["## Registered Agents\n"]
        for info in registry.list_all_agents():
            tier = "core" if info in registry.list_core_agents() else \
                   "activated" if info.is_activated else "available"
            running = "running" if info.is_running else "stopped"
            lines.append(f"- **{info.manifest.name}** ({info.manifest.type.value}) "
                         f"[{tier}] {running} ({len(info.tool_schemas)} tools)")
        return "\n".join(lines)

    # 动态 Agent 工具暴露: {agent_name}__{tool_name}
    ...

    return mcp
```

**MCP 工具命名规范：**

| Agent | 工具 | MCP 完整名称 |
|-------|------|-------------|
| doc-filler | analyze_template | doc-filler__analyze_template |
| doc-filler | fill_template | doc-filler__fill_template |
| code-reviewer | review_diff | code-reviewer__review_diff |
| requirements-analyzer | chat | requirements-analyzer__chat |

### 8.3 通信矩阵

| 通信场景 | 协议 | 说明 |
|----------|------|------|
| Agent 内部代码执行 | Python Runtime | IPythonRuntime，对象持久化 |
| Agent-to-Agent | IPC（stdin/stdout JSON-lines） | Platform Router 中转，管道通信 |
| Agent-to-外部框架 | MCP Server | stdio / SSE，协议无关 |
| Agent-to-外部 API | MCP Tool Call | 通过 MCP 调用外部工具 |
| Platform 路由到 Agent | stdin/stdout JSON-lines | Platform Router ↔ Agent subprocess |
| 远程 Agent 通信 | MCP SSE / 按需扩展 | 跨机器 Agent 间 |

### 8.4 Platform Router

> **参考模块**: deer-flow `packages/harness/deerflow/subagents/executor.py` — `SubagentExecutor` 超时与并行控制, `packages/harness/deerflow/agents/lead_agent/prompt.py` — Continue/Spawn 决策; OpenHarness `src/openharness/coordinator/coordinator_mode.py` — Continue vs Spawn 决策矩阵

Platform Router 是自建的编排协调器，负责：

1. **编排协调**：使用自建 TaskGraph 管理多个 Agent 的任务依赖和执行顺序
2. **MCP 路由**：将外部 MCP 请求路由到正确的 Agent
3. **Composite Agent 编排**：解析 composition.toml DAG，协调多个 Atomic Agent
4. **子任务管理**：使用 TaskGraph 跟踪子任务状态（pending/in_progress/completed/blocked）
5. **进程生命周期**：管理 Agent 子进程（通过 ProcessManager）

```python
class PlatformRouter:
    async def route_chat(self, agent_name, message, conversation_id) -> dict
    async def route_composite(self, agent_name, message, conversation_id) -> dict
    async def route_to_atomic(self, atomic_name, message, conversation_id) -> dict
    async def get_tools(self) -> list[dict]
    async def stop_all(self)
```

**Subagent 超时和限制控制**（借鉴 deer-flow）：

```python
@dataclass
class SubtaskConfig:
    timeout_seconds: float = 60.0
    max_retries: int = 2
    max_parallel: int = 3

class SubtaskController:
    async def run_with_timeout(self, coro, timeout) -> Any
    async def run_with_retry(self, coro, max_retries) -> Any
    async def run_parallel(self, coros) -> list[Any]
```

#### Coordinator Decision Matrix（Continue vs Spawn 决策框架）

> **参考模块**: OpenHarness `src/openharness/coordinator/coordinator_mode.py` lines 435-443 — 原始决策矩阵; deer-flow `packages/harness/deerflow/agents/lead_agent/prompt.py` `_build_subagent_section()` — Prompt-based 决策引导

Platform Router 在编排 Composite Agent 时需要决策是复用已有 Worker（Continue）还是创建新 Worker（Spawn fresh）。参考 OpenHarness 的 Continue vs Spawn 决策矩阵。

| 情况 | 决策 | 原因 |
|------|------|------|
| Worker 已有上下文 + 明确后续计划 | **Continue** | 避免重复探索，上下文已在内存 |
| 广泛研究 → 窄化实现 | **Spawn fresh** | 避免探索噪声污染实现 |
| 修复失败（同方向） | **Continue** | 保留错误上下文，精准修复 |
| 验证他人代码 | **Spawn fresh** | 全新视角，无预设立场 |
| 方向完全错误 | **Spawn fresh** | 污染上下文导致重蹈覆辙 |
| 不相关的新任务 | **Spawn fresh** | 无可用上下文，新建更高效 |

**Key principle: "绝不把理解交给另一个 Worker"** — Coordinator 必须自己理解所有发现后，再决定后续分配。

#### 4-Phase Workflow（四阶段工作流）

| 阶段 | 执行者 | 并行性 | 目的 |
|------|--------|--------|------|
| **Research** | Workers（并行） | 高 | 探索代码、查找文件、理解现状 |
| **Synthesis** | Coordinator（串行） | — | 汇总发现、制定实现规格 |
| **Implementation** | Workers（并行） | 高 | 按规格执行具体修改 |
| **Verification** | Worker（独立） | — | 对抗性验证，证明修改有效 |

```
Research (parallel workers) → Synthesis (coordinator alone) → Implementation (parallel workers) → Verification (fresh worker)
```

### 8.5 SKILL.md 规范

> **参考模块**: deer-flow `packages/harness/deerflow/skills/loader.py` — `load_skills()` 函数, `packages/harness/deerflow/skills/parser.py` — `parse_skill_file()` YAML frontmatter 解析; OpenHarness `src/openharness/skills/loader.py` — SKILL.md 加载, `src/openharness/skills/types.py` — `SkillDefinition`; nanobot `nanobot/agent/skills.py` — `SkillsLoader`

SKILL.md 遵循 deer-flow 的渐进式加载理念，分为三层：

| 层级 | 内容 | 加载时机 |
|------|------|----------|
| **Metadata** | name, agent_type, triggers, capabilities | 即时加载（YAML frontmatter） |
| **Body** | role, workflow, constraints | 首轮交互前加载 |
| **Resources** | examples, templates, references | 按需加载 |

**Atomic Agent SKILL.md 示例：**

```markdown
---
name: requirements-analyzer
agent_type: atomic
description: 多轮对话分析模糊需求，输出结构化需求说明书
triggers:
  - 需求分析
  - 提取需求
compatible_agents: [nanobot, hermes, openclaw, claude-code]
capabilities: [requirements-analysis, structured-output, web-search]
model_config:
  recommended: "powerful"
  fallback: "default"
---

# Requirements Analyzer Agent

## 角色
你是一个专业的需求分析师。通过多轮对话将用户模糊的需求转化为结构化的需求说明书。

## 工作流程
1. 接收用户初始需求
2. 多轮提问澄清（每次只问一个带选项的问题）
3. 可选：搜索行业背景信息
4. 生成结构化需求说明书

## 对话规则
- 每次只问一个问题
- 优先给选项（A/B/C/D），而非开放式问题
- 最多问 12 个问题，到阈值自动总结
- 不编造：无法确认的内容标记为「待确认」
```

**Composite Agent SKILL.md 额外包含 Team Template**：

```markdown
---
name: feature-delivery-pipeline
agent_type: composite
description: 需求驱动并行生成 API 文档、测试套件和代码审查
dependencies:
  - requirements-analyzer
  - api-doc-generator
  - test-suite-generator
  - code-reviewer
---

# Feature Delivery Pipeline

## 编排 DSL
（此处嵌入 composition.toml 内容）

## 工作流程
1. 需求分析：调用 Requirements Analyzer
2. 并行生成：API Doc + Test + Code Review
3. 质量验证：交叉审查
4. 输出汇总
```

### 8.6 Hook System（生命周期钩子）

> **参考模块**: OpenHarness `src/openharness/hooks/executor.py` — `HookExecutor`, `src/openharness/hooks/schemas.py` — Hook 类型定义, `src/openharness/hooks/events.py` — `HookEvent` 枚举; deer-flow `packages/harness/deerflow/agents/middlewares/` — 16 个中间件实现, `packages/harness/deerflow/agents/factory.py` — 中间件组装

#### 8.6.1 设计理念

参考 OpenHarness 的 HookExecutor 设计，Agent 生命周期中的可扩展事件驱动机制。Hook 用于在关键执行节点注入自定义逻辑，实现横切关注点（如验证、通知、审计）的分离。

> `HookExecutor` runtime is implemented in `agent_nexus.platform.hooks.executor`. Hook models are defined in `agent_nexus.models.hooks`.

#### 8.6.2 Hook 类型

| 类型 | 执行方式 | 延迟 | 用途 | 适用场景 |
|------|---------|------|------|---------|
| **Command** | Shell 子进程 | 低 | 脚本验证、文件检查 | 文件存在性验证 |
| **HTTP** | HTTP POST | 中 | 外部服务回调 | CI/CD 触发、通知 |
| **Prompt** | LLM 短调用（小模型） | 中 | 快速校验、格式检查 | 输入验证、格式校验 |
| **Agent** | LLM 深度调用（大模型） | 高 | 复杂推理、质量评审 | 输出质量评估 |

#### 8.6.3 支持的事件

| 事件 | 触发时机 | 常见用途 |
|------|---------|---------|
| `pre_execution` | Agent 执行前 | 输入验证、环境检查 |
| `post_execution` | Agent 执行后 | 输出质量检查、通知 |
| `pre_tool_use` | 工具调用前 | 参数验证、权限增强 |
| `post_tool_use` | 工具调用后 | 结果审计、日志记录 |
| `on_error` | 错误发生时 | 错误通知、降级策略 |
| `on_evolution` | Skill 进化后 | 进化审计、质量门禁 |

#### 8.6.4 Hook 定义格式

```yaml
# hooks/hooks.yaml
pre_execution:
  - type: prompt
    prompt: "验证输入文件存在且格式为 .docx，返回 {ok: true/false, reason: '...'}"
    model: haiku
    block_on_failure: true
    timeout_seconds: 10

post_execution:
  - type: command
    command: "python -c 'import sys; sys.exit(0 if open(sys.argv[1]).read().strip() else 1)' $OUTPUT_PATH"
    block_on_failure: false
    timeout_seconds: 5

  - type: agent
    prompt: "评估填充后的文档质量：检查所有占位符是否已填充、样式是否保持一致"
    model: sonnet
    block_on_failure: true
    timeout_seconds: 30

pre_tool_use:
  - type: prompt
    matcher: "file_write*"    # Glob 匹配工具名
    prompt: "确认写入路径不在敏感目录中"
    block_on_failure: true
```

#### 8.6.5 执行语义

- **阻塞语义**：`block_on_failure: true` 时，Hook 失败阻止后续执行
- **Glob 匹配**：`matcher` 字段使用 `fnmatch` 语法匹配工具名/事件
- **LLM 响应格式**：Prompt/Agent 类型 Hook 返回 `{"ok": true}` 或 `{"ok": false, "reason": "..."}`
- **超时控制**：`timeout_seconds` 防止 Hook 无限等待
- **聚合结果**：`AggregatedHookResult.blocked` 任一 Hook 阻塞即为 blocked

#### 8.6.6 Hook 注册来源

| 来源 | 位置 | 说明 |
|------|------|---------|
| Agent Package 内置 | `hooks/hooks.yaml` | 随 Agent 发布 |
| 用户全局配置 | `~/.agent-nexus/hooks.yaml` | 所有 Agent 生效 |
| 项目级配置 | `.agent-nexus/hooks.yaml` | 当前项目生效 |
| Git 分发安装 | Agent manifest 中声明 | 安装时自动注册 |

### 8.7 数据流场景

**场景一：MCP Standalone（外部框架 → MCP → Agent）**

```
nanobot → MCP stdio → Agent 直接响应（无需 Platform 介入）
```

**场景二：Platform Router（Web UI → Router → Agent）**

```
Browser → POST /api/chat/doc-filler
  → Platform Router (route_chat)
    → stdin.write({"type": "chat", ...})
    → Agent stdout → JSON response
  → SSE stream → Browser
```

**场景三：Composite Agent（TOML → 多 Workers）**

```
User → feature-pipeline__deliver({spec: "..."})
  → Platform Router
    → 加载 composition.toml
    → Step 1: requirements-analyzer（串行）
    → Step 2: [api-doc-gen + test-gen + code-reviewer]（并行）
    → 聚合结果 → 返回
```

### 8.8 Token 优化：Agent 级 Deferred Loading

> **参考来源**: nanobot `docs/proposals/tool-skill-deferred-loading.md` — Deferred Tool Loading 方案; OpenClaw/SoulClaw Tiered Bootstrap 社区验证方案; Anthropic Tool Search API (defer_loading)

#### 8.8.1 问题分析

MCP Gateway 聚合所有 Agent 的 tool schema 后，Token 开销随 Agent 数量线性增长：

| 配置 | Agent 数 | Tool 数 | 全量 Schema Token |
|------|---------|---------|-------------------|
| 2 MCP server (nanobot 典型) | 1 | 20 | ~6,000 |
| 10 Atomic Agent (MCP Standalone) | 10 | 30-50 | ~15,000-25,000 |
| 10 Atomic + 5 Composite (Router 模式) | 15 | 45-75 | **~30,000-60,000** |

Platform Router 模式下，15 个 Agent 的全量 schema 可达 30,000-60,000 tokens（占 200K context 的 15-30%），且大多数对话只使用 1-3 个 Agent。

**行业教训**（nanobot 提案深度调研结论）：

| 问题 | 来源 | 影响 | Agent Nexus 是否受影响 |
|------|------|------|---------------------|
| Bootstrap 每轮全量重注入 | OpenClaw #9157 | 100 条消息浪费 3.4M tokens | ⚠️ 受影响（通过 Tiered Loading 缓解） |
| Compaction 死循环 | OpenClaw #68032 | SELF_IMPROVEMENT 复制 30+ 次，session reset | ⚠️ 受影响（通过 CompactionGuard 缓解） |
| MCP Tool Schema 是最大黑洞 | nanobot 实测 | 单个 GitHub MCP server: ~26K tokens | ✅ 核心问题（通过 Agent 级 Deferred 根治） |
| Session 无硬上限冻结 | nanobot #3029, #2638 | consolidation 失败后冻结 | ✅ 已设 95% 硬上限 |
| Tool 排序破坏 cache prefix | nanobot #2723 | MCP 变更导致 built-in cache 失效 | ✅ 已设排序规则（§10.3.7） |

#### 8.8.2 设计：Agent 级 Deferred Loading

nanobot 的 DeferredToolRegistry 粒度是单个 Tool，但 Agent Nexus 的自然粒度是 Agent（每个 Agent 的工具是内聚的领域工具集）。因此采用 Agent 级 deferred：

```python
class DeferredAgentRegistry:
    """Agent 级 Deferred Loading — 三区设计"""

    def __init__(self):
        self._core_agents: dict[str, AgentInfo] = {}        # 始终加载（Platform 核心工具）
        self._deferred_agents: dict[str, AgentManifest] = {} # 仅 manifest
        self._activated_schemas: dict[str, list[dict]] = {}  # 已激活的完整 schema
        self._activated_processes: dict[str, AgentHandle] = {} # 已启动的子进程

    def register_agent(self, agent: AgentInfo, deferred: bool = True):
        if deferred:
            self._deferred_agents[agent.name] = AgentManifest(
                name=agent.name,
                description=agent.description.split("\n")[0][:80],
                capabilities=agent.capabilities,
                agent_type=agent.agent_type,
            )
        else:
            self._core_agents[agent.name] = agent

    async def activate_agent(self, name: str) -> list[dict]:
        """激活 Agent：返回完整 tool schema + 确保子进程已启动"""
        if name in self._activated_schemas:
            return self._activated_schemas[name]

        # 1. 确保 Agent 子进程已启动
        if name not in self._activated_processes:
            handle = await self.supervisor.start_agent(name)
            self._activated_processes[name] = handle

        # 2. 获取完整 tool schema（MCP initialize 时交换）
        schemas = await self._fetch_agent_tools(name)
        self._activated_schemas[name] = schemas
        return schemas

    def get_tools_for_llm(self) -> list[dict]:
        """返回 core tools + 已激活 agents 的完整 schema + deferred agents 的 manifest"""
        tools = []

        # Core: 始终加载的 Agent tools
        for agent in self._core_agents.values():
            tools.extend(agent.tool_schemas)

        # Activated: 已激活的 deferred agents
        for name, schemas in self._activated_schemas.items():
            tools.extend(schemas)

        return tools

    def build_manifest(self) -> str:
        """构建 deferred agents 的轻量清单（注入 system prompt）"""
        lines = []
        for name, manifest in self._deferred_agents.items():
            status = "activated" if name in self._activated_schemas else "available"
            caps = ", ".join(manifest.capabilities[:3])
            lines.append(f"- {name}: {manifest.description} [{status}] ({caps})")
        return "\n".join(lines)

    def search_agents(self, query: str, max_results: int = 5) -> list[AgentManifest]:
        """关键词搜索 deferred agents"""
        query_lower = query.lower()
        scored = []
        for name, manifest in self._deferred_agents.items():
            text = f"{name} {manifest.description} {' '.join(manifest.capabilities)}".lower()
            score = sum(1 for word in query_lower.split() if word in text)
            if score > 0:
                scored.append((score, manifest))
        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:max_results]]
```

#### 8.8.3 AgentSearchTool（新增内置工具）

```python
@mcp.tool()
async def search_and_activate(query: str, max_results: int = 3) -> str:
    """搜索并激活 Agent。当你需要当前 Agent 列表中不存在的领域能力时使用。"""
    results = registry.search_agents(query, max_results)
    if not results:
        return "未找到匹配的 Agent。"

    activated = []
    for manifest in results:
        schemas = await registry.activate_agent(manifest.name)
        activated.append(f"- {manifest.name}: {manifest.description} ({len(schemas)} tools loaded)")

    return "找到并激活以下 Agent（下轮可直接调用其工具）：\n" + "\n".join(activated)
```

#### 8.8.4 Tiered Context Loading（Agent 全上下文四层规范）

SKILL.md 三层（metadata → body → resources）是**单文件内分层**，只覆盖 Skill 描述层。升级为 Agent 全上下文四层：

| Layer | 内容 | 加载时机 | Token 预算 |
|-------|------|---------|-----------|
| **L0 身份核心** | SKILL.md Metadata + Agent Role 摘要 + Tool 名称列表 + Type 名称列表 + 进化指标摘要 | 每轮 | ≤ 800 |
| **L1 执行上下文** | SKILL.md Body + 高频 Tool Schema + Runtime Functions/Variables describe + 相关 Type Schema + 当前任务进化建议 | 首轮 | ≤ 3,000 |
| **L2 扩展知识** | SKILL.md Resources + 不常用 Tool Schema + Memory/历史分析 + 进化产物详情 + 完整 Type Schema | 按需 | 0 baseline |
| **L3 实时数据** | Variable 当前值 + 跨 Agent 数据引用 + 外部 API 响应 | 运行时动态 | 不预加载 |

System Prompt 构建逻辑：

```python
def build_context(request: ContextRequest) -> str:
    parts = []

    # L0: 每轮注入（身份核心）
    parts.append(build_identity_core(agent.skill_metadata, agent.role_summary))
    parts.append(build_tool_manifest(registry.build_manifest()))
    parts.append(build_type_manifest(agent.runtime.type_names()))
    parts.append(build_evolution_summary(agent.evolution_metrics))
    if request.turn == 1:
        # L1: 首轮注入（执行上下文）
        parts.append(agent.skill_body)
        parts.append(build_frequent_tools(registry.get_core_tools()))
        parts.append(agent.runtime.describe_functions())
        parts.append(agent.runtime.describe_variables())
        parts.append(build_relevant_type_schemas(request.task_types))
        parts.append(build_evolution_suggestions(request.task_suggestions))
    # L2/L3: 不预加载，通过 tool call 按需获取

    return "\n\n".join(parts)
```

#### 8.8.5 Provider Adaptation

> **Implementation Status**: `DeferredAgentRegistry` with `search_and_activate` is the baseline mechanism and fully implemented in `src/agent_nexus/platform/gateway/deferred_registry.py`. Provider-specific optimizations (`ProviderAwareToolStrategy`, `AnthropicNativeStrategy`) remain as design targets for a future iteration.

Gateway 是唯一感知用户模型 Provider 的位置（来自 `config.toml`）：

```python
class ProviderAwareToolStrategy:
    """根据用户模型自动选择 Tool Loading 策略"""

    def get_strategy(self, model: str) -> ToolLoadingStrategy:
        if model.startswith("anthropic:"):
            return AnthropicNativeStrategy()  # 用原生 defer_loading，零 round-trip
        else:
            return ToolSearchFallbackStrategy()  # 标准 function calling

# Anthropic 原生优化（可选）
class AnthropicNativeStrategy:
    def build_tools(self, registry: DeferredAgentRegistry) -> list[dict]:
        tools = registry.get_tools_for_llm()
        for agent_name in registry._deferred_agents:
            if agent_name not in registry._activated_schemas:
                # 标记为 deferred，利用 Anthropic 原生 Tool Search
                tools.append({
                    "type": "tool_search_tool_bm25_20251119",
                })
                for schema in registry._deferred_agents[agent_name].full_schemas:
                    tools.append({**schema, "defer_loading": True})
        return tools
```

#### 8.8.6 与编排层的交互

| 组件 | 是否感知 deferred | 说明 |
|------|-----------------|------|
| TaskGraph | 否 | 任务分配只需 Agent 名 + capability，不需要 tool schema |
| IPC | 否 | 消息传递与 tool schema 无关 |
| ProcessManager | 间接 | `activate_agent()` 通过 ProcessManager 启动子进程 |
| composition.toml | **新增 `tool_loading` 字段** | 声明 Agent 级加载策略 |

Composite Agent `composition.toml` 增加工具加载策略：

```toml
[tool_loading]
strategy = "eager"            # eager | lazy | manifest_only
preload_agents = ["code-reviewer"]  # eager 策略下预加载

[[agents]]
name = "code-reviewer"
tool_loading = "eager"        # 核心成员，立即加载完整 schema

[[agents]]
name = "security-scanner"
tool_loading = "lazy"         # 条件性使用，按需激活
```

- **eager**: Composite Agent 启动时立即加载该 Worker 的完整 tool schema + 启动子进程
- **lazy**: Leader 分配任务给该 Worker 时才激活
- **manifest_only**: 只给 LLM Agent 的能力描述，LLM 通过 `search_and_activate()` 按需发现

#### 8.8.7 预期收益

| 场景 | 当前 Token | 优化后 Token | 节省 |
|------|-----------|-------------|------|
| 15 Agents 全量 (Router 模式) | 30,000-60,000 | 3,000-5,000 (manifest only) | **83-92%** |
| 单个 Composite Agent (4 Atomic) | 6,000-12,000 | 1,000-2,000 (manifest + 1-2 activated) | **75-83%** |
| MCP Standalone (单 Agent) | 600-4,000 | 600-4,000 (无需优化) | 0% |
| 后续轮次 (Tiered Loading) | 全量重复注入 | L0 only (~800) | **60%** |

#### 8.8.8 Token 用量追踪与可观测性

> **设计动机**: OpenClaw 用户报告 $200-3600/月费用，根因是缺乏 token 消耗可见性（nanobot #1193, #2020, #2149）。Agent Nexus 在三个层级提供追踪。

> `TokenTracker` class with tiered alerts (80%/90%/95%) and session-level aggregation is implemented in `agent_nexus.platform.runtime.token_tracker`. Low-level budget logging exists in `EvolutionStore.log_budget_event()` and `TokenUsage` / `ContextBudget` models are defined in `agent_nexus.models.context`.

```python
class TokenTracker:
    """Token 用量追踪 — 挂载在 MCP Gateway"""

    def __init__(self, db: SQLite):
        self._db = db
        self._session_usage: dict[str, TokenUsage] = {}  # agent_name → usage

    def record(self, agent_name: str, prompt_tokens: int, completion_tokens: int):
        usage = self._session_usage.setdefault(agent_name, TokenUsage())
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        usage.total_tokens += prompt_tokens + completion_tokens

    def get_session_summary(self) -> dict:
        """返回当前 session 所有 Agent 的 token 用量"""
        return {
            name: asdict(usage) for name, usage in self._session_usage.items()
        }

    def flush_to_log(self, session_id: str):
        """Session 结束时写入持久化日志"""
        for name, usage in self._session_usage.items():
            self._db.execute("""
                INSERT INTO context_budget_log
                (log_id, agent_id, session_id, prompt_tokens, completion_tokens,
                 total_tokens, compaction_triggered, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(uuid4()), name, session_id, usage.prompt_tokens,
                  usage.completion_tokens, usage.total_tokens,
                  usage.compaction_count, datetime.now().isoformat()))
```

**追踪层级**：

| 层级 | 数据 | 用途 |
|------|------|------|
| Gateway 实时 | `TokenTracker._session_usage` | Session 内预算检查（80%/90%/95% 三级告警） |
| Evolution 持久化 | `context_budget_log` 表 | 跨 Session 分析 token 消耗趋势 |
| Agent 聚合 | Evolution Engine `execution_analyses` 表 | 自进化引擎按 Agent 维度评估 token 效率 |

---
