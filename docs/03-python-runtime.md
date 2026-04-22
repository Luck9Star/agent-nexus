# Python Runtime 执行层

> Agent Nexus Design Doc — §5 Python Runtime 执行层：Runtime vs Tool Call 范式、CaveAgent 实测数据、架构核心、SecurityChecker、Runtime-First Hybrid 策略、隔离级别、与 Atomic Agent 集成

> **Status**: ✅ Implemented
> **Code**: `src/agent_nexus/platform/runtime/` (PythonRuntime 245 lines, IPythonExecutor 438 lines, SecurityChecker 267 lines, SecurityRules 281 lines, TieredDescriber 139 lines, PermissionChecker 403 lines, TokenTracker 175 lines)
> **Tests**: `tests/unit/test_runtime.py`, `tests/unit/test_runtime_models.py`, `tests/unit/test_executor.py`, `tests/unit/test_security_checker.py`, `tests/unit/test_security_rules.py`, `tests/unit/test_describer.py`, `tests/unit/test_permission_checker.py`, `tests/unit/test_permission_models.py`, `tests/unit/test_token_tracker.py`

## §5 Python Runtime 执行层

> **参考项目**: [acodercat/cave-agent](https://github.com/acodercat/cave-agent) — MIT License
>
> **本地源码**: `/Users/yangyitian/Documents/dev/Agents/cave-agent/`
>
> **论文**: CaveAgent: Transforming LLMs into Stateful Runtime Operators (arXiv:2601.01569)

### 5.1 核心范式：Runtime vs Tool Call

> **参考模块**: cave-agent `src/cave_agent/runtime/runtime.py` — `Runtime` 基类, `src/cave_agent/runtime/ipython_runtime.py` — `IPythonRuntime`, `src/cave_agent/agent.py` — `CaveAgent` 执行循环

**传统 Tool Call 流程：**

```
LLM → JSON tool_call {name, args} → Host 解析 → 执行函数 → JSON 结果 → LLM context
（每轮无状态，所有数据序列化到文本，context window 膨胀）
```

**CaveAgent Runtime 流程：**

```
LLM → Python 代码 → Runtime 执行 → Python 对象持久存在 → LLM 看到结果
（对象跨轮存在，DataFrame 留在 runtime，零序列化开销）
```

### 5.2 CaveAgent 实测数据

Tau-2 benchmark（6 个 SOTA LLM）：

| 模型 | 基线成功率 | CaveAgent 成功率 | 提升 |
|------|-----------|-----------------|------|
| GPT-4o | 31.0% | 37.0% | +6.0% |
| Claude 3.5 Sonnet | 28.5% | 40.5% | +12.0% |
| Gemini 2.0 Flash | 21.5% | 31.5% | +10.0% |
| Gemini 2.5 Pro | 38.5% | 46.5% | +8.0% |
| DeepSeek-V3 | 20.5% | 33.5% | +13.0% |
| Qwen 2.5 72B | 19.0% | 33.0% | +14.0% |
| **平均** | **26.5%** | **37.0%** | **+10.5%** |

- Token 消耗：**-28.4%**（平均）
- 数据密集任务 Token：**-59%**

### 5.3 CaveAgent 架构核心

> **实现模块**: `src/agent_nexus/platform/runtime/runtime.py` — `PythonRuntime`, `src/agent_nexus/platform/runtime/executor.py` — `IPythonExecutor`

```python
class PythonRuntime:
    """持久 Python 命名空间，管理 Variables / Functions / Types"""
    _executor: IPythonExecutor       # IPython InteractiveShell wrapper
    _variables: Dict[str, Variable]  # 持久化 Python 对象
    _functions: Dict[str, Function]  # 可调用的 Python 函数
    _types: Dict[str, RuntimeType]   # 可用的 Python 类型（含 schema）
    _security_checker: SecurityChecker  # AST 级安全检查

    def inject_variable(self, variable: Variable)
    def inject_function(self, function: Function)
    def inject_type(self, type_obj: RuntimeType)
    async def execute(self, code: str) -> ExecutionResult
    def retrieve(self, name: str) -> Any

    # LLM Prompt 生成（由 TieredRuntimeDescriber 调用）
    def describe_variables(self) -> str
    def describe_functions(self) -> str
    def describe_types(self) -> str
```

### 5.4 SecurityChecker（AST 级安全检查）

> **实现模块**: `src/agent_nexus/platform/runtime/security_checker.py` — `SecurityChecker`, `src/agent_nexus/platform/runtime/security_rules.py` — `ImportRule`, `FunctionRule`, `AttributeRule`, `RegexRule`

> **参考模块**: cave-agent `src/cave_agent/security/checker.py` — `SecurityChecker` 类, `src/cave_agent/security/rules.py` — `ImportRule`, `FunctionRule`, `AttributeRule`, `RegexRule`

```python
class SecurityChecker:
    rules: List[SecurityRule]
    def check_code(self, code: str) -> List[SecurityViolation]

# 规则类型
ImportRule({"os", "subprocess", "sys", "socket", ...})    # 禁止模块导入
FunctionRule({"eval", "exec", "open", "__import__", ...})  # 禁止函数调用
AttributeRule({"__globals__", "__builtins__", ...})         # 禁止属性访问
RegexRule("forbidden pattern", r"delete")                   # 自定义正则
```

### 5.5 Runtime-First Hybrid 策略

Python Runtime 优先，MCP 用于外部通信。不可完全抛弃 Tool Call。

| 场景 | Runtime | MCP | 推荐 |
|------|---------|-----|------|
| 数据处理 (DataFrame, 文件 IO) | ✅ 完美 | ❌ 序列化灾难 | **Runtime** |
| 对象方法调用 (df.query()) | ✅ 直接 | ❌ 需映射 | **Runtime** |
| Pydantic 模型验证 | ✅ 原生 | ❌ 需要 JSON schema | **Runtime** |
| 跨进程 Agent 通信 | ❌ 命名空间隔离 | ✅ 协议无关 | **MCP** |
| 外部 API 调用 | ⚠️ 需注入 client | ✅ 天然适配 | **MCP** |
| Shell 命令执行 | ⚠️ 安全风险 | ✅ 可控 | **MCP** |
| 浏览器操作 | ❌ 不适合 | ✅ Playwright MCP | **MCP** |
| 多 Agent 共享状态 | ⚠️ 需设计 | ✅ MailboxManager | **消息** |
| 对外暴露（非 Python 客户端） | ❌ | ✅ | **MCP** |

### 5.6 隔离级别：IPythonExecutor（同进程）

> **实现模块**: `src/agent_nexus/platform/runtime/executor.py` — `IPythonExecutor` (InteractiveShell wrapper, lazy-init, 禁用 history/automagic/colors)

> **参考模块**: cave-agent `src/cave_agent/runtime/executor.py` — `IPythonExecutor` (InteractiveShell wrapper), `src/cave_agent/runtime/ipykernel_runtime.py` — `IPyKernelRuntime` (备选)

选择 IPythonRuntime 而非 IPyKernelRuntime 的理由：

- Agent 本身已经是子进程（ProcessManager spawn / Platform Router）
- 无需在子进程内再做进程隔离
- 性能最优：零拷贝，即时启动
- 安全由 Agent 进程边界 + SecurityChecker 保证

| Runtime | 隔离 | 启动 | 对象传输 | 崩溃行为 |
|---------|------|------|---------|---------|
| `IPythonRuntime` | 同进程 | 即时 | 直接引用（零拷贝） | 宿主死 |
| `IPyKernelRuntime` | 独立进程(Jupyter) | ~1s | dill 序列化 | kernel 重启，宿主存活 |

#### 超时强制执行

`IPythonExecutor.execute()` 通过 `asyncio.to_thread()` 将 `run_cell()` 调度到线程池执行，配合 `asyncio.wait_for(timeout)` 实现可强制执行的超时机制。由于 IPython 内部的代码执行是同步阻塞操作，直接在事件循环中运行无法被 `wait_for` 取消；通过 `to_thread` 将执行移入线程，事件循环保留控制权，超时时 `CancelledError` 可立即触发。

### 5.7 与 Atomic Agent 的集成

```python
class DocFillerAgent:
    """每个 Atomic Agent 内部嵌入 PythonRuntime"""

    runtime = PythonRuntime(
        variables=[
            Variable("template", docx_template, "Word 模板对象"),
            Variable("source_data", markdown_content, "Markdown 源数据"),
            Variable("output", None, "填充后的文档"),
        ],
        functions=[
            Function(parse_markdown, "解析 Markdown 结构"),
            Function(fill_template, "填充 Word 模板"),
            Function(validate_output, "校验输出完整性"),
        ],
        types=[
            Type(TemplateMapping, include_schema=True),
            Type(FillResult, include_schema=True),
        ],
        security_checker=SecurityChecker([
            ImportRule({"os", "subprocess", "sys", "socket"}),
            FunctionRule({"eval", "exec", "open", "__import__"}),
            AttributeRule({"__globals__", "__builtins__"}),
        ]),
    )

    # MCP 只用于对外暴露和跨进程调用
    mcp_server = FastMCP("doc-filler")
```

### 5.7.1 Runtime 与 Atomic Agent 的关系：基础设施 vs 领域封装

> **核心结论**: Runtime 是通用代码执行引擎，Atomic Agent 是封装了领域知识的业务单元。二者是上下游关系，不是替代关系。

**Python Runtime 做什么：**

```
Runtime = IPythonExecutor + SecurityChecker + Namespace 管理
输入: 一段 Python 代码 → 输出: ExecutionResult
能力: 执行代码、注入变量/函数/类型、AST 安全检查
局限: 无领域知识、无结构化流程、无外部接口、不可分发
```

**Atomic Agent 在 Runtime 之上额外做了什么：**

| 能力 | Runtime 有 | Atomic Agent 额外提供 |
|------|-----------|---------------------|
| 领域知识（规则库、方法论） | 无 | 有（如 code-reviewer 的多语言安全模式数据库） |
| 结构化流程（多阶段管道） | 无 | 有（如 analyze→check→review 三阶段） |
| 可分发（Git 安装/版本管理） | 无 | 有（agent-nexus install） |
| 可发现（SKILL.md 声明能力） | 无 | 有（index.yaml 索引） |
| 可编排（Composite Agent DAG） | 无 | 有（blocked_by 依赖图） |
| MCP Server 暴露（外部框架调用） | 无 | 有（FastMCP per Agent） |
| 独立测试套件 | 无 | 有（每个 Agent 有 tests/） |
| 可进化（FIX/DERIVED/CAPTURED） | 无 | 有（自进化引擎） |

**类比**：Runtime 是 Python 解释器，Atomic Agent 是 pip 包。你能用解释器写任何逻辑，但 pip 包封装了领域最佳实践。

**什么时候"用 Runtime 就够了"**：

- 一次性脚本任务（不重复使用）
- 简单的 LLM prompt + API 调用（领域逻辑薄）
- 探索性任务（不需要分发和版本管理）

**什么时候值得封装为 Atomic Agent**：

- 有结构化多阶段流程（如代码审查的三阶段管道）
- 有领域规则库（如安全模式数据库、WCAG 标准库）
- 需要独立分发和版本管理
- 需要被 Composite Agent 编排
- 需要暴露给外部框架调用

### 5.8 Runtime Context Tiered Loading

> **实现模块**: `src/agent_nexus/platform/runtime/describer.py` — `TieredRuntimeDescriber`

> **参考来源**: nanobot Token 优化方案 — Type Schema 是 Runtime context 中的 token 大头

#### 5.8.1 Token 开销分析

`describe_types(include_schema=True)` 注入完整 JSON Schema（包含所有字段名、类型、描述、默认值、约束），是 Runtime Context 的 Token 消耗主要来源：

| Agent 复杂度 | Variables | Functions | Types | Token 估算 |
|-------------|-----------|-----------|-------|-----------|
| 轻量 (3+3+2) | ~150 | ~200 | ~500-2,000 | **~850-2,700** |
| 中等 (10+8+5) | ~300 | ~400 | ~1,500-6,000 | **~2,500-8,000** |
| 重量 (20+15+10) | ~500 | ~600 | ~3,000-12,000 | **~5,000-15,000** |

**Type Schema 是唯一需要分层的 Runtime 组件。** Variables 和 Functions 的 describe 仅含 name + description + 签名，开销可控。

#### 5.8.2 分层策略

| 组件 | L0 (每轮) | L1 (首轮) | L2 (按需) |
|------|----------|----------|----------|
| `describe_variables()` | 全量（仅 name + description） | — | Variable 当前值（`l3_value()`） |
| `describe_functions()` | — | 全量（name + description + 签名） | — |
| `describe_types(level="names")` | Type 名称 + 一句话描述 | 与当前任务相关的 Type Schema | 完整 JSON Schema（所有 Types） |

```python
class TieredRuntimeDescriber:
    """Runtime Context 四层描述"""

    def __init__(self, runtime: PythonRuntime): ...

    def l0_context(self) -> str:
        """L0 身份核心（每轮注入, ~100 tokens）"""
        # Variable names + descriptions + Type names
        ...

    def l1_context(self) -> str:
        """L1 执行上下文（首轮注入, ~500 tokens）"""
        # Function signatures + relevant Type schemas
        ...

    def l2_context(self) -> str:
        """L2 按需获取完整 Type Schema"""
        # Full Type JSON Schema + Memory/history
        ...

    def l3_value(self, var_name: str) -> str:
        """L3 运行时获取 Variable 当前值"""
        ...
```

#### 5.8.3 跨 Agent 数据传递优化

同进程内 `runtime.retrieve()` 是零 token 的 Python 对象引用。但跨进程（Composite Agent 中的多个 Atomic Agent）数据必须序列化，采用"引用传递 + 按需加载"策略：

| 传递方式 | Token 开销 | 适用场景 |
|---------|-----------|---------|
| 同进程 `retrieve()` | **0** (对象引用) | Agent 内部跨轮 |
| Mailbox 引用传递 | **~50** (ID + 摘要) | 跨 Agent 通信 |
| Mailbox 全量传递 | 取决于数据大小 | 仅小型数据 |

```python
# Mailbox 引用消息格式（推荐）
{
    "type": "data_reference",
    "ref_id": "var://template_mapping_abc123",
    "summary": "TemplateMapping: 15 fields mapped",
    "agent_source": "requirements-analyzer",
    "size_hint": "~2KB"
}

# 接收方按需加载
async def handle_data_reference(ref: DataReference) -> Any:
    """通过 runtime.retrieve() 按需获取完整数据"""
    return await source_agent.runtime.retrieve(ref.ref_id)
```

---
