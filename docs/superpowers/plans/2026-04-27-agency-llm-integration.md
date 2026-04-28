# Agency LLM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the agency pipeline's planning, integration, and QA stages from rule-based logic to LLM-powered semantic analysis, with per-stage and per-expert model configuration.

**Architecture:** Agency-agents is an expert pool component within the Agent Nexus platform (Layer 2: Orchestration). The upgrade replaces keyword matching in `infer_capabilities()` with LLM task decomposition, mechanical dict merge in `Integrator.merge()` with LLM synthesis, and section-presence checks in `QAGate` with LLM quality evaluation. All LLM calls go through the shared `LLMClient`.

**Model resolution priority** (aligned across README, code, and plan):

| Priority | Source | Scope |
|----------|--------|-------|
| 1 (highest) | Expert profile `model` field | Per-expert override |
| 2 | Explicit `model_string` parameter | Per-client constructor |
| 3 | `[models.stages].<stage>` | Per-pipeline-stage |
| 4 | `AGENT_MODEL` env var | Global override |
| 5 | `[models].default` | Fallback |

This matches `LLMClient.__init__` behavior: explicit `model_string` > `resolve_stage_model(stage)` > `resolve_model()` (which checks `AGENT_MODEL` env > tier > default).

**Tech Stack:** Python 3.11+, httpx (LLM API calls), Pydantic (config models), pytest (testing), ruff (linting)

**Branch:** `feat/agency-agents-integration` (current)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Stabilize | `src/agent_nexus/platform/agency/llm_client.py` | Shared LLM API client (already exists, may need stash restore) |
| Modify | `src/agent_nexus/models/config.py` | Add `stages: dict[str, str]` to `ModelConfig` |
| Modify | `src/agent_nexus/platform/config/model_config.py` | Add `resolve_stage_model()` method |
| Modify | `src/agent_nexus/platform/agency/executor.py` | Refactor `LLMExecutor` to use `LLMClient` + per-expert model |
| Create | `src/agent_nexus/platform/agency/llm_planner.py` | LLM-powered task decomposition |
| Create | `src/agent_nexus/platform/agency/llm_integrator.py` | LLM-powered artifact synthesis |
| Create | `src/agent_nexus/platform/agency/llm_qa_gate.py` | LLM-powered quality evaluation |
| Modify | `src/agent_nexus/platform/agency/task_composer.py` | Wire LLMPlanner/LLMIntegrator/LLMQualityGate into pipeline |
| Modify | `src/agent_nexus/platform/agency/cli.py` | Add `--use-llm` flag to `run-composition` |
| Create | `tests/unit/test_llm_planner.py` | Unit tests for LLMPlanner |
| Create | `tests/unit/test_llm_integrator.py` | Unit tests for LLMIntegrator |
| Create | `tests/unit/test_llm_qa_gate.py` | Unit tests for LLMQualityGate |

---

## Task 1: Stabilize LLMClient + Config Stages Foundation

**Files:**
- Stabilize: `src/agent_nexus/platform/agency/llm_client.py` (already in working tree)
- Modify: `src/agent_nexus/models/config.py:33-49` — add `stages` field
- Modify: `src/agent_nexus/platform/config/model_config.py:219-220` — add `resolve_stage_model()`
- Modify: `src/agent_nexus/platform/agency/executor.py:123-293` — **delegate to LLMClient, remove httpx code** (from stash)

**CRITICAL**: These 4 changes form an atomic unit. The stash (`stash@{0}`) contains changes to all three Python files. `llm_client.py` already exists in the working tree and calls `resolve_stage_model()` at runtime. Applying any subset will break imports or runtime behavior. **All changes must be applied together before any test run.**

- [ ] **Step 1: Check stash + working tree state**

Run: `git stash list && echo "---" && ls -la src/agent_nexus/platform/agency/llm_client.py && echo "---" && git diff --name-only stash@{0}`

Expected: `stash@{0}` exists, `llm_client.py` exists in working tree, stash touches `config.py`, `executor.py`, `model_config.py`

- [ ] **Step 2: Apply ALL stashed changes atomically (config.py + model_config.py + executor.py)**

**Do NOT use `git stash pop`** — it may create merge conflicts. Instead, apply each file's changes manually:

**2a.** Edit `src/agent_nexus/models/config.py` — add `stages` field to `ModelConfig`:

```python
# In ModelConfig class, after the `providers` field (line 49):
    stages: dict[str, str] = Field(default_factory=dict)
    """Per-stage model overrides (e.g. ``{"planning": "openai:gpt-4o"}``).

    Supported stages: ``planning``, ``integration``, ``qa``, ``execution``.
    Falls back to ``default`` if a stage is not specified.
    """
```

**2b.** Edit `src/agent_nexus/platform/agency/executor.py` — refactor `LLMExecutor` to delegate to `LLMClient`, removing `httpx` and config-loading code. Replace lines 1-14 (imports) with imports from `.llm_client`, replace `__init__` (lines 134-177) with `LLMClient`-based constructor, replace `__call__` (lines 179-214) to use `self._get_client(profile)`, and remove `_call_anthropic_api` / `_call_openai_api` (lines 220-292). See Task 2 for the full refactored code.

- [ ] **Step 3: Add `resolve_stage_model()` to ModelConfigManager**

Edit `src/agent_nexus/platform/config/model_config.py` — add method after `resolve_api_key` (after line 220):

```python
    def resolve_stage_model(self, stage: str) -> str | None:
        """Resolve the model string for a specific pipeline stage.

        Looks up ``[models.stages].<stage>`` in config, falls back to
        ``default`` if not set.

        Parameters
        ----------
        stage:
            Pipeline stage name (e.g. ``"planning"``, ``"integration"``,
            ``"qa"``, ``"execution"``).

        Returns
        -------
        str | None
            The resolved ``provider:model`` string, or None if neither
            the stage override nor default is configured.
        """
        stage_model = self._config.models.stages.get(stage)
        if stage_model:
            logger.debug("Stage '%s' model: %s", stage, stage_model)
            return stage_model

        default = self._config.models.default
        if default:
            logger.debug("Stage '%s' falling back to default: %s", stage, default)
            return default

        return None
```

- [ ] **Step 4: Write test for config stages**

Create `tests/unit/test_config_stages.py`:

```python
"""Tests for ModelConfig.stages and resolve_stage_model()."""

from agent_nexus.models.config import ModelConfig, PlatformConfig
from agent_nexus.platform.config.model_config import ModelConfigManager


def test_resolve_stage_model_returns_stage_override():
    config = PlatformConfig(
        models=ModelConfig(
            default="openai:gpt-4o",
            stages={"planning": "anthropic:claude-sonnet-4-20250514"},
        )
    )
    mgr = ModelConfigManager(config)
    assert mgr.resolve_stage_model("planning") == "anthropic:claude-sonnet-4-20250514"


def test_resolve_stage_model_falls_back_to_default():
    config = PlatformConfig(
        models=ModelConfig(default="openai:gpt-4o", stages={})
    )
    mgr = ModelConfigManager(config)
    assert mgr.resolve_stage_model("planning") == "openai:gpt-4o"


def test_resolve_stage_model_returns_none_when_no_default():
    config = PlatformConfig(models=ModelConfig(default="", stages={}))
    mgr = ModelConfigManager(config)
    assert mgr.resolve_stage_model("planning") is None


def test_resolve_stage_model_unknown_stage_uses_default():
    config = PlatformConfig(
        models=ModelConfig(
            default="openai:gpt-4o",
            stages={"planning": "anthropic:claude-sonnet-4-20250514"},
        )
    )
    mgr = ModelConfigManager(config)
    # "integration" not in stages → fallback to default
    assert mgr.resolve_stage_model("integration") == "openai:gpt-4o"
```

- [ ] **Step 5: Run config stage tests**

Run: `uv run pytest tests/unit/test_config_stages.py -v`

Expected: 4 tests PASS

- [ ] **Step 6: Verify LLMClient imports correctly**

Run: `uv run python -c "from agent_nexus.platform.agency.llm_client import LLMClient, LLMResponse; print('LLMClient OK')"`

Expected: `LLMClient OK`

- [ ] **Step 7: Commit foundation**

```bash
git add src/agent_nexus/models/config.py src/agent_nexus/platform/config/model_config.py tests/unit/test_config_stages.py
git commit -m "feat(agency): add per-stage model config (ModelConfig.stages + resolve_stage_model)"
```

---

## Task 2: Refactor LLMExecutor to Use LLMClient + Per-Expert Model Override

**Files:**
- Modify: `src/agent_nexus/platform/agency/executor.py:123-293`
- Test: `tests/unit/test_agency_executor.py`

Refactor `LLMExecutor` to delegate all HTTP calls to `LLMClient`, and support per-expert model overrides via the `model` field in expert profiles.

- [ ] **Step 1: Write test for per-expert model override**

Add to `tests/unit/test_agency_executor.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from agent_nexus.platform.agency.executor import LLMExecutor
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.llm_client import LLMResponse


def _make_registry_with_model_override():
    """Registry where one expert has a model override."""
    registry = ExpertRegistry()
    registry.add("agency.expert-a", {
        "id": "agency.expert-a",
        "name": "Expert A",
        "capabilities": ["code_review"],
        "profile": {"body": "You are a code reviewer."},
        "output_contract": {
            "artifact_type": "report",
            "required_sections": ["summary"],
        },
        # No model override — uses default
    }, ["code_review"])
    registry.add("agency.expert-b", {
        "id": "agency.expert-b",
        "name": "Expert B",
        "capabilities": ["security_review"],
        "profile": {"body": "You are a security expert."},
        "output_contract": {
            "artifact_type": "report",
            "required_sections": ["summary"],
        },
        "model": "anthropic:claude-sonnet-4-20250514",  # Per-expert override
    }, ["security_review"])
    return registry


@patch("agent_nexus.platform.agency.executor.LLMClient")
def test_llm_executor_uses_per_expert_model(MockLLMClient):
    mock_default = MagicMock()
    mock_default.call.return_value = LLMResponse(
        text="## summary\nExpert A summary",
        model="default-model",
        provider="api",
    )
    mock_expert = MagicMock()
    mock_expert.call.return_value = LLMResponse(
        text="## summary\nExpert B summary",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
    )
    # First call creates default client, second creates expert-b client
    MockLLMClient.side_effect = [mock_default, mock_expert]

    registry = _make_registry_with_model_override()
    executor = LLMExecutor(registry=registry, model_string="api:default-model")

    # Expert A should use default client
    artifact_a = executor("agency.expert-a", "review code")
    assert artifact_a.metadata["model"] == "default-model"

    # Expert B should use per-expert client
    artifact_b = executor("agency.expert-b", "security review")
    assert artifact_b.metadata["model"] == "claude-sonnet-4-20250514"
    assert MockLLMClient.call_count == 2  # default + expert override
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_agency_executor.py::test_llm_executor_uses_per_expert_model -v`

Expected: FAIL (LLMExecutor still uses httpx directly)

- [ ] **Step 3: Refactor LLMExecutor to use LLMClient**

Replace `LLMExecutor` in `executor.py` (lines 123-293) with:

```python
class LLMExecutor:
    """Executor that calls a real LLM API using expert profiles as system prompts.

    Delegates API calls to :class:`LLMClient`.  Supports per-expert model
    overrides via the ``model`` field in expert profiles.
    """

    def __init__(
        self,
        registry: ExpertRegistry,
        model_string: str | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        self._config_dir = config_dir

        # Create default client (used when expert has no model override)
        self._default_client = LLMClient(
            model_string=model_string,
            config_dir=config_dir,
        )

        # Cache per-expert clients (keyed by model string)
        self._expert_clients: dict[str, LLMClient] = {}

    @property
    def _model_name(self) -> str:
        """Default model name (backward compat)."""
        return self._default_client.model_name

    def _get_client(self, profile: dict[str, Any]) -> LLMClient:
        """Get LLMClient for an expert, respecting per-expert model override."""
        expert_model = profile.get("model")
        if not expert_model:
            return self._default_client

        if expert_model not in self._expert_clients:
            self._expert_clients[expert_model] = LLMClient(
                model_string=expert_model,
                config_dir=self._config_dir,
            )
            logger.info(
                "Per-expert model override: %s -> %s",
                profile.get("id", "unknown"),
                expert_model,
            )
        return self._expert_clients[expert_model]

    def __call__(self, profile_id: str, task: str) -> Artifact:
        profile = self._registry.get(profile_id)
        if profile is None:
            raise ValueError(
                f"Profile '{profile_id}' not found in registry "
                "— cannot produce artifact"
            )

        name: str = profile.get("name", profile_id)
        body: str = profile.get("profile", {}).get("body", "")
        capabilities: list[str] = profile.get("capabilities", [])
        output_contract: dict[str, Any] = profile.get("output_contract", {})
        artifact_type: str = output_contract.get("artifact_type", "report")
        required_sections: list[str] = output_contract.get("required_sections", ["summary"])

        system_prompt = self._build_system_prompt(
            name=name,
            body=body,
            capabilities=capabilities,
            required_sections=required_sections,
        )

        # Use per-expert client if available
        client = self._get_client(profile)
        response = client.call(system_prompt=system_prompt, user_message=task)
        sections = self._parse_sections(response.text, required_sections)

        return Artifact(
            source_agent=profile_id,
            artifact_type=artifact_type,
            sections=sections,
            metadata={"llm": True, "model": response.model, "provider": response.provider},
        )

    # ------------------------------------------------------------------
    # Prompt building & parsing
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        name: str,
        body: str,
        capabilities: list[str],
        required_sections: list[str],
    ) -> str:
        """Build the full system prompt with section output instructions."""
        parts: list[str] = []

        if body:
            parts.append(body)
        else:
            parts.append(f"You are {name}, an expert assistant.")

        if capabilities:
            parts.append(
                "Your areas of expertise: " + ", ".join(capabilities) + "."
            )

        section_list = ", ".join(required_sections)
        parts.append(
            "Your response must include these sections as ## markdown headings: "
            + section_list + "."
        )
        parts.append(
            "Use exactly these heading names so they can be parsed. "
            "Provide substantive content under each heading."
        )

        return "\n\n".join(parts)

    def _parse_sections(
        self, response_text: str, required_sections: list[str],
    ) -> dict[str, object]:
        """Parse LLM response into sections using ``##`` markdown headings."""
        required_normalized: dict[str, str] = {
            _normalize_heading(s): s for s in required_sections
        }

        pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
        splits = pattern.split(response_text)

        sections: dict[str, object] = {}

        for i in range(1, len(splits) - 1, 2):
            heading_raw = splits[i].strip()
            content = splits[i + 1].strip()
            heading_norm = _normalize_heading(heading_raw)

            if heading_norm in required_normalized:
                original_key = required_normalized[heading_norm]
                sections[original_key] = content
            else:
                sections[heading_raw] = content

        for key in required_sections:
            if key not in sections:
                sections[key] = ""

        return sections


def _normalize_heading(heading: str) -> str:
    """Normalize a heading for case-insensitive, whitespace-insensitive comparison."""
    return re.sub(r"\s+", "_", heading.strip().lower())
```

Also remove unused imports at the top of `executor.py`: remove `import httpx`, `from agent_nexus.models.config import ProviderApiType`, `from agent_nexus.platform.config.loader import ConfigLoader`, `from agent_nexus.platform.config.model_config import ModelConfigManager`. Add `from .llm_client import LLMClient`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_agency_executor.py::test_llm_executor_uses_per_expert_model -v`

Expected: PASS

- [ ] **Step 5: Run full executor test suite to verify no regressions**

Run: `uv run pytest tests/unit/test_agency_executor.py -v`

Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_nexus/platform/agency/executor.py tests/unit/test_agency_executor.py
git commit -m "refactor(agency): LLMExecutor uses LLMClient + per-expert model override"
```

---

## Task 3: LLMPlanner — Semantic Task Decomposition

**Files:**
- Create: `src/agent_nexus/platform/agency/llm_planner.py`
- Create: `tests/unit/test_llm_planner.py`

Replace keyword-based `infer_capabilities()` with LLM-powered task decomposition. Falls back to keyword matching when LLM is unavailable.

- [ ] **Step 1: Write test for LLMPlanner with mocked LLMClient**

Create `tests/unit/test_llm_planner.py`:

```python
"""Tests for LLMPlanner — LLM-powered task decomposition."""

import json
import pytest
from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.llm_planner import LLMPlanner, PlannerOutput
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.llm_client import LLMResponse


def _make_registry():
    registry = ExpertRegistry()
    registry.add("agency.code-reviewer", {
        "id": "agency.code-reviewer",
        "name": "Code Reviewer",
        "capabilities": ["code_review", "security_review"],
    }, ["code_review", "security_review"])
    registry.add("agency.architect", {
        "id": "agency.architect",
        "name": "System Architect",
        "capabilities": ["system_design", "architecture_review"],
    }, ["system_design", "architecture_review"])
    return registry


def test_planner_output_from_llm_response():
    """PlannerOutput parses structured JSON from LLM."""
    raw = json.dumps({
        "capabilities": ["code_review", "system_design"],
        "focus_hints": {
            "agency.code-reviewer": "Focus on security vulnerabilities",
            "agency.architect": "Focus on scalability concerns",
        },
        "decomposition_strategy": "parallel",
    })
    output = PlannerOutput.from_json(raw)
    assert output.capabilities == ["code_review", "system_design"]
    assert output.decomposition_strategy == "parallel"
    assert "agency.code-reviewer" in output.focus_hints


def test_planner_output_from_invalid_json_falls_back():
    """PlannerOutput gracefully handles malformed JSON."""
    output = PlannerOutput.from_json("not json at all")
    assert output.capabilities == []
    assert output.decomposition_strategy == "parallel"


@patch("agent_nexus.platform.agency.llm_planner.LLMClient")
def test_llm_planner_analyze(MockLLMClient):
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps({
            "capabilities": ["code_review", "system_design"],
            "focus_hints": {"agency.code-reviewer": "security"},
            "decomposition_strategy": "parallel",
        }),
        model="test-model",
        provider="test",
    )
    MockLLMClient.return_value = mock_client

    registry = _make_registry()
    planner = LLMPlanner(registry=registry, client=mock_client)
    result = planner.analyze_task("Review the payment system architecture for security issues")

    assert "code_review" in result.capabilities or "system_design" in result.capabilities
    mock_client.call.assert_called_once()
    # Verify system prompt contains expert info
    call_args = mock_client.call.call_args
    assert "code_review" in call_args.kwargs["system_prompt"] or "system_design" in call_args.kwargs["system_prompt"]


def test_llm_planner_fallback_to_keywords():
    """When no client provided, falls back to keyword inference."""
    registry = _make_registry()
    planner = LLMPlanner(registry=registry, client=None)
    result = planner.analyze_task("Review the architecture design")
    # Should use keyword matching as fallback
    assert isinstance(result, PlannerOutput)
    assert isinstance(result.capabilities, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_planner.py -v`

Expected: FAIL (module not found)

- [ ] **Step 3: Implement LLMPlanner**

Create `src/agent_nexus/platform/agency/llm_planner.py`:

```python
"""LLMPlanner — LLM-powered task decomposition for the agency pipeline.

Replaces keyword-based ``infer_capabilities()`` with semantic task analysis.
Falls back to keyword matching when LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .task_composer import infer_capabilities

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from .registry import ExpertRegistry

logger = logging.getLogger(__name__)


@dataclass
class PlannerOutput:
    """Structured output from task decomposition."""

    capabilities: list[str] = field(default_factory=list)
    focus_hints: dict[str, str] = field(default_factory=dict)
    decomposition_strategy: str = "parallel"
    """Either ``"parallel"`` or ``"sequential"``."""

    @classmethod
    def from_json(cls, raw: str) -> PlannerOutput:
        """Parse LLM JSON response into PlannerOutput.

        Returns a default (empty) PlannerOutput on parse failure.
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLMPlanner: failed to parse JSON response, returning empty output")
            return cls()

        return cls(
            capabilities=data.get("capabilities", []),
            focus_hints=data.get("focus_hints", {}),
            decomposition_strategy=data.get("decomposition_strategy", "parallel"),
        )


class LLMPlanner:
    """LLM-powered task decomposition replacing keyword-based inference.

    Uses an LLM to analyze user tasks and determine:
    1. Required capabilities (from the known capability set)
    2. Per-expert focus areas
    3. Decomposition strategy (parallel vs sequential)

    Falls back to keyword-based ``infer_capabilities()`` when no LLM client
    is available or the LLM call fails.
    """

    def __init__(
        self,
        registry: ExpertRegistry,
        client: LLMClient | None = None,
    ) -> None:
        self._registry = registry
        self._client = client

    def analyze_task(self, task: str) -> PlannerOutput:
        """Analyze a task and return structured decomposition.

        Parameters
        ----------
        task:
            The user's task description.

        Returns
        -------
        PlannerOutput
            Capabilities, focus hints, and decomposition strategy.
        """
        if self._client is None:
            logger.debug("LLMPlanner: no LLM client, falling back to keywords")
            return self._keyword_fallback(task)

        try:
            return self._llm_analyze(task)
        except Exception:
            logger.exception("LLMPlanner: LLM call failed, falling back to keywords")
            return self._keyword_fallback(task)

    def _llm_analyze(self, task: str) -> PlannerOutput:
        """Perform LLM-based task analysis."""
        system_prompt = self._build_planning_prompt()
        response = self._client.call(
            system_prompt=system_prompt,
            user_message=task,
        )
        return PlannerOutput.from_json(response.text)

    def _build_planning_prompt(self) -> str:
        """Build system prompt with available expert info."""
        # Collect all capabilities from registry
        all_profiles = self._registry.search_by_capability([])
        if not all_profiles:
            all_profiles = [
                self._registry.get(pid)
                for pid in self._registry.list_all()
            ]
        all_profiles = [p for p in all_profiles if p is not None]

        all_caps: set[str] = set()
        expert_summary: list[str] = []
        for profile in all_profiles:
            caps = profile.get("capabilities", [])
            all_caps.update(caps)
            name = profile.get("name", profile.get("id", "unknown"))
            expert_summary.append(
                f"- {name}: {', '.join(caps)}"
            )

        return (
            "You are a task decomposition specialist. Given a user task and a pool of "
            "available experts, analyze the task and determine which capabilities are "
            "required.\n\n"
            f"Available capabilities: {', '.join(sorted(all_caps))}\n\n"
            f"Available experts:\n" + "\n".join(expert_summary) + "\n\n"
            "Respond with ONLY a JSON object (no markdown fences):\n"
            "{\n"
            '  "capabilities": ["cap1", "cap2"],\n'
            '  "focus_hints": {"expert-id": "specific focus area"},\n'
            '  "decomposition_strategy": "parallel" or "sequential"\n'
            "}\n\n"
            "The capabilities must come from the available capabilities list above. "
            "The focus_hints should guide each expert on what to focus on. "
            "Use \"parallel\" unless the task clearly requires sequential execution."
        )

    def _keyword_fallback(self, task: str) -> PlannerOutput:
        """Fall back to keyword-based capability inference."""
        capabilities = infer_capabilities(task)
        return PlannerOutput(
            capabilities=capabilities,
            decomposition_strategy="parallel",
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_llm_planner.py -v`

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_planner.py tests/unit/test_llm_planner.py
git commit -m "feat(agency): add LLMPlanner for semantic task decomposition with keyword fallback"
```

---

## Task 4: LLMIntegrator — Semantic Artifact Synthesis

**Files:**
- Create: `src/agent_nexus/platform/agency/llm_integrator.py`
- Create: `tests/unit/test_llm_integrator.py`

Replace mechanical dict merge with LLM synthesis. Falls back to `Integrator.merge()` when LLM unavailable.

- [ ] **Step 1: Write test for LLMIntegrator**

Create `tests/unit/test_llm_integrator.py`:

```python
"""Tests for LLMIntegrator — LLM-powered artifact synthesis."""

import json
import pytest
from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.integrator import Artifact, IntegratedArtifact
from agent_nexus.platform.agency.llm_integrator import LLMIntegrator
from agent_nexus.platform.agency.llm_client import LLMResponse


def _make_artifacts():
    return [
        Artifact(
            source_agent="agency.expert-a",
            artifact_type="report",
            sections={"summary": "Security risk: SQL injection", "recommendations": ["Use parameterized queries"]},
            metadata={"llm": True},
        ),
        Artifact(
            source_agent="agency.expert-b",
            artifact_type="report",
            sections={"summary": "Architecture issue: tight coupling", "recommendations": ["Introduce interfaces"]},
            metadata={"llm": True},
        ),
    ]


@patch("agent_nexus.platform.agency.llm_integrator.LLMClient")
def test_llm_integrator_synthesizes(MockLLMClient):
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps({
            "summary": "Combined analysis reveals both security and architecture concerns",
            "recommendations": ["Use parameterized queries", "Introduce interfaces"],
            "conflicts": [],
            "gaps": [],
        }),
        model="test-model",
        provider="test",
    )
    MockLLMClient.return_value = mock_client

    integrator = LLMIntegrator(client=mock_client)
    result = integrator.synthesize(_make_artifacts(), task="Review payment system")

    assert isinstance(result, IntegratedArtifact)
    assert len(result.source_agents) == 2
    mock_client.call.assert_called_once()


def test_llm_integrator_fallback_to_rules():
    """When no client, falls back to Integrator.merge."""
    integrator = LLMIntegrator(client=None)
    result = integrator.synthesize(_make_artifacts(), task="Review payment system")

    assert isinstance(result, IntegratedArtifact)
    assert len(result.source_agents) == 2
    # Should contain mechanically merged data
    assert "summary" in result.merged_sections


def test_llm_integrator_single_artifact():
    """Single artifact should work without LLM call."""
    single = [_make_artifacts()[0]]
    mock_client = MagicMock()

    integrator = LLMIntegrator(client=mock_client)
    result = integrator.synthesize(single, task="review")

    assert isinstance(result, IntegratedArtifact)
    # Single artifact: no synthesis needed, direct pass-through
    mock_client.call.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_integrator.py -v`

Expected: FAIL (module not found)

- [ ] **Step 3: Implement LLMIntegrator**

Create `src/agent_nexus/platform/agency/llm_integrator.py`:

```python
"""LLMIntegrator — LLM-powered multi-expert artifact synthesis.

Replaces mechanical dict/list concatenation with semantic LLM synthesis.
Falls back to :class:`Integrator.merge()` when LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .integrator import Artifact, IntegratedArtifact, Integrator

if TYPE_CHECKING:
    from .llm_client import LLMClient

logger = logging.getLogger(__name__)


class LLMIntegrator:
    """LLM-powered artifact synthesis replacing mechanical merge.

    Uses an LLM to:
    1. Understand semantic content of each expert's output
    2. Resolve conflicts with reasoning (not just severity comparison)
    3. Generate a coherent unified report

    Falls back to :class:`Integrator.merge()` when no LLM client is
    available or the LLM call fails.
    """

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client

    def synthesize(
        self,
        artifacts: list[Artifact],
        task: str,
    ) -> IntegratedArtifact:
        """Synthesize multi-expert artifacts into a unified output.

        Parameters
        ----------
        artifacts:
            List of expert artifacts to synthesize.
        task:
            The original task description (for context).

        Returns
        -------
        IntegratedArtifact
            Unified output with synthesized content.
        """
        if not artifacts:
            raise ValueError("Need at least one artifact to synthesize")

        # Single artifact: no synthesis needed
        if len(artifacts) == 1:
            art = artifacts[0]
            return IntegratedArtifact(
                source_agents=[art.source_agent],
                merged_sections=art.sections,
            )

        if self._client is None:
            logger.debug("LLMIntegrator: no LLM client, falling back to rules")
            return Integrator.merge(artifacts)

        try:
            return self._llm_synthesize(artifacts, task)
        except Exception:
            logger.exception("LLMIntegrator: LLM call failed, falling back to rules")
            return Integrator.merge(artifacts)

    def _llm_synthesize(
        self,
        artifacts: list[Artifact],
        task: str,
    ) -> IntegratedArtifact:
        """Perform LLM-based synthesis."""
        system_prompt = self._build_synthesis_prompt(artifacts)
        user_message = f"Original task: {task}\n\nPlease synthesize the expert outputs above into a unified analysis."

        response = self._client.call(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        return self._parse_synthesis(response.text, artifacts)

    def _build_synthesis_prompt(self, artifacts: list[Artifact]) -> str:
        """Build system prompt with all expert outputs."""
        expert_outputs: list[str] = []
        for art in artifacts:
            sections_str = "\n".join(
                f"  {k}: {v}" for k, v in art.sections.items()
            )
            expert_outputs.append(
                f"Expert: {art.source_agent}\n{sections_str}"
            )

        return (
            "You are a synthesis specialist. Multiple experts have analyzed a task "
            "and provided their findings. Your job is to:\n"
            "1. Combine their insights into a coherent summary\n"
            "2. Resolve any conflicting recommendations with reasoning\n"
            "3. Identify gaps or blind spots in the expert analyses\n"
            "4. Produce a unified set of recommendations\n\n"
            "Expert outputs:\n\n"
            + "\n\n".join(expert_outputs)
            + "\n\nRespond with ONLY a JSON object:\n"
            "{\n"
            '  "summary": "unified summary",\n'
            '  "recommendations": ["rec1", "rec2"],\n'
            '  "conflicts": [{"field": "...", "description": "...", "resolution": "..."}],\n'
            '  "gaps": ["gap1"],\n'
            '  "risks": ["risk1"]\n'
            "}"
        )

    def _parse_synthesis(
        self,
        raw: str,
        artifacts: list[Artifact],
    ) -> IntegratedArtifact:
        """Parse LLM synthesis response into IntegratedArtifact."""
        source_agents = [a.source_agent for a in artifacts]

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLMIntegrator: failed to parse JSON, using raw text")
            return IntegratedArtifact(
                source_agents=source_agents,
                merged_sections={"synthesis": raw},
            )

        merged_sections: dict[str, object] = {}
        if "summary" in data:
            merged_sections["summary"] = data["summary"]
        if "recommendations" in data:
            merged_sections["recommendations"] = data["recommendations"]

        # Preserve original expert sections as sub-keys
        for art in artifacts:
            prefix = art.source_agent.split(".")[-1]
            for key, value in art.sections.items():
                merged_sections[f"{prefix}.{key}"] = value

        merged_sections["decision_summary"] = (
            f"LLM-synthesized {len(artifacts)} expert outputs"
        )

        from .integrator import ConflictItem

        conflicts = []
        for c in data.get("conflicts", []):
            conflicts.append(ConflictItem(
                field=c.get("field", "unknown"),
                description=c.get("description", ""),
                agents=source_agents,
            ))

        return IntegratedArtifact(
            source_agents=source_agents,
            merged_sections=merged_sections,
            conflicts=conflicts,
            risks=data.get("risks", []),
            open_questions=data.get("gaps", []),
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_llm_integrator.py -v`

Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_integrator.py tests/unit/test_llm_integrator.py
git commit -m "feat(agency): add LLMIntegrator for semantic artifact synthesis with rule fallback"
```

---

## Task 5: LLMQualityGate — Semantic Quality Evaluation

**Files:**
- Create: `src/agent_nexus/platform/agency/llm_qa_gate.py`
- Create: `tests/unit/test_llm_qa_gate.py`

Add a semantic quality evaluation layer on top of the existing structural `QAGate`.

- [ ] **Step 1: Write test for LLMQualityGate**

Create `tests/unit/test_llm_qa_gate.py`:

```python
"""Tests for LLMQualityGate — LLM-powered quality evaluation."""

import json
import pytest
from unittest.mock import MagicMock

from agent_nexus.platform.agency.integrator import IntegratedArtifact
from agent_nexus.platform.agency.llm_qa_gate import LLMQualityGate
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.qa_gate import QAGateResult


def _make_integrated():
    return IntegratedArtifact(
        source_agents=["agency.expert-a", "agency.expert-b"],
        merged_sections={
            "summary": "Security and architecture issues found",
            "recommendations": ["Fix SQL injection", "Reduce coupling"],
        },
    )


@patch("agent_nexus.platform.agency.llm_qa_gate.LLMClient")
def test_llm_qa_gate_evaluates(MockLLMClient):
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps({
            "passed": True,
            "score": 0.85,
            "issues": [],
            "coverage": {
                "task_addressed": True,
                "depth_sufficient": True,
                "recommendations_actionable": True,
            },
        }),
        model="test-model",
        provider="test",
    )
    MockLLMClient.return_value = mock_client

    gate = LLMQualityGate(client=mock_client)
    result = gate.evaluate(_make_integrated(), task="Review payment system")

    assert result.passed is True
    mock_client.call.assert_called_once()


@patch("agent_nexus.platform.agency.llm_qa_gate.LLMClient")
def test_llm_qa_gate_flags_issues(MockLLMClient):
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps({
            "passed": False,
            "score": 0.4,
            "issues": ["No security analysis provided", "Recommendations too vague"],
            "coverage": {
                "task_addressed": True,
                "depth_sufficient": False,
                "recommendations_actionable": False,
            },
        }),
        model="test-model",
        provider="test",
    )
    MockLLMClient.return_value = mock_client

    gate = LLMQualityGate(client=mock_client)
    result = gate.evaluate(_make_integrated(), task="Security audit of payment system")

    assert result.passed is False
    assert len(result.failures) > 0


def test_llm_qa_gate_fallback_to_structural():
    """When no client, runs structural QAGate only."""
    gate = LLMQualityGate(client=None)
    result = gate.evaluate(_make_integrated(), task="review")

    # Structural check passes because sections exist
    assert isinstance(result, QAGateResult)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_qa_gate.py -v`

Expected: FAIL (module not found)

- [ ] **Step 3: Implement LLMQualityGate**

Create `src/agent_nexus/platform/agency/llm_qa_gate.py`:

```python
"""LLMQualityGate — LLM-powered quality evaluation for agency artifacts.

Adds a semantic quality evaluation layer on top of the existing structural
:class:`QAGate`.  The LLM evaluates content relevance, depth, and completeness.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .integrator import IntegratedArtifact
from .qa_gate import QAGate, QAGateInput, QAGateResult

if TYPE_CHECKING:
    from .llm_client import LLMClient

logger = logging.getLogger(__name__)

# Minimum score threshold for LLM QA pass
_PASS_THRESHOLD = 0.6


class LLMQualityGate:
    """LLM-powered quality evaluation replacing structural-only checks.

    Two-layer evaluation:
    1. **Structural** (always): Checks required sections exist and non-empty.
    2. **Semantic** (when LLM available): Evaluates content relevance, depth,
       and completeness against the original task.

    Falls back to structural-only when no LLM client is available.
    """

    def __init__(
        self,
        client: LLMClient | None = None,
        pass_threshold: float = _PASS_THRESHOLD,
    ) -> None:
        self._client = client
        self._pass_threshold = pass_threshold

    def evaluate(
        self,
        integrated: IntegratedArtifact,
        task: str,
        required_sections: list[str] | None = None,
        task_type: str = "plan",
    ) -> QAGateResult:
        """Evaluate integrated output quality.

        Parameters
        ----------
        integrated:
            The integrated artifact from multiple experts.
        task:
            The original task description.
        required_sections:
            Sections that must be present (structural check).
        task_type:
            Task type for GitNexus gate check.

        Returns
        -------
        QAGateResult
            Pass/fail with detailed failures list.
        """
        # Layer 1: Structural check (always runs)
        sections = required_sections or []
        structural_input = QAGateInput(
            output={"sections": integrated.merged_sections},
            required_sections=sections,
            task_type=task_type,
        )
        structural_result = QAGate.run(structural_input)

        if not structural_result.passed:
            return structural_result

        # Layer 2: Semantic check (LLM)
        if self._client is None:
            logger.debug("LLMQualityGate: no LLM client, structural-only")
            return structural_result

        try:
            return self._llm_evaluate(integrated, task, structural_result)
        except Exception:
            logger.exception("LLMQualityGate: LLM call failed, structural-only")
            return structural_result

    def _llm_evaluate(
        self,
        integrated: IntegratedArtifact,
        task: str,
        structural_result: QAGateResult,
    ) -> QAGateResult:
        """Run LLM-based semantic evaluation."""
        sections_preview = "\n".join(
            f"  {k}: {str(v)[:200]}..."
            for k, v in list(integrated.merged_sections.items())[:10]
        )

        system_prompt = (
            "You are a quality assurance evaluator for expert analysis reports. "
            "Given the original task and the synthesized expert output, evaluate:\n"
            "1. Whether all aspects of the task are addressed\n"
            "2. Whether the depth of analysis is sufficient\n"
            "3. Whether the recommendations are actionable\n\n"
            "Respond with ONLY a JSON object:\n"
            "{\n"
            '  "passed": true/false,\n'
            '  "score": 0.0-1.0,\n'
            '  "issues": ["issue1", "issue2"],\n'
            '  "coverage": {\n'
            '    "task_addressed": true/false,\n'
            '    "depth_sufficient": true/false,\n'
            '    "recommendations_actionable": true/false\n'
            '  }\n'
            "}"
        )
        user_message = (
            f"Original task: {task}\n\n"
            f"Experts consulted: {', '.join(integrated.source_agents)}\n\n"
            f"Synthesized output:\n{sections_preview}"
        )

        response = self._client.call(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        return self._parse_evaluation(response.text, structural_result)

    def _parse_evaluation(
        self,
        raw: str,
        structural_result: QAGateResult,
    ) -> QAGateResult:
        """Parse LLM evaluation response."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLMQualityGate: failed to parse JSON, returning structural result")
            return structural_result

        score = data.get("score", 0.0)
        issues = data.get("issues", [])
        passed = data.get("passed", score >= self._pass_threshold)

        failures: list[str] = []
        if not passed:
            failures.append(f"LLM quality score: {score:.2f} (threshold: {self._pass_threshold})")
            for issue in issues:
                failures.append(f"Quality issue: {issue}")

        # Merge with structural failures
        failures.extend(structural_result.failures)

        return QAGateResult(
            passed=passed and structural_result.passed,
            contract_result=structural_result.contract_result,
            gitnexus_result=structural_result.gitnexus_result,
            failures=failures,
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_llm_qa_gate.py -v`

Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_qa_gate.py tests/unit/test_llm_qa_gate.py
git commit -m "feat(agency): add LLMQualityGate for semantic quality evaluation"
```

---

## Task 6: Wire LLM Components into TaskComposer

**Files:**
- Modify: `src/agent_nexus/platform/agency/task_composer.py:142-348`

Wire `LLMPlanner`, `LLMIntegrator`, and `LLMQualityGate` into `TaskComposer.run()`. Uses dependency injection — accepts optional LLM components; falls back to rule-based when not provided.

- [ ] **Step 1: Write test for TaskComposer with LLM components**

Add to `tests/unit/test_task_composer.py`:

```python
import json
from unittest.mock import MagicMock

from agent_nexus.platform.agency.task_composer import TaskComposer, TaskComposerInput
from agent_nexus.platform.agency.integrator import IntegratedArtifact
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.registry import ExpertRegistry


def _registry_with_two_experts():
    registry = ExpertRegistry()
    registry.add("agency.reviewer", {
        "id": "agency.reviewer",
        "name": "Code Reviewer",
        "capabilities": ["code_review"],
        "permissions": {"mode": "plan"},
        "output_contract": {"artifact_type": "report", "required_sections": ["summary"]},
        "profile": {"body": "You review code."},
    }, ["code_review"])
    registry.add("agency.security", {
        "id": "agency.security",
        "name": "Security Expert",
        "capabilities": ["security_review"],
        "permissions": {"mode": "plan"},
        "output_contract": {"artifact_type": "report", "required_sections": ["summary"]},
        "profile": {"body": "You review security."},
    }, ["security_review"])
    return registry


def test_task_composer_with_llm_planner():
    """TaskComposer uses LLMPlanner when provided."""
    registry = _registry_with_two_experts()
    composer = TaskComposer(registry)

    # Mock LLMPlanner
    from agent_nexus.platform.agency.llm_planner import PlannerOutput
    mock_planner = MagicMock()
    mock_planner.analyze_task.return_value = PlannerOutput(
        capabilities=["code_review", "security_review"],
    )

    input_ = TaskComposerInput(task="review code for security issues", mode="review")
    result = composer.run(input_, llm_planner=mock_planner)

    mock_planner.analyze_task.assert_called_once_with("review code for security issues")
    assert result.selected_agents  # Should select experts


def test_task_composer_with_llm_integrator():
    """TaskComposer uses LLMIntegrator when provided."""
    registry = _registry_with_two_experts()
    composer = TaskComposer(registry)

    mock_integrator = MagicMock()
    mock_integrator.synthesize.return_value = IntegratedArtifact(
        source_agents=["agency.reviewer", "agency.security"],
        merged_sections={"summary": "LLM-synthesized output"},
    )

    input_ = TaskComposerInput(task="review code", mode="review")
    result = composer.run(input_, llm_integrator=mock_integrator)

    assert result.integrated is not None
    assert "summary" in result.integrated.merged_sections
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_task_composer.py::test_task_composer_with_llm_planner tests/unit/test_task_composer.py::test_task_composer_with_llm_integrator -v`

Expected: FAIL (TaskComposer.run doesn't accept llm_planner/llm_integrator kwargs yet)

- [ ] **Step 3: Modify TaskComposer to accept LLM components**

Edit `task_composer.py` — modify `TaskComposer.run()` signature and body:

1. Add import at top:
```python
from typing import TYPE_CHECKING
```

2. Add to `TYPE_CHECKING` block (or create one if missing):
```python
if TYPE_CHECKING:
    from .llm_planner import LLMPlanner, PlannerOutput
    from .llm_integrator import LLMIntegrator
    from .llm_qa_gate import LLMQualityGate
```

3. Modify `TaskComposer.run()` signature — add optional kwargs + `concurrent` flag:
```python
    def run(
        self,
        input: TaskComposerInput,
        expert_executor: ExpertExecutor | None = None,
        task_graph: TaskGraph | None = None,
        llm_planner: LLMPlanner | None = None,
        llm_integrator: LLMIntegrator | None = None,
        llm_qa_gate: LLMQualityGate | None = None,
        concurrent: bool = False,
    ) -> TaskComposerResult:
```

Also pass `concurrent` through to `DAGDispatcher` (around line 233):
```python
        dispatcher = DAGDispatcher(
            graph=task_graph,
            executor=executor,
            max_batch_size=input.max_parallel,
            timeout_seconds=input.timeout_seconds,
            concurrent=concurrent,
        )
```

4. Replace Step 1 (capability inference) — change line ~184:
```python
        # Step 1: Infer capabilities (LLM or keyword fallback)
        if llm_planner is not None:
            planner_output = llm_planner.analyze_task(input.task)
            required_caps = planner_output.capabilities
        else:
            required_caps = infer_capabilities(input.task)
```

5. Replace Step 5 (integration) — change line ~322:
```python
        # Step 5: Integrate (LLM or rule-based fallback)
        if llm_integrator is not None:
            integrated = llm_integrator.synthesize(artifacts, task=input.task)
        else:
            integrated = Integrator.merge(artifacts)
```

6. Replace Step 6 (QA) — change line ~338:
```python
        # Step 6: QA Gate validation (LLM or structural-only)
        first_profile = self.registry.get(selected[0].agent_id)
        required_sections: list[str] = []
        if first_profile:
            required_sections = first_profile.get("output_contract", {}).get(
                "required_sections", []
            )

        if llm_qa_gate is not None:
            qa_result = llm_qa_gate.evaluate(
                integrated,
                task=input.task,
                required_sections=required_sections,
                task_type=input.mode,
            )
        else:
            gate_input = QAGateInput(
                output={"sections": integrated.merged_sections},
                required_sections=required_sections,
                task_type=input.mode,
            )
            qa_result = QAGate.run(gate_input)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_task_composer.py -v`

Expected: All tests PASS (including existing ones — backward compatible)

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run pytest tests/unit/ -v --timeout=30`

Expected: All unit tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_nexus/platform/agency/task_composer.py tests/unit/test_task_composer.py
git commit -m "feat(agency): wire LLMPlanner/LLMIntegrator/LLMQualityGate into TaskComposer"
```

---

## Task 6.5: Integration Test — LLM Pipeline End-to-End

**Files:**
- Create: `tests/integration/test_llm_pipeline_e2e.py`

Verify that LLMPlanner → SpecialistSelector → LLMExecutor → LLMIntegrator → LLMQualityGate work end-to-end through TaskComposer with mocked LLMClient.

- [ ] **Step 1: Create integration test**

Create `tests/integration/test_llm_pipeline_e2e.py`:

```python
"""Integration test: LLM pipeline end-to-end through TaskComposer."""
import json
from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.task_composer import TaskComposer, TaskComposerInput
from agent_nexus.platform.agency.integrator import IntegratedArtifact
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.llm_planner import PlannerOutput
from agent_nexus.platform.agency.qa_gate import QAGateResult
from agent_nexus.platform.agency.registry import ExpertRegistry


def _registry_with_experts():
    registry = ExpertRegistry()
    registry.add("agency.reviewer", {
        "id": "agency.reviewer",
        "name": "Code Reviewer",
        "capabilities": ["code_review"],
        "permissions": {"mode": "plan"},
        "output_contract": {"artifact_type": "report", "required_sections": ["summary"]},
        "profile": {"body": "You review code."},
    }, ["code_review"])
    registry.add("agency.security", {
        "id": "agency.security",
        "name": "Security Expert",
        "capabilities": ["security_review"],
        "permissions": {"mode": "plan"},
        "output_contract": {"artifact_type": "report", "required_sections": ["summary"]},
        "profile": {"body": "You review security."},
    }, ["security_review"])
    return registry


@patch("agent_nexus.platform.agency.executor.LLMClient")
def test_llm_pipeline_e2e_with_mocked_llm(MockLLMClient):
    """Full pipeline with mocked LLM at every stage."""
    # Mock LLMClient for executor (2 experts = 2 calls)
    mock_exec_client = MagicMock()
    mock_exec_client.call.side_effect = [
        LLMResponse(text="## summary\nCode looks good", model="test", provider="test"),
        LLMResponse(text="## summary\nNo security issues", model="test", provider="test"),
    ]
    mock_exec_client.model_name = "test-model"
    MockLLMClient.return_value = mock_exec_client

    registry = _registry_with_experts()

    # Mock planner
    mock_planner = MagicMock()
    mock_planner.analyze_task.return_value = PlannerOutput(
        capabilities=["code_review", "security_review"],
    )

    # Mock integrator
    mock_integrator = MagicMock()
    mock_integrator.synthesize.return_value = IntegratedArtifact(
        source_agents=["agency.reviewer", "agency.security"],
        merged_sections={"summary": "Synthesized result"},
    )

    # Mock QA gate
    mock_qa = MagicMock()
    mock_qa.evaluate.return_value = QAGateResult(
        passed=True,
        contract_result=MagicMock(passed=True, missing_sections=[]),
        gitnexus_result=MagicMock(passed=True, skipped=True, failed_checks=[]),
        failures=[],
    )

    composer = TaskComposer(registry)
    result = composer.run(
        TaskComposerInput(task="review code for security", mode="review"),
        llm_planner=mock_planner,
        llm_integrator=mock_integrator,
        llm_qa_gate=mock_qa,
    )

    # Verify pipeline executed all stages
    mock_planner.analyze_task.assert_called_once()
    mock_integrator.synthesize.assert_called_once()
    mock_qa.evaluate.assert_called_once()
    assert result.qa_passed is True
    assert result.integrated is not None
    assert len(result.selected_agents) > 0


@patch("agent_nexus.platform.agency.executor.LLMClient")
def test_llm_pipeline_fallback_without_llm(MockLLMClient):
    """Without LLM components, pipeline uses rule-based fallback."""
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text="## summary\nTest output", model="test", provider="test",
    )
    mock_client.model_name = "test"
    MockLLMClient.return_value = mock_client

    registry = _registry_with_experts()
    composer = TaskComposer(registry)
    result = composer.run(
        TaskComposerInput(task="review code", mode="review"),
    )

    # Pipeline should complete with rule-based components
    assert result.integrated is not None
    assert result.qa_passed is not None
```

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/integration/test_llm_pipeline_e2e.py -v`

Expected: 2 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_llm_pipeline_e2e.py
git commit -m "test(agency): add integration test for LLM pipeline end-to-end"
```

---


**Files:**
- Modify: `src/agent_nexus/platform/agency/cli.py:310-467`

Add `--use-llm` flag to `run-composition` command. When set, creates LLMPlanner/LLMIntegrator/LLMQualityGate instances and passes them to TaskComposer.

**Design decision**: The CLI currently duplicates TaskComposer's pipeline steps (infer → select → DAG → dispatch → integrate → QA) manually. To avoid double-execution when LLM components are enabled, the CLI delegates ALL steps after expert loading to `TaskComposer.run()`. Expert loading stays in CLI because it depends on CLI-specific `--vendor-path` and `--allowlist` flags.

- [ ] **Step 1: Add `--use-llm` option to run-composition**

Edit `cli.py` — add option to `run_composition` function signature:

```python
@click.option("--use-llm", is_flag=True, default=False, help="Use LLM for planning, integration, and QA (requires API config)")
def run_composition(
    task: str,
    mode: str,
    max_parallel: int,
    vendor_path: str,
    allowlist: str,
    model: str | None,
    config_dir: str | None,
    use_llm: bool,
) -> None:
```

- [ ] **Step 2: Refactor run_composition to delegate to TaskComposer**

Replace the manual pipeline code (current lines 352-462, from capability inference through QA) with TaskComposer-based execution. The expert loading (lines 336-350) stays in CLI.

**Replace lines 352-462** with:

```python
    # Step 2-6: Delegate to TaskComposer (avoids duplicating pipeline logic)
    from .task_composer import TaskComposer, TaskComposerInput

    # Initialize LLM components if requested
    llm_planner = None
    llm_integrator = None
    llm_qa_gate = None

    if use_llm:
        try:
            from .llm_client import LLMClient
            from .llm_planner import LLMPlanner
            from .llm_integrator import LLMIntegrator
            from .llm_qa_gate import LLMQualityGate

            config_path = Path(config_dir) if config_dir else None
            planner_client = LLMClient(
                model_string=model,  # explicit model from CLI
                stage="planning",
                config_dir=config_path,
            )
            integrator_client = LLMClient(
                model_string=model,
                stage="integration",
                config_dir=config_path,
            )
            qa_client = LLMClient(
                model_string=model,
                stage="qa",
                config_dir=config_path,
            )

            llm_planner = LLMPlanner(registry=registry, client=planner_client)
            llm_integrator = LLMIntegrator(client=integrator_client)
            llm_qa_gate = LLMQualityGate(client=qa_client)
            click.echo("LLM-powered planning, integration, and QA enabled")
        except Exception as exc:
            click.echo(
                f"Warning: LLM components unavailable ({exc}), "
                "using rule-based fallback. Check config.toml and API key.",
                err=True,
            )

    # Create executor (LLM for experts if config available)
    from .executor import LLMExecutor, ProfileBasedExecutor

    try:
        executor = LLMExecutor(
            registry=registry,
            model_string=model,
            config_dir=Path(config_dir) if config_dir else None,
        )
        click.echo(f"Using LLM executor (model: {executor._model_name})")
    except Exception as exc:
        click.echo(
            f"LLM config unavailable ({exc}), "
            "falling back to profile-based executor",
            err=True,
        )
        executor = ProfileBasedExecutor(registry=registry)

    from agent_nexus.platform.orchestration.task_graph import TaskGraph

    graph = TaskGraph(":memory:")
    composer = TaskComposer(registry)
    composer_input = TaskComposerInput(
        task=task,
        mode=mode,
        max_parallel=max_parallel,
        timeout_seconds=120.0,
    )
    composer_result = composer.run(
        composer_input,
        expert_executor=executor,
        task_graph=graph,
        llm_planner=llm_planner,
        llm_integrator=llm_integrator,
        llm_qa_gate=llm_qa_gate,
        concurrent=True,  # LLM calls are I/O-bound
    )
    graph.close()

    # Output results
    click.echo("\n=== Composition Result ===")
    click.echo(f"Selected: {len(composer_result.selected_agents)} experts")
    click.echo(f"QA passed: {composer_result.qa_passed}")
    if composer_result.skipped_tasks:
        click.echo(f"Skipped: {composer_result.skipped_tasks}")

    if composer_result.integrated:
        click.echo("\n--- Merged Output ---")
        for key, value in composer_result.integrated.merged_sections.items():
            click.echo(f"\n## {key}")
            click.echo(str(value))
    else:
        click.echo("No artifacts produced — all experts failed.")
```

Note: This replaces ALL manual pipeline steps (capacity inference, specialist selection, DAG building, dispatch, integration, QA) with a single `TaskComposer.run()` call. Expert loading stays in CLI. This eliminates the double-execution bug and ensures CLI always uses the same pipeline as programmatic callers.

- [ ] **Step 3: Verify CLI help shows new flag**

Run: `uv run python -m agent_nexus.platform.agency.cli run-composition --help`

Expected: Shows `--use-llm` flag in help text

- [ ] **Step 4: Run existing tests**

Run: `uv run pytest tests/ -v --timeout=60 -x`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/cli.py
git commit -m "feat(agency): add --use-llm flag to run-composition, delegate pipeline to TaskComposer"
```

---

## Task 8: Drop Stale Stash + Final Verification

**Files:**
- N/A (git housekeeping)

- [ ] **Step 1: Drop the applied stash**

```bash
git stash drop stash@{0}
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=60`

Expected: All tests PASS

- [ ] **Step 3: Run lint**

Run: `uv run ruff check src/agent_nexus/platform/agency/`

Expected: No errors

- [ ] **Step 4: Verify all new files exist**

Run: `ls -la src/agent_nexus/platform/agency/llm_planner.py src/agent_nexus/platform/agency/llm_integrator.py src/agent_nexus/platform/agency/llm_qa_gate.py src/agent_nexus/platform/agency/llm_client.py`

Expected: All 4 files exist

---

## Self-Review Checklist

**1. Spec coverage:**
- Per-stage model config: Task 1 (config.py + resolve_stage_model)
- Per-expert model override: Task 2 (LLMExecutor refactor)
- LLM Planner: Task 3
- LLM Integrator: Task 4
- LLM QA Gate: Task 5
- TaskComposer wiring + concurrent flag: Task 6
- Integration test E2E: Task 6.5
- CLI integration (no duplication): Task 7
- All covered.

**2. Placeholder scan:** No TBD, TODO, "implement later", "add error handling" patterns found. All steps have complete code.

**3. Type consistency:**
- `LLMPlanner.analyze_task()` returns `PlannerOutput` → TaskComposer accesses `.capabilities` ✅
- `LLMIntegrator.synthesize()` returns `IntegratedArtifact` → TaskComposer accesses `.merged_sections` ✅
- `LLMQualityGate.evaluate()` returns `QAGateResult` → TaskComposer accesses `.passed` ✅
- `LLMClient.call()` returns `LLMResponse` with `.text`, `.model`, `.provider` ✅
- `ExpertExecutor` protocol: `__call__(profile_id: str, task: str) -> Artifact` — unchanged ✅

**4. Atomic dependencies checked:**
- Task 1: `stages` field + `resolve_stage_model()` + executor refactor = atomic unit ✅
- Task 7: CLI delegates ALL pipeline steps to TaskComposer (no duplication) ✅
- Task 6: `concurrent` parameter passes through to DAGDispatcher ✅
- Task 6.5: Integration test covers LLM + fallback paths ✅

**5. Model resolution priority aligned:**
- README / plan / `LLMClient.__init__` all agree on priority order ✅
- `LLMClient(stage="planning")` works after `resolve_stage_model()` added ✅
