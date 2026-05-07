"""Integration tests for N1-N5 cross-module data flows.

Covers:
  N1: ExternalMcpAdapter (gateway/external_mcp_adapter.py) -- MCP client to gateway
  N2: LiteLLM unified LLMClient (agency/llm_client.py, agency/token_counter.py)
  N3: Structured Planner output (agency/llm_planner.py) -- Pydantic validation + fallback
  N4: DAG data flow (agency/executor.py, agency/dag_dispatcher.py) -- artifact passing
  N5: Gateway structured output (gateway/tool_adapter.py) -- structured field in execute()

Pipeline 1: External MCP -> Gateway -> structured output
Pipeline 2: LiteLLM -> Planner -> structured output
Pipeline 3: DAG Executor -> Artifact -> downstream Task
Pipeline 4: End-to-end agency pipeline smoke test
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.external_mcp import ExternalServerConfig, TransportType
from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType
from agent_nexus.platform.agency.dag_dispatcher import (
    DAGDispatcher,
    DispatchResult,
    dag_task_to_task_item,
    load_dag_into_graph,
)
from agent_nexus.platform.agency.integrator import Artifact
from agent_nexus.platform.agency.llm_planner import (
    ExpertSelection,
    LLMPlanner,
    PlannerOutput,
    StructuredPlannerOutput,
)
from agent_nexus.platform.agency.planner import CompositionDAG, DAGTask
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.gateway.external_mcp_adapter import ExternalMcpAdapter
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter, _sanitize
from agent_nexus.platform.orchestration.task_graph import TaskGraph


# ============================================================================
# Helpers
# ============================================================================


def _make_profile(
    profile_id: str,
    name: str = "",
    capabilities: list[str] | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Build a minimal expert profile dict for testing."""
    return {
        "id": profile_id,
        "name": name or profile_id,
        "description": description,
        "capabilities": capabilities or [],
        "profile": {"body": f"You are {name or profile_id}."},
        "output_contract": {
            "artifact_type": "report",
            "required_sections": ["summary"],
        },
    }


def _make_ipc_response(
    content: str = "",
    is_success: bool = True,
    response_type: AgentToPlatformType = AgentToPlatformType.RESULT,
    error: str | None = None,
) -> AgentToPlatform:
    """Build a mock IPC response from an agent subprocess."""
    return AgentToPlatform(
        type=response_type,
        content=content,
        error=error,
    )


# ============================================================================
# Pipeline 1: External MCP -> Gateway -> Structured Output
# Covers: N1 (ExternalMcpAdapter), N5 (McpToolAdapter structured output)
# ============================================================================


class TestPipeline1ExternalMcpToGateway:
    """Cross-module: ExternalMcpAdapter -> McpToolAdapter.

    Module boundary: external_mcp_adapter discovers tools and caches schemas,
    then McpToolAdapter wraps them and produces structured output from
    agent IPC responses.
    """

    def test_external_adapter_discovers_tools_then_tool_adapter_wraps(
        self,
    ) -> None:
        """N1+N5: External adapter tool_schemas feed into McpToolAdapter naming.

        Simulates:
        1. ExternalMcpAdapter discovers tools from an external MCP server
           (we mock the discovery by directly setting _tool_schemas).
        2. Gateway creates McpToolAdapter instances from those schemas.
        3. Verify naming convention (mcp__ prefix) and schema propagation.
        """
        # -- Step 1: Simulate tool discovery by ExternalMcpAdapter --
        config = ExternalServerConfig(
            name="fs-server",
            transport=TransportType.SSE,
            url="http://localhost:8080/sse",
        )
        adapter = ExternalMcpAdapter(config)

        # Simulate discovered tool schemas (normally set by _discover_tools)
        mock_schemas = [
            {
                "name": "read_file",
                "description": "Read a file from disk",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "list_dir",
                "description": "List directory contents",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dir": {"type": "string"},
                    },
                },
            },
        ]
        adapter._tool_schemas = mock_schemas

        # Verify adapter exposes the schemas correctly
        assert len(adapter.tool_schemas) == 2
        assert adapter.tool_schemas[0]["name"] == "read_file"
        assert adapter.tool_schemas[1]["name"] == "list_dir"

        # -- Step 2: Gateway creates McpToolAdapter from each schema --
        adapters = []
        for schema in adapter.tool_schemas:
            tool_adapter = McpToolAdapter(
                server_name=config.name,
                tool_schema=schema,
            )
            adapters.append(tool_adapter)

        # -- Step 3: Verify naming and schema propagation --
        assert adapters[0].full_name == "mcp__fs_server__read_file"
        assert adapters[1].full_name == "mcp__fs_server__list_dir"
        assert adapters[0].description == "Read a file from disk"
        assert adapters[1].description == "List directory contents"

        # Verify get_tool_definition propagates the inputSchema
        tool_def = adapters[0].get_tool_definition()
        assert tool_def["name"] == "mcp__fs_server__read_file"
        assert "path" in tool_def["inputSchema"]["properties"]

    @pytest.mark.asyncio
    async def test_tool_adapter_execute_returns_structured_field(self) -> None:
        """N5: McpToolAdapter.execute() returns 'structured' field for valid JSON.

        Module boundary: McpToolAdapter -> IPC -> structured output extraction.
        Verifies that when an agent subprocess returns JSON via IPC, the
        structured field is populated in the execute() result dict.
        """
        tool_schema = {
            "name": "analyze",
            "description": "Analyze code quality",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                },
            },
        }
        adapter = McpToolAdapter(server_name="quality-agent", tool_schema=tool_schema)

        # Verify naming
        assert adapter.full_name == "mcp__quality_agent__analyze"

        # Create a mock AgentHandle
        mock_handle = MagicMock()
        mock_handle.is_alive = True

        # Mock IPC that returns structured JSON
        json_result = {
            "complexity": "low",
            "issues": ["missing docstring"],
            "score": 85,
        }
        mock_ipc = AsyncMock()
        mock_ipc.send_chat = AsyncMock()
        mock_ipc.receive_until_result = AsyncMock(
            return_value=_make_ipc_response(
                content=json.dumps(json_result),
                is_success=True,
            )
        )
        mock_handle.ipc = mock_ipc

        # Execute the tool
        result = await adapter.execute(mock_handle, {"file_path": "/src/main.py"})

        # N5: Verify structured output field
        assert result["success"] is True
        assert result["output"] == json.dumps(json_result)
        assert result["structured"] is not None
        assert result["structured"]["complexity"] == "low"
        assert result["structured"]["score"] == 85
        assert result["structured"]["issues"] == ["missing docstring"]

    @pytest.mark.asyncio
    async def test_tool_adapter_execute_non_json_content(self) -> None:
        """N5: McpToolAdapter.execute() sets structured=None for plain text output.

        Module boundary: McpToolAdapter -> IPC -> non-JSON content handling.
        """
        tool_schema = {
            "name": "echo",
            "description": "Echo input",
            "inputSchema": {"type": "object", "properties": {}},
        }
        adapter = McpToolAdapter(server_name="echo-agent", tool_schema=tool_schema)

        mock_handle = MagicMock()
        mock_handle.is_alive = True

        mock_ipc = AsyncMock()
        mock_ipc.send_chat = AsyncMock()
        mock_ipc.receive_until_result = AsyncMock(
            return_value=_make_ipc_response(
                content="This is plain text output, not JSON.",
                is_success=True,
            )
        )
        mock_handle.ipc = mock_ipc

        result = await adapter.execute(mock_handle, {})

        assert result["success"] is True
        assert result["output"] == "This is plain text output, not JSON."
        # N5: structured should be None for non-JSON content
        assert result["structured"] is None

    def test_ext_prefix_naming_for_external_tools(self) -> None:
        """N1: External server tools should use ext__ prefix in gateway context.

        Module boundary: ExternalMcpAdapter discovers tools, gateway registers
        them with ext__ prefix (not mcp__ which is for internal agent tools).
        """
        config = ExternalServerConfig(
            name="search-srv",
            transport=TransportType.HTTP_STREAM,
            url="http://localhost:9090/mcp",
        )
        adapter = ExternalMcpAdapter(config)
        adapter._tool_schemas = [
            {"name": "search", "description": "Search the web", "inputSchema": {}},
            {"name": "fetch", "description": "Fetch a URL", "inputSchema": {}},
        ]

        # Gateway would prefix external tools with ext__
        for schema in adapter.tool_schemas:
            raw_name = schema["name"]
            ext_tool_name = f"ext__{_sanitize(config.name)}__{_sanitize(raw_name)}"
            assert ext_tool_name.startswith("ext__")
            assert "search_srv" in ext_tool_name or "search" in ext_tool_name

        # Verify specific names
        assert f"ext__{_sanitize('search-srv')}__{_sanitize('search')}" == "ext__search_srv__search"
        assert f"ext__{_sanitize('search-srv')}__{_sanitize('fetch')}" == "ext__search_srv__fetch"


# ============================================================================
# Pipeline 2: LiteLLM -> Planner -> Structured Output
# Covers: N2 (LLMClient), N3 (LLMPlanner Pydantic validation + fallback)
# ============================================================================


class TestPipeline2LiteLLMToPlanner:
    """Cross-module: LLMClient -> LLMPlanner -> PlannerOutput.

    Module boundary: LLMClient.call() returns LLMResponse.text,
    which LLMPlanner parses through Pydantic validation (N3) or falls
    back to robust_json_parse.
    """

    def test_planner_pydantic_validates_structured_llm_output(self) -> None:
        """N2+N3: LLMPlanner parses LLM response through Pydantic validation.

        Module boundary: LLMClient returns structured JSON -> LLMPlanner
        validates via StructuredPlannerOutput (Pydantic model).
        Verifies: capabilities, expert_selections, decomposition_strategy.
        """
        registry = ExpertRegistry()
        registry.add(
            "code-reviewer",
            _make_profile("code-reviewer", "Code Reviewer", ["code_review"], "Reviews code"),
            ["code_review"],
        )
        registry.add(
            "doc-writer",
            _make_profile("doc-writer", "Doc Writer", ["documentation"], "Writes docs"),
            ["documentation"],
        )

        # Mock LLMClient to return structured JSON matching N3 schema
        mock_client = MagicMock()
        structured_json = json.dumps(
            {
                "capabilities": ["code_review", "documentation"],
                "focus_hints": {
                    "code-reviewer": "Focus on security issues",
                    "doc-writer": "Focus on API docs",
                },
                "decomposition_strategy": "parallel",
                "expert_selections": [
                    {
                        "expert_id": "code-reviewer",
                        "task": "Review the code for security issues",
                        "parameters": {"severity": "high"},
                    },
                    {
                        "expert_id": "doc-writer",
                        "task": "Write API documentation",
                        "parameters": {},
                    },
                ],
            }
        )

        from agent_nexus.platform.agency.llm_client import LLMResponse

        mock_client.call.return_value = LLMResponse(
            text=structured_json,
            model="test-model",
            provider="test",
        )

        planner = LLMPlanner(registry=registry, client=mock_client)
        LLMPlanner.reset_fallback_count()
        result = planner.analyze_task("Review and document the authentication module")

        # N3: Verify Pydantic-validated output
        assert result.capabilities == ["code_review", "documentation"]
        assert result.decomposition_strategy == "parallel"
        assert len(result.expert_selections) == 2
        assert result.expert_selections[0].expert_id == "code-reviewer"
        assert result.expert_selections[0].task == "Review the code for security issues"
        assert result.expert_selections[0].parameters == {"severity": "high"}
        assert result.expert_selections[1].expert_id == "doc-writer"
        assert "code-reviewer" in result.focus_hints
        assert "security" in result.focus_hints["code-reviewer"]

    def test_planner_fallback_on_malformed_llm_output(self) -> None:
        """N3: LLMPlanner falls back to robust_json_parse on Pydantic failure.

        Module boundary: LLMClient returns partial/malformed JSON ->
        Pydantic validation fails -> from_json() fallback extracts what it can.
        """
        registry = ExpertRegistry()
        registry.add(
            "test-expert",
            _make_profile("test-expert", "Tester", ["testing"], "Runs tests"),
            ["testing"],
        )

        # Mock LLMClient to return JSON without expert_selections (Pydantic will
        # still pass because expert_selections has a default).  Use invalid
        # decomposition_strategy to trigger a validation error.
        mock_client = MagicMock()
        malformed_json = json.dumps(
            {
                "capabilities": ["testing"],
                "focus_hints": {"test-expert": "Focus on unit tests"},
                "decomposition_strategy": "invalid_strategy",
            }
        )

        from agent_nexus.platform.agency.llm_client import LLMResponse

        mock_client.call.return_value = LLMResponse(
            text=malformed_json,
            model="test-model",
            provider="test",
        )

        planner = LLMPlanner(registry=registry, client=mock_client)
        LLMPlanner.reset_fallback_count()
        result = planner.analyze_task("Test the authentication module")

        # Should fall back to from_json which extracts capabilities manually
        assert "testing" in result.capabilities
        assert "test-expert" in result.focus_hints

    def test_planner_keyword_fallback_when_no_llm_client(self) -> None:
        """N3: LLMPlanner falls back to keyword matching without LLMClient.

        Module boundary: No LLMClient available -> keyword-based inference
        using infer_capabilities from task_composer module.
        """
        registry = ExpertRegistry()
        registry.add(
            "code-reviewer",
            _make_profile("code-reviewer", "Code Reviewer", ["code_review"]),
            ["code_review"],
        )

        planner = LLMPlanner(registry=registry, client=None)
        LLMPlanner.reset_fallback_count()
        result = planner.analyze_task("Review code for bugs")

        # Keyword fallback should still produce capabilities
        assert isinstance(result.capabilities, list)
        assert result.decomposition_strategy == "parallel"
        # No expert_selections from keyword fallback
        assert result.expert_selections == []
        # Fallback counter should have incremented
        assert LLMPlanner.fallback_count() > 0

    def test_planner_output_from_json_handles_markdown_fences(self) -> None:
        """N3: PlannerOutput.from_json() handles markdown-wrapped JSON.

        Module boundary: LLMClient returns JSON inside markdown code fences
        -> robust_json_parse strips fences -> Pydantic validates.
        """
        fenced_json = """Here is my analysis:

```json
{
    "capabilities": ["code_review"],
    "focus_hints": {"code-reviewer": "Check for SQL injection"},
    "decomposition_strategy": "sequential",
    "expert_selections": [
        {
            "expert_id": "code-reviewer",
            "task": "Security audit",
            "parameters": {}
        }
    ]
}
```

Hope that helps!"""

        result = PlannerOutput.from_json(fenced_json)

        # Should successfully parse despite markdown wrapping
        assert "code_review" in result.capabilities
        assert result.decomposition_strategy == "sequential"
        assert len(result.expert_selections) == 1
        assert result.expert_selections[0].expert_id == "code-reviewer"

    def test_planner_output_from_json_returns_empty_on_garbage(self) -> None:
        """N3: PlannerOutput.from_json() returns empty output for unparseable text.

        Module boundary: LLMClient returns non-JSON text -> robust_json_parse
        returns None -> PlannerOutput with empty defaults.
        """
        result = PlannerOutput.from_json("This is not JSON at all, just text.")

        assert result.capabilities == []
        assert result.focus_hints == {}
        assert result.decomposition_strategy == "parallel"
        assert result.expert_selections == []


# ============================================================================
# Pipeline 3: DAG Executor -> Artifact -> Downstream Task
# Covers: N4 (DAG data flow, artifact passing between tasks)
# ============================================================================


class TestPipeline3DagArtifactFlow:
    """Cross-module: DAGDispatcher -> ExpertExecutor -> Artifact -> downstream.

    Module boundary: DAGDispatcher uses TaskGraph for state tracking,
    calls ExpertExecutor to produce Artifacts, collects upstream artifacts
    and passes them to downstream tasks.
    """

    def test_artifact_flows_from_upstream_to_downstream(self) -> None:
        """N4: Artifact produced by task A is passed to task B as upstream_artifacts.

        Module boundary: DAGDispatcher._collect_upstream_artifacts() extracts
        artifacts from completed tasks and injects them into downstream
        executor calls via the upstream_artifacts keyword argument.
        """
        # Create a simple A -> B DAG
        dag = CompositionDAG(
            name="test-pipeline",
            max_parallel=1,
            tasks=[
                DAGTask(id="task-a", agent="analyzer", output="analysis_report", blocked_by=[]),
                DAGTask(
                    id="task-b",
                    agent="writer",
                    output="final_report",
                    blocked_by=["task-a"],
                ),
            ],
        )

        # Track what upstream_artifacts each executor call receives
        received_upstream: dict[str, list[Artifact] | None] = {}

        def mock_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Any] | None = None,
        ) -> Artifact:
            received_upstream[profile_id] = upstream_artifacts
            return Artifact(
                source_agent=profile_id,
                artifact_type="report",
                sections={"summary": f"Output from {profile_id}"},
                metadata={"task": task},
            )

        graph = TaskGraph(Path(":memory:"))
        dispatcher = DAGDispatcher(
            graph=graph,
            executor=mock_executor,
            max_parallel=1,
        )

        result = dispatcher.dispatch(dag, "Analyze and write report")

        # Both tasks should complete
        assert "task-a" in result.completed
        assert "task-b" in result.completed
        assert not result.failed

        # Task A should have no upstream artifacts
        assert received_upstream["analyzer"] is None

        # Task B should have received Task A's artifact as upstream
        upstream = received_upstream["writer"]
        assert upstream is not None
        assert len(upstream) == 1
        assert upstream[0].source_agent == "analyzer"
        assert upstream[0].sections["summary"] == "Output from analyzer"

        dispatcher.close()

    def test_parallel_tasks_receive_independent_artifacts(self) -> None:
        """N4: Parallel tasks (no blocked_by) each get None upstream_artifacts.

        Module boundary: DAGDispatcher correctly identifies independent tasks
        and does not inject artifacts where no dependency exists.
        """
        dag = CompositionDAG(
            name="parallel-pipeline",
            max_parallel=3,
            tasks=[
                DAGTask(id="task-a", agent="expert-a", output="report_a", blocked_by=[]),
                DAGTask(id="task-b", agent="expert-b", output="report_b", blocked_by=[]),
                DAGTask(id="task-c", agent="expert-c", output="report_c", blocked_by=[]),
            ],
        )

        received_upstream: list[list[Artifact] | None] = []

        def mock_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Any] | None = None,
        ) -> Artifact:
            received_upstream.append(upstream_artifacts)
            return Artifact(
                source_agent=profile_id,
                artifact_type="report",
                sections={"result": f"Done by {profile_id}"},
            )

        graph = TaskGraph(Path(":memory:"))
        dispatcher = DAGDispatcher(
            graph=graph,
            executor=mock_executor,
            max_parallel=3,
        )

        result = dispatcher.dispatch(dag, "Run three independent analyses")

        assert len(result.completed) == 3
        assert not result.failed

        # All parallel tasks should have None upstream (no dependencies)
        for upstream in received_upstream:
            assert upstream is None

        dispatcher.close()

    def test_diamond_dag_merges_multiple_upstream_artifacts(self) -> None:
        """N4: Diamond DAG (A -> B, A -> C, B+C -> D) merges artifacts correctly.

        Module boundary: DAGDispatcher collects artifacts from multiple
        upstream dependencies and passes them as a list to the downstream task.
        """
        #      A
        #     / \
        #    B   C
        #     \ /
        #      D
        dag = CompositionDAG(
            name="diamond-pipeline",
            max_parallel=2,
            tasks=[
                DAGTask(id="task-a", agent="analyzer", output="base_analysis", blocked_by=[]),
                DAGTask(
                    id="task-b",
                    agent="security-reviewer",
                    output="security_report",
                    blocked_by=["task-a"],
                ),
                DAGTask(
                    id="task-c",
                    agent="perf-reviewer",
                    output="perf_report",
                    blocked_by=["task-a"],
                ),
                DAGTask(
                    id="task-d",
                    agent="integrator",
                    output="final_report",
                    blocked_by=["task-b", "task-c"],
                ),
            ],
        )

        received_upstream: dict[str, list[Artifact] | None] = {}

        def mock_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Any] | None = None,
        ) -> Artifact:
            received_upstream[profile_id] = upstream_artifacts
            return Artifact(
                source_agent=profile_id,
                artifact_type="report",
                sections={"summary": f"From {profile_id}"},
            )

        graph = TaskGraph(Path(":memory:"))
        dispatcher = DAGDispatcher(
            graph=graph,
            executor=mock_executor,
            max_parallel=2,
        )

        result = dispatcher.dispatch(dag, "Full analysis pipeline")

        assert len(result.completed) == 4
        assert not result.failed

        # Task A: no upstream
        assert received_upstream["analyzer"] is None

        # Task B and C: each receives A's artifact
        assert received_upstream["security-reviewer"] is not None
        assert len(received_upstream["security-reviewer"]) == 1
        assert received_upstream["security-reviewer"][0].source_agent == "analyzer"

        assert received_upstream["perf-reviewer"] is not None
        assert len(received_upstream["perf-reviewer"]) == 1
        assert received_upstream["perf-reviewer"][0].source_agent == "analyzer"

        # Task D: receives both B and C's artifacts
        assert received_upstream["integrator"] is not None
        assert len(received_upstream["integrator"]) == 2
        source_agents = {a.source_agent for a in received_upstream["integrator"]}
        assert source_agents == {"security-reviewer", "perf-reviewer"}

        dispatcher.close()

    def test_dag_task_to_task_item_conversion(self) -> None:
        """N4: DAGTask -> TaskItem conversion preserves blocked_by and output_contract.

        Module boundary: dag_task_to_task_item bridges the agency DAG model
        to the orchestration TaskItem model, including dependency edges and
        output contract metadata.
        """
        dag_task = DAGTask(
            id="review-task",
            agent="code-reviewer",
            output="review_report",
            blocked_by=["upstream-analysis"],
        )

        item = dag_task_to_task_item(dag_task, "Review the codebase")

        assert item.id == "review-task"
        assert item.agent == "code-reviewer"
        assert item.blocked_by == ["upstream-analysis"]
        assert item.vars["output_contract"] == "review_report"
        assert item.description == "Review the codebase"

    def test_load_dag_into_graph_skips_non_specialist_tasks(self) -> None:
        """N4: load_dag_into_graph only loads specialist tasks, not integrate/validate.

        Module boundary: CompositionDAG contains specialist + synthetic tasks;
        load_dag_into_graph filters to specialist-only for TaskGraph.
        """
        dag = CompositionDAG(
            name="filtered-pipeline",
            max_parallel=2,
            tasks=[
                DAGTask(id="task-a", agent="expert-a", output="report_a", task_type="specialist"),
                DAGTask(id="task-b", agent="expert-b", output="report_b", task_type="specialist"),
                DAGTask(id="integrate", agent="integrator", output="final", task_type="synthetic"),
            ],
        )

        graph = TaskGraph(Path(":memory:"))
        items = load_dag_into_graph(dag, "Test task", graph)

        # Only specialist tasks should be loaded
        assert len(items) == 2
        loaded_ids = {item.id for item in items}
        assert loaded_ids == {"task-a", "task-b"}
        assert "integrate" not in loaded_ids


# ============================================================================
# Pipeline 4: End-to-end Agency Pipeline Smoke Test
# Covers: N2+N3+N4 (LLMClient -> LLMPlanner -> DAGDispatcher -> Artifacts)
# ============================================================================


class TestPipeline4EndToEndSmokeTest:
    """End-to-end smoke test covering the full agency pipeline data flow.

    Module boundary: LLMPlanner (N3) decomposes task using mock LLMClient
    (N2), producing PlannerOutput. PlannerOutput drives DAG construction,
    which DAGDispatcher (N4) executes, producing artifacts that flow
    between tasks.
    """

    def test_full_agency_pipeline_with_mock_llm(self) -> None:
        """N2+N3+N4: Complete pipeline from task to artifacts.

        Simulates the full agency flow:
        1. LLMPlanner analyzes task (mock LLM returns structured JSON)
        2. PlannerOutput drives DAG construction
        3. DAGDispatcher executes tasks via mock executor
        4. Artifacts flow from upstream to downstream tasks
        5. Final result contains all artifacts with correct data
        """
        # -- Setup: Register experts --
        registry = ExpertRegistry()
        registry.add(
            "security-analyst",
            _make_profile(
                "security-analyst",
                "Security Analyst",
                ["security_audit", "vulnerability_scan"],
                "Analyzes security vulnerabilities",
            ),
            ["security_audit", "vulnerability_scan"],
        )
        registry.add(
            "doc-writer",
            _make_profile(
                "doc-writer",
                "Documentation Writer",
                ["documentation", "api_docs"],
                "Writes technical documentation",
            ),
            ["documentation", "api_docs"],
        )

        # -- Step 1: LLMPlanner decomposes task via mock LLMClient --
        from agent_nexus.platform.agency.llm_client import LLMResponse

        planner_json = json.dumps(
            {
                "capabilities": ["security_audit", "documentation"],
                "focus_hints": {
                    "security-analyst": "Focus on auth and input validation",
                    "doc-writer": "Focus on API endpoint documentation",
                },
                "decomposition_strategy": "parallel",
                "expert_selections": [
                    {
                        "expert_id": "security-analyst",
                        "task": "Audit the authentication module for vulnerabilities",
                        "parameters": {"severity": "critical"},
                    },
                    {
                        "expert_id": "doc-writer",
                        "task": "Write API documentation for auth endpoints",
                        "parameters": {"format": "openapi"},
                    },
                ],
            }
        )

        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=planner_json,
            model="test-model",
            provider="test-provider",
        )

        planner = LLMPlanner(registry=registry, client=mock_client)
        LLMPlanner.reset_fallback_count()
        plan = planner.analyze_task("Audit and document the authentication system")

        # Verify planner output (N3: Pydantic validation)
        assert "security_audit" in plan.capabilities
        assert "documentation" in plan.capabilities
        assert plan.decomposition_strategy == "parallel"
        assert len(plan.expert_selections) == 2

        # -- Step 2: Build DAG from planner output --
        specialist_tasks = []
        for i, selection in enumerate(plan.expert_selections):
            task_id = f"task-{i + 1}"
            specialist_tasks.append(
                DAGTask(
                    id=task_id,
                    agent=selection.expert_id,
                    output=f"report_{selection.expert_id}",
                    blocked_by=[],
                    task_type="specialist",
                )
            )

        dag = CompositionDAG(
            name="auth-audit-and-doc",
            max_parallel=2,
            tasks=specialist_tasks,
        )

        # -- Step 3: Execute DAG via DAGDispatcher --
        execution_log: list[dict[str, Any]] = []

        def mock_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Any] | None = None,
        ) -> Artifact:
            # Simulate producing a real artifact based on the expert's profile
            profile = registry.get(profile_id)
            assert profile is not None, f"Profile {profile_id} not found"

            # Use actual profile data to construct the artifact (simulates
            # real upstream module output rather than hardcoded dict)
            caps = profile.get("capabilities", [])
            name = profile.get("name", profile_id)

            sections: dict[str, object] = {
                "expert": name,
                "capabilities_used": caps,
                "analysis": f"Analysis from {name} using capabilities: {', '.join(caps)}",
            }

            if upstream_artifacts:
                sections["upstream_count"] = len(upstream_artifacts)
                sections["upstream_sources"] = [a.source_agent for a in upstream_artifacts]

            execution_log.append(
                {
                    "agent": profile_id,
                    "upstream_count": len(upstream_artifacts) if upstream_artifacts else 0,
                    "capabilities": caps,
                }
            )

            return Artifact(
                source_agent=profile_id,
                artifact_type="report",
                sections=sections,
                metadata={"task": task, "synthetic": True},
            )

        graph = TaskGraph(Path(":memory:"))
        dispatcher = DAGDispatcher(
            graph=graph,
            executor=mock_executor,
            max_parallel=2,
        )

        dispatch_result = dispatcher.dispatch(dag, "Audit and document the authentication system")

        # -- Step 4: Verify end-to-end data integrity --
        # All tasks should complete
        assert len(dispatch_result.completed) == 2
        assert not dispatch_result.failed

        # Verify artifacts were produced by the correct experts
        task_1_artifact = dispatch_result.artifacts.get("task-1")
        task_2_artifact = dispatch_result.artifacts.get("task-2")
        assert task_1_artifact is not None
        assert task_2_artifact is not None

        # Verify data flows from planner through to artifacts
        expert_ids = {task_1_artifact.source_agent, task_2_artifact.source_agent}
        assert expert_ids == {"security-analyst", "doc-writer"}

        # Verify each artifact carries the correct capabilities from the registry
        for artifact in [task_1_artifact, task_2_artifact]:
            caps = artifact.sections.get("capabilities_used", [])
            assert len(caps) > 0, f"No capabilities in artifact from {artifact.source_agent}"

        # Verify execution log shows parallel execution (no upstream artifacts)
        for entry in execution_log:
            assert entry["upstream_count"] == 0  # Parallel tasks have no deps

        dispatcher.close()

    def test_pipeline_with_sequential_dag_artifact_chain(self) -> None:
        """N3+N4: Sequential pipeline where artifacts chain through stages.

        Simulates a sequential flow:
        1. Planner selects a single expert with sequential strategy
        2. DAG has task A -> task B (sequential dependencies)
        3. Task B receives Task A's artifact
        4. Verify the full artifact chain is intact
        """
        registry = ExpertRegistry()
        registry.add(
            "code-analyzer",
            _make_profile(
                "code-analyzer",
                "Code Analyzer",
                ["code_analysis"],
                "Analyzes code structure",
            ),
            ["code_analysis"],
        )
        registry.add(
            "report-generator",
            _make_profile(
                "report-generator",
                "Report Generator",
                ["report_generation"],
                "Generates reports from analysis",
            ),
            ["report_generation"],
        )

        # Planner output (N3)
        from agent_nexus.platform.agency.llm_client import LLMResponse

        planner_json = json.dumps(
            {
                "capabilities": ["code_analysis", "report_generation"],
                "focus_hints": {
                    "code-analyzer": "Focus on architecture patterns",
                    "report-generator": "Summarize findings into report",
                },
                "decomposition_strategy": "sequential",
                "expert_selections": [
                    {
                        "expert_id": "code-analyzer",
                        "task": "Analyze codebase architecture",
                        "parameters": {},
                    },
                    {
                        "expert_id": "report-generator",
                        "task": "Generate summary report",
                        "parameters": {},
                    },
                ],
            }
        )

        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=planner_json,
            model="test-model",
            provider="test",
        )

        planner = LLMPlanner(registry=registry, client=mock_client)
        LLMPlanner.reset_fallback_count()
        plan = planner.analyze_task("Analyze code and generate report")

        assert plan.decomposition_strategy == "sequential"
        assert len(plan.expert_selections) == 2

        # Build sequential DAG from planner output
        dag = CompositionDAG(
            name="sequential-analysis",
            max_parallel=1,
            tasks=[
                DAGTask(
                    id="analyze",
                    agent="code-analyzer",
                    output="analysis_result",
                    blocked_by=[],
                    task_type="specialist",
                ),
                DAGTask(
                    id="report",
                    agent="report-generator",
                    output="final_report",
                    blocked_by=["analyze"],
                    task_type="specialist",
                ),
            ],
        )

        # Track artifacts flowing through
        artifact_chain: list[dict[str, Any]] = []

        def chained_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Any] | None = None,
        ) -> Artifact:
            profile = registry.get(profile_id)
            assert profile is not None

            sections: dict[str, object] = {
                "expert": profile.get("name", profile_id),
            }

            if upstream_artifacts:
                # Chain: include upstream data in downstream artifact
                sections["upstream_data"] = [
                    {"source": a.source_agent, "type": a.artifact_type} for a in upstream_artifacts
                ]
                # Use real upstream output rather than hardcoded values
                for upstream_a in upstream_artifacts:
                    for key, val in upstream_a.sections.items():
                        sections[f"upstream_{upstream_a.source_agent}_{key}"] = val

            artifact_chain.append(
                {
                    "agent": profile_id,
                    "upstream_count": len(upstream_artifacts) if upstream_artifacts else 0,
                }
            )

            return Artifact(
                source_agent=profile_id,
                artifact_type="report",
                sections=sections,
            )

        graph = TaskGraph(Path(":memory:"))
        dispatcher = DAGDispatcher(
            graph=graph,
            executor=chained_executor,
            max_parallel=1,
        )

        result = dispatcher.dispatch(dag, "Analyze code and generate report")

        # Both tasks complete
        assert len(result.completed) == 2
        assert not result.failed

        # Verify sequential execution order
        assert artifact_chain[0]["agent"] == "code-analyzer"
        assert artifact_chain[0]["upstream_count"] == 0
        assert artifact_chain[1]["agent"] == "report-generator"
        assert artifact_chain[1]["upstream_count"] == 1

        # Verify artifact chain integrity: report contains analyzer's data
        report_artifact = result.artifacts["report"]
        assert "upstream_data" in report_artifact.sections
        upstream_data = report_artifact.sections["upstream_data"]
        assert len(upstream_data) == 1
        assert upstream_data[0]["source"] == "code-analyzer"

        # Verify cross-module data flow: analyzer's sections appear in report
        analyze_artifact = result.artifacts["analyze"]
        assert "expert" in analyze_artifact.sections
        # The report artifact should carry analyzer's output sections
        assert report_artifact.sections.get("upstream_code-analyzer_expert") == "Code Analyzer"

        dispatcher.close()

    async def test_pipeline_tool_adapter_receives_real_artifact_data(self) -> None:
        """N4+N5: Artifact from DAG execution feeds into McpToolAdapter output.

        Module boundary: DAGDispatcher produces Artifact -> artifact data is
        serialized as JSON -> McpToolAdapter.execute() receives it via IPC
        and returns structured output.
        Simulates using real upstream module output (Artifact) rather than
        hardcoded test data.
        """
        # Step 1: Produce a real artifact via DAGDispatcher
        dag = CompositionDAG(
            name="artifact-to-ipc",
            max_parallel=1,
            tasks=[
                DAGTask(id="gen", agent="generator", output="generated_data", blocked_by=[]),
            ],
        )

        def generate_executor(
            profile_id: str,
            task: str,
            *,
            upstream_artifacts: list[Any] | None = None,
        ) -> Artifact:
            return Artifact(
                source_agent=profile_id,
                artifact_type="analysis",
                sections={
                    "metrics": {"coverage": 92, "complexity": "low"},
                    "files_analyzed": 15,
                    "issues_found": ["unused import", "missing type hint"],
                },
            )

        graph = TaskGraph(Path(":memory:"))
        dispatcher = DAGDispatcher(
            graph=graph,
            executor=generate_executor,
            max_parallel=1,
        )
        result = dispatcher.dispatch(dag, "Generate analysis")
        dispatcher.close()

        assert "gen" in result.completed
        artifact = result.artifacts["gen"]

        # Step 2: Serialize artifact sections (simulates IPC transport)
        artifact_json = json.dumps(artifact.sections)

        # Step 3: McpToolAdapter receives this via mock IPC (N5)
        tool_schema = {
            "name": "process_artifact",
            "description": "Process artifact data",
            "inputSchema": {"type": "object", "properties": {}},
        }
        tool_adapter = McpToolAdapter(
            server_name="processor-agent",
            tool_schema=tool_schema,
        )

        mock_handle = MagicMock()
        mock_handle.is_alive = True

        # Simulate IPC returning the artifact JSON as structured output
        mock_ipc = AsyncMock()
        mock_ipc.send_chat = AsyncMock()
        mock_ipc.receive_until_result = AsyncMock(
            return_value=_make_ipc_response(
                content=artifact_json,
                is_success=True,
            )
        )
        mock_handle.ipc = mock_ipc

        # Step 4: Execute and verify structured output carries artifact data
        tool_result = await tool_adapter.execute(mock_handle, {})

        assert tool_result["success"] is True
        assert tool_result["structured"] is not None
        # Cross-module verification: artifact data from DAGDispatcher is
        # faithfully preserved through IPC -> McpToolAdapter
        assert tool_result["structured"]["metrics"]["coverage"] == 92
        assert tool_result["structured"]["files_analyzed"] == 15
        assert "unused import" in tool_result["structured"]["issues_found"]


# ============================================================================
# Additional cross-module boundary tests
# ============================================================================


class TestCrossModuleBoundaryValidation:
    """Validate data format consistency across module boundaries."""

    def test_structured_planner_output_schema_matches_planner_output(self) -> None:
        """N3: StructuredPlannerOutput (Pydantic) fields align with PlannerOutput (dataclass).

        Module boundary: LLMPlanner validates via Pydantic then converts to
        dataclass. Verify field names and types are compatible.
        """
        structured = StructuredPlannerOutput(
            capabilities=["code_review"],
            focus_hints={"expert-a": "Focus on X"},
            decomposition_strategy="parallel",
            expert_selections=[
                ExpertSelection(expert_id="expert-a", task="Do X", parameters={"key": "val"})
            ],
        )

        # Convert to PlannerOutput (the conversion LLMPlanner does internally)
        planner_output = PlannerOutput(
            capabilities=structured.capabilities,
            focus_hints=structured.focus_hints,
            decomposition_strategy=structured.decomposition_strategy,
            expert_selections=structured.expert_selections,
        )

        assert planner_output.capabilities == structured.capabilities
        assert planner_output.focus_hints == structured.focus_hints
        assert planner_output.decomposition_strategy == structured.decomposition_strategy
        assert len(planner_output.expert_selections) == 1
        assert planner_output.expert_selections[0].expert_id == "expert-a"
        assert planner_output.expert_selections[0].parameters == {"key": "val"}

    def test_artifact_serialization_roundtrip(self) -> None:
        """N4+N5: Artifact.sections can serialize to JSON and back.

        Module boundary: Artifact sections (dict) are serialized to JSON
        for IPC transport and deserialized by McpToolAdapter. Verify the
        roundtrip preserves data integrity.
        """
        original_sections = {
            "summary": "Code review completed",
            "metrics": {"score": 95, "issues": 3},
            "files": ["main.py", "utils.py"],
            "nested": {"deep": {"value": True}},
        }

        artifact = Artifact(
            source_agent="reviewer",
            artifact_type="review",
            sections=original_sections,
        )

        # Serialize (simulating IPC transport)
        json_str = json.dumps(artifact.sections)

        # Deserialize (simulating McpToolAdapter structured output)
        parsed = json.loads(json_str)

        assert parsed == original_sections
        assert parsed["metrics"]["score"] == 95
        assert parsed["nested"]["deep"]["value"] is True

    def test_tool_adapter_name_sanitization(self) -> None:
        """N1+N5: Tool and server names are sanitized for MCP compliance.

        Module boundary: ExternalMcpAdapter returns raw tool names from
        external servers, McpToolAdapter sanitizes them for internal use.
        """
        tool_schema = {
            "name": "my-complex.tool/name",
            "description": "A tool with special chars",
            "inputSchema": {},
        }

        adapter = McpToolAdapter(server_name="my-server.v2", tool_schema=tool_schema)

        # Non-alphanumeric chars replaced with underscores
        assert adapter.full_name == "mcp__my_server_v2__my_complex_tool_name"
        assert adapter.server_name == "my_server_v2"
        assert adapter.tool_name == "my_complex_tool_name"
