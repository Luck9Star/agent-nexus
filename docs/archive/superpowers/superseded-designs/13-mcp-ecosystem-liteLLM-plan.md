# 13 — MCP 生态集成 + LiteLLM 统一调用层方案

> **来源**: atomic-agents 项目深度对比分析（团队 3 并发 agent 分析结果）
> **前置**: `docs/12-atomic-agents-improvement-plan.md` P0-P4 已实施（commit `666f713`）
> **创建日期**: 2026-05-06
> **状态**: 待实施

## 背景：12 号文的 P0-P4 已覆盖了什么

| 已实施 | 对应文件 | 状态 |
|--------|---------|------|
| P0 SchemaTransformer | `platform/gateway/schema_transformer.py` | ✅ |
| P1 Hook 事件系统 | `platform/agency/hooks.py` | ✅ |
| P2 Token 计数 + 优先级裁剪 | `platform/agency/token_counter.py` | ✅ |
| P3 Context Provider | `platform/agency/context_provider.py` | ✅ |
| P4 Reflect Loop | `platform/agency/reflector.py` | ✅ |

12 号文关注的是 **atomic-agents 的内部组件模式**（Schema/Hook/Token/Context/Reflect）。
本文关注的是 **MCP 生态集成 + 调用层统一**——这是 12 号文未覆盖的互补维度。

---

## 核心发现：两个项目的 MCP 互补性

| 维度 | atomic-agents | agent-nexus |
|------|--------------|-------------|
| MCP 角色 | **客户端**（连接外部 MCP Server） | **服务端**（暴露 Agent 为 MCP Server） |
| 传输协议 | SSE / HTTP_STREAM / STDIO | STDIO + SSE |
| Schema 发现 | 运行时 `list_tools()` | YAML 声明 + IPC 发现 |
| 核心能力 | 把外部 MCP 工具变成 typed Python 类 | 把 Agent 工具聚合为统一 MCP 端点 |

**结论**：agent-nexus 是 MCP Server，缺少 MCP Client 能力。两者高度互补。

---

## 定级总览

| # | 建议 | 定级 | 改动量 | 收益 |
|---|------|------|--------|------|
| N1 | MCP 客户端到 Gateway | **P0** | M | High |
| N2 | LiteLLM 统一 LLMClient 调用层 | **P0** | L | High |
| N3 | Discriminated Union 约束 Planner | **P1** | M | High |
| N4 | DAG 数据流（task 间 artifact 传递） | **P2** | L | High |
| N5 | Gateway 输出结构化处理 | **P2** | S | Medium |

---

## N1: MCP 客户端到 Gateway（P0）

### 现状

Gateway 当前只能聚合"自管 Agent subprocess"的工具（通过 `DeferredAgentRegistry` + `ProcessManager`）。
没有能力连接外部 MCP Server（如 filesystem server、web search server）。

### 目标

Gateway 同时聚合两类工具来源：
1. **自管 Agent**（现有能力）— subprocess + IPC
2. **外部 MCP Server**（新增）— SSE/HTTP_STREAM/STDIO 连接

用户在 `config.toml` 中配置外部 MCP Server 端点，Gateway 启动时自动发现并注册其工具。

### 决策

| 决策项 | 结论 |
|--------|------|
| 新增文件 | `src/agent_nexus/platform/gateway/external_mcp_adapter.py` |
| 配置格式 | `config.toml` 新增 `[[mcp.external_servers]]` 段 |
| 传输协议 | 支持 SSE / HTTP_STREAM / STDIO（参考 atomic-agents 的 `MCPDefinitionService`） |
| 工具注册 | 复用现有 `SchemaTransformer` + `_make_tool_func()` 流程 |
| 生命周期 | 启动时连接 + 健康检查 + 断线重连 |

### 配置格式

```toml
# config.toml
[[mcp.external_servers]]
name = "filesystem"
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
enabled = true

[[mcp.external_servers]]
name = "web-search"
transport = "sse"
url = "http://localhost:3001/sse"
enabled = true

[[mcp.external_servers]]
name = "github"
transport = "http_stream"
url = "http://localhost:3002/mcp"
headers = { Authorization = "Bearer ${GITHUB_TOKEN}" }
enabled = true
```

### ExternalMcpAdapter 接口

```python
class ExternalMcpAdapter:
    """连接外部 MCP Server，发现并缓存工具 schema。"""

    def __init__(self, config: ExternalServerConfig):
        self._config = config
        self._session: ClientSession | None = None
        self._tool_schemas: list[dict] = []

    async def connect(self) -> None:
        """建立连接，发现工具。"""

    async def disconnect(self) -> None:
        """断开连接。"""

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用外部工具。"""

    @property
    def tool_schemas(self) -> list[dict]:
        """返回发现的工具 schema 列表。"""

    @property
    def is_alive(self) -> bool:
        """连接是否存活。"""
```

### Gateway 集成方式

```python
# gateway.py — 新增方法
async def register_external_server(self, config: ExternalServerConfig) -> None:
    """注册外部 MCP Server，发现工具并注册到 FastMCP。"""
    adapter = ExternalMcpAdapter(config)
    await adapter.connect()

    for schema in adapter.tool_schemas:
        # 复用现有的 McpToolAdapter 包装逻辑
        # 区别：execute 走 adapter.call_tool() 而不是 IPC
        ...
```

### 工具名命名约定

外部工具使用 `ext__{server_name}__{tool_name}` 前缀（区别于自管 agent 的 `mcp__`）。

### 关键注意事项

- 使用 `mcp` 库的 `ClientSession`（不是 `fastmcp`），因为要做 MCP 客户端
- `mcp` 库已在项目依赖中（`fastmcp` 依赖 `mcp`），不需要新增依赖
- STDIO 传输需要 `asyncio.create_subprocess_exec` 启动子进程
- SSE/HTTP_STREAM 传输使用 `httpx`
- 连接失败不阻塞 Gateway 启动，只 warning 并跳过

---

## N2: LiteLLM 统一 LLMClient 调用层（P0）

### 现状

`LLMClient` 有 4 条 API 调用路径 + 1 条 CLI 路径：

```
_call_anthropic()      — Anthropic SDK streaming + non-streaming   (~70行)
_call_openai()         — OpenAI SDK streaming + non-streaming      (~65行)
_call_anthropic_raw()  — httpx fallback                            (~25行)
_call_openai_raw()     — httpx fallback                            (~25行)
_call_cli()            — GenericCLIBackend subprocess              (~50行)
```

总计 ~385 行调用层代码。每新增一个 provider（Google、Mistral、Ollama）需要再加 2-3 个 `_call_xxx` 方法。

### 目标

用 LiteLLM 替代 4 条 API 调用路径为 1 条，CLI Backend 保留不动。

### 重构后调用架构

```
LLMClient.call()
├── CLI Backend → GenericCLIBackend（原样保留，LiteLLM 不覆盖）
└── API Provider → litellm.completion()（替代 4 条 _call_xxx）
                    ├── 自动路由（anthropic: / openai: / deepseek: / ollama: / ...）
                    ├── 内置 retry + fallback
                    ├── 内置 streaming
                    └── 内置 token 计数
```

### 决策

| 决策项 | 结论 |
|--------|------|
| 新增依赖 | `litellm>=1.50.0`（加到 pyproject.toml） |
| 保留不变 | `CLI Backend` / `ModelCapabilityRegistry` / `HookManager` / `ConfigLoader` |
| 删除 | `_call_anthropic()` / `_call_openai()` / `_call_anthropic_raw()` / `_call_openai_raw()` / `_build_anthropic_*` / `_build_openai_*` / `_apply_sampling_params` / `_call_with_retry` |
| 保留但调整 | `from_config()` / `close()` / `__init__` |
| model_string 映射 | `provider:model` → `litellm provider/model`（如 `anthropic:claude-sonnet-4-20250514` → `anthropic/claude-sonnet-4-20250514`） |

### 重构后的 `call()` 方法

```python
def call(self, system_prompt, user_message, max_tokens=None,
         temperature=None, top_p=None, timeout=None,
         session_id=None, response_format=None) -> LLMResponse:

    ctx = CallContext(model=self._model_name, system_prompt=system_prompt, ...)
    self._hooks.dispatch(HookEvent.BEFORE_CALL, ctx=ctx)

    # CLI Backend — 走原路径
    if self._provider_config.api == ProviderApiType.CLI:
        return self._call_cli(ctx, session_id)

    # API Provider — 走 LiteLLM
    litellm_model = self._to_litellm_model()
    kwargs = self._build_litellm_kwargs(ctx, max_tokens, top_p)
    response = litellm.completion(model=litellm_model, **kwargs)

    text = response.choices[0].message.content or ""
    actual_model = response.model or self._model_name

    result = CallResult(content=text, model=actual_model, ...)
    self._hooks.dispatch(HookEvent.AFTER_CALL, ctx=ctx, result=result)
    return LLMResponse(text=text, model=actual_model, provider=self._provider_name)
```

### model_string 映射

```python
def _to_litellm_model(self) -> str:
    """将 agent-nexus 格式转为 litellm 格式。

    agent-nexus:  'anthropic:claude-sonnet-4-20250514'
    litellm:      'anthropic/claude-sonnet-4-20250514'

    agent-nexus:  'api:MiniMax-M2.7-highspeed'
    litellm:      'openai/MiniMax-M2.7-highspeed'  (通过 base_url 路由)
    """
    provider = self._provider_name
    model = self._model_name

    # 已知映射
    known = {
        "anthropic": "anthropic",
        "openai": "openai",
        "deepseek": "deepseek",
        "ollama": "ollama",
        "api": "openai",  # OpenAI-compatible APIs (MiniMax, Qwen, etc.)
    }
    litellm_provider = known.get(provider, "openai")
    return f"{litellm_provider}/{model}"
```

### Token 计数增强

LiteLLM 自带 `litellm.token_counter()`，替代当前的 tiktoken + len/4：

```python
# token_counter.py — 增强为三档
class TokenCounter:
    def count(self, text: str, model: str = "") -> int:
        # 1. LiteLLM 精确计数（最准，支持 100+ provider）
        try:
            return litellm.token_counter(model=model, text=text)
        except Exception:
            pass
        # 2. tiktoken 精确计数（OpenAI 系模型 fallback）
        if self._tiktoken_available:
            ...
        # 3. len/4 估算（最终 fallback）
        return max(1, len(text) // 4)
```

### 保留的能力

以下能力不会被 LiteLLM 替代，保持原样：

1. **CLI Backend**（`GenericCLIBackend`）— subprocess 调用，LiteLLM 不支持
2. **ModelCapabilityRegistry** — 精细的 per-model temperature/vision/tool_use 元数据，比 LiteLLM 的 `get_model_info()` 更准确
3. **HookManager** — `CallContext` 是 mutable 的，handler 能修改 prompt，LiteLLM 的 callback 机制不同
4. **ConfigLoader + ModelConfigManager** — config.toml 解析和 provider 配置

### 关键注意事项

- LiteLLM 会自动设置 `api_key`、`base_url`，但需要通过环境变量或 `litellm.completion()` 参数传入
- 对于 OpenAI-compatible API（MiniMax、Qwen 等），用 `openai/model_name` + `api_base` 路由
- `openai` 和 `anthropic` SDK 包从硬依赖降为可选（LiteLLM 内部会按需安装）
- 流式调用用 `litellm.completion(stream=True)` 替代，保留流式中断处理
- `response_format="json"` 用 `litellm` 的 `response_format` 参数传递

### 依赖影响

```toml
# pyproject.toml
[project]
dependencies = [
    "litellm>=1.50.0",      # 新增（替代 openai/anthropic 的直接调用）
    "openai",                # 降为可选（LiteLLM 内部按需使用）
    "anthropic",             # 降为可选
    # ... 其他不变
]
```

---

## N3: Discriminated Union 约束 Planner（P1）

### 现状

Agency Planner 的输出是自由文本（task 描述 + expert 分配），没有结构化约束。
LLMExecutor 的输出用 `## heading` 解析（已经做了 code block 隔离 + heading 归一化，比较稳健）。

### 目标

约束 Planner 的 expert 选择过程，使用 Discriminated Union 让 LLM 必须：
1. 选择一个 expert（通过 Literal 字段）
2. 填充该 expert 对应的任务参数

### 依赖

- 依赖 N2（LiteLLM）的 structured output 支持（`response_format` + JSON schema）
- 或者使用现有 `response_format="json"` + Pydantic model 验证

### 设计

```python
from typing import Literal, Union
from pydantic import BaseModel

# 为每个 expert profile 动态生成 input schema
class PlannerOutput(BaseModel):
    """Planner 的结构化输出。"""
    selected_expert: str  # expert profile ID
    task_description: str  # 给 expert 的任务描述
    priority: int = 5  # 任务优先级
    rationale: str = ""  # 为什么选这个 expert

# 多 expert 场景（Discriminated Union）
# 动态生成：Literal["expert-a", "expert-b", ...] + 每个 expert 的参数结构
class ExpertSelection(BaseModel):
    expert_id: str  # discriminated field
    task: str
    parameters: dict = {}
```

### 集成方式

```python
# llm_planner.py — 使用 structured output
class LLMPlanner:
    def plan(self, task: str, expert_profiles: list[dict]) -> Plan:
        # 构建 discriminated union schema
        expert_ids = [p["id"] for p in expert_profiles]
        output_schema = self._build_selection_schema(expert_ids)

        # 调用 LLM（使用 structured output）
        response = self._client.call(
            system_prompt=self._build_planner_prompt(expert_profiles),
            user_message=task,
            response_format="json",
        )

        # 解析为 Pydantic model（替代自由文本解析）
        selection = PlannerOutput.model_validate_json(response.text)
        return Plan(tasks=[selection])
```

### 关键注意事项

- 不需要引入 atomic-agents 的 `Literal["tool_name"]` 注入模式
- 用 JSON mode + Pydantic 验证即可实现等价效果
- Planner 的 prompt 需要调整，告知 LLM 输出 JSON schema
- fallback：如果 LLM 不支持 JSON mode，回退到当前自由文本解析

---

## N4: DAG 数据流（P2）

### 现状

DAG `blocked_by` 只控制调度顺序，task 间 **没有数据传递**。
每个 task 收到相同的原始 `task_description`，上游 artifact 不传给下游。
IPC 层预留了 `send_data_reference()` 但从未接线。

### 目标

task A 的 Artifact 能传递给 task B 作为输入上下文。

### 改造点

| 文件 | 改动 |
|------|------|
| `executor.py` | `__call__` 签名增加 `upstream_artifacts: list[Artifact] | None` |
| `dag_dispatcher.py` | `_run_executor()` 收集上游 artifact，拼接后传给下游 |
| `ipc.py` | `send_data_reference()` 接线（或用 `send_chat` 传递摘要） |
| `token_counter.py` | 上游 artifact 拼接后需要重新计算 token 预算 |

### 数据传递策略

两种方案：

**方案 A：全量传递**（简单但 token 开销大）
- 上游 artifact 的全部 section 拼接成字符串，作为下游 task 的 context 注入
- 适合 artifact 较小的场景

**方案 B：摘要传递**（推荐）
- 上游 artifact 经过 Integrator 摘要后，生成 ~500 token 的摘要
- 摘要通过 ContextProvider 注入下游 task 的 StructuredPrompt
- 配合 P3 ContextProvider 的 priority 机制控制裁剪

### 前置依赖

- N2（LiteLLM）：structured output 支持摘要生成
- N5（输出结构化）：保留 artifact 的结构化形式

---

## N5: Gateway 输出结构化处理（P2）

### 现状

`McpToolAdapter.execute()` 返回 `{"output": str, "success": bool}`。
Agent 返回的结构化 JSON 被 flatten 成字符串。

### 目标

保留 agent 返回的结构化数据，为 DAG 数据流和 tool chaining 提供基础。

### 改造点

```python
# tool_adapter.py — execute 返回值增强
async def execute(self, handle, arguments) -> dict:
    ...
    # 尝试解析结构化输出
    content = response.content or ""
    structured = None
    try:
        structured = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass

    return {
        "output": content,        # 原始字符串（向后兼容）
        "structured": structured,  # 结构化数据（None 如果不是 JSON）
        "success": response.is_success,
    }
```

### 前置依赖

- N4（DAG 数据流）：有了结构化输出，下游 task 才能使用上游的结构化数据

---

## 实施路线图

```
Phase 1（并行，可同时启动）
┌─────────────────────────┐    ┌─────────────────────────┐
│ N1: MCP 客户端到 Gateway │    │ N2: LiteLLM 统一调用层  │
│                         │    │                         │
│ ExternalMcpAdapter      │    │ 替换 4 条 _call_xxx     │
│ config.toml 新增段      │    │ 保留 CLI Backend        │
│ ext__ 命名约定          │    │ TokenCounter 三档增强    │
│                         │    │ model_string 映射       │
└────────────┬────────────┘    └────────────┬────────────┘
             │                              │
Phase 2      │                              │
┌────────────▼──────────────────────────────▼┐
│ N3: Discriminated Union 约束 Planner       │
│                                            │
│ JSON mode + Pydantic 验证                  │
│ 动态 expert selection schema               │
│ fallback 到自由文本                        │
└────────────────────────┬───────────────────┘
                         │
Phase 3                  │
┌────────────────────────▼───────────────────┐
│ N4: DAG 数据流  +  N5: 输出结构化          │
│                                            │
│ executor 签名改造                          │
│ _run_executor 收集上游 artifact            │
│ 摘要传递（ContextProvider 注入）           │
│ McpToolAdapter 返回 structured data        │
└────────────────────────────────────────────┘
```

---

## 文件清单

| 建议 | 新文件 | 修改文件 | 删除 |
|------|--------|---------|------|
| N1 | `platform/gateway/external_mcp_adapter.py` | `gateway.py`、`config/loader.py` | — |
| N2 | — | `agency/llm_client.py`（大幅重构）、`agency/token_counter.py` | `_call_anthropic*`、`_call_openai*`、`_build_anthropic_*`、`_build_openai_*` |
| N3 | — | `agency/llm_planner.py` | — |
| N4 | — | `agency/executor.py`、`agency/dag_dispatcher.py` | — |
| N5 | — | `gateway/tool_adapter.py` | — |

---

## 不需要做的

| 建议 | 原因 |
|------|------|
| Pipeline 级事件总线 | 已有 logging + HookManager + ContextProvider 覆盖 |
| BaseTool/BaseResource 泛型基类 | 与 FastMCP 两套并行机制，无收益 |
| SystemPromptGenerator | StructuredPrompt + ContextProvider 已更成熟 |
| ChatHistory 完整搬移 | Agency 是 task-based 单轮编排，等 P4 Reflect Loop 评估 |
| BaseIOSchema 强制 docstring | 需先统一继承关系，ROI 不如直接用 SchemaTransformer |
