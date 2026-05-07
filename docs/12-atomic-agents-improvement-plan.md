# 12 — Atomic Agents 借鉴改进方案（实施版）

> **来源**: https://github.com/BrainBlend-AI/atomic-agents (MIT License)
> **创建日期**: 2026-05-06
> **状态**: 待实施

## 项目定位对比

| 维度 | Atomic Agents | Agent-Nexus |
|------|--------------|-------------|
| 定位 | 轻量级 Agent **开发库** (~2K LOC) | MCP-native Agent **平台** (~30K+ LOC) |
| 哲学 | 函数式组合，Python 编排 | 声明式编排 + 生命周期管理 |
| 类比 | React | Next.js |

## 概念映射

| Atomic Agents | Agent-Nexus 等价物 |
|---|---|
| `AtomicAgent[Input, Output]` | CaveAgent-based Agent (`agents/atomic/`) |
| `BaseTool` / `BaseResource` / `BasePrompt` | Agent (通过 MCP tool 暴露) |
| `ChatHistory` (turn-based) | 无独立组件 (嵌入 Orchestration IPC) |
| `SystemPromptGenerator` | SKILL.md → Agent 配置 |
| `ContextProvider` | 无 (system_prompt 静态拼接) |
| `MCPFactory` | MCP Gateway (`platform/gateway/`) |
| 手动编排 (Python 函数链) | TOML DAG + TaskGraph |
| `SchemaTransformer` | `tool_adapter._resolve_json_schema_type` (弱化版) |

---

## P0: Schema Transformer 增强

**优先级**: 最高（第一批实施）
**现状**: `gateway.py` 的 `_resolve_json_schema_type()` 只处理 5 种基本类型 + nullable，复杂 schema 静默退化成 `str`
**目标**: 支持任意 JSON Schema 的完整类型转换（`$ref`、嵌套 object、`oneOf`/`anyOf`）

### 决策

| 决策项 | 结论 |
|--------|------|
| 产出形态 | 独立模块，不照搬 Atomic Agents 的 `BaseIOSchema` |
| 接口设计 | 统一入口 `resolve()`（自动判断复杂度分发）+ 显式 `resolve_model()`（强制生成 BaseModel） |
| 依赖剥离 | 去掉 `BaseIOSchema`、`Literal["tool_name"]`；保留 `model_cache` 防 `$ref` 循环引用 |
| `$ref` 上下文 | 构造时绑定完整 schema（`SchemaTransformer(full_schema)`），1:1 绑定工具实例 |
| 新文件 | `src/agent_nexus/platform/gateway/schema_transformer.py` |

### 接口设计

```python
class SchemaTransformer:
    """JSON Schema → Python type / Pydantic model 转换器。"""

    def __init__(self, full_schema: dict):
        """
        Args:
            full_schema: 完整的 JSON Schema 文档（包含 $defs 等），
                         用于解析 $ref 引用。
        """
        self._full_schema = full_schema
        self._model_cache: dict[str, type[BaseModel]] = {}

    def resolve(self, schema: dict, name: str = "Anonymous") -> type:
        """统一入口：自动判断复杂度，返回对应的 Python 类型。

        - {"type": "string"}                    → str
        - {"type": "integer"}                   → int
        - {"$ref": "#/$defs/Foo"}               → Foo (动态 BaseModel)
        - {"type": "object", "properties": ...} → DynamicModel (BaseModel)
        - {"oneOf": [...]}                      → Union[...]
        """
        ...

    def resolve_model(self, schema: dict, name: str = "DynamicModel") -> type[BaseModel]:
        """显式入口：强制生成完整 Pydantic BaseModel。

        用于需要运行时参数校验的场景（如 Agency Pipeline 对 MCP 工具的输入校验）。
        """
        ...
```

### 集成方式

```python
# gateway.py — 替换 _resolve_json_schema_type
# 之前:
py_type, is_nullable = _resolve_json_schema_type(prop_def)

# 之后:
transformer = SchemaTransformer(tool_input_schema)
py_type = transformer.resolve(prop_def, name=param_name)
```

### 关键注意事项

- `model_cache` + placeholder 防循环引用是核心，必须保留
- `$defs` 和 `definitions` 两种 key 都要支持
- discriminated union 的 `Literal` 字段不移植（Agent-Nexus Gateway 不做工具路由）
- 新文件不依赖 Atomic Agents 的任何代码，纯移植核心逻辑

---

## P1: Hook 事件系统

**优先级**: 最高（第一批实施，和 P0 并行）
**现状**: `LLMClient` 被 4 个调用点使用（Planner/Executor/Integrator/QAGate），调用链路完全黑盒
**目标**: 轻量事件系统，支持无侵入的日志/审计/重试策略/上下文裁剪/监控

### 决策

| 决策项 | 结论 |
|--------|------|
| 消费者 | 全支持：日志/审计 > 重试策略外置 > 上下文裁剪(P2联动) > 监控/指标 |
| Handler 风格 | 返回值有语义：`on_error` → `RetryDecision`，`before_call` 可修改 context |
| CallContext | Mutable（handler 可改 `system_prompt` 等，支持 P2 token 裁剪联动） |
| 归属 | 每个 `LLMClient` 独立持有 `HookManager`，默认空（零开销） |
| 新文件 | `src/agent_nexus/platform/agency/hooks.py` |

### 数据模型

```python
from dataclasses import dataclass, field
from enum import Enum
import uuid

class HookEvent(Enum):
    BEFORE_CALL = "before_call"
    AFTER_CALL  = "after_call"
    ON_ERROR    = "on_error"
    ON_RETRY    = "on_retry"

@dataclass
class CallContext:
    """调用上下文，handler 可修改。"""
    model: str
    system_prompt: str
    user_message: str
    temperature: float | None
    response_format: str | None
    timeout: float | None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attempt: int = 1
    metadata: dict = field(default_factory=dict)

@dataclass
class CallResult:
    """调用结果。"""
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float

@dataclass
class RetryDecision:
    """on_error handler 的返回值。"""
    retry: bool
    delay: float = 0.0
    reason: str = ""
```

### Handler 签名

```python
# before_call: 可修改 ctx.metadata / ctx.system_prompt，raise HookAbort 取消调用
def on_before_call(ctx: CallContext) -> None: ...

# after_call: 纯观察
def on_after_call(ctx: CallContext, result: CallResult) -> None: ...

# on_error: 返回 RetryDecision 控制重试行为，返回 None 用默认重试
def on_error(ctx: CallContext, error: Exception) -> RetryDecision | None: ...

# on_retry: 纯观察
def on_retry(ctx: CallContext, error: Exception, next_attempt: int) -> None: ...
```

### HookManager 接口

```python
class HookManager:
    def register(self, event: HookEvent, handler: Callable) -> None: ...
    def dispatch(self, event: HookEvent, **kwargs) -> Any: ...
    # handler 异常不中断主流程（错误隔离）
```

### 集成方式

```python
class LLMClient:
    def __init__(self, ...):
        self._hooks = HookManager()  # 空 manager，零开销

    def call(self, ...):
        ctx = CallContext(model=self._model_name, ...)
        self._hooks.dispatch(HookEvent.BEFORE_CALL, ctx=ctx)
        try:
            response = self._do_call(...)
            result = CallResult(...)
            self._hooks.dispatch(HookEvent.AFTER_CALL, ctx=ctx, result=result)
            return response
        except Exception as e:
            decision = self._hooks.dispatch(HookEvent.ON_ERROR, ctx=ctx, error=e)
            if decision and decision.retry:
                self._hooks.dispatch(HookEvent.ON_RETRY, ctx=ctx, error=e, next_attempt=...)
                # ... 重试逻辑
            raise
```

### 使用方示例

```python
client = LLMClient.from_config("anthropic:claude-sonnet-4-20250514")
client.hooks.register(HookEvent.BEFORE_CALL, log_before_call)
client.hooks.register(HookEvent.AFTER_CALL, collect_metrics)
client.hooks.register(HookEvent.ON_ERROR, smart_retry_strategy)
```

### 关键注意事项

- Handler 必须同步且快速（不能阻塞 LLM 调用链路）
- Handler 异常只记录日志，不传播到主流程
- 默认无 hook 注册 = 零性能开销
- `RetryDecision` 返回 `None` 等同于"用默认重试策略"

---

## P2: Token 计数 + 按优先级段落裁剪

**优先级**: 第二批（依赖 P1 Hook 系统）
**现状**: Agency Pipeline 各阶段用字符数硬截断（50K/120K 字符），无 token 精确计算
**目标**: 运行时 token 计数 + 按 `PromptSection` 优先级智能裁剪

### 决策

| 决策项 | 结论 |
|--------|------|
| 计数方式 | 混合：可选 tiktoken（精确），无则降级字符估算 `len/4`（±30%） |
| 裁剪策略 | 按 `PromptSection.priority` 裁剪，低优先级先砍 |
| 段落模型 | `StructuredPrompt` + `PromptSection(title, content, priority)` |
| 和 P3 统一设计 | P3 的 Context Provider 产出 `PromptSection`，P2 对 `StructuredPrompt` 整体裁剪 |
| 新文件 | `src/agent_nexus/platform/agency/token_counter.py` |

### 数据模型

```python
from dataclasses import dataclass

@dataclass
class TokenCountResult:
    total: int
    system_prompt: int
    user_message: int
    model: str
    max_tokens: int
    utilization: float  # 0.0 ~ 1.0

@dataclass
class PromptSection:
    title: str
    content: str
    priority: int  # 1=最高(不可砍) ~ 9=最低(最先砍)

    @property
    def token_count(self) -> int:
        """估算本段落的 token 数。"""
        ...

class StructuredPrompt:
    def __init__(self):
        self.sections: list[PromptSection] = []

    def add(self, title: str, content: str, priority: int = 5) -> None: ...
    def add_from_providers(self, providers: dict[str, ContextProvider], priority: int = 5) -> None: ...
    def render(self) -> str: ...
    def trim_to(self, max_tokens: int, counter: TokenCounter) -> None:
        """从最低 priority 开始删除段落，直到 total_tokens <= max_tokens。"""
        ...

class TokenCounter:
    """混合 token 计数器。"""
    def __init__(self):
        self._tiktoken_available: bool = False
        try:
            import tiktoken
            self._tiktoken_available = True
        except ImportError:
            pass

    def count(self, text: str, model: str = "") -> int:
        """计算文本的 token 数。有 tiktoken 就精确，没有就 len/4 估算。"""
        ...

    def count_prompt(self, prompt: StructuredPrompt, model: str, max_tokens: int) -> TokenCountResult:
        """计算整个 StructuredPrompt 的 token 明细。"""
        ...
```

### 优先级分层定义

| 优先级 | 含义 | 典型段落 |
|--------|------|---------|
| 1 | 不可砍（核心角色 + 任务指令） | 角色定义、任务描述 |
| 2 | 高（输出格式要求） | JSON schema 约束、输出格式指令 |
| 3 | 中高（专家信息） | 专家列表、能力描述 |
| 5 | 中（中间产物） | 已执行任务摘要、工具结果摘要 |
| 7 | 低（辅助上下文） | 动态注入的上下文信息（P3 Provider） |
| 9 | 最低（可完全省略） | 示例、额外说明 |

### 集成方式

```python
# P1 Hook + P2 Token 裁剪联动
def token_trim_hook(ctx: CallContext) -> None:
    """注册为 BEFORE_CALL handler，在调用前检查并裁剪。"""
    prompt = StructuredPrompt()
    prompt.add("角色", ctx.system_prompt, priority=1)
    prompt.add("任务", ctx.user_message, priority=1)
    # ... 重建段落结构
    counter = TokenCounter()
    prompt.trim_to(max_tokens=registry.get(ctx.model).context_window, counter=counter)
    ctx.system_prompt = prompt.render()

client.hooks.register(HookEvent.BEFORE_CALL, token_trim_hook)
```

### 关键注意事项

- 不引入 tiktoken 强依赖（`try import`，无则降级）
- 利用已有的 `ModelCapabilityRegistry.context_window` 计算利用率
- `trim_to` 是破坏性操作（删除段落），调用前应记录日志
- 字符估算时按 `len(text) / 4`，中文内容可能偏少但作为安全阈值足够

---

## P3: Context Provider 动态注入

**优先级**: 第二批（和 P2 统一设计）
**现状**: Agency Pipeline 各阶段的 system_prompt 是静态字符串拼接
**目标**: 支持运行时动态注入上下文，和 P2 的段落裁剪共用 `StructuredPrompt` 抽象

### 决策

| 决策项 | 结论 |
|--------|------|
| 注册位置 | Pipeline 级别（跨阶段共享），不在 LLMClient 或单次 Prompt 上 |
| 接口 | `ContextProvider` 协议：`title` + `get_context() -> str` |
| 和 P2 联动 | Provider 产出 → `PromptSection`（带 priority）→ P2 裁剪时按 priority 决定保留 |
| 跨阶段数据流 | Pipeline 统一管理 provider 生命周期，Planner 写入 → Executor/Integrator 读取 |

### 接口设计

```python
from typing import Protocol

class ContextProvider(Protocol):
    """动态上下文注入协议。"""
    title: str

    def get_context(self) -> str:
        """返回当前上下文内容。每次调用可返回不同值（动态）。"""
        ...
```

### Pipeline 集成

```python
class AgencyPipeline:
    def __init__(self, client: LLMClient, ...):
        self._client = client
        self._providers: dict[str, ContextProvider] = {}

    def register_provider(self, name: str, provider: ContextProvider, priority: int = 7) -> None:
        self._providers[name] = provider

    def _build_prompt(self, stage: str, **kwargs) -> StructuredPrompt:
        prompt = StructuredPrompt()
        # 基础段落（来自 SKILL.md / 阶段配置）
        prompt.add("角色定义", self._get_role(stage), priority=1)
        prompt.add("任务指令", self._get_task(stage, **kwargs), priority=1)
        prompt.add("输出格式", self._get_output_format(stage), priority=2)
        # 动态注入（来自 Context Providers）
        for name, provider in self._providers.items():
            content = provider.get_context()
            if content:
                prompt.add(provider.title, content, priority=self._provider_priorities.get(name, 7))
        return prompt
```

### 典型 Provider 示例

```python
class TaskSummaryProvider:
    """将 Planner 的输出摘要注入到后续阶段的上下文中。"""
    title = "已规划任务摘要"

    def __init__(self):
        self._tasks: list[str] = []

    def update(self, tasks: list[str]) -> None:
        self._tasks = tasks

    def get_context(self) -> str:
        if not self._tasks:
            return ""
        return "\n".join(f"- {t}" for t in self._tasks)


class ExpertListProvider:
    """将可用专家列表注入到 Planner 的上下文中。"""
    title = "可用专家"

    def __init__(self, experts: list[ExpertProfile]):
        self._experts = experts

    def get_context(self) -> str:
        return "\n".join(f"- {e.name}: {e.description}" for e in self._experts)


class ReflectionFeedbackProvider:
    """将 Reflector 的反馈注入到下一轮 Executor 的上下文中（P4 用）。"""
    title = "改进建议"

    def __init__(self):
        self._feedback: str = ""

    def update(self, feedback: str) -> None:
        self._feedback = feedback

    def get_context(self) -> str:
        return self._feedback
```

### 关键注意事项

- Provider 是无状态的协议（`Protocol`），不强制继承基类
- `get_context()` 每次调用都重新求值（动态），不缓存
- Pipeline 级别的 provider 跨阶段共享数据，通过 `update()` 写入、`get_context()` 读取
- Provider 产出空字符串时，`StructuredPrompt` 自动跳过（不产生空段落）

---

## P4: 条件回边 / Reflect Loop

**优先级**: 第三批（依赖 P1 + P2 + P3）
**现状**: TaskGraph 是严格无环 DAG，无法表达"执行→评估→循环"模式
**目标**: 支持 deep-research 类的多轮搜索-提取-评估循环

### 决策

| 决策项 | 结论 |
|--------|------|
| 实施路径 | 先 Python 编排验证（不改 TaskGraph），验证有效后再抽象到 TaskGraph |
| 循环位置 | 包裹 Executor 阶段：执行 → Reflector 评估 → 不充分则注入反馈再执行 |
| Reflector | 混合：规则层快速过滤 + LLM 层精细判断 |
| 迭代保护 | `max_iterations` 硬上限 + `max_agent_calls` 总量限制 |

### Reflector 设计

```python
@dataclass
class Reflection:
    sufficient: bool
    reason: str
    feedback: str = ""      # 不充分时的改进建议
    next_queries: list[str] = field(default_factory=list)  # 下一轮探索方向

class ReflectionRule(Protocol):
    """规则层的快速判断协议。"""
    def check(self, task: str, result: str) -> Reflection | None:
        """返回 None 表示规则无明确结论，交给下一层判断。"""
        ...

class LLMReflector:
    """LLM 驱动的精细评估。"""
    def __init__(self, client: LLMClient):
        self._client = client

    def evaluate(self, task: str, result: str, attempt: int, max_iterations: int) -> Reflection:
        ...

class Reflector:
    """混合 Reflector：规则 + LLM。"""
    def __init__(self, rules: list[ReflectionRule], llm: LLMReflector | None = None):
        self._rules = rules
        self._llm = llm

    def evaluate(self, task: str, result: str, attempt: int, max_iterations: int) -> Reflection:
        # 规则层：快速判断
        for rule in self._rules:
            verdict = rule.check(task, result)
            if verdict is not None:
                return verdict
        # LLM 层：精细判断
        if self._llm:
            return self._llm.evaluate(task, result, attempt, max_iterations)
        # 无 LLM → 默认通过
        return Reflection(sufficient=True, reason="No rules failed and no LLM configured")
```

### 编排伪代码

```python
def execute_with_reflect(self, plan: Plan, providers: dict) -> list[Artifact]:
    results = []
    for task in plan.tasks:
        for attempt in range(1, self._max_iterations + 1):
            # 构建结构化 prompt（P3 Provider + P2 裁剪）
            prompt = self._build_prompt("executor", task=task, providers=providers)
            result = self._executor.execute(task, prompt)

            # Reflector 评估
            reflection = self._reflector.evaluate(task, result, attempt, self._max_iterations)

            if reflection.sufficient:
                results.append(result)
                break

            # 不充分：注入反馈，下一轮 Executor 能看到
            providers["reflection_feedback"].update(reflection.feedback)
    return results
```

### 内置规则示例

```python
class EmptyResultRule:
    """结果为空或过短时直接判定不充分。"""
    def check(self, task: str, result: str) -> Reflection | None:
        if not result or len(result.strip()) < 50:
            return Reflection(sufficient=False, reason="结果为空或过短", feedback="请提供更详细的内容")
        return None

class MaxIterationRule:
    """达到最大迭代次数时强制通过。"""
    def __init__(self, max_iterations: int):
        self._max = max_iterations

    def check(self, task: str, result: str, attempt: int) -> Reflection | None:
        if attempt >= self._max:
            return Reflection(sufficient=True, reason="已达最大迭代次数，强制通过")
        return None
```

### 后续抽象到 TaskGraph（Phase 2）

验证模式有效后，在 TOML DSL 中增加条件回边语法：

```toml
[tasks.reflect]
agent = "reflector"
depends_on = ["extract"]
# 条件分支
on_complete = { sufficient = "integrate", insufficient = "search" }
max_iterations = 3
```

TaskGraph 需要修改：
- `cycle detection` 改为 `max_iterations guard`
- 任务输出增加 `branch` 字段（`sufficient` / `insufficient`）
- SQLite schema 增加 `iteration_count` 和 `max_iterations` 列

**注意**：这部分是 Phase 2，不在当前实施范围内。

---

## 不需要借鉴的点

| 特性 | 原因 |
|------|------|
| Instructor 依赖 | Agent-Nexus 自建 LLMClient，不需要结构化输出中间件 |
| Pydantic 泛型 PEP 695 | Python 3.12+ 特性，Agent-Nexus 需要保持兼容性 |
| BaseIOSchema docstring 强制验证 | 过于严格，与 SKILL.md 文档体系冲突 |
| atomic CLI / Forge 工具分发 | Agent-Nexus 已有 Git-based 分发（Homebrew tap model） |
| BaseResource / BasePrompt 抽象 | Agent-Nexus 通过 MCP 协议原生暴露，不需要额外 Python 抽象层 |

---

## 实施路线图

```
第一批（可并行）     第二批                  第三批
┌──────────┐       ┌──────────────────┐    ┌─────────────────┐
│ P0 Schema│       │ P2 Token 计数     │    │                 │
│ Trans-   │       │ + 按优先级裁剪    │    │ P4 Reflect Loop │
│ former   │       ├──────────────────┤    │                 │
├──────────┤       │ P3 Context        │    │ (依赖 P1+P2+P3) │
│ P1 Hook  │ ────→ │ Provider          │ ──→│                 │
│ 事件系统 │       │                   │    │                 │
└──────────┘       └──────────────────┘    └─────────────────┘
```

### 文件清单

| 改进点 | 新文件 | 修改文件 |
|--------|--------|---------|
| P0 | `src/agent_nexus/platform/gateway/schema_transformer.py` | `gateway.py`（替换 `_resolve_json_schema_type`） |
| P1 | `src/agent_nexus/platform/agency/hooks.py` | `llm_client.py`（嵌入 hook dispatch） |
| P2 | `src/agent_nexus/platform/agency/token_counter.py` | 各 Pipeline 阶段（改字符截断为 token 裁剪） |
| P3 | (集成在 Pipeline 中) | Agency Pipeline 主文件（provider 注册 + prompt 构建） |
| P4 | `src/agent_nexus/platform/agency/reflector.py` | Pipeline 执行逻辑（包裹 reflect loop） |
