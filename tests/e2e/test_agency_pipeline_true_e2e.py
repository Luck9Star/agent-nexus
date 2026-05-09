"""TRUE_E2E: Agency Pipeline with deterministic fake LLM client.

Exercises the full Agency Pipeline (ExpertRegistry -> LLMPlanner ->
LLMExecutor/ProfileBasedExecutor -> LLMIntegrator -> LLMQualityGate ->
TaskComposer) using a DeterministicLLMClient that returns structured JSON
matching each component's expected schema.

No real LLM API calls. No unittest.mock on internals. All real components
except the LLM wire.
"""

from __future__ import annotations

import json

import pytest

from agent_nexus.platform.agency.executor import ProfileBasedExecutor
from agent_nexus.platform.agency.integrator import Artifact, IntegratedArtifact
from agent_nexus.platform.agency.llm_client import LLMClient, LLMResponse
from agent_nexus.platform.agency.llm_integrator import LLMIntegrator
from agent_nexus.platform.agency.llm_planner import LLMPlanner, PlannerOutput
from agent_nexus.platform.agency.llm_qa_gate import LLMQualityGate
from agent_nexus.platform.agency.qa_gate import QAGateResult
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.task_composer import (
    TaskComposer,
    TaskComposerInput,
)

# ---------------------------------------------------------------------------
# Helpers: inline expert profiles
# ---------------------------------------------------------------------------

_ARCHITECT_PROFILE: dict = {
    "id": "agency.architect",
    "name": "System Architect",
    "capabilities": ["system_design", "architecture_review"],
    "routing": {"task_types": ["system_design", "architecture_review"]},
    "permissions": {"mode": "plan"},
    "profile": {"body": "You are a system architecture expert."},
    "output_contract": {
        "artifact_type": "architecture_plan",
        "required_sections": ["context", "proposed_design", "tradeoffs"],
    },
}

_SECURER_PROFILE: dict = {
    "id": "agency.security-reviewer",
    "name": "Security Reviewer",
    "capabilities": ["security_review", "vulnerability_assessment"],
    "routing": {"task_types": ["security_review", "vulnerability_assessment"]},
    "permissions": {"mode": "plan"},
    "profile": {"body": "You are a security review expert."},
    "output_contract": {
        "artifact_type": "review_report",
        "required_sections": ["summary", "findings", "severity"],
    },
}

_RELIABILITY_PROFILE: dict = {
    "id": "agency.reliability-engineer",
    "name": "Reliability Engineer",
    "capabilities": ["reliability_review", "observability"],
    "routing": {"task_types": ["reliability_review", "observability"]},
    "permissions": {"mode": "plan"},
    "profile": {"body": "You are a reliability engineering expert."},
    "output_contract": {
        "artifact_type": "reliability_report",
        "required_sections": ["summary", "incident_analysis", "recommendations"],
    },
}


def _build_registry(*profiles: dict) -> ExpertRegistry:
    """Build a registry with the given profiles."""
    reg = ExpertRegistry()
    for p in profiles:
        reg.add(p["id"], p, p.get("capabilities", []))
    return reg


# ---------------------------------------------------------------------------
# DeterministicLLMClient
# ---------------------------------------------------------------------------


class DeterministicLLMClient(LLMClient):
    """LLMClient that returns deterministic JSON for each pipeline stage.

    Does NOT call super().__init__() because that triggers config loading
    and httpx client creation.  Instead, sets only the attributes that the
    pipeline components read (model_name property).
    """

    def __init__(self, registry: ExpertRegistry | None = None) -> None:
        # Bypass LLMClient.__init__ entirely — we only need the call() API.
        # Set attrs that __del__ / close() may access.
        self._cli_backend = None
        self._provider_name = "deterministic"
        self._model_name = "fake-model"
        self._provider_config = None  # type: ignore[assignment]
        self._api_key = ""
        self._session_store = None
        self._capability_registry = None  # type: ignore[assignment]
        self._capability = None  # type: ignore[assignment]
        self._platform_config = None
        self._hooks = None  # type: ignore[assignment]

        self._call_count = 0
        self._call_history: list[dict[str, str]] = []
        self._registry = registry

    # ------------------------------------------------------------------
    # Public API (duck-type compatible with LLMClient)
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:  # type: ignore[override]
        return self._model_name

    @property
    def provider_name(self) -> str:  # type: ignore[override]
        return self._provider_name

    def call(  # type: ignore[override]
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        timeout: float | None = None,
        session_id: str | None = None,
        response_format: str | None = None,
    ) -> LLMResponse:
        self._call_count += 1
        self._call_history.append(
            {"system_prompt": system_prompt, "user_message": user_message}
        )

        text = self._route(system_prompt, user_message)
        return LLMResponse(
            text=text,
            model=self._model_name,
            provider=self._provider_name,
        )

    def close(self) -> None:
        pass

    def __enter__(self) -> DeterministicLLMClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Routing logic — detect pipeline stage from prompt content
    # ------------------------------------------------------------------

    def _route(self, system_prompt: str, user_message: str) -> str:
        sp = system_prompt.lower()
        # QA gate must be checked BEFORE integrator because the QA prompt
        # contains "synthesized" which also matches the integrator pattern.
        if "quality assurance" in sp or "evaluat" in sp and "assurance" in sp:
            return self._qa_gate_response_pass()
        if "task decomposition" in sp or ("expert" in sp and "select" in sp):
            return self._planner_response(system_prompt)
        if "synthes" in sp or "combine" in sp:
            return self._integrator_response(system_prompt)
        # Default: executor response (## markdown sections)
        return self._executor_response(system_prompt)

    # ------------------------------------------------------------------
    # Stage-specific deterministic responses
    # ------------------------------------------------------------------

    def _planner_response(self, _prompt: str) -> str:
        """Return JSON matching StructuredPlannerOutput schema."""
        expert_ids: list[str] = []
        if self._registry is not None:
            expert_ids = self._registry.list_all()

        selections = [
            {"expert_id": eid, "task": "Analyze the task", "parameters": {}}
            for eid in expert_ids[:3]
        ]

        data = {
            "capabilities": ["system_design", "security_review"],
            "focus_hints": {"agency.architect": "Focus on design"},
            "decomposition_strategy": "parallel",
            "expert_selections": selections,
        }
        return json.dumps(data)

    def _executor_response(self, prompt: str) -> str:
        """Return markdown with ## sections for LLMExecutor parsing."""
        # Detect which sections are required from the prompt
        sections: list[str] = ["summary", "findings", "recommendations"]
        if "required sections" in prompt.lower() or "## markdown" in prompt.lower():
            # Try to extract section names from the prompt
            import re

            match = re.search(r"sections?:\s*(.+?)(?:\n|$)", prompt, re.IGNORECASE)
            if match:
                sections = [s.strip().strip(",") for s in match.group(1).split()]

        parts: list[str] = []
        for section in sections:
            parts.append(f"## {section}")
            parts.append(f"Deterministic content for {section}.")
            parts.append("")

        return "\n".join(parts)

    def _integrator_response(self, _prompt: str) -> str:
        """Return JSON matching LLMIntegrator synthesis schema."""
        data = {
            "summary": "Unified analysis from multiple experts.",
            "recommendations": ["Apply combined expertise", "Review tradeoffs"],
            "conflicts": [],
            "gaps": ["Edge case coverage could be improved"],
            "risks": ["Complexity in integration layer"],
        }
        return json.dumps(data)

    def _qa_gate_response_pass(self) -> str:
        """Return JSON for a passing QA evaluation."""
        data = {
            "passed": True,
            "score": 0.85,
            "issues": [],
            "coverage": {
                "task_addressed": True,
                "depth_sufficient": True,
                "recommendations_actionable": True,
            },
        }
        return json.dumps(data)

    def _qa_gate_response_fail(self) -> str:
        """Return JSON for a failing QA evaluation."""
        data = {
            "passed": False,
            "score": 0.3,
            "issues": ["Insufficient depth", "Missing key analysis"],
            "coverage": {
                "task_addressed": False,
                "depth_sufficient": False,
                "recommendations_actionable": True,
            },
        }
        return json.dumps(data)


# ---------------------------------------------------------------------------
# Failing variant: always returns low-score QA
# ---------------------------------------------------------------------------


class FailingQADeterministicClient(DeterministicLLMClient):
    """Deterministic client where QA gate always fails (score 0.3)."""

    def _route(self, system_prompt: str, user_message: str) -> str:
        sp = system_prompt.lower()
        if ("quality" in sp or "evaluat" in sp) and "assurance" in sp:
            return self._qa_gate_response_fail()
        return super()._route(system_prompt, user_message)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> ExpertRegistry:
    """Registry with 3 expert profiles."""
    return _build_registry(_ARCHITECT_PROFILE, _SECURER_PROFILE, _RELIABILITY_PROFILE)


@pytest.fixture()
def det_client(registry: ExpertRegistry) -> DeterministicLLMClient:
    """Deterministic LLM client wired to the registry."""
    return DeterministicLLMClient(registry=registry)


@pytest.fixture(autouse=True)
def _reset_fallback_counters():
    """Reset class-level fallback counters for test isolation."""
    LLMPlanner.reset_fallback_count()
    LLMIntegrator.reset_fallback_count()
    LLMQualityGate.reset_fallback_count()
    yield


# ===========================================================================
# TestDeterministicPlannerIntegration
# ===========================================================================


class TestDeterministicPlannerIntegration:
    """LLMPlanner with deterministic client returns valid selections."""

    def test_analyze_task_returns_capabilities(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        planner = LLMPlanner(registry, client=det_client)
        result = planner.analyze_task("Design a secure microservice architecture")

        assert isinstance(result, PlannerOutput)
        assert len(result.capabilities) > 0
        assert result.decomposition_strategy in ("parallel", "sequential")

    def test_planner_returns_expert_selections(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        planner = LLMPlanner(registry, client=det_client)
        result = planner.analyze_task("Review system reliability")

        assert len(result.expert_selections) > 0
        for sel in result.expert_selections:
            assert sel.expert_id in registry.list_all()

    def test_planner_no_client_falls_back_to_keywords(self, registry: ExpertRegistry) -> None:
        planner = LLMPlanner(registry, client=None)
        result = planner.analyze_task("Review system architecture for security")

        assert isinstance(result, PlannerOutput)
        # Keyword-based fallback should still produce capabilities
        assert "architecture_review" in result.capabilities or "security_review" in result.capabilities
        assert LLMPlanner.fallback_count() >= 1

    def test_planner_makes_llm_call(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        planner = LLMPlanner(registry, client=det_client)
        planner.analyze_task("Design a reliable API")

        assert det_client._call_count == 1
        assert "task decomposition" in det_client._call_history[0]["system_prompt"].lower()

    def test_planner_focus_hints_populated(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        planner = LLMPlanner(registry, client=det_client)
        result = planner.analyze_task("Analyze architecture")

        # DeterministicLLMClient returns focus_hints for architect
        assert isinstance(result.focus_hints, dict)


# ===========================================================================
# TestDeterministicIntegratorIntegration
# ===========================================================================


class TestDeterministicIntegratorIntegration:
    """LLMIntegrator with deterministic client produces merged artifact."""

    def test_synthesize_two_artifacts(self, det_client: DeterministicLLMClient) -> None:
        integrator = LLMIntegrator(client=det_client)
        artifacts = [
            Artifact(
                source_agent="agency.architect",
                artifact_type="report",
                sections={"summary": "Design analysis", "risks": ["Complexity"]},
            ),
            Artifact(
                source_agent="agency.security-reviewer",
                artifact_type="report",
                sections={"summary": "Security findings", "risks": ["XSS"]},
            ),
        ]

        result = integrator.synthesize(artifacts, task="Review architecture")

        assert isinstance(result, IntegratedArtifact)
        assert len(result.source_agents) == 2
        assert "agency.architect" in result.source_agents
        assert "agency.security-reviewer" in result.source_agents

    def test_synthesize_single_artifact_passthrough(self, det_client: DeterministicLLMClient) -> None:
        integrator = LLMIntegrator(client=det_client)
        artifact = Artifact(
            source_agent="agency.architect",
            artifact_type="report",
            sections={"summary": "Solo analysis"},
        )

        result = integrator.synthesize([artifact], task="Solo task")

        assert result.source_agents == ["agency.architect"]
        assert result.merged_sections["summary"] == "Solo analysis"
        # Single artifact should NOT trigger an LLM call
        assert det_client._call_count == 0

    def test_synthesize_produces_decision_summary(self, det_client: DeterministicLLMClient) -> None:
        integrator = LLMIntegrator(client=det_client)
        artifacts = [
            Artifact(source_agent="a", artifact_type="report", sections={"s": "x"}),
            Artifact(source_agent="b", artifact_type="report", sections={"s": "y"}),
        ]

        result = integrator.synthesize(artifacts, task="Merge these")

        assert "decision_summary" in result.merged_sections

    def test_synthesize_preserves_expert_sections(self, det_client: DeterministicLLMClient) -> None:
        integrator = LLMIntegrator(client=det_client)
        artifacts = [
            Artifact(
                source_agent="agency.architect",
                artifact_type="report",
                sections={"design": "microservice approach"},
            ),
            Artifact(
                source_agent="agency.security-reviewer",
                artifact_type="report",
                sections={"vulns": "SQL injection risk"},
            ),
        ]

        result = integrator.synthesize(artifacts, task="Analyze")

        # Original expert sections preserved as prefixed keys
        assert "architect.design" in result.merged_sections
        assert "security-reviewer.vulns" in result.merged_sections

    def test_integrator_no_client_falls_back(self, det_client: DeterministicLLMClient) -> None:
        integrator = LLMIntegrator(client=None)
        artifacts = [
            Artifact(source_agent="a", artifact_type="report", sections={"x": "1"}),
            Artifact(source_agent="b", artifact_type="report", sections={"y": "2"}),
        ]

        result = integrator.synthesize(artifacts, task="Merge")

        assert isinstance(result, IntegratedArtifact)
        assert LLMIntegrator.fallback_count() >= 1

    def test_synthesize_raises_on_empty_artifacts(self, det_client: DeterministicLLMClient) -> None:
        integrator = LLMIntegrator(client=det_client)
        with pytest.raises(ValueError, match="at least one artifact"):
            integrator.synthesize([], task="Nothing to merge")


# ===========================================================================
# TestDeterministicQAGateIntegration
# ===========================================================================


class TestDeterministicQAGateIntegration:
    """LLMQualityGate with deterministic client passes quality check."""

    def test_evaluate_passes(self, det_client: DeterministicLLMClient) -> None:
        gate = LLMQualityGate(client=det_client)
        integrated = IntegratedArtifact(
            source_agents=["agency.architect"],
            merged_sections={"summary": "Design reviewed", "findings": "No issues"},
        )

        result = gate.evaluate(integrated, task="Review architecture")

        assert isinstance(result, QAGateResult)
        assert result.passed

    def test_evaluate_fails_with_low_score(self, registry: ExpertRegistry) -> None:
        client = FailingQADeterministicClient(registry=registry)
        gate = LLMQualityGate(client=client)
        integrated = IntegratedArtifact(
            source_agents=["agency.architect"],
            merged_sections={"summary": "Shallow analysis"},
        )

        result = gate.evaluate(integrated, task="Review architecture")

        assert not result.passed

    def test_evaluate_no_client_structural_only(self) -> None:
        gate = LLMQualityGate(client=None)
        integrated = IntegratedArtifact(
            source_agents=["agency.architect"],
            merged_sections={"summary": "Good"},
        )

        result = gate.evaluate(
            integrated, task="Review", required_sections=["summary"]
        )

        # Structural check should pass since "summary" is present
        assert result.passed
        assert LLMQualityGate.fallback_count() >= 1

    def test_evaluate_structural_failure_blocks_semantic(self, det_client: DeterministicLLMClient) -> None:
        gate = LLMQualityGate(client=det_client)
        integrated = IntegratedArtifact(
            source_agents=["agency.architect"],
            merged_sections={"summary": "Partial"},
        )

        # Require a section that doesn't exist -> structural fail
        result = gate.evaluate(
            integrated,
            task="Review",
            required_sections=["summary", "missing_section"],
        )

        assert not result.passed
        # LLM should NOT have been called (structural failure short-circuits)
        assert det_client._call_count == 0


# ===========================================================================
# TestDeterministicPipelineFullFlow
# ===========================================================================


class TestDeterministicPipelineFullFlow:
    """Full pipeline: registry -> plan -> execute -> integrate -> QA -> verify."""

    def test_full_pipeline_passes(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        composer = TaskComposer(registry)
        planner = LLMPlanner(registry, client=det_client)
        integrator = LLMIntegrator(client=det_client)
        qa_gate = LLMQualityGate(client=det_client)

        result = composer.run(
            TaskComposerInput(task="Review system architecture for security risks", mode="plan"),
            llm_planner=planner,
            llm_integrator=integrator,
            llm_qa_gate=qa_gate,
        )

        assert result.task == "Review system architecture for security risks"
        assert len(result.selected_agents) > 0
        assert result.integrated is not None
        assert result.qa_passed is True

    def test_full_pipeline_without_llm_fallback(self, registry: ExpertRegistry) -> None:
        composer = TaskComposer(registry)

        result = composer.run(
            TaskComposerInput(task="Review system architecture for security", mode="plan"),
        )

        assert result.task == "Review system architecture for security"
        assert len(result.selected_agents) > 0
        # Without LLM, uses ProfileBasedExecutor + Integrator.merge + QAGate
        assert result.integrated is not None

    def test_full_pipeline_multiple_experts_selected(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        composer = TaskComposer(registry)
        planner = LLMPlanner(registry, client=det_client)
        integrator = LLMIntegrator(client=det_client)
        qa_gate = LLMQualityGate(client=det_client)

        result = composer.run(
            TaskComposerInput(
                task="Design architecture and review security and evaluate reliability",
                mode="plan",
            ),
            llm_planner=planner,
            llm_integrator=integrator,
            llm_qa_gate=qa_gate,
        )

        # Multiple keywords should match multiple experts
        assert len(result.selected_agents) >= 2

    def test_pipeline_detects_output_target(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        composer = TaskComposer(registry)

        result = composer.run(
            TaskComposerInput(task="Review architecture and output to review.md", mode="plan"),
            llm_planner=LLMPlanner(registry, client=det_client),
            llm_integrator=LLMIntegrator(client=det_client),
            llm_qa_gate=LLMQualityGate(client=det_client),
        )

        assert result.output_target == "review.md"

    def test_pipeline_no_matching_experts(self) -> None:
        # Empty registry -> no experts selected
        empty_reg = ExpertRegistry()
        composer = TaskComposer(empty_reg)

        result = composer.run(
            TaskComposerInput(task="Do something impossible", mode="plan"),
        )

        assert result.selected_agents == []
        assert result.integrated is None
        assert result.qa_passed is None

    def test_pipeline_llm_call_count(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        composer = TaskComposer(registry)
        planner = LLMPlanner(registry, client=det_client)
        integrator = LLMIntegrator(client=det_client)
        qa_gate = LLMQualityGate(client=det_client)

        composer.run(
            TaskComposerInput(task="Review architecture", mode="plan"),
            llm_planner=planner,
            llm_integrator=integrator,
            llm_qa_gate=qa_gate,
        )

        # Expect: 1 planner call + 1 integrator call + 1 QA call = 3
        assert det_client._call_count == 3

    def test_pipeline_with_profile_based_executor(self, registry: ExpertRegistry, det_client: DeterministicLLMClient) -> None:
        executor = ProfileBasedExecutor(registry)
        composer = TaskComposer(registry)

        result = composer.run(
            TaskComposerInput(task="Review architecture", mode="plan"),
            expert_executor=executor,
            llm_planner=LLMPlanner(registry, client=det_client),
            llm_integrator=LLMIntegrator(client=det_client),
            llm_qa_gate=LLMQualityGate(client=det_client),
        )

        assert result.integrated is not None
        assert result.dag is not None
        assert len(result.dag.specialist_tasks) > 0
