"""Tests for EvolutionEngine -- unified facade for the Self-Evolution Engine."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_nexus.platform.evolution.analyzer import AnalysisResult, EvolutionSuggestion
from agent_nexus.platform.evolution.compaction import AgentContext
from agent_nexus.platform.evolution.engine import EvolutionEngine
from agent_nexus.platform.evolution.evolver import EvolutionTrigger, EvolveResult
from agent_nexus.platform.evolution.health import HealthReport
from agent_nexus.platform.evolution.promotion import PromotionCandidate, PromotionResult
from agent_nexus.platform.evolution.store import EvolutionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_store() -> MagicMock:
    """Create a mock EvolutionStore with all required sub-stores."""
    store = MagicMock(spec=EvolutionStore)
    store.skill_store = MagicMock()
    store.analysis_store = MagicMock()
    store.budget_store = MagicMock()
    return store


def _make_engine(**kwargs: Any) -> tuple[EvolutionEngine, MagicMock]:
    """Create EvolutionEngine with mock store; return (engine, store)."""
    store = _make_mock_store()
    engine = EvolutionEngine(store, **kwargs)
    return engine, store


# ---------------------------------------------------------------------------
# __init__ & properties
# ---------------------------------------------------------------------------


class TestEvolutionEngineInit:
    def test_default_agent_id(self):
        engine, store = _make_engine()
        assert engine.store is store

    def test_custom_agent_id(self):
        engine, _ = _make_engine(agent_id="my-agent")
        assert engine._agent_id == "my-agent"

    def test_custom_agents_root(self, tmp_path):
        engine, _ = _make_engine(agents_root=tmp_path)
        assert engine._agents_root == tmp_path

    def test_sub_components_created(self):
        engine, _ = _make_engine()
        assert hasattr(engine.analyzer, "analyze_execution")
        assert hasattr(engine.evolver, "process_analysis")
        assert hasattr(engine.health_checker, "check_health")
        assert hasattr(engine.compaction_guard, "should_compact")
        assert hasattr(engine.promoter, "promote")


class TestEvolutionEngineProperties:
    def test_store_property(self):
        engine, store = _make_engine()
        assert engine.store is store

    def test_analyzer_property(self):
        engine, _ = _make_engine()
        assert engine.analyzer is engine._analyzer

    def test_evolver_property(self):
        engine, _ = _make_engine()
        assert engine.evolver is engine._evolver

    def test_health_checker_property(self):
        engine, _ = _make_engine()
        assert engine.health_checker is engine._health_checker

    def test_compaction_guard_property(self):
        engine, _ = _make_engine()
        assert engine.compaction_guard is engine._compaction_guard

    def test_promoter_property(self):
        engine, _ = _make_engine()
        assert engine.promoter is engine._promoter


# ---------------------------------------------------------------------------
# evolve() routing
# ---------------------------------------------------------------------------


class TestEvolvePostAnalysis:
    def test_post_analysis_delegates_to_analyzer_and_evolver(self):
        engine, _ = _make_engine()
        ctx = MagicMock()
        analysis = AnalysisResult(task_id="t1", agent_name="a1", analysis_text="ok")
        engine._analyzer.analyze_execution = MagicMock(return_value=analysis)
        engine._evolver.process_analysis = MagicMock()

        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)

        engine._analyzer.analyze_execution.assert_called_once_with(ctx)
        engine._evolver.process_analysis.assert_called_once_with(analysis)
        assert result is analysis

    def test_post_analysis_requires_ctx(self):
        engine, _ = _make_engine()
        with pytest.raises(ValueError, match="ctx.*required"):
            engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS)

    def test_post_analysis_with_none_ctx_raises(self):
        engine, _ = _make_engine()
        with pytest.raises(ValueError, match="ctx.*required"):
            engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=None)


class TestEvolveToolDegradation:
    def test_tool_degradation_delegates(self):
        engine, _ = _make_engine()
        results = [EvolveResult(success=True)]
        engine._evolver.process_tool_degradation = MagicMock(return_value=results)

        result = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="api-call",
            problem_description="timeout",
            affected_skill_ids={"s1"},
        )

        engine._evolver.process_tool_degradation.assert_called_once_with(
            tool_key="api-call",
            problem_description="timeout",
            affected_skill_ids={"s1"},
        )
        assert result is results

    def test_tool_degradation_requires_tool_key(self):
        engine, _ = _make_engine()
        with pytest.raises(ValueError, match="tool_key.*required"):
            engine.evolve(trigger=EvolutionTrigger.TOOL_DEGRADATION)

    def test_tool_degradation_defaults_problem_description(self):
        engine, _ = _make_engine()
        engine._evolver.process_tool_degradation = MagicMock(return_value=[])

        engine.evolve(trigger=EvolutionTrigger.TOOL_DEGRADATION, tool_key="x")

        engine._evolver.process_tool_degradation.assert_called_once_with(
            tool_key="x", problem_description="", affected_skill_ids=None,
        )


class TestEvolveMetricCheck:
    def test_metric_check_delegates(self):
        engine, _ = _make_engine()
        results = [EvolveResult(success=False, error="too few")]
        engine._evolver.process_metric_check = MagicMock(return_value=results)

        result = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=10)

        engine._evolver.process_metric_check.assert_called_once_with(min_selections=10)
        assert result is results

    def test_metric_check_min_selections_clamped_to_1(self):
        engine, _ = _make_engine()
        engine._evolver.process_metric_check = MagicMock(return_value=[])

        engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=0)

        engine._evolver.process_metric_check.assert_called_once_with(min_selections=1)

    def test_metric_check_negative_min_selections_clamped(self):
        engine, _ = _make_engine()
        engine._evolver.process_metric_check = MagicMock(return_value=[])

        engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=-5)

        engine._evolver.process_metric_check.assert_called_once_with(min_selections=1)


class TestEvolveUnknownTrigger:
    def test_unknown_trigger_raises(self):
        engine, _ = _make_engine()
        with pytest.raises(ValueError, match="Unknown trigger"):
            engine.evolve(trigger="nonexistent")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# check_health()
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_check_health_returns_suggestions(self):
        engine, _ = _make_engine()
        suggestions = [MagicMock(spec=EvolutionSuggestion)]
        engine._health_checker.check_health = MagicMock(return_value=suggestions)

        result = engine.check_health("skill-1")

        assert result is suggestions

    def test_check_health_raises_on_missing_skill(self):
        engine, store = _make_engine()
        store.get_skill_record = MagicMock(return_value=None)

        with pytest.raises(ValueError, match="Skill not found"):
            engine.check_health("nonexistent")

    def test_check_health_healthy_skill_returns_empty(self):
        engine, _ = _make_engine()
        engine._health_checker.check_health = MagicMock(return_value=[])

        result = engine.check_health("healthy-skill")
        assert result == []


# ---------------------------------------------------------------------------
# diagnose_all()
# ---------------------------------------------------------------------------


class TestDiagnoseAll:
    def test_diagnose_all_delegates(self):
        engine, _ = _make_engine()
        reports = {"s1": MagicMock(spec=HealthReport)}
        engine._health_checker.diagnose_all = MagicMock(return_value=reports)

        result = engine.diagnose_all()
        assert result is reports

    def test_diagnose_all_empty(self):
        engine, _ = _make_engine()
        engine._health_checker.diagnose_all = MagicMock(return_value={})

        result = engine.diagnose_all()
        assert result == {}


# ---------------------------------------------------------------------------
# promote_candidate()
# ---------------------------------------------------------------------------


class TestPromoteCandidate:
    def test_promote_delegates(self):
        engine, _ = _make_engine()
        candidate = PromotionCandidate(
            skill_id="s1", skill_name="my-skill",
            effective_rate=0.9, total_selections=50,
            directory="/tmp/s1", reason="high usage",
        )
        promo_result = PromotionResult(success=True, agent_name="my-skill-agent")
        engine._promoter.promote = MagicMock(return_value=promo_result)

        result = engine.promote_candidate(candidate)
        assert result is promo_result
        engine._promoter.promote.assert_called_once_with(candidate)

    def test_promote_failed_returns_error(self):
        engine, _ = _make_engine()
        candidate = PromotionCandidate(
            skill_id="s2", skill_name="bad",
            effective_rate=0.1, total_selections=2,
            directory="/tmp/s2", reason="test",
        )
        promo_result = PromotionResult(success=False, error="directory not found")
        engine._promoter.promote = MagicMock(return_value=promo_result)

        result = engine.promote_candidate(candidate)
        assert result.success is False
        assert result.error == "directory not found"


# ---------------------------------------------------------------------------
# should_compact()
# ---------------------------------------------------------------------------


class TestShouldCompact:
    def test_should_compact_delegates(self):
        engine, _ = _make_engine()
        ctx = MagicMock(spec=AgentContext)
        engine._compaction_guard.should_compact = MagicMock(return_value=True)

        assert engine.should_compact(ctx) is True
        engine._compaction_guard.should_compact.assert_called_once_with(ctx)

    def test_should_compact_false(self):
        engine, _ = _make_engine()
        ctx = MagicMock(spec=AgentContext)
        engine._compaction_guard.should_compact = MagicMock(return_value=False)

        assert engine.should_compact(ctx) is False
