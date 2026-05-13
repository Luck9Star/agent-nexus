"""Tests for TaskComposer — end-to-end DAG orchestration."""

from pathlib import Path

import pytest

from agent_nexus.platform.agency.importer import AgencyImporter
from agent_nexus.platform.agency.integrator import Artifact
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.task_composer import (
    TaskComposer,
    TaskComposerInput,
    TaskComposerResult,
    detect_output_target,
    infer_capabilities,
)

# Paths
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"


def _build_composer() -> TaskComposer:
    """Create a TaskComposer with real importer-loaded registry."""
    importer = AgencyImporter(
        vendor_path=str(_VENDOR_DIR),
        allowlist_path=str(_ALLOWLIST_PATH),
        output_dir="/tmp/agency-test-composer",
    )
    profiles = importer.dry_run()

    registry = ExpertRegistry()
    for pkg in profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])

    return TaskComposer(registry=registry)


@pytest.mark.timeout(30)
class TestTaskComposerInput:
    """TaskComposerInput dataclass validates fields."""


@pytest.mark.timeout(30)
class TestTaskComposerSelect:
    """TaskComposer selects specialists based on task."""

    def test_select_returns_specialists(self) -> None:
        composer = _build_composer()
        inp = TaskComposerInput(
            task="Design a system architecture",
            mode="plan",
        )
        result = composer.run(inp)
        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) > 0


@pytest.mark.timeout(30)
class TestTaskComposerFullRun:
    """TaskComposer runs the full pipeline with mock expert execution."""

    def test_full_run_with_mock_experts(self) -> None:
        composer = _build_composer()
        inp = TaskComposerInput(
            task="Design a system architecture",
            mode="plan",
        )

        # Provide mock expert executor
        def mock_executor(profile_id: str, task: str) -> Artifact:
            return Artifact(
                source_agent=profile_id,
                artifact_type="architecture_plan",
                sections={
                    "context": task,
                    "recommendation": f"Recommendation from {profile_id}",
                    "risks": ["Token cost may increase"],
                },
            )

        result = composer.run(inp, expert_executor=mock_executor)
        assert isinstance(result, TaskComposerResult)
        assert result.integrated is not None
        assert result.qa_passed is not None


@pytest.mark.timeout(30)
class TestTaskComposerNoMatch:
    """TaskComposer handles case where no specialist matches."""

    def test_no_matching_specialist(self) -> None:
        composer = _build_composer()
        inp = TaskComposerInput(
            task="Design a rocket engine",  # no expert has this capability
            mode="plan",
        )
        result = composer.run(inp)
        assert isinstance(result, TaskComposerResult)
        # Should still succeed — may select best available or return empty
        # The key is it doesn't crash


@pytest.mark.timeout(30)
class TestTaskComposerWithTaskGraph:
    """TaskComposer.run() with task_graph parameter uses DAGDispatcher path."""

    def test_task_composer_with_task_graph(self) -> None:
        """When task_graph is provided, DAGDispatcher path is used."""
        from agent_nexus.platform.orchestration.task_graph import TaskGraph

        composer = _build_composer()
        graph = TaskGraph(":memory:")

        inp = TaskComposerInput(
            task="Design a system architecture",
            mode="plan",
            max_parallel=2,
        )

        # Provide mock expert executor that returns artifacts
        def mock_executor(profile_id: str, task: str) -> Artifact:
            return Artifact(
                source_agent=profile_id,
                artifact_type="report",
                sections={
                    "context": f"Analysis from {profile_id}",
                    "risks": [f"Risk by {profile_id}"],
                    "next_steps": ["Fix issues"],
                },
            )

        result = composer.run(inp, expert_executor=mock_executor, task_graph=graph)

        assert isinstance(result, TaskComposerResult)
        assert result.selected_agents is not None
        assert len(result.selected_agents) > 0
        assert result.dag is not None
        assert result.integrated is not None
        assert result.qa_passed is not None


# ---------------------------------------------------------------------------
# C3 fix: Chinese capability inference
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestChineseCapabilityInference:
    """infer_capabilities correctly maps Chinese task descriptions."""

    def test_chinese_architecture_keywords(self) -> None:
        """Chinese keywords like '架构设计' infer system_design and architecture_review."""
        caps = infer_capabilities("进行架构设计")
        assert "system_design" in caps
        assert "architecture_review" in caps

    def test_mixed_chinese_english(self) -> None:
        """Mixed Chinese+English tasks get capabilities from both."""
        caps = infer_capabilities("架构设计 security review")
        assert "system_design" in caps
        assert "architecture_review" in caps
        assert "code_review" in caps or "security_review" in caps

    def test_pure_chinese_security(self) -> None:
        """Pure Chinese '安全评审' gets security capabilities."""
        caps = infer_capabilities("安全评审")
        assert "security_review" in caps

    def test_unknown_chinese_returns_empty(self) -> None:
        """Chinese text with no matching keywords returns empty list."""
        caps = infer_capabilities("这是一段随机文字没有关键词")
        assert caps == []

    def test_deduplication_across_maps(self) -> None:
        """Deduplication when both Chinese and English keywords match same capability.

        'architecture' (English) and '架构' (Chinese) both map to system_design.
        The result should contain system_design exactly once.
        """
        caps = infer_capabilities("architecture 架构")
        assert "system_design" in caps
        # Count occurrences
        assert caps.count("system_design") == 1


class TestDetectOutputTarget:
    """Tests for detect_output_target() — pipeline-level output intent detection."""

    def test_chinese_specific_path(self) -> None:
        assert detect_output_target("设计架构，输出到 docs/arch.md") == "docs/arch.md"

    def test_english_specific_path(self) -> None:
        assert detect_output_target("Review the API, output to reviews/api.md") == "reviews/api.md"

    def test_save_to_path(self) -> None:
        assert detect_output_target("save to reports/result.md") == "reports/result.md"

    def test_chinese_write_to_path(self) -> None:
        assert detect_output_target("写入 docs/plan.md") == "docs/plan.md"

    def test_generic_file_intent_chinese(self) -> None:
        assert detect_output_target("输出到文件给我") == "file"

    def test_generic_file_intent_english(self) -> None:
        assert detect_output_target("output to file") == "file"

    def test_save_to_file_intent(self) -> None:
        assert detect_output_target("save to file") == "file"

    def test_no_intent(self) -> None:
        assert detect_output_target("Review the architecture design") is None

    def test_generic_wins_when_no_path(self) -> None:
        """'写入文件' without a specific path returns 'file', not None."""
        assert detect_output_target("写入文件") == "file"

    def test_specific_wins_over_generic(self) -> None:
        """When both specific and generic patterns match, specific path wins."""
        result = detect_output_target("输出到 report.md 文件")
        assert result == "report.md"
