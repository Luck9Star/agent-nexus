# Agent 能力契约驱动测试 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Agent Nexus 建立 contract-driven 能力测试体系，覆盖 Atomic/Composite/Agency 三层能力 x CLI/API 两种模式 x CI/Release 两级验证。

**Architecture:** 契约（纯数据定义）-> Provider（CLI subprocess / LLMClient API）-> Validator（结构/语义/编排验证）-> Test（组装层）。每个维度独立变化，新增 Agent 只加契约，新增模式只加 Provider。

**Tech Stack:** pytest, pytest-asyncio, asyncio.subprocess, LLMClient（已有）, dataclass

**Spec:** `docs/superpowers/specs/2026-04-29-agent-capability-test-plan-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `tests/capabilities/__init__.py` | Package marker |
| Create | `tests/capabilities/conftest.py` | Markers, CLI options, shared fixtures |
| Create | `tests/capabilities/contracts/__init__.py` | Package marker |
| Create | `tests/capabilities/contracts/schema.py` | CapabilityContract, InputSpec, OutputSpec, QualityThresholds, ValidationResult |
| Create | `tests/capabilities/contracts/atomic.py` | 11 个 Atomic Agent 契约定义 + ALL_ATOMIC_CONTRACTS 列表 |
| Create | `tests/capabilities/contracts/composite.py` | 5 个 Composite Agent 契约定义 + ALL_COMPOSITE_CONTRACTS 列表 |
| Create | `tests/capabilities/contracts/agency.py` | Agency Pipeline 契约定义 |
| Create | `tests/capabilities/providers/__init__.py` | Package marker |
| Create | `tests/capabilities/providers/base.py` | ProviderResult dataclass, build_test_inputs() |
| Create | `tests/capabilities/providers/cli_provider.py` | CLIProvider — subprocess 调用 Agent |
| Create | `tests/capabilities/providers/api_provider.py` | APIProvider — LLMClient 真实调用 |
| Create | `tests/capabilities/validators/__init__.py` | Package marker |
| Create | `tests/capabilities/validators/structure.py` | StructureValidator |
| Create | `tests/capabilities/validators/semantic.py` | SemanticValidator |
| Create | `tests/capabilities/validators/orchestration.py` | OrchestrationValidator |
| Create | `tests/capabilities/test_atomic_cli.py` | Atomic x CLI 测试 |
| Create | `tests/capabilities/test_atomic_api.py` | Atomic x API 测试 |
| Create | `tests/capabilities/test_composite_cli.py` | Composite x CLI 测试 |
| Create | `tests/capabilities/test_composite_api.py` | Composite x API 测试 |
| Create | `tests/capabilities/test_agency_cli.py` | Agency x CLI 测试 |
| Create | `tests/capabilities/test_agency_api.py` | Agency x API 测试 |

---

## Task 1: Schema 基础类型 + conftest

**Files:**
- Create: `tests/capabilities/__init__.py`
- Create: `tests/capabilities/conftest.py`
- Create: `tests/capabilities/contracts/__init__.py`
- Create: `tests/capabilities/contracts/schema.py`

- [ ] **Step 1: 创建 package 结构和 schema 类型**

`tests/capabilities/__init__.py`:
```python
```

`tests/capabilities/contracts/__init__.py`:
```python
```

`tests/capabilities/contracts/schema.py`:
```python
"""Contract schema types for capability-driven testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class InputSpec:
    type: str
    description: str
    examples: list[str]
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
    cli_method: str = "run"
    cli_params_mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class ValidationResult:
    passed: bool
    score: float
    failures: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 2: 创建 conftest.py — markers + CLI options**

`tests/capabilities/conftest.py`:
```python
"""Capability test configuration — markers and CLI options."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.capability)


def pytest_addoption(parser):
    parser.addoption(
        "--run-release",
        action="store_true",
        default=False,
        help="Run release acceptance tests",
    )
    parser.addoption(
        "--run-api",
        action="store_true",
        default=False,
        help="Run real API call tests",
    )


def pytest_runtest_setup(item):
    markers = [m.name for m in item.iter_markers()]
    if "capability_release" in markers and not item.config.getoption("--run-release"):
        pytest.skip("release tests require --run-release")
    if "requires_api" in markers and not item.config.getoption("--run-api"):
        pytest.skip("API tests require --run-api")
```

- [ ] **Step 3: 验证 schema 可导入**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run python -c "from tests.capabilities.contracts.schema import CapabilityContract, InputSpec, OutputSpec, QualityThresholds, ValidationResult; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add tests/capabilities/
git commit -m "feat(test): add capability test schema types and conftest markers"
```

---

## Task 2: Provider 基础类型

**Files:**
- Create: `tests/capabilities/providers/__init__.py`
- Create: `tests/capabilities/providers/base.py`

- [ ] **Step 1: 创建 Provider 基础类型**

`tests/capabilities/providers/__init__.py`:
```python
```

`tests/capabilities/providers/base.py`:
```python
"""Provider base types for capability testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.capabilities.contracts.schema import CapabilityContract


@dataclass
class ProviderResult:
    success: bool
    raw_output: Any
    exit_code: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def build_test_inputs(contract: CapabilityContract) -> dict[str, Any]:
    """从契约的 InputSpec.examples 构建测试输入。"""
    inputs: dict[str, Any] = {}
    for name, spec in contract.required_inputs.items():
        if spec.examples:
            inputs[name] = spec.examples[0]
    return inputs
```

- [ ] **Step 2: 验证导入**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run python -c "from tests.capabilities.providers.base import ProviderResult, build_test_inputs; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/capabilities/providers/
git commit -m "feat(test): add provider base types (ProviderResult, build_test_inputs)"
```

---

## Task 3: StructureValidator

**Files:**
- Create: `tests/capabilities/validators/__init__.py`
- Create: `tests/capabilities/validators/structure.py`

- [ ] **Step 1: 创建 StructureValidator**

`tests/capabilities/validators/__init__.py`:
```python
```

`tests/capabilities/validators/structure.py`:
```python
"""StructureValidator — CI layer, pure structure assertions."""

from __future__ import annotations

import json
from typing import Any

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    OutputSpec,
    ValidationResult,
)


class StructureValidator:
    """Validate Agent output structure against contract output_schema."""

    def validate(
        self,
        contract: CapabilityContract,
        raw_output: Any,
    ) -> ValidationResult:
        failures: list[str] = []

        parsed = self._parse_output(raw_output, contract.output_format)
        if parsed is None:
            return ValidationResult(
                passed=False,
                score=0.0,
                failures=["Output is not parseable"],
            )

        if contract.output_format == "json" and isinstance(parsed, dict):
            self._validate_json_fields(parsed, contract.output_schema, failures)
        elif contract.output_format == "text" and isinstance(parsed, str):
            self._validate_text_length(parsed, contract.quality_thresholds, failures)
        elif contract.output_format == "structured":
            self._validate_json_fields(
                parsed if isinstance(parsed, dict) else {},
                contract.output_schema,
                failures,
            )

        score = 1.0 - (len(failures) / max(len(contract.output_schema), 1))
        return ValidationResult(
            passed=len(failures) == 0,
            score=max(0.0, score),
            failures=failures,
            details={"parsed_type": type(parsed).__name__},
        )

    def _parse_output(self, raw: Any, fmt: str) -> Any:
        if isinstance(raw, str):
            stripped = raw.strip()
            if fmt == "json":
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    return None
            return stripped
        return raw

    def _validate_json_fields(
        self,
        data: dict,
        schema: dict[str, OutputSpec],
        failures: list[str],
    ) -> None:
        for field_name, spec in schema.items():
            if field_name not in data:
                if spec.required:
                    failures.append(f"Missing required field: {field_name}")
                continue
            value = data[field_name]
            if not self._check_type(value, spec.type):
                failures.append(
                    f"Field '{field_name}' has wrong type: "
                    f"expected {spec.type}, got {type(value).__name__}"
                )
            if spec.min_length is not None and isinstance(value, (str, list)):
                if len(value) < spec.min_length:
                    failures.append(
                        f"Field '{field_name}' length {len(value)} "
                        f"below minimum {spec.min_length}"
                    )
            if spec.allowed_values and value not in spec.allowed_values:
                failures.append(
                    f"Field '{field_name}' value '{value}' "
                    f"not in allowed values: {spec.allowed_values}"
                )

    def _validate_text_length(
        self,
        text: str,
        thresholds: Any,
        failures: list[str],
    ) -> None:
        if len(text) < thresholds.min_output_length:
            failures.append(
                f"Output length {len(text)} below minimum {thresholds.min_output_length}"
            )
        if len(text) > thresholds.max_output_length:
            failures.append(
                f"Output length {len(text)} exceeds maximum {thresholds.max_output_length}"
            )

    @staticmethod
    def _check_type(value: Any, expected: str) -> bool:
        type_map = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
            "list": list,
            "dict": dict,
        }
        expected_type = type_map.get(expected)
        if expected_type is None:
            return True
        return isinstance(value, expected_type)
```

- [ ] **Step 2: 验证导入**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run python -c "from tests.capabilities.validators.structure import StructureValidator; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/capabilities/validators/
git commit -m "feat(test): add StructureValidator for CI layer contract validation"
```

---

## Task 4: Atomic Agent 契约定义

**Files:**
- Create: `tests/capabilities/contracts/atomic.py`

- [ ] **Step 1: 定义 11 个 Atomic Agent 契约**

`tests/capabilities/contracts/atomic.py`:
```python
"""Atomic Agent capability contracts."""

from __future__ import annotations

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    InputSpec,
    OutputSpec,
    QualityThresholds,
)

SECURITY_SCANNER = CapabilityContract(
    agent_name="security-scanner",
    agent_type="atomic",
    description="代码安全漏洞扫描",
    required_inputs={
        "file_path": InputSpec(
            type="str",
            description="待扫描文件路径",
            examples=["src/agent_nexus/platform/agency/llm_client.py"],
        ),
    },
    optional_inputs={
        "language": InputSpec(
            type="str",
            description="编程语言",
            examples=["python"],
            required=False,
        ),
    },
    output_schema={
        "findings": OutputSpec(type="list", min_length=0),
        "summary": OutputSpec(type="str", min_length=5),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["finding", "vulnerability"],
        score_threshold=0.7,
    ),
    cli_method="scan_code",
)

CODE_REVIEWER = CapabilityContract(
    agent_name="code-reviewer",
    agent_type="atomic",
    description="代码质量审查",
    required_inputs={
        "file_path": InputSpec(
            type="str",
            description="待审查代码路径",
            examples=["src/agent_nexus/platform/agency/executor.py"],
        ),
    },
    optional_inputs={
        "language": InputSpec(
            type="str",
            description="编程语言",
            examples=["python"],
            required=False,
        ),
    },
    output_schema={
        "issues": OutputSpec(type="list", min_length=0),
        "summary": OutputSpec(type="str", min_length=5),
        "overall_score": OutputSpec(type="float"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["issue", "quality"],
        score_threshold=0.7,
    ),
    cli_method="analyze",
)

ACCESSIBILITY_AUDITOR = CapabilityContract(
    agent_name="accessibility-auditor",
    agent_type="atomic",
    description="WCAG 2.2 AA 无障碍审计",
    required_inputs={
        "content": InputSpec(
            type="str",
            description="待审计的 HTML 内容",
            examples=["<html><body><h1>Hello</h1></body></html>"],
        ),
    },
    optional_inputs={
        "content_type": InputSpec(
            type="str",
            description="内容类型",
            examples=["html"],
            required=False,
        ),
    },
    output_schema={
        "issues": OutputSpec(type="list", min_length=0),
        "score": OutputSpec(type="float"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["accessibility", "wcag"],
        score_threshold=0.6,
    ),
    cli_method="audit",
)

API_DOC_GENERATOR = CapabilityContract(
    agent_name="api-doc-generator",
    agent_type="atomic",
    description="API 文档生成",
    required_inputs={
        "code_path": InputSpec(
            type="str",
            description="API 源码路径",
            examples=["src/agent_nexus/platform/agency/"],
        ),
    },
    optional_inputs={},
    output_schema={
        "endpoints": OutputSpec(type="list", min_length=0),
        "schema": OutputSpec(type="dict"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["endpoint", "api"],
        score_threshold=0.6,
    ),
    cli_method="extract_endpoints",
)

CONTRACT_ANALYZER = CapabilityContract(
    agent_name="contract-analyzer",
    agent_type="atomic",
    description="合同条款分析",
    required_inputs={
        "contract_text": InputSpec(
            type="str",
            description="合同文本内容",
            examples=["This agreement is between Party A and Party B..."],
        ),
    },
    optional_inputs={},
    output_schema={
        "clauses": OutputSpec(type="list", min_length=0),
        "risks": OutputSpec(type="list", min_length=0),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["clause", "risk"],
        score_threshold=0.6,
    ),
    cli_method="extract_clauses",
)

DOC_FILLER = CapabilityContract(
    agent_name="doc-filler",
    agent_type="atomic",
    description="文档模板填充",
    required_inputs={
        "template": InputSpec(
            type="str",
            description="文档模板路径或内容",
            examples=["# Project\n## Overview\n{{overview}}"],
        ),
    },
    optional_inputs={
        "data": InputSpec(
            type="dict",
            description="填充数据",
            examples=['{"overview": "A test project"}'],
            required=False,
        ),
    },
    output_schema={
        "filled_content": OutputSpec(type="str", min_length=10),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=30,
        required_keywords=[],
        score_threshold=0.5,
    ),
    cli_method="fill",
)

LOCALIZATION_SPECIALIST = CapabilityContract(
    agent_name="localization-specialist",
    agent_type="atomic",
    description="本地化翻译专家",
    required_inputs={
        "text": InputSpec(
            type="str",
            description="待本地化文本",
            examples=["Welcome to our platform"],
        ),
    },
    optional_inputs={
        "target_locale": InputSpec(
            type="str",
            description="目标语言",
            examples=["zh-CN"],
            required=False,
        ),
    },
    output_schema={
        "translated": OutputSpec(type="str", min_length=1),
        "locale": OutputSpec(type="str"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=10,
        required_keywords=[],
        score_threshold=0.6,
    ),
    cli_method="translate",
)

MARKET_INTELLIGENCE = CapabilityContract(
    agent_name="market-intelligence-analyst",
    agent_type="atomic",
    description="市场情报分析",
    required_inputs={
        "topic": InputSpec(
            type="str",
            description="分析主题",
            examples=["AI Agent market trend analysis"],
        ),
    },
    optional_inputs={},
    output_schema={
        "insights": OutputSpec(type="list", min_length=0),
        "summary": OutputSpec(type="str", min_length=10),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=["market", "trend"],
        score_threshold=0.6,
    ),
    cli_method="analyze_market",
)

REQUIREMENTS_ANALYZER = CapabilityContract(
    agent_name="requirements-analyzer",
    agent_type="atomic",
    description="需求分析",
    required_inputs={
        "requirements_text": InputSpec(
            type="str",
            description="需求描述",
            examples=["The system shall support user login, registration, and password recovery"],
        ),
    },
    optional_inputs={},
    output_schema={
        "requirements": OutputSpec(type="list", min_length=0),
        "questions": OutputSpec(type="list", min_length=0),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["requirement"],
        score_threshold=0.6,
    ),
    cli_method="analyze",
)

TEST_SUITE_GENERATOR = CapabilityContract(
    agent_name="test-suite-generator",
    agent_type="atomic",
    description="测试套件生成",
    required_inputs={
        "code_path": InputSpec(
            type="str",
            description="待测试代码路径",
            examples=["src/agent_nexus/platform/agency/llm_client.py"],
        ),
    },
    optional_inputs={},
    output_schema={
        "test_cases": OutputSpec(type="list", min_length=0),
        "coverage_estimate": OutputSpec(type="float", required=False),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["test"],
        score_threshold=0.6,
    ),
    cli_method="generate",
)

GOOD_SKILL = CapabilityContract(
    agent_name="good-skill",
    agent_type="atomic",
    description="自动晋升的通用技能 Agent",
    required_inputs={
        "task": InputSpec(
            type="str",
            description="任务描述",
            examples=["Execute a general task"],
        ),
    },
    optional_inputs={
        "context": InputSpec(
            type="dict",
            description="上下文",
            examples=['{"key": "value"}'],
            required=False,
        ),
    },
    output_schema={
        "output": OutputSpec(type="str", min_length=1),
    },
    output_format="json",
    quality_thresholds=QualityThresholds(
        min_output_length=10,
        required_keywords=[],
        score_threshold=0.5,
    ),
    cli_method="run",
)

ALL_ATOMIC_CONTRACTS: list[CapabilityContract] = [
    SECURITY_SCANNER,
    CODE_REVIEWER,
    ACCESSIBILITY_AUDITOR,
    API_DOC_GENERATOR,
    CONTRACT_ANALYZER,
    DOC_FILLER,
    LOCALIZATION_SPECIALIST,
    MARKET_INTELLIGENCE,
    REQUIREMENTS_ANALYZER,
    TEST_SUITE_GENERATOR,
    GOOD_SKILL,
]

KEY_ATOMIC_CONTRACTS: list[CapabilityContract] = [
    SECURITY_SCANNER,
    CODE_REVIEWER,
    ACCESSIBILITY_AUDITOR,
    REQUIREMENTS_ANALYZER,
]
```

- [ ] **Step 2: 验证所有契约可导入且数量正确**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run python -c "from tests.capabilities.contracts.atomic import ALL_ATOMIC_CONTRACTS, KEY_ATOMIC_CONTRACTS; print(f'ALL: {len(ALL_ATOMIC_CONTRACTS)}, KEY: {len(KEY_ATOMIC_CONTRACTS)}')"`
Expected: `ALL: 11, KEY: 4`

- [ ] **Step 3: Commit**

```bash
git add tests/capabilities/contracts/atomic.py
git commit -m "feat(test): add 11 atomic agent capability contracts"
```

---

## Task 5: CLI Provider

**Files:**
- Create: `tests/capabilities/providers/cli_provider.py`

- [ ] **Step 1: 实现 CLIProvider**

`tests/capabilities/providers/cli_provider.py`:
```python
"""CLIProvider — invoke agents via subprocess (local_adapter protocol)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.base import ProviderResult, build_test_inputs

_REPO_ROOT = __file__.resolve().parents[4]


def _agent_package_dir(agent_name: str) -> str:
    return str(_REPO_ROOT / "agents" / "atomic" / agent_name)


class CLIProvider:
    """Invoke agents through local_adapter stdin/stdout JSON-lines protocol."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def invoke_sync(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any] | None = None,
    ) -> ProviderResult:
        return asyncio.get_event_loop().run_until_complete(
            self.invoke(contract, inputs)
        )

    async def invoke(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any] | None = None,
    ) -> ProviderResult:
        if inputs is None:
            inputs = build_test_inputs(contract)

        message = json.dumps({
            "method": contract.cli_method,
            "params": inputs,
        }) + "\n"

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_ # NOSONAR
                "uv", "run", "python", "-m",
                f"agent_{contract.agent_name.replace('-', '_')}.main",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_agent_package_dir(contract.agent_name),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(message.encode()),
                timeout=self.timeout,
            )
            duration_ms = (time.monotonic() - start) * 1000

            if proc.returncode != 0:
                return ProviderResult(
                    success=False,
                    raw_output=None,
                    exit_code=proc.returncode,
                    duration_ms=duration_ms,
                    error=stderr_bytes.decode(errors="replace"),
                )

            response = json.loads(stdout_bytes.decode())
            if response.get("status") == "ok":
                return ProviderResult(
                    success=True,
                    raw_output=response.get("result"),
                    exit_code=0,
                    duration_ms=duration_ms,
                )
            return ProviderResult(
                success=False,
                raw_output=response,
                exit_code=0,
                duration_ms=duration_ms,
                error=response.get("error", "Unknown error from local_adapter"),
            )

        except asyncio.TimeoutError:
            return ProviderResult(
                success=False,
                raw_output=None,
                duration_ms=(time.monotonic() - start) * 1000,
                error=f"Timeout after {self.timeout}s",
            )
        except Exception as exc:
            return ProviderResult(
                success=False,
                raw_output=None,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
```

- [ ] **Step 2: 验证导入**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run python -c "from tests.capabilities.providers.cli_provider import CLIProvider; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/capabilities/providers/cli_provider.py
git commit -m "feat(test): add CLIProvider with local_adapter subprocess invocation"
```

---

## Task 6: test_atomic_cli.py — 第一个能力测试

**Files:**
- Create: `tests/capabilities/test_atomic_cli.py`

- [ ] **Step 1: 编写 Atomic x CLI 测试**

`tests/capabilities/test_atomic_cli.py`:
```python
"""Atomic Agent x CLI mode — structure validation via local_adapter."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.atomic import ALL_ATOMIC_CONTRACTS
from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.cli_provider import CLIProvider
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.validators.structure import StructureValidator


@pytest.fixture(params=ALL_ATOMIC_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def cli_provider():
    return CLIProvider(timeout=10.0)


@pytest.fixture
def validator():
    return StructureValidator()


class TestAtomicCLI:
    """Atomic Agent x CLI mode — CI layer structure validation."""

    def test_agent_local_adapter_responds(self, contract, cli_provider):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, (
            f"Agent '{contract.agent_name}' local_adapter failed: {result.error}"
        )

    def test_agent_output_has_required_fields(self, contract, cli_provider, validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = validator.validate(contract, result.raw_output)
        assert validation.passed, (
            f"Agent '{contract.agent_name}' validation failed: {validation.failures}"
        )

    def test_agent_output_type_correct(self, contract, cli_provider, validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = validator.validate(contract, result.raw_output)
        type_failures = [f for f in validation.failures if "wrong type" in f]
        assert len(type_failures) == 0, f"Type mismatches: {type_failures}"
```

- [ ] **Step 2: 运行测试验证结构正确**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run pytest tests/capabilities/test_atomic_cli.py -v --timeout=30 2>&1 | head -60`

- [ ] **Step 3: 根据失败结果调整契约或 provider，确保至少 70% 通过**

- [ ] **Step 4: Commit**

```bash
git add tests/capabilities/test_atomic_cli.py
git commit -m "feat(test): add atomic agent CLI capability tests"
```

---

## Task 7: Composite Agent 契约 + OrchestrationValidator

**Files:**
- Create: `tests/capabilities/contracts/composite.py`
- Create: `tests/capabilities/validators/orchestration.py`

- [ ] **Step 1: 定义 5 个 Composite Agent 契约**

`tests/capabilities/contracts/composite.py`:
```python
"""Composite Agent capability contracts."""

from __future__ import annotations

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    InputSpec,
    OutputSpec,
    QualityThresholds,
)

CICD_QUALITY_GATE = CapabilityContract(
    agent_name="cicd-quality-gate",
    agent_type="composite",
    description="CI/CD multi-model parallel quality gate",
    required_inputs={
        "code_path": InputSpec(
            type="str",
            description="Code path to check",
            examples=["src/agent_nexus/"],
        ),
    },
    optional_inputs={
        "config": InputSpec(
            type="dict",
            description="Gate config",
            examples=['{"security_threshold": 80, "review_threshold": 70}'],
            required=False,
        ),
    },
    output_schema={
        "checks": OutputSpec(type="list", min_length=1),
        "overall_passed": OutputSpec(type="bool"),
        "gate_score": OutputSpec(type="float"),
        "blockers": OutputSpec(type="list"),
        "warnings": OutputSpec(type="list"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["check", "gate"],
        score_threshold=0.7,
    ),
    cli_method="run",
)

COMPETITIVE_INTELLIGENCE = CapabilityContract(
    agent_name="competitive-intelligence-briefing",
    agent_type="composite",
    description="Competitive intelligence briefing — sequential chain",
    required_inputs={
        "topic": InputSpec(
            type="str",
            description="Analysis topic",
            examples=["AI Agent market competitive landscape"],
        ),
    },
    optional_inputs={},
    output_schema={
        "briefing": OutputSpec(type="str", min_length=50),
        "sources": OutputSpec(type="list"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["intelligence", "competitor"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

DOCUMENT_COMPLIANCE = CapabilityContract(
    agent_name="document-compliance-gateway",
    agent_type="composite",
    description="Document compliance gateway — full parallel + conflict detection",
    required_inputs={
        "document_path": InputSpec(
            type="str",
            description="Document path",
            examples=["docs/"],
        ),
    },
    optional_inputs={},
    output_schema={
        "checks": OutputSpec(type="list", min_length=1),
        "compliant": OutputSpec(type="bool"),
        "issues": OutputSpec(type="list"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=["compliance"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

FEATURE_DELIVERY = CapabilityContract(
    agent_name="feature-delivery-pipeline",
    agent_type="composite",
    description="Feature delivery pipeline — sequential to parallel",
    required_inputs={
        "feature_spec": InputSpec(
            type="str",
            description="Feature specification",
            examples=["Implement user login functionality"],
        ),
    },
    optional_inputs={},
    output_schema={
        "artifacts": OutputSpec(type="list", min_length=1),
        "status": OutputSpec(type="str"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=["feature", "delivery"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

PRODUCT_DOCUMENTATION = CapabilityContract(
    agent_name="product-documentation-suite",
    agent_type="composite",
    description="Product documentation suite — parallel + sequential aggregation",
    required_inputs={
        "project_path": InputSpec(
            type="str",
            description="Project path",
            examples=["src/agent_nexus/"],
        ),
    },
    optional_inputs={},
    output_schema={
        "documents": OutputSpec(type="list", min_length=1),
        "summary": OutputSpec(type="str", min_length=10),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["document"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

ALL_COMPOSITE_CONTRACTS: list[CapabilityContract] = [
    CICD_QUALITY_GATE,
    COMPETITIVE_INTELLIGENCE,
    DOCUMENT_COMPLIANCE,
    FEATURE_DELIVERY,
    PRODUCT_DOCUMENTATION,
]
```

- [ ] **Step 2: 实现 OrchestrationValidator**

`tests/capabilities/validators/orchestration.py`:
```python
"""OrchestrationValidator — DAG topology and parallelism validation."""

from __future__ import annotations

from typing import Any

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    ValidationResult,
)


class OrchestrationValidator:
    """Validate composite/agency orchestration behavior."""

    def validate(
        self,
        contract: CapabilityContract,
        raw_output: Any,
    ) -> ValidationResult:
        failures: list[str] = []

        if contract.agent_type == "composite":
            self._validate_composite(contract, raw_output, failures)
        elif contract.agent_type == "agency":
            self._validate_agency(contract, raw_output, failures)

        score = 1.0 - (len(failures) * 0.25)
        return ValidationResult(
            passed=len(failures) == 0,
            score=max(0.0, score),
            failures=failures,
        )

    def _validate_composite(
        self,
        contract: CapabilityContract,
        output: Any,
        failures: list[str],
    ) -> None:
        if not isinstance(output, dict):
            failures.append("Composite output must be a dict")
            return

        if "checks" in output:
            checks = output["checks"]
            if not isinstance(checks, list):
                failures.append("'checks' must be a list")
            elif len(checks) == 0:
                failures.append("'checks' must not be empty for composite agent")

        if "overall_passed" in output and not isinstance(output["overall_passed"], bool):
            failures.append("'overall_passed' must be bool")

        if "gate_score" in output:
            score = output["gate_score"]
            if not isinstance(score, (int, float)):
                failures.append("'gate_score' must be numeric")
            elif score < 0 or score > 100:
                failures.append(f"'gate_score' {score} out of range [0, 100]")

    def _validate_agency(
        self,
        contract: CapabilityContract,
        output: Any,
        failures: list[str],
    ) -> None:
        if not isinstance(output, dict):
            failures.append("Agency output must be a dict")
            return

        if "plan" not in output:
            failures.append("Agency output missing 'plan'")

        if "artifacts" in output:
            artifacts = output["artifacts"]
            if not isinstance(artifacts, list):
                failures.append("'artifacts' must be a list")
            elif len(artifacts) == 0:
                failures.append("'artifacts' must not be empty after agency run")

        if "qa_score" in output:
            qa_score = output["qa_score"]
            if isinstance(qa_score, (int, float)):
                threshold = contract.quality_thresholds.score_threshold
                if qa_score < threshold:
                    failures.append(
                        f"QA score {qa_score} below threshold {threshold}"
                    )
```

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run python -c "from tests.capabilities.contracts.composite import ALL_COMPOSITE_CONTRACTS; from tests.capabilities.validators.orchestration import OrchestrationValidator; print(f'Contracts: {len(ALL_COMPOSITE_CONTRACTS)}')"`
Expected: `Contracts: 5`

- [ ] **Step 4: Commit**

```bash
git add tests/capabilities/contracts/composite.py tests/capabilities/validators/orchestration.py
git commit -m "feat(test): add composite agent contracts and orchestration validator"
```

---

## Task 8: test_composite_cli.py

**Files:**
- Create: `tests/capabilities/test_composite_cli.py`

- [ ] **Step 1: 编写 Composite x CLI 测试**

`tests/capabilities/test_composite_cli.py`:
```python
"""Composite Agent x CLI mode — DAG orchestration + structure validation."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.composite import ALL_COMPOSITE_CONTRACTS
from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.cli_provider import CLIProvider
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.validators.structure import StructureValidator
from tests.capabilities.validators.orchestration import OrchestrationValidator


@pytest.fixture(params=ALL_COMPOSITE_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def cli_provider():
    return CLIProvider(timeout=15.0)


@pytest.fixture
def struct_validator():
    return StructureValidator()


@pytest.fixture
def orch_validator():
    return OrchestrationValidator()


class TestCompositeCLI:
    """Composite Agent x CLI mode — DAG orchestration and structure validation."""

    def test_composite_agent_responds(self, contract, cli_provider):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, (
            f"Composite '{contract.agent_name}' failed: {result.error}"
        )

    def test_composite_output_structure(self, contract, cli_provider, struct_validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = struct_validator.validate(contract, result.raw_output)
        assert validation.passed, (
            f"Composite '{contract.agent_name}' structure: {validation.failures}"
        )

    def test_composite_orchestration(self, contract, cli_provider, orch_validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = orch_validator.validate(contract, result.raw_output)
        assert validation.passed, (
            f"Composite '{contract.agent_name}' orchestration: {validation.failures}"
        )
```

- [ ] **Step 2: 运行测试**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run pytest tests/capabilities/test_composite_cli.py -v --timeout=30 2>&1 | tail -20`

- [ ] **Step 3: Commit**

```bash
git add tests/capabilities/test_composite_cli.py
git commit -m "feat(test): add composite agent CLI capability tests"
```

---

## Task 9: Agency Pipeline 契约 + test_agency_cli.py

**Files:**
- Create: `tests/capabilities/contracts/agency.py`
- Create: `tests/capabilities/test_agency_cli.py`

- [ ] **Step 1: 定义 Agency Pipeline 契约**

`tests/capabilities/contracts/agency.py`:
```python
"""Agency Pipeline capability contracts."""

from __future__ import annotations

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    InputSpec,
    OutputSpec,
    QualityThresholds,
)

AGENCY_PIPELINE = CapabilityContract(
    agent_name="agency-pipeline",
    agent_type="agency",
    description="LLM-powered expert orchestration pipeline",
    required_inputs={
        "task": InputSpec(
            type="str",
            description="User task description",
            examples=["Analyze code security of the agent-nexus project"],
        ),
    },
    optional_inputs={
        "vendor_path": InputSpec(
            type="str",
            description="Agency agents vendor path",
            examples=["vendor/agency-agents"],
            required=False,
        ),
        "allowlist": InputSpec(
            type="str",
            description="Expert allowlist YAML path",
            examples=["config/agency-agents-minimal.allowlist.yaml"],
            required=False,
        ),
        "max_parallel": InputSpec(
            type="int",
            description="Max parallel executions",
            examples=["3"],
            required=False,
        ),
    },
    output_schema={
        "plan": OutputSpec(type="dict"),
        "artifacts": OutputSpec(type="list"),
        "integration": OutputSpec(type="str"),
        "qa_score": OutputSpec(type="float"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=200,
        required_keywords=["recommendation", "analysis"],
        score_threshold=0.6,
    ),
    cli_method="run-composition",
)

ALL_AGENCY_CONTRACTS: list[CapabilityContract] = [AGENCY_PIPELINE]
```

- [ ] **Step 2: 编写 Agency x CLI 测试**

`tests/capabilities/test_agency_cli.py`:
```python
"""Agency Pipeline x CLI mode — expert orchestration validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.capabilities.contracts.agency import AGENCY_PIPELINE
from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.validators.structure import StructureValidator
from tests.capabilities.validators.orchestration import OrchestrationValidator

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def contract():
    return AGENCY_PIPELINE


@pytest.fixture
def struct_validator():
    return StructureValidator()


@pytest.fixture
def orch_validator():
    return OrchestrationValidator()


class TestAgencyCLI:
    """Agency Pipeline x CLI mode — CI layer structure validation."""

    def test_agency_cli_help(self):
        result = subprocess.run(
            ["uv", "run", "python", "-m", "agent_nexus.platform.agency.cli",
             "run-composition", "--help"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=30,
        )
        assert result.returncode == 0
        assert "message" in result.stdout.lower() or "task" in result.stdout.lower()

    def test_agency_cli_no_llm_runs(self, contract, struct_validator, orch_validator):
        result = subprocess.run(
            ["uv", "run", "python", "-m", "agent_nexus.platform.agency.cli",
             "run-composition",
             "--message", contract.required_inputs["task"].examples[0],
             "--timeout", "60"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=90,
        )
        assert result.returncode == 0, f"Agency CLI failed: {result.stderr[:500]}"

        output = result.stdout.strip()
        assert len(output) > contract.quality_thresholds.min_output_length, (
            f"Output too short: {len(output)} chars"
        )

    def test_agency_composer_produces_plan(self):
        from agent_nexus.platform.agency.task_composer import (
            TaskComposer,
            TaskComposerInput,
        )
        from agent_nexus.platform.agency.registry import ExpertRegistry

        registry = ExpertRegistry()
        registry.add(
            "security-expert",
            {"name": "Security Expert", "capabilities": ["security_analysis"]},
            ["security_analysis"],
        )

        composer = TaskComposer(registry=registry)
        composer_input = TaskComposerInput(
            task="Analyze code security",
            max_parallel=3,
        )
        result = composer.compose(composer_input)
        assert result is not None
        assert len(result.subtasks) > 0
```

- [ ] **Step 3: 运行测试**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run pytest tests/capabilities/test_agency_cli.py -v --timeout=120 2>&1 | tail -20`

- [ ] **Step 4: Commit**

```bash
git add tests/capabilities/contracts/agency.py tests/capabilities/test_agency_api.py
git commit -m "feat(test): add agency pipeline contracts and CLI tests"
```

---

## Task 10: API Provider + SemanticValidator

**Files:**
- Create: `tests/capabilities/providers/api_provider.py`
- Create: `tests/capabilities/validators/semantic.py`

- [ ] **Step 1: 实现 APIProvider**

`tests/capabilities/providers/api_provider.py`:
```python
"""APIProvider — invoke agents via real LLM API calls."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.base import ProviderResult, build_test_inputs


class APIProvider:
    """Invoke agent capabilities through real LLM API via LLMClient."""

    def __init__(self, model: str, config_dir: str | None = None) -> None:
        self.model = model
        self.config_dir = config_dir

    async def invoke(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any] | None = None,
    ) -> ProviderResult:
        if inputs is None:
            inputs = build_test_inputs(contract)

        prompt = self._build_prompt(contract, inputs)

        start = time.monotonic()
        try:
            from agent_nexus.platform.agency.llm_client import LLMClient
            from agent_nexus.models.capability import ModelCapabilityRegistry

            config_path = Path(self.config_dir) if self.config_dir else None
            registry = ModelCapabilityRegistry()

            async with LLMClient(
                model_string=self.model,
                config_dir=config_path,
                capability_registry=registry,
            ) as client:
                response = await client.call(prompt)
                duration_ms = (time.monotonic() - start) * 1000

                return ProviderResult(
                    success=True,
                    raw_output=response,
                    duration_ms=duration_ms,
                )
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return ProviderResult(
                success=False,
                raw_output=None,
                duration_ms=duration_ms,
                error=str(exc),
            )

    def _build_prompt(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any],
    ) -> str:
        parts = [
            f"You are an expert {contract.agent_name} agent.",
            f"Task: {contract.description}",
            "",
            "Inputs:",
        ]
        for key, value in inputs.items():
            parts.append(f"  {key}: {value}")

        parts.append("")
        parts.append("Output as JSON with these fields:")
        for field_name, spec in contract.output_schema.items():
            req = " (required)" if spec.required else " (optional)"
            parts.append(f"  {field_name}: {spec.type}{req}")

        return "\n".join(parts)
```

- [ ] **Step 2: 实现 SemanticValidator**

`tests/capabilities/validators/semantic.py`:
```python
"""SemanticValidator — Release layer, keyword and relevance checks."""

from __future__ import annotations

import json
from typing import Any

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    ValidationResult,
)


class SemanticValidator:
    """Validate Agent output quality beyond structure — keywords, relevance."""

    async def validate(
        self,
        contract: CapabilityContract,
        raw_output: Any,
    ) -> ValidationResult:
        failures: list[str] = []
        details: dict[str, Any] = {}

        text = self._to_text(raw_output)
        if not text:
            return ValidationResult(
                passed=False,
                score=0.0,
                failures=["Output is empty or not text-convertible"],
            )

        details["output_length"] = len(text)

        keyword_score = self._check_keywords(
            text, contract.quality_thresholds.required_keywords
        )
        details["keyword_score"] = keyword_score
        if keyword_score < 0.5 and contract.quality_thresholds.required_keywords:
            failures.append(f"Keyword coverage {keyword_score:.0%} below 50%")

        length_ok = (
            len(text) >= contract.quality_thresholds.min_output_length
            and len(text) <= contract.quality_thresholds.max_output_length
        )
        if not length_ok:
            failures.append(
                f"Output length {len(text)} outside expected range "
                f"[{contract.quality_thresholds.min_output_length}, "
                f"{contract.quality_thresholds.max_output_length}]"
            )

        score = (keyword_score + (1.0 if length_ok else 0.0)) / 2.0
        return ValidationResult(
            passed=len(failures) == 0,
            score=score,
            failures=failures,
            details=details,
        )

    def _to_text(self, raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, (dict, list)):
            return json.dumps(raw, ensure_ascii=False)
        return str(raw) if raw is not None else ""

    def _check_keywords(self, text: str, keywords: list[str]) -> float:
        if not keywords:
            return 1.0
        text_lower = text.lower()
        found = sum(1 for kw in keywords if kw.lower() in text_lower)
        return found / len(keywords)
```

- [ ] **Step 3: 验证导入**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run python -c "from tests.capabilities.providers.api_provider import APIProvider; from tests.capabilities.validators.semantic import SemanticValidator; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add tests/capabilities/providers/api_provider.py tests/capabilities/validators/semantic.py
git commit -m "feat(test): add APIProvider and SemanticValidator for release layer"
```

---

## Task 11: test_atomic_api.py + test_composite_api.py

**Files:**
- Create: `tests/capabilities/test_atomic_api.py`
- Create: `tests/capabilities/test_composite_api.py`

- [ ] **Step 1: 编写 Atomic x API 测试**

`tests/capabilities/test_atomic_api.py`:
```python
"""Atomic Agent x API mode — semantic validation with real LLM calls."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.atomic import KEY_ATOMIC_CONTRACTS
from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.api_provider import APIProvider
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.validators.structure import StructureValidator
from tests.capabilities.validators.semantic import SemanticValidator


@pytest.fixture(params=KEY_ATOMIC_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def api_provider():
    return APIProvider(model="anthropic:claude-haiku-4-5-20251001")


@pytest.fixture
def struct_validator():
    return StructureValidator()


@pytest.fixture
def semantic_validator():
    return SemanticValidator()


@pytest.mark.requires_api
class TestAtomicAPI:
    """Atomic Agent x API mode — Release layer semantic validation."""

    async def test_api_call_succeeds(self, contract, api_provider):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"
        assert result.raw_output is not None
        assert result.duration_ms > 0

    async def test_api_output_structure(self, contract, api_provider, struct_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"
        validation = struct_validator.validate(contract, result.raw_output)
        assert validation.score >= 0.5, (
            f"Structure score too low for '{contract.agent_name}': "
            f"{validation.score} — {validation.failures}"
        )

    async def test_api_output_semantic(self, contract, api_provider, semantic_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"
        validation = await semantic_validator.validate(contract, result.raw_output)
        assert validation.score >= contract.quality_thresholds.score_threshold, (
            f"Semantic score {validation.score} below threshold "
            f"{contract.quality_thresholds.score_threshold}: {validation.failures}"
        )
```

- [ ] **Step 2: 编写 Composite x API 测试**

`tests/capabilities/test_composite_api.py`:
```python
"""Composite Agent x API mode — semantic validation with real LLM calls."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.composite import ALL_COMPOSITE_CONTRACTS
from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.api_provider import APIProvider
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.validators.structure import StructureValidator
from tests.capabilities.validators.orchestration import OrchestrationValidator
from tests.capabilities.validators.semantic import SemanticValidator


@pytest.fixture(params=ALL_COMPOSITE_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def api_provider():
    return APIProvider(model="anthropic:claude-haiku-4-5-20251001")


@pytest.fixture
def struct_validator():
    return StructureValidator()


@pytest.fixture
def orch_validator():
    return OrchestrationValidator()


@pytest.fixture
def semantic_validator():
    return SemanticValidator()


@pytest.mark.requires_api
@pytest.mark.capability_release
class TestCompositeAPI:
    """Composite Agent x API mode — Release layer full validation."""

    async def test_api_composite_responds(self, contract, api_provider):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"

    async def test_api_composite_structure(self, contract, api_provider, struct_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success
        validation = struct_validator.validate(contract, result.raw_output)
        assert validation.score >= 0.5, f"Structure: {validation.failures}"

    async def test_api_composite_semantic(self, contract, api_provider, semantic_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success
        validation = await semantic_validator.validate(contract, result.raw_output)
        assert validation.score >= contract.quality_thresholds.score_threshold
```

- [ ] **Step 3: 验证测试文件可收集**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run pytest tests/capabilities/test_atomic_api.py tests/capabilities/test_composite_api.py --collect-only 2>&1 | tail -20`

- [ ] **Step 4: Commit**

```bash
git add tests/capabilities/test_atomic_api.py tests/capabilities/test_composite_api.py
git commit -m "feat(test): add atomic and composite API capability tests"
```

---

## Task 12: test_agency_api.py

**Files:**
- Create: `tests/capabilities/test_agency_api.py`

- [ ] **Step 1: 编写 Agency x API 测试**

`tests/capabilities/test_agency_api.py`:
```python
"""Agency Pipeline x API mode — full LLM-powered orchestration validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.capabilities.contracts.agency import AGENCY_PIPELINE
from tests.capabilities.contracts.schema import CapabilityContract

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def contract():
    return AGENCY_PIPELINE


@pytest.mark.requires_api
@pytest.mark.capability_release
class TestAgencyAPI:
    """Agency Pipeline x API mode — full pipeline with real LLM."""

    def test_agency_pipeline_with_llm(self, contract):
        result = subprocess.run(
            [
                "uv", "run", "python", "-m", "agent_nexus.platform.agency.cli",
                "run-composition",
                "--message", contract.required_inputs["task"].examples[0],
                "--use-llm",
                "--timeout", "180",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=300,
        )
        assert result.returncode == 0, f"Pipeline failed: {result.stderr[:500]}"
        output = result.stdout.strip()
        assert len(output) > contract.quality_thresholds.min_output_length, (
            f"Output too short: {len(output)} chars"
        )

    def test_agency_pipeline_qa_score(self, contract):
        result = subprocess.run(
            [
                "uv", "run", "python", "-m", "agent_nexus.platform.agency.cli",
                "run-composition",
                "--message", "Review code quality of the agency module",
                "--use-llm",
                "--timeout", "180",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=300,
        )
        assert result.returncode == 0, f"Pipeline failed: {result.stderr[:500]}"
```

- [ ] **Step 2: 验证测试可收集**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run pytest tests/capabilities/test_agency_api.py --collect-only 2>&1 | tail -10`

- [ ] **Step 3: Commit**

```bash
git add tests/capabilities/test_agency_api.py
git commit -m "feat(test): add agency pipeline API capability tests"
```

---

## Task 13: 全量验证 + lint

**Files:** None (verification only)

- [ ] **Step 1: 运行全量能力测试（CI 层，不含 API）**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run pytest tests/capabilities/ -m "capability and not capability_release and not requires_api" -v --timeout=30 2>&1 | tail -30`

- [ ] **Step 2: 运行 lint**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run ruff check tests/capabilities/ && uv run ruff format --check tests/capabilities/`
Expected: 无 lint 错误

- [ ] **Step 3: 确认现有测试不受影响**

Run: `cd /Users/yangyitian/Documents/dev/Agents/agent-nexus && uv run pytest tests/unit/ -x --timeout=60 -q 2>&1 | tail -5`
Expected: 全部通过

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(test): complete agent capability contract-driven test suite"
```

---

## Self-Review

### 1. Spec Coverage

| Spec Section | Covered by Task |
|-------------|----------------|
| Schema types (sec 4.1) | Task 1 |
| Atomic contracts (sec 4.3) | Task 4 |
| Composite contracts (sec 4.2) | Task 7 |
| Agency contract (sec 4.2) | Task 9 |
| CLI Provider (sec 5.2) | Task 5 |
| API Provider (sec 5.3) | Task 10 |
| StructureValidator (sec 6.1) | Task 3 |
| SemanticValidator (sec 6.2) | Task 10 |
| OrchestrationValidator (sec 6.3) | Task 7 |
| test_atomic_cli | Task 6 |
| test_atomic_api | Task 11 |
| test_composite_cli | Task 8 |
| test_composite_api | Task 11 |
| test_agency_cli | Task 9 |
| test_agency_api | Task 12 |
| conftest markers | Task 1 |
| Full verification | Task 13 |

### 2. Placeholder Scan

No TBD/TODO/fill-in-later. All steps have complete code.

### 3. Type Consistency

- `CapabilityContract` fields (`cli_method`, `output_format`) used consistently
- `ProviderResult` fields (`success`, `raw_output`, `error`) used consistently
- `ValidationResult` fields (`passed`, `score`, `failures`) used consistently
