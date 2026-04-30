# Agent 能力契约驱动测试方案设计

> 创建: 2026-04-29 | 状态: DRAFT

## 1. 目标

为 Agent Nexus 的 Python 平台建立分层能力测试体系，覆盖：

- **三个能力层**: Atomic Agent（11 个）、Composite Agent（5 个）、Agency Pipeline（LLM 专家编排）
- **两个运行模式**: CLI 模式（agent-nexus CLI / Agent CLI）、API 模式（真实 LLM API 调用）
- **两个测试层级**: CI 快速门禁（mock，<5min）、Release 验收（真实 LLM，10-30min）

## 2. 架构: 契约驱动（Contract-Driven）

```
Contract(what to expect)
    │
    ▼
Provider(how to invoke)  ──▶  Agent 执行  ──▶  Raw Output
    │                                              │
    │◀────────── Validator(verify output) ◀─────────┘
```

- **Contract** — 纯数据定义，描述输入 schema、输出 schema、质量阈值
- **Provider** — 驱动方式，CLI（子进程）或 API（LLMClient）
- **Validator** — 验证逻辑，CI 层验结构、Release 层加语义
- **Test 文件** — 组装层，只负责 contract → provider → validator 串联

## 3. 目录结构

```
tests/
  capabilities/                    ← 能力测试根目录
    conftest.py                    ← 共享 fixtures + marker 注入
    contracts/                     ← 契约定义（纯数据）
      __init__.py
      schema.py                    ← 基础类型: CapabilityContract, InputSpec, OutputSpec, QualityThresholds
      atomic.py                    ← 11 个 Atomic Agent 契约
      composite.py                 ← 5 个 Composite Agent 契约
      agency.py                    ← Agency Pipeline 契约
    providers/                     ← Provider 层
      __init__.py
      base.py                      ← CapabilityProvider Protocol + ProviderResult dataclass
      cli_provider.py              ← CLI 模式: subprocess 调用 Agent
      api_provider.py              ← API 模式: LLMClient 真实调用
    validators/                    ← 验证器
      __init__.py
      structure.py                 ← StructureValidator: 字段存在、类型、约束
      semantic.py                  ← SemanticValidator: 关键词、相关性、完整性
      orchestration.py             ← OrchestrationValidator: DAG 拓扑、并行度、artifacts
    test_atomic_cli.py             ← Atomic × CLI
    test_atomic_api.py             ← Atomic × API
    test_composite_cli.py          ← Composite × CLI
    test_composite_api.py          ← Composite × API
    test_agency_cli.py             ← Agency × CLI
    test_agency_api.py             ← Agency × API
```

## 4. 契约 Schema

### 4.1 基础类型

```python
from dataclasses import dataclass, field
from typing import Any, Literal

@dataclass
class InputSpec:
    type: str                    # "str", "dict", "list[str]", "Path"
    description: str
    examples: list[str]          # 供测试用例使用
    required: bool = True

@dataclass
class OutputSpec:
    type: str
    required: bool = True
    min_length: int | None = None
    allowed_values: list[str] | None = None

@dataclass
class QualityThresholds:
    min_output_length: int = 50
    max_output_length: int = 50000
    required_keywords: list[str] = field(default_factory=list)
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
```

### 4.2 各层契约侧重

| 层级 | 输入契约重点 | 输出契约重点 | 独有验证 |
|------|-------------|-------------|---------|
| Atomic | 单一 task 字符串 + 可选 context dict | 结构化 JSON（每个 Agent 自有 schema） | 工具接口正确性 |
| Composite | composition.toml 中的输入字段 | GateResult / 聚合报告结构 | DAG 拓扑执行顺序、并行度 |
| Agency | 用户任务描述字符串 | CompositionReport（plan → artifacts → integration → qa） | 专家选择合理性、QA 分数 |

### 4.3 契约示例

**Atomic — security-scanner:**

```python
SECURITY_SCANNER_CONTRACT = CapabilityContract(
    agent_name="security-scanner",
    agent_type="atomic",
    description="代码安全漏洞扫描",
    required_inputs={
        "code_path": InputSpec(type="str", description="待扫描代码路径",
                               examples=["src/agent_nexus/"]),
    },
    optional_inputs={
        "config": InputSpec(type="dict", description="扫描配置",
                            examples=['{"severity_threshold": "high"}'],
                            required=False),
    },
    output_schema={
        "vulnerabilities": OutputSpec(type="list", min_length=0),
        "risk_score": OutputSpec(type="float"),
        "scan_summary": OutputSpec(type="str", min_length=10),
    },
    output_format="json",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["vulnerability", "scan"],
        score_threshold=0.7,
    ),
)
```

**Agency — full pipeline:**

```python
AGENCY_PIPELINE_CONTRACT = CapabilityContract(
    agent_name="agency-pipeline",
    agent_type="agency",
    description="LLM 驱动的专家编排流水线",
    required_inputs={
        "task": InputSpec(type="str", description="用户任务",
                          examples=["分析这段代码的安全性和质量"]),
    },
    optional_inputs={
        "vendor_path": InputSpec(type="str", required=False, ...),
        "allowlist": InputSpec(type="str", required=False, ...),
    },
    output_schema={
        "plan": OutputSpec(type="dict"),
        "artifacts": OutputSpec(type="list"),
        "integration": OutputSpec(type="str"),
        "qa_score": OutputSpec(type="float"),
    },
    output_format="json",
    quality_thresholds=QualityThresholds(
        min_output_length=200,
        required_keywords=["recommendation", "analysis"],
        score_threshold=0.6,
    ),
)
```

## 5. Provider 层

### 5.1 接口

```python
from typing import Protocol

class CapabilityProvider(Protocol):
    async def invoke(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any],
    ) -> ProviderResult: ...

@dataclass
class ProviderResult:
    success: bool
    raw_output: Any
    exit_code: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 5.2 CLI Provider

通过子进程调用 Agent CLI，捕获 stdout/stderr。

| 能力层 | 调用方式 |
|--------|---------|
| Atomic | `uv run python -m agent_<name>.main` 或 local_adapter stdin/stdout JSON-lines |
| Composite | `AGENT_MODE=cli python -m agent_<name> run --<params>` |
| Agency | `uv run python -m agent_nexus.platform.agency.cli run-composition --task "..."` |

设计决策: 用 subprocess 而非 in-process 调用，真实隔离，验证 Agent 作为独立进程的正确性。

### 5.3 API Provider

通过 `LLMClient` 调用真实 LLM API（OpenAI / Anthropic）。

设计决策: 复用已有 LLMClient 的 streaming/config 体系，不重新造轮子。使用 haiku 等低成本模型做常规验证，sonnet 做关键场景。

## 6. Validator 层

### 6.1 StructureValidator（CI 层核心）

纯结构断言，无 LLM 依赖:

1. 输出可解析（JSON / text / structured）
2. 必需字段全部存在
3. 字段类型正确
4. min_length / allowed_values 约束满足

### 6.2 SemanticValidator（Release 层追加）

LLM 辅助质量判断:

1. required_keywords 出现率
2. 输出与任务的相关性
3. 输出完整性（是否截断、格式正确）

### 6.3 OrchestrationValidator（Composite / Agency 专用）

1. DAG 拓扑执行顺序正确（blocked_by 关系满足）
2. 并行任务确实并行执行（时间戳验证）
3. Agency: 专家选择合理性、artifacts 完整性

### 6.4 ValidationResult

```python
@dataclass
class ValidationResult:
    passed: bool
    score: float                     # 0.0 ~ 1.0
    failures: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
```

## 7. 测试分层

| 层级 | Marker | 运行条件 | 单测超时 | 验证深度 |
|------|--------|---------|---------|---------|
| CI Gate | `@pytest.mark.capability` | 默认跑 | 5s | StructureValidator |
| Release | `@pytest.mark.capability_release` | `--run-release` | 60s | Structure + Semantic + Orchestration |
| API 真实调用 | `@pytest.mark.requires_api` | `--run-api` + API key env | 30s | Structure + Semantic |

```bash
# CI 快速门禁（~2min）
uv run pytest tests/capabilities/ -m "capability and not capability_release" -v

# 发版前验收 — CLI 模式（~10min）
uv run pytest tests/capabilities/ -m "capability_release" --run-release -v

# 发版前验收 — API 模式（~15min，需 API key）
uv run pytest tests/capabilities/ -m "capability_release" --run-release --run-api -v

# 全量
uv run pytest tests/capabilities/ -v --run-release --run-api
```

## 8. 测试文件示例

### 8.1 test_atomic_cli.py

```python
class TestAtomicCLI:
    """Atomic Agent × CLI 模式 — CI 层结构验证。"""

    @pytest.fixture(params=ALL_ATOMIC_CONTRACTS)
    def contract(self, request):
        return request.param

    @pytest.fixture
    def provider(self):
        return CLIProvider(timeout=5.0)

    def test_cli_returns_valid_structure(self, contract, provider):
        """每个 Atomic Agent CLI 调用返回结构合规的输出。"""
        inputs = build_test_inputs(contract)
        result = provider.invoke_sync(contract, inputs)
        validation = StructureValidator().validate(contract, result)
        assert validation.passed, validation.failures
```

### 8.2 test_atomic_api.py

```python
@pytest.mark.requires_api
class TestAtomicAPI:
    """Atomic Agent × API 模式 — Release 层语义验证。"""

    @pytest.fixture(params=KEY_ATOMIC_CONTRACTS)
    def contract(self, request):
        return request.param

    @pytest.fixture
    def provider(self):
        return APIProvider(model="anthropic:claude-haiku-4-5-20251001")

    async def test_api_output_quality(self, contract, provider):
        inputs = build_test_inputs(contract)
        result = await provider.invoke(contract, inputs)
        struct = StructureValidator().validate(contract, result)
        assert struct.passed
        semantic = await SemanticValidator().validate(contract, result)
        assert semantic.score >= contract.quality_thresholds.score_threshold
```

### 8.3 test_composite_cli.py

```python
class TestCompositeCLI:
    """Composite Agent × CLI 模式 — DAG 编排验证。"""

    @pytest.fixture(params=ALL_COMPOSITE_CONTRACTS)
    def contract(self, request):
        return request.param

    def test_composite_dag_execution_order(self, contract, provider):
        """Composite Agent 的 DAG 执行顺序满足 blocked_by 约束。"""
        inputs = build_test_inputs(contract)
        result = provider.invoke_sync(contract, inputs)
        orch = OrchestrationValidator().validate(contract, result)
        assert orch.passed, orch.failures
```

### 8.4 test_agency_api.py

```python
@pytest.mark.requires_api
@pytest.mark.capability_release
class TestAgencyAPI:
    """Agency Pipeline × API 模式 — 真实 LLM 端到端验证。"""

    @pytest.fixture
    def provider(self):
        return APIProvider(model="anthropic:claude-sonnet-4-20250514")

    async def test_agency_full_pipeline(self, provider):
        contract = AGENCY_PIPELINE_CONTRACT
        inputs = {"task": "分析 agent-nexus 项目的代码安全性"}
        result = await provider.invoke(contract, inputs)
        struct = StructureValidator().validate(contract, result)
        assert struct.passed
        semantic = await SemanticValidator().validate(contract, result)
        assert semantic.score >= 0.6
```

## 9. conftest.py 设计

```python
# tests/capabilities/conftest.py

import pytest

def pytest_collection_modifyitems(items):
    """为 capabilities/ 下所有测试自动打 marker。"""
    for item in items:
        item.add_marker(pytest.mark.capability)

def pytest_addoption(parser):
    parser.addoption("--run-release", action="store_true", help="Run release acceptance tests")
    parser.addoption("--run-api", action="store_true", help="Run real API call tests")

def pytest_runtest_setup(item):
    if "capability_release" in [m.name for m in item.iter_markers()]:
        if not item.config.getoption("--run-release"):
            pytest.skip("release tests require --run-release")
    if "requires_api" in [m.name for m in item.iter_markers()]:
        if not item.config.getoption("--run-api"):
            pytest.skip("API tests require --run-api")
```

## 10. 测试用例估算

| 测试文件 | CI 层（结构） | Release 层（语义） | 总计 |
|---------|-------------|-----------------|------|
| test_atomic_cli.py | 11 | 11 | 22 |
| test_atomic_api.py | — | 6（关键 Agent） | 6 |
| test_composite_cli.py | 5 | 5 | 10 |
| test_composite_api.py | — | 3（关键场景） | 3 |
| test_agency_cli.py | 3 | 3 | 6 |
| test_agency_api.py | — | 2（端到端） | 2 |
| **合计** | **19** | **30** | **49** |

CI 层 ~19 个测试，预计 <2min；Release 层 ~30 个测试，预计 ~15min。

## 11. 实施优先级

| Phase | 内容 | 预计工时 |
|-------|------|---------|
| P0 | schema.py + contracts/atomic.py + StructureValidator + CLI Provider + test_atomic_cli.py | 2h |
| P1 | contracts/composite.py + OrchestrationValidator + test_composite_cli.py | 1.5h |
| P2 | contracts/agency.py + test_agency_cli.py | 1h |
| P3 | API Provider + SemanticValidator + test_atomic_api.py + test_composite_api.py | 2h |
| P4 | test_agency_api.py + Release 层完整验证 | 1h |

## 12. 与现有测试的关系

- **不替代**现有 unit/integration/e2e 测试 — 那些验证模块内部逻辑
- **补充**一层面向用户的能力验证 — 验证 Agent 作为黑盒的端到端行为
- 复用现有 conftest.py 的 fixture 模式（session-scoped executor 等）
- 遵循现有 marker 体系（unit/integration/e2e）的约定
