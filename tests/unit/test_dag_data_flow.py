"""Unit tests for DAG data flow — artifact passing between upstream/downstream tasks.

Covers:
- Executor accepts upstream_artifacts parameter (backward compatible)
- DAGDispatcher collects upstream artifacts and passes to downstream tasks
- Multi-level DAG (A -> B -> C) artifact passing
- Empty artifact handling
- Artifact content injection into prompt via _inject_upstream_context
- Summarizer integration (scheme B)
"""

from __future__ import annotations

import pytest

from agent_nexus.platform.agency.dag_dispatcher import (
    DAGDispatcher,
)
from agent_nexus.platform.agency.executor import (
    ProfileBasedExecutor,
    _inject_upstream_context,
)
from agent_nexus.platform.agency.integrator import Artifact
from agent_nexus.platform.agency.planner import CompositionDAG, DAGTask
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.orchestration.task_graph import TaskGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(agent: str, label: str = "ok") -> Artifact:
    """Create a simple test artifact."""
    return Artifact(source_agent=agent, artifact_type="test", sections={"label": label})


def _build_dag(
    tasks: list[DAGTask],
    name: str = "test-dag",
    max_parallel: int = 3,
) -> CompositionDAG:
    return CompositionDAG(name=name, max_parallel=max_parallel, tasks=tasks)


def _make_registry(*agent_ids: str) -> ExpertRegistry:
    """Create a registry with simple profiles for the given agent IDs."""
    registry = ExpertRegistry()
    for aid in agent_ids:
        registry.add(
            aid,
            {
                "id": aid,
                "name": aid.replace("agency.", "").replace("-", " ").title(),
                "capabilities": ["general"],
                "profile": {"body": f"You are {aid}."},
                "output_contract": {
                    "artifact_type": "report",
                    "required_sections": ["context", "summary"],
                },
            },
            ["general"],
        )
    return registry


# ---------------------------------------------------------------------------
# 1. Executor upstream_artifacts parameter
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestExecutorUpstreamArtifactsParameter:
    """ProfileBasedExecutor accepts upstream_artifacts kwarg without error."""

    def test_accepts_none_upstream(self) -> None:
        """upstream_artifacts=None is the default — backward compatible."""
        registry = _make_registry("agency.test-agent")
        executor = ProfileBasedExecutor(registry)
        artifact = executor("agency.test-agent", "do something", upstream_artifacts=None)
        assert isinstance(artifact, Artifact)

    def test_accepts_artifact_list(self) -> None:
        """upstream_artifacts=[Artifact(...)] is accepted without error."""
        registry = _make_registry("agency.test-agent")
        executor = ProfileBasedExecutor(registry)
        upstream = [_make_artifact("upstream-agent", "upstream-data")]
        artifact = executor("agency.test-agent", "do something", upstream_artifacts=upstream)
        assert isinstance(artifact, Artifact)

    def test_upstream_content_appears_in_context_section(self) -> None:
        """When upstream artifacts are passed, content appears in 'context' section."""
        registry = _make_registry("agency.test-agent")
        executor = ProfileBasedExecutor(registry)
        upstream = [
            Artifact(
                source_agent="upstream-agent",
                artifact_type="report",
                sections={"summary": "upstream analysis result"},
            )
        ]
        artifact = executor("agency.test-agent", "design a system", upstream_artifacts=upstream)
        # The 'context' section should contain the original task + upstream data
        context = artifact.sections.get("context", "")
        assert "design a system" in context
        assert "upstream-agent" in context


# ---------------------------------------------------------------------------
# 2. _inject_upstream_context helper
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestInjectUpstreamContext:
    """_inject_upstream_context formats artifacts into task description."""

    def test_none_returns_original_task(self) -> None:
        result = _inject_upstream_context("do something", None)
        assert result == "do something"

    def test_artifact_appended_to_task(self) -> None:
        upstream = [
            Artifact(
                source_agent="agent-a",
                artifact_type="report",
                sections={"summary": "key finding"},
            )
        ]
        result = _inject_upstream_context("analyze this", upstream)
        assert "analyze this" in result
        assert "Upstream Artifacts" in result
        assert "agent-a" in result
        assert "key finding" in result

    def test_multiple_artifacts(self) -> None:
        upstream = [
            Artifact(source_agent="a1", artifact_type="report", sections={"x": "1"}),
            Artifact(source_agent="a2", artifact_type="report", sections={"y": "2"}),
        ]
        result = _inject_upstream_context("task", upstream)
        assert "a1" in result
        assert "a2" in result
        assert result.count("### Artifact") == 2

    def test_non_artifact_objects_use_str(self) -> None:
        """Non-Artifact objects are converted to string."""
        result = _inject_upstream_context("task", [{"key": "value"}])
        assert "{'key': 'value'}" in result


# ---------------------------------------------------------------------------
# 3. DAGDispatcher collects upstream artifacts
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestDagDispatcherCollectsUpstream:
    """DAGDispatcher passes upstream artifacts to downstream tasks."""

    def test_sequential_chain_a_to_b(self) -> None:
        """A -> B chain: B receives A's artifact as upstream."""
        received_upstream: dict[str, list[Artifact] | None] = {}

        def capturing_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Artifact] | None = None,
        ) -> Artifact:
            received_upstream[profile_id] = upstream_artifacts
            return _make_artifact(profile_id, f"{profile_id}-output")

        dag = _build_dag(
            [
                DAGTask(id="a", agent="agent-a", output="o1"),
                DAGTask(id="b", agent="agent-b", output="o2", blocked_by=["a"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, capturing_executor)
        result = dispatcher.dispatch(dag, "test chain")

        assert set(result.completed) == {"a", "b"}
        # A should have no upstream (None)
        assert received_upstream["agent-a"] is None
        # B should have A's artifact as upstream
        assert received_upstream["agent-b"] is not None
        assert len(received_upstream["agent-b"]) == 1
        assert received_upstream["agent-b"][0].source_agent == "agent-a"

    def test_parallel_tasks_no_upstream(self) -> None:
        """Parallel tasks with no dependencies receive None upstream."""
        received_upstream: dict[str, list[Artifact] | None] = {}

        def capturing_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Artifact] | None = None,
        ) -> Artifact:
            received_upstream[profile_id] = upstream_artifacts
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, capturing_executor)
        result = dispatcher.dispatch(dag, "parallel test")

        assert set(result.completed) == {"s1", "s2"}
        assert received_upstream["a1"] is None
        assert received_upstream["a2"] is None


# ---------------------------------------------------------------------------
# 4. Multi-level DAG (A -> B -> C)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestMultiLevelDagFlow:
    """Three-level chain: A -> B -> C with artifact passing at each level."""

    def test_three_level_chain_all_complete(self) -> None:
        """A -> B -> C: all complete, C receives B's artifact."""
        received_upstream: dict[str, list[Artifact] | None] = {}

        def capturing_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Artifact] | None = None,
        ) -> Artifact:
            received_upstream[profile_id] = upstream_artifacts
            return _make_artifact(profile_id, f"{profile_id}-done")

        dag = _build_dag(
            [
                DAGTask(id="a", agent="agent-a", output="o1"),
                DAGTask(id="b", agent="agent-b", output="o2", blocked_by=["a"]),
                DAGTask(id="c", agent="agent-c", output="o3", blocked_by=["b"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, capturing_executor)
        result = dispatcher.dispatch(dag, "three-level")

        assert set(result.completed) == {"a", "b", "c"}
        assert received_upstream["agent-a"] is None
        assert received_upstream["agent-b"] is not None
        assert received_upstream["agent-b"][0].source_agent == "agent-a"
        assert received_upstream["agent-c"] is not None
        assert received_upstream["agent-c"][0].source_agent == "agent-b"

    def test_diamond_dependency_collects_multiple_upstream(self) -> None:
        """Diamond: A -> B, A -> C, B+C -> D. D receives both B and C artifacts."""
        received_upstream: dict[str, list[Artifact] | None] = {}

        def capturing_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Artifact] | None = None,
        ) -> Artifact:
            received_upstream[profile_id] = upstream_artifacts
            return _make_artifact(profile_id, f"{profile_id}-done")

        dag = _build_dag(
            [
                DAGTask(id="a", agent="agent-a", output="o1"),
                DAGTask(id="b", agent="agent-b", output="o2", blocked_by=["a"]),
                DAGTask(id="c", agent="agent-c", output="o3", blocked_by=["a"]),
                DAGTask(id="d", agent="agent-d", output="o4", blocked_by=["b", "c"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, capturing_executor)
        result = dispatcher.dispatch(dag, "diamond")

        assert set(result.completed) == {"a", "b", "c", "d"}
        # D should receive both B and C artifacts
        assert received_upstream["agent-d"] is not None
        assert len(received_upstream["agent-d"]) == 2
        upstream_sources = {a.source_agent for a in received_upstream["agent-d"]}
        assert upstream_sources == {"agent-b", "agent-c"}


# ---------------------------------------------------------------------------
# 5. Empty artifact handling
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestEmptyArtifactHandling:
    """Tasks with no upstream artifacts behave identically to pre-N4 behavior."""

    def test_upstream_failed_no_artifact(self) -> None:
        """If upstream fails, downstream gets None (artifact not in collection)."""
        received: dict[str, list[Artifact] | None] = {}

        def selective_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Artifact] | None = None,
        ) -> Artifact:
            received[profile_id] = upstream_artifacts
            if profile_id == "fail-agent":
                raise RuntimeError("upstream failure")
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="upstream", agent="fail-agent", output="o1"),
                DAGTask(id="downstream", agent="ok-agent", output="o2", blocked_by=["upstream"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective_executor)
        result = dispatcher.dispatch(dag, "cascading fail")

        assert "upstream" in result.failed
        assert "downstream" in result.failed
        # downstream never executed — no entry in received
        assert "ok-agent" not in received


# ---------------------------------------------------------------------------
# 7. Artifact content flows through to downstream task descriptions
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestArtifactContentFlow:
    """Verify that upstream artifact content is actually visible to the downstream executor."""

    def test_upstream_content_in_downstream_task(self) -> None:
        """Upstream artifact content flows through ProfileBasedExecutor."""
        registry = _make_registry("agency.agent-b")
        # Manually add agent-a with context+summary sections
        registry.add(
            "agency.agent-a",
            {
                "id": "agency.agent-a",
                "name": "Agent A",
                "capabilities": ["general"],
                "profile": {"body": "You are agent-a."},
                "output_contract": {
                    "artifact_type": "report",
                    "required_sections": ["context", "summary"],
                },
            },
            ["general"],
        )

        executor = ProfileBasedExecutor(registry)

        # Agent A's artifact (simulating upstream)
        upstream_a = Artifact(
            source_agent="agency.agent-a",
            artifact_type="report",
            sections={"summary": "critical design insight"},
        )

        # Agent B receives upstream from A
        artifact_b = executor(
            "agency.agent-b",
            "design review",
            upstream_artifacts=[upstream_a],
        )

        # The context section should contain upstream artifact data
        context = artifact_b.sections.get("context", "")
        assert "design review" in context
        assert "Upstream Artifacts" in context
        assert "critical design insight" in context
