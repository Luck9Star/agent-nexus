"""Tests for iteration 16 bug fixes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.context import ContextBudget
from agent_nexus.platform.evolution.health import HealthReport
from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry
from agent_nexus.platform.runtime.security_rules import RegexRule


# ---------------------------------------------------------------------------
# Fix 1: Router raises RuntimeError for empty phase_agents
# ---------------------------------------------------------------------------


class TestRouterEmptyPhaseFails:
    """Router._execute_phase must raise when no agents available."""

    @pytest.mark.asyncio
    async def test_execute_phase_raises_on_no_agents(self) -> None:
        from agent_nexus.platform.router.router import (
            PlatformRouter,
            WorkflowPhase,
        )
        from agent_nexus.platform.orchestration.dsl import (
            OrchestrationDefinition,
            DSLToolLoading,
        )

        pm = MagicMock()
        router = PlatformRouter(process_manager=pm)
        definition = OrchestrationDefinition(
            goal="test",
            agent_name="test-agent",
            agents={},
            tasks=[],
            tool_loading=DSLToolLoading(),
        )
        mock_tg = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.task_graph = mock_tg

        with pytest.raises(RuntimeError, match="No agents available"):
            await router._execute_phase(
                mock_ctx, WorkflowPhase.research, definition, "test"
            )


# ---------------------------------------------------------------------------
# Fix 2: ContextBudget validates threshold range (0.0 - 1.0)
# ---------------------------------------------------------------------------


class TestContextBudgetValidation:
    """ContextBudget must reject thresholds > 1.0."""

    def test_valid_default_thresholds(self) -> None:
        budget = ContextBudget()
        assert budget.session_hard_ceiling == 0.95
        assert budget.compaction_trigger == 0.8

    def test_valid_custom_fractional_thresholds(self) -> None:
        budget = ContextBudget(
            session_hard_ceiling=0.99,
            forced_truncate_threshold=0.85,
            compaction_trigger=0.7,
            compaction_target=0.3,
        )
        assert budget.session_hard_ceiling == 0.99

    def test_rejects_session_hard_ceiling_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(session_hard_ceiling=95.0)

    def test_rejects_forced_truncate_threshold_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(forced_truncate_threshold=90.0)

    def test_rejects_compaction_trigger_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(compaction_trigger=80.0)

    def test_rejects_compaction_target_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(compaction_target=1.5)

    def test_boundary_value_one_is_accepted(self) -> None:
        budget = ContextBudget(session_hard_ceiling=1.0)
        assert budget.session_hard_ceiling == 1.0

    def test_zero_value_is_accepted(self) -> None:
        budget = ContextBudget(compaction_trigger=0.0)
        assert budget.compaction_trigger == 0.0


# ---------------------------------------------------------------------------
# Fix 3: DeferredRegistry deduplicates tools by name
# ---------------------------------------------------------------------------


class TestDeferredRegistryDeduplication:
    """get_tools_for_llm must not return duplicate tool names."""

    def test_no_duplication_when_core_and_adapters_overlap(self) -> None:
        """Core agent schemas that are also in _tool_adapters should not duplicate."""
        pm = MagicMock()
        registry = DeferredAgentRegistry(pm)

        manifest = MagicMock()
        manifest.name = "test-agent"
        manifest.description = "Test"
        manifest.type = MagicMock(value="atomic")
        manifest.role = None
        manifest.dependencies = MagicMock(atomic_agents=[])

        registry.register_agent(manifest, deferred=False)

        # Simulate tool schemas in core agents
        info = registry.get_agent_info("test-agent")
        assert info is not None
        info.tool_schemas = [
            {"name": "tool_a", "description": "Tool A"},
            {"name": "tool_b", "description": "Tool B"},
        ]

        # Simulate same tools also in _tool_adapters (what happens when
        # gateway's _register_agent_tools is called for a core agent)
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter_a = MagicMock(spec=McpToolAdapter)
        adapter_a.full_name = "tool_a"
        adapter_a.get_tool_definition.return_value = {"name": "tool_a", "description": "Tool A"}

        adapter_b = MagicMock(spec=McpToolAdapter)
        adapter_b.full_name = "tool_b"
        adapter_b.get_tool_definition.return_value = {"name": "tool_b", "description": "Tool B"}

        registry._tool_adapters["test-agent"] = [adapter_a, adapter_b]

        tools = registry.get_tools_for_llm()
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names)), f"Duplicates found: {names}"
        assert "tool_a" in names
        assert "tool_b" in names


# ---------------------------------------------------------------------------
# Fix 4: RegexRule.check() returns empty list (no-op)
# ---------------------------------------------------------------------------


class TestRegexRuleCheckNoOp:
    """RegexRule.check() must return empty list (dead code path removed)."""

    def test_check_returns_empty(self) -> None:
        import ast

        rule = RegexRule(patterns=[r"getattr"])
        node = ast.parse("getattr(obj, 'x')").body[0]
        violations = rule.check(node)
        assert violations == []

    def test_check_source_still_works(self) -> None:
        rule = RegexRule(patterns=[r"getattr"])
        violations = rule.check_source("getattr(obj, 'x')")
        assert len(violations) >= 1
