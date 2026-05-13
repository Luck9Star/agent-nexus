"""Integration tests for Agency Pipeline -> EvolutionEngine hook.

Validates that TaskComposer._trigger_evolution() correctly:
1. Builds EvolutionContext from pipeline results
2. Calls EvolutionEngine.evolve(trigger=POST_ANALYSIS, ctx=...)
3. Sets result.evolution_triggered when suggestions are produced
4. Swallows exceptions without crashing the pipeline
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest  # pyright: ignore[reportMissingImports]

from agent_nexus.models.evolution import (
    EvolutionContext,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.selector import SelectionResult
from agent_nexus.platform.agency.task_composer import (
    TaskComposer,
    TaskComposerInput,
    TaskComposerResult,
)
from agent_nexus.platform.evolution.engine import EvolutionEngine
from agent_nexus.platform.evolution.store import EvolutionStore


@pytest.fixture
def in_memory_store() -> Generator[EvolutionStore, None, None]:
    """Create an in-memory EvolutionStore for testing."""
    store = EvolutionStore(Path(":memory:"))
    yield store
    store.close()


@pytest.fixture
def engine(in_memory_store: EvolutionStore) -> EvolutionEngine:
    """Create an EvolutionEngine backed by an in-memory store."""
    return EvolutionEngine(in_memory_store)


def _seed_skill(store: EvolutionStore, skill_id: str = "test-skill__v1") -> None:
    """Insert a minimal active skill into the store."""
    store.save_skill_record(
        SkillRecord(
            id=skill_id,
            name=skill_id.split("__")[0],
            lineage=SkillLineage(origin=SkillOrigin.IMPORTED),
            total_selections=10,
            total_applied=5,
            total_completions=2,
            total_fallbacks=3,
        )
    )


# ---------------------------------------------------------------------------
# Test: _trigger_evolution with real EvolutionEngine
# ---------------------------------------------------------------------------


class TestTriggerEvolutionWithEngine:
    """Tests for the _trigger_evolution() -> EvolutionEngine.evolve() path."""

    def test_trigger_evolution_with_engine_and_suggestions(self, engine: EvolutionEngine) -> None:
        """When skill IDs match, analyzer produces suggestions for unhealthy skills.

        Note: The agency pipeline's agent_id format (e.g. 'agency.expert-a')
        differs from the skill store ID format (e.g. 'skill-name__v1'). The
        analyzer's fuzzy matcher requires '__' in the ID to work. So for
        suggestions to be produced, the skill_ids_used must match a known
        skill ID in the store.
        """
        # Seed a skill with poor health and use a skill_id that matches exactly
        _seed_skill(engine.store, "test-skill__v1")

        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=engine)

        # Use a skill_id that matches the stored skill exactly
        result = TaskComposerResult(
            task="review the code",
            selected_agents=[
                SelectionResult(
                    agent_id="test-skill__v1",
                    score=0.9,
                )
            ],
            qa_passed=True,
        )

        composer._trigger_evolution(result)
        assert result.evolution_triggered is True

    def test_trigger_evolution_agent_id_format_mismatch(self, engine: EvolutionEngine) -> None:
        """Agent IDs like 'agency.expert-a' won't fuzzy-match 'skill__v1' format.

        This documents the current behavior: the analyzer requires skill IDs to
        contain '__' for fuzzy matching. Agency pipeline agent_id format does
        not contain '__', so no suggestions are produced for those agents.
        This is expected — evolution analysis works best when skill_ids_used
        align with the skill registry's naming convention.
        """
        _seed_skill(engine.store, "agency.expert-a__v1")

        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=engine)

        result = TaskComposerResult(
            task="review the code",
            selected_agents=[
                SelectionResult(
                    agent_id="agency.expert-a",
                    score=0.9,
                )
            ],
            qa_passed=True,
        )

        composer._trigger_evolution(result)
        # No suggestions because 'agency.expert-a' doesn't fuzzy-match
        # to 'agency.expert-a__v1' (no '__' separator in the query ID)
        assert result.evolution_triggered is False

    def test_trigger_evolution_no_agents_sets_default_agent_id(
        self, engine: EvolutionEngine
    ) -> None:
        """When no agents selected, agent_id should fall back to 'agency-pipeline'."""
        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=engine)

        result = TaskComposerResult(task="empty task")

        # This should not crash even with no selected_agents
        composer._trigger_evolution(result)

        # evolution_triggered may be False if no skills in store,
        # but should not raise
        assert isinstance(result.evolution_triggered, bool)

    def test_trigger_evolution_exception_swallowed(
        self,
    ) -> None:
        """Exceptions from EvolutionEngine should be caught and logged."""
        # Create a mock engine that raises
        mock_engine = MagicMock(spec=EvolutionEngine)
        mock_engine.evolve.side_effect = RuntimeError("DB corrupted")

        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=mock_engine)

        result = TaskComposerResult(
            task="test",
            selected_agents=[
                SelectionResult(
                    agent_id="agency.x",
                    score=0.9,
                )
            ],
            qa_passed=True,
        )

        # Should NOT raise
        composer._trigger_evolution(result)
        assert result.evolution_triggered is False

    def test_trigger_evolution_no_suggestions(self, engine: EvolutionEngine) -> None:
        """When engine finds no suggestions, evolution_triggered stays False."""
        # Empty store, no skills to analyze -> no suggestions
        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=engine)

        result = TaskComposerResult(
            task="simple task",
            selected_agents=[
                SelectionResult(
                    agent_id="agency.x",
                    score=0.9,
                )
            ],
            qa_passed=True,
        )

        composer._trigger_evolution(result)
        assert result.evolution_triggered is False

    def test_trigger_evolution_uses_post_analysis_trigger(
        self,
    ) -> None:
        """Verify the engine.evolve() is called with POST_ANALYSIS trigger."""
        from agent_nexus.platform.evolution.evolver import EvolutionTrigger

        mock_engine = MagicMock(spec=EvolutionEngine)
        # Return an AnalysisResult with no suggestions (safe default)
        from agent_nexus.platform.evolution.analyzer import AnalysisResult

        mock_engine.evolve.return_value = AnalysisResult(
            task_id="t1",
            agent_name="a1",
            analysis_text="ok",
            suggestions=[],
        )

        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=mock_engine)

        result = TaskComposerResult(
            task="test",
            selected_agents=[
                SelectionResult(
                    agent_id="agency.x",
                    score=0.9,
                )
            ],
        )

        composer._trigger_evolution(result)

        mock_engine.evolve.assert_called_once()
        call_kwargs = mock_engine.evolve.call_args[1]
        assert call_kwargs["trigger"] == EvolutionTrigger.POST_ANALYSIS
        assert isinstance(call_kwargs["ctx"], EvolutionContext)
        assert call_kwargs["ctx"].agent_id == "agency.x"

    def test_run_method_triggers_evolution_with_engine(self, engine: EvolutionEngine) -> None:
        """Full run() path: when evolution_engine is injected, it's called.

        With an empty ExpertRegistry, no specialists are selected so the
        pipeline returns early (line 288) and _trigger_evolution is NOT called.
        This confirms the early-return optimization works correctly.
        """
        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=engine)
        inp = TaskComposerInput(task="test task")

        result = composer.run(inp)

        # Early return because registry is empty, no experts selected
        assert result.evolution_triggered is False

    def test_evolution_context_fields_populated(
        self,
    ) -> None:
        """Verify EvolutionContext is correctly built from pipeline result."""
        captured: list[EvolutionContext] = []

        mock_engine = MagicMock(spec=EvolutionEngine)
        from agent_nexus.platform.evolution.analyzer import AnalysisResult

        def capture_evolve(**kwargs):
            captured.append(kwargs["ctx"])
            return AnalysisResult(
                task_id=kwargs["ctx"].task_id,
                agent_name=kwargs["ctx"].agent_id,
                analysis_text="",
                suggestions=[],
            )

        mock_engine.evolve.side_effect = capture_evolve

        registry = ExpertRegistry()
        composer = TaskComposer(registry, evolution_engine=mock_engine)

        result = TaskComposerResult(
            task="review security code",
            selected_agents=[
                SelectionResult(
                    agent_id="agency.expert-a",
                    score=0.9,
                ),
                SelectionResult(
                    agent_id="agency.expert-b",
                    score=0.8,
                ),
            ],
            qa_passed=True,
        )

        composer._trigger_evolution(result)

        assert len(captured) == 1
        ctx = captured[0]
        assert ctx.agent_id == "agency.expert-a"
        assert ctx.task_description == "review security code"
        assert ctx.task_completed is True
        assert ctx.skill_ids_used == ["agency.expert-a", "agency.expert-b"]
        assert ctx.task_id.startswith("composition-")
