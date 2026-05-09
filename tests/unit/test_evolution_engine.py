"""Unit tests for agent_nexus.platform.evolution.engine module."""

from pathlib import Path
from unittest.mock import MagicMock

from agent_nexus.models.evolution import EvolutionContext, EvolutionType, SkillRecord
from agent_nexus.platform.evolution.analyzer import AnalysisResult
from agent_nexus.platform.evolution.compaction import AgentContext
from agent_nexus.platform.evolution.engine import EvolutionEngine
from agent_nexus.platform.evolution.evolver import EvolutionTrigger, EvolveResult
from agent_nexus.platform.evolution.promotion import PromotionCandidate, PromotionResult

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
        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        assert isinstance(result, AnalysisResult)
        assert result.task_id == "task-1"

    def test_post_analysis_calls_analyzer_and_records(self):
        store = _make_store()
        store.get_active_skills.return_value = [
            SkillRecord(id="sk-1", name="skill-1"),
        ]
        engine = EvolutionEngine(store)
        ctx = _make_ctx()
        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        # Analyzer records analysis even if no suggestions produced
        store.record_analysis.assert_called_once()
        assert result.task_id == "task-1"

    def test_post_analysis_requires_ctx(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        import pytest  # pyright: ignore[reportMissingImports]

        with pytest.raises(ValueError, match="ctx"):
            engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS)


class TestEvolveToolDegradation:
    def test_tool_degradation_returns_list(self):
        store = _make_store()
        store.get_active_skills.return_value = [
            SkillRecord(id="sk-1", name="skill-1"),
        ]
        engine = EvolutionEngine(store)
        results = engine.evolve(trigger=EvolutionTrigger.TOOL_DEGRADATION, tool_key="api-x")
        assert len(results) == 1
        assert results[0].success is False

    def test_tool_degradation_requires_tool_key(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        import pytest  # pyright: ignore[reportMissingImports]

        with pytest.raises(ValueError, match="tool_key"):
            engine.evolve(trigger=EvolutionTrigger.TOOL_DEGRADATION)


class TestEvolveMetricCheck:
    def test_metric_check_returns_list(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        assert results == []


class TestEvolveUnknownTrigger:
    def test_raises_on_unknown_trigger(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        import pytest  # pyright: ignore[reportMissingImports]

        with pytest.raises(ValueError, match="Unknown trigger"):
            engine.evolve(trigger="bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_delegates_to_health_checker(self):
        store = _make_store()
        skill = SkillRecord(
            id="sk-1",
            name="s1",
            total_selections=10,
            total_applied=6,
            total_completions=1,
            total_fallbacks=5,
        )
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
            skill_id="sk-1",
            skill_name="good-skill",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/good",
            reason="high performance",
        )
        result = engine.promote_candidate(candidate)
        assert result.success is True
        assert result.agent_name == "good-skill"


class TestShouldCompact:
    def test_delegates_to_compaction_guard(self):
        store = _make_store()
        engine = EvolutionEngine(store)
        from agent_nexus.models.context import TokenUsage

        ctx = AgentContext(
            agent_id="a1",
            session_id="s1",
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
        )
        assert engine.should_compact(ctx) is False


# iter122 regression: min_selections minimum guard


class TestEvolveMinSelections:
    """evolve(metric_check) clamps min_selections to max(n, 1)."""

    def test_metric_check_min_selections_zero(self) -> None:
        store = _make_store()
        store.get_active_skills.return_value = []
        engine = EvolutionEngine(store)
        # min_selections=0 is clamped to 1 — should not error
        result = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=0)
        assert result == []

    def test_metric_check_min_selections_negative(self) -> None:
        store = _make_store()
        store.get_active_skills.return_value = []
        engine = EvolutionEngine(store)
        result = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=-5)
        assert result == []
