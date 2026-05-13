# Agent 能力契约测试

> 最后更新: 2026-04-30 | 测试总数: **80** | 目录: `tests/capabilities/`

---

## 目录

- [概述](#概述)
- [架构: 契约驱动](#架构-契约驱动)
- [目录结构](#目录结构)
- [契约 Schema](#契约-schema)
- [Provider 层](#provider-层)
- [Validator 层](#validator-层)
- [测试分层](#测试分层)
- [运行指南](#运行指南)
- [新增 Agent 测试](#新增-agent-测试)

---

## 概述

能力测试（Capability Tests）验证 Agent 作为**黑盒**的端到端行为，与现有 unit/integration/e2e 测试互补：

| 测试类型 | 验证目标 | 运行频率 |
|---------|---------|---------|
| Unit / Integration / E2E | 模块内部逻辑正确性 | 每次 CI |
| **Capability** | Agent 作为独立实体的输入/输出契约 | 每次 CI（结构）+ 发版前（语义） |

核心特点：

- **三层能力覆盖**: Atomic（20 个）、Composite（5 个）、Agency Pipeline（1 个）
- **两种运行模式**: CLI（subprocess local_adapter）、API（LLMClient 真实调用）
- **两级验证深度**: CI Gate（纯结构）和 Release（语义质量）

---

## 架构: 契约驱动

```
Contract (what to expect)
    │
    ▼
Provider (how to invoke)  ──▶  Agent 执行  ──▶  Raw Output
    │                                              │
    │◀────────── Validator (verify output) ◀─────────┘
```

| 层 | 职责 | 变化频率 |
|----|------|---------|
| **Contract** | 纯数据定义：输入 schema、输出 schema、质量阈值 | 新增 Agent 时 |
| **Provider** | 驱动方式：CLI（子进程）/ API（LLMClient） | 新增运行模式时 |
| **Validator** | 验证逻辑：结构 / 语义 / 编排 | 新增验证维度时 |
| **Test** | 组装层：contract → provider → validator | 不单独变化 |

每个维度独立变化，新增 Agent 只需加契约，新增运行模式只需加 Provider。

---

## 目录结构

```
tests/capabilities/
  conftest.py                    ← markers + CLI options
  contracts/
    schema.py                    ← 基础类型定义
    atomic.py                    ← 11 个 Atomic Agent 契约
    composite.py                 ← 5 个 Composite Agent 契约
    agency.py                    ← Agency Pipeline 契约
  providers/
    base.py                      ← ProviderResult + build_test_inputs()
    cli_provider.py              ← CLI 模式: subprocess local_adapter
    api_provider.py              ← API 模式: LLMClient 真实调用
  validators/
    structure.py                 ← 结构验证 (CI 层)
    semantic.py                  ← 语义验证 (Release 层)
    orchestration.py             ← 编排验证 (Composite/Agency)
  test_atomic_cli.py             ← Atomic × CLI (33 tests)
  test_atomic_api.py             ← Atomic × API (12 tests)
  test_composite_cli.py          ← Composite × CLI (15 tests)
  test_composite_api.py          ← Composite × API (15 tests)
  test_agency_cli.py             ← Agency × CLI (3 tests)
  test_agency_api.py             ← Agency × API (2 tests)
```

---

## 契约 Schema

### 基础类型

```python
@dataclass
class InputSpec:
    type: str                    # "str", "dict", "list", "int"
    description: str
    examples: list[str]          # 测试用例使用第一个 example
    required: bool = True

@dataclass
class OutputSpec:
    type: str                    # "str", "int", "float", "bool", "list", "dict"
    required: bool = True
    min_length: int | None = None
    allowed_values: list[str] | None = None

@dataclass
class QualityThresholds:
    min_output_length: int = 50
    max_output_length: int = 50000
    required_keywords: list[str] = []
    score_threshold: float = 0.6

@dataclass
class CapabilityContract:
    agent_name: str
    agent_type: Literal["atomic", "composite", "agency"]
    description: str
    required_inputs: dict[str, InputSpec]
    optional_inputs: dict[str, InputSpec]
    output_schema: dict[str, OutputSpec]
    output_format: Literal["json", "text", "structured"]
    quality_thresholds: QualityThresholds
    cli_method: str = "run"      # local_adapter 方法名
```

### 各层契约侧重

| 层级 | 输入契约重点 | 输出契约重点 | 独有验证 |
|------|-------------|-------------|---------|
| Atomic | 单一 task/path 字符串 + 可选 context dict | 结构化 JSON（每个 Agent 自有 schema） | 字段存在性 + 类型 |
| Composite | composition.toml 中的输入字段 | GateResult / 聚合报告 | DAG 拓扑、并行度、checks 完整性 |
| Agency | 用户任务描述字符串 | plan → artifacts → integration → qa_score | 专家选择、QA 分数 |

---

## Provider 层

### CLIProvider

通过子进程调用 Agent 的 `local_adapter`（stdin/stdout JSON-lines 协议）：

```python
# 发送
{"method": "scan_code", "params": {"file_path": "src/..."}}
# 接收
{"status": "ok", "result": {"findings": [...], "summary": "..."}}
```

- 自动设置 `AGENT_MODE=local` 环境变量
- 根据 `agent_type` 路由到 `agents/atomic/` 或 `agents/composite/`
- 支持同步（`invoke_sync`）和异步（`invoke`）两种调用方式
- 超时保护（默认 10s）

### APIProvider

通过 `LLMClient` 调用真实 LLM API：

- 复用已有 LLMClient 的 config/streaming 体系
- 根据契约自动生成 prompt（包含输出 schema 约束）
- 依赖 `--run-api` flag 才执行

---

## Validator 层

### StructureValidator（CI 层核心）

纯结构断言，无 LLM 依赖：

1. 输出可解析（JSON / text / structured）
2. 必需字段全部存在
3. 字段类型正确
4. `min_length` / `allowed_values` 约束满足

### SemanticValidator（Release 层追加）

关键词和质量判断：

1. `required_keywords` 出现率
2. 输出长度在 `[min_output_length, max_output_length]` 范围内
3. 综合评分 = (keyword_score + length_score) / 2

### OrchestrationValidator（Composite / Agency 专用）

编排行为验证：

- Composite: checks 列表非空、overall_passed 为 bool、gate_score 在 [0, 100]
- Agency: plan 存在、artifacts 非空、qa_score 达标

---

## 测试分层

| 层级 | Marker | 运行条件 | 单测超时 | 验证深度 |
|------|--------|---------|---------|---------|
| CI Gate | `@pytest.mark.capability` | 默认跑 | 5s | StructureValidator |
| Release | `@pytest.mark.capability_release` | `--run-release` | 60s | Structure + Semantic + Orchestration |
| API | `@pytest.mark.requires_api` | `--run-api` + API key | 30s | Structure + Semantic |

### 测试分布

| 测试文件 | CI 层 | Release 层 | 总计 |
|---------|------|-----------|------|
| test_atomic_cli.py | 33 | — | 33 |
| test_atomic_api.py | — | 12 | 12 |
| test_composite_cli.py | 15 | — | 15 |
| test_composite_api.py | — | 15 | 15 |
| test_agency_cli.py | 3 | — | 3 |
| test_agency_api.py | — | 2 | 2 |
| **合计** | **51** | **29** | **80** |

---

## 运行指南

```bash
# CI 快速门禁（结构验证，不调用 LLM）
uv run pytest tests/capabilities/ -v

# 发版前验收 — CLI 模式全部
uv run pytest tests/capabilities/ --run-release -v

# 发版前验收 — API 模式（需 API key）
uv run pytest tests/capabilities/ --run-release --run-api -v

# 仅运行特定 Agent 的测试
uv run pytest tests/capabilities/test_atomic_cli.py -k "security-scanner" -v

# 跳过能力测试（现有 unit/integration/e2e 不受影响）
uv run pytest tests/ --ignore=tests/capabilities/ -v
```

### 自定义 Marker 注册

已注册在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
markers = [
    # ... existing markers ...
    "capability: agent capability contract tests",
    "capability_release: release acceptance tests (requires --run-release)",
    "requires_api: real LLM API call tests (requires --run-api)",
]
```

---

## 新增 Agent 测试

新增 Agent 时只需两步：

### 1. 添加契约

在对应的 `contracts/` 文件中添加：

```python
# tests/capabilities/contracts/atomic.py
NEW_AGENT = CapabilityContract(
    agent_name="new-agent",
    agent_type="atomic",
    description="新 Agent 描述",
    required_inputs={
        "input_field": InputSpec(type="str", description="...", examples=["..."]),
    },
    optional_inputs={},
    output_schema={
        "result": OutputSpec(type="str", min_length=1),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["keyword1"],
        score_threshold=0.6,
    ),
    cli_method="handle_message",
)
```

然后将契约加入 `ALL_ATOMIC_CONTRACTS` 或 `ALL_COMPOSITE_CONTRACTS` 列表。由于测试文件通过 `@pytest.fixture(params=ALL_*)` 参数化，**无需修改测试文件**，新 Agent 的测试会自动被收集。

### 2. 验证

```bash
# 验证新契约导入正确
uv run python -c "from tests.capabilities.contracts.atomic import ALL_ATOMIC_CONTRACTS; print(len(ALL_ATOMIC_CONTRACTS))"

# 运行新 Agent 的测试
uv run pytest tests/capabilities/test_atomic_cli.py -k "new-agent" -v
```
