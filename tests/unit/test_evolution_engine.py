"""Unit tests for agent_nexus.platform.evolution.engine module."""

from pathlib import Path
from unittest.mock import MagicMock

from agent_nexus.models.evolution import EvolutionContext, SkillRecord
from agent_nexus.platform.evolution.analyzer import AnalysisResult
from agent_nexus.platform.evolution.compaction import AgentContext
from agent_nexus.platform.evolution.engine import EvolutionEngine
from agent_nexus.platform.evolution.evolver import EvolveResult
from agent_nexus.platform.evolution.promotion import PromotionCandidate, PromotionResult
from agent_nexus.models.evolution import EvolutionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> MagicMock:
    store = MagicMock()
    store.get_active_skills.return_value = []
    store.get_skill_record.return_value = None
    store.record_analysis.return_value = "analysis-1"
    store.evolve_skill.return_value = EvolveResult(success=True)
    return store


def _make_ctx(**overrides) -> EvolutionContext:
    defaults = dict(
        agent_id="agent-1",
        task_id="task-1",
        task_completed=True,
        skill_ids_used=["sk-1"],
        skills_applied=["sk-1"],
    )
    defaults.update(overrides)
    return EvolutionContext(**defaults)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# Constructor + properties
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_sub_components(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        assert engine.store is store
        assert engine.analyzer is not None
        assert engine.evolver is not None
        assert engine.health_checker is not None
        assert engine.compaction_guard is not None
        assert engine.promoter is not None

    def test_passes_agent_id_and_agents_root(self):
        store = _make_store()
        root = Path("/tmp/agents")
        engine = EvolutionEngine(store, agent_id="my-agent", agents_root=root)
        assert engine.compaction_guard._agent_id == "my-agent"
        assert engine.promoter._agents_root == root.resolve()


# ---------------------------------------------------------------------------
# evolve() routing
# ---------------------------------------------------------------------------

class TestEvolvePostAnalysis:
    def test_post_analysis_returns_analysis_result(self):
        store = _make_store()
        store.get_active_skills.return_value = [
            SkillRecord(id="sk-1", name="skill-1"),
        ]
        engine = EvolutionEngine(store)
        ctx = _make_ctx()
        result = engine.evolve(trigger="post_analysis", ctx=ctx)
        assert isinstance(result, AnalysisResult)
        assert result.task_id == "task-1"

    def test_post_analysis_calls_analyzer_and_records(self):
        store = _make_store()
        store.get_active_skills.return_value = [
            SkillRecord(id="sk-1", name="skill-1"),
        ]
        engine = EvolutionEngine(store)
        ctx = _make_ctx()
        result = engine.evolve(trigger="post_analysis", ctx=ctx)
        # Analyzer records analysis even if no suggestions produced
        store.record_analysis.assert_called_once()
        assert isinstance(result, AnalysisResult)

    def test_post_analysis_requires_ctx(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        import pytest  # pyright: ignore[reportMissingImports]
        with pytest.raises(ValueError, match="ctx"):
            engine.evolve(trigger="post_analysis")


class TestEvolveToolDegradation:
    def test_tool_degradation_returns_list(self):
        store = _make_store()
        store.get_active_skills.return_value = [
            SkillRecord(id="sk-1", name="skill-1"),
        ]
        engine = EvolutionEngine(store)
        results = engine.evolve(trigger="tool_degradation", tool_key="api-x")
        assert isinstance(results, list)

    def test_tool_degradation_requires_tool_key(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        import pytest  # pyright: ignore[reportMissingImports]
        with pytest.raises(ValueError, match="tool_key"):
            engine.evolve(trigger="tool_degradation")


class TestEvolveMetricCheck:
    def test_metric_check_returns_list(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        results = engine.evolve(trigger="metric_check")
        assert isinstance(results, list)


class TestEvolveUnknownTrigger:
    def test_raises_on_unknown_trigger(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        import pytest  # pyright: ignore[reportMissingImports]
        with pytest.raises(ValueError, match="Unknown trigger"):
            engine.evolve(trigger="bogus")


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------

class TestCheckHealth:
    def test_delegates_to_health_checker(self):
        store = _make_store()
        skill = SkillRecord(id="sk-1", name="s1", total_selections=10,
                            total_applied=6, total_completions=1, total_fallbacks=5)
        store.get_skill_record.return_value = skill
        engine = EvolutionEngine(store)
        suggestions = engine.check_health("sk-1")
        assert any(s.evolution_type == EvolutionType.FIX for s in suggestions)

    def test_raises_on_missing_skill(self):
        store = _make_store()
        store.get_skill_record.return_value = None
        engine = EvolutionEngine(store)
        import pytest  # pyright: ignore[reportMissingImports]
        with pytest.raises(ValueError, match="Skill not found"):
            engine.check_health("missing")


class TestDiagnoseAll:
    def test_returns_dict_of_reports(self):
        store = _make_store()
        store.get_active_skills.return_value = [
            SkillRecord(id="sk-1", name="s1"),
        ]
        engine = EvolutionEngine(store)
        reports = engine.diagnose_all()
        assert isinstance(reports, dict)
        assert "sk-1" in reports


class TestPromoteCandidate:
    def test_delegates_to_promoter(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        candidate = PromotionCandidate(
            skill_id="sk-1", skill_name="good-skill",
            effective_rate=0.9, total_selections=100,
            directory="skills/good",
            reason="high performance",
        )
        result = engine.promote_candidate(candidate)
        assert isinstance(result, PromotionResult)


class TestShouldCompact:
    def test_delegates_to_compaction_guard(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        from agent_nexus.models.context import TokenUsage
        ctx = AgentContext(
            agent_id="a1", session_id="s1",
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
        )
        assert engine.should_compact(ctx) is False
