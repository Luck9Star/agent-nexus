"""Unit tests for agent_nexus.platform.evolution.evolver module."""

from unittest.mock import MagicMock

from agent_nexus.models.evolution import (
    EvolutionType, SkillLineage, SkillOrigin, SkillRecord,
)
from agent_nexus.platform.evolution.analyzer import AnalysisResult, EvolutionSuggestion
from agent_nexus.platform.evolution.evolver import EvolveResult, SkillEvolver


def _skill(id="sk-1", name="test-skill", selections=0, applied=0,
           completions=0, fallbacks=0, gen=1, directory="skills/test") -> SkillRecord:
    return SkillRecord(
        id=id, name=name,
        total_selections=selections, total_applied=applied,
        total_completions=completions, total_fallbacks=fallbacks,
        lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=gen),
        directory=directory,
    )


def _make_store(skills=None):
    store = MagicMock()
    store.get_active_skills.return_value = skills or []
    store.evolve_skill.return_value = EvolveResult(success=True)
    store.get_skill_records_batch.return_value = {}
    return store


class TestEvolveFix:
    def test_fix_creates_new_record(self):
        parent = _skill()
        store = _make_store()
        store.get_skill_record.return_value = parent
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.FIX, target_skill_ids=["sk-1"], direction="broken",
        ))
        assert result.success is True
        assert result.new_record is not None
        assert result.new_record.lineage.origin == SkillOrigin.FIXED

    def test_fix_requires_exactly_one_parent(self):
        store = _make_store()
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.FIX, target_skill_ids=["a", "b"], direction="x",
        ))
        assert result.success is False
        assert "exactly 1 parent" in result.error

    def test_fix_fails_if_parent_missing(self):
        store = _make_store()
        store.get_skill_record.return_value = None
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.FIX, target_skill_ids=["missing"], direction="x",
        ))
        assert result.success is False

    def test_fix_with_empty_target_ids(self):
        result = SkillEvolver(_make_store()).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.FIX, target_skill_ids=[],
        ))
        assert result.success is False

    def test_fix_store_write_failure(self):
        """FIX should return failure when store.evolve_skill fails."""
        parent = _skill()
        store = _make_store()
        store.get_skill_record.return_value = parent
        store.evolve_skill.return_value = EvolveResult(success=False, error="DB write failed")
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.FIX, target_skill_ids=["sk-1"], direction="broken",
        ))
        assert result.success is False
        assert result.new_record is None


class TestEvolveDerived:
    def test_derived_creates_enhanced_record(self):
        parent = _skill()
        store = _make_store()
        store.get_skill_records_batch.return_value = {"sk-1": parent}
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED, target_skill_ids=["sk-1"], direction="enhance",
        ))
        assert result.success is True
        assert result.new_record is not None
        assert "enhanced" in result.new_record.name
        assert result.new_record.lineage.origin == SkillOrigin.DERIVED

    def test_derived_merge_multiple_parents(self):
        p1, p2 = _skill(id="a", name="alpha"), _skill(id="b", name="beta", gen=2)
        store = _make_store()
        store.get_skill_records_batch.return_value = {"a": p1, "b": p2}
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED, target_skill_ids=["a", "b"], direction="merge",
        ))
        assert result.success is True
        assert result.new_record is not None
        assert "merged" in result.new_record.name

    def test_derived_with_empty_targets_fails(self):
        result = SkillEvolver(_make_store()).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED, target_skill_ids=[],
        ))
        assert result.success is False
        assert "at least 1 parent" in result.error

    def test_derived_parent_not_found_fails(self):
        store = _make_store()
        store.get_skill_records_batch.return_value = {}  # no parents found
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED, target_skill_ids=["missing"],
        ))
        assert result.success is False
        assert "Parent skill not found" in result.error


class TestEvolveCaptured:
    def test_captured_creates_new_skill(self):
        store = _make_store()
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED, target_skill_ids=[],
            direction="A useful pattern. First sentence.",
        ))
        assert result.success is True
        assert result.new_record is not None
        assert result.new_record.lineage.origin == SkillOrigin.CAPTURED

    def test_captured_requires_direction(self):
        result = SkillEvolver(_make_store()).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED, target_skill_ids=[], direction="",
        ))
        assert result.success is False
        assert "direction" in result.error

    def test_captured_sanitizes_special_chars_in_name(self):
        store = _make_store()
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED, target_skill_ids=[],
            direction="Pattern with @#$% special chars! here",
        ))
        assert result.success is True
        assert result.new_record is not None
        # Name should only contain a-z, 0-9, hyphens
        name = result.new_record.name
        assert all(c.isalnum() or c == "-" for c in name)

    def test_captured_with_custom_directory(self):
        store = _make_store()
        result = SkillEvolver(store).evolve(
            EvolutionSuggestion(
                evolution_type=EvolutionType.CAPTURED, target_skill_ids=[],
                direction="Custom pattern.",
            ),
            capture_directory="custom/dir",
        )
        assert result.success is True
        assert result.new_record is not None
        assert result.new_record.directory == "custom/dir"

    def test_captured_long_direction_truncated_name(self):
        store = _make_store()
        long_direction = "A" * 200 + ". rest of it"
        result = SkillEvolver(store).evolve(EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED, target_skill_ids=[],
            direction=long_direction,
        ))
        assert result.success is True
        assert result.new_record is not None
        # Name base comes from first 50 chars of direction
        assert len(result.new_record.name) <= 50

    def test_captured_with_task_id_in_snapshot(self):
        store = _make_store()
        result = SkillEvolver(store).evolve(
            EvolutionSuggestion(
                evolution_type=EvolutionType.CAPTURED, target_skill_ids=[],
                direction="Pattern found.",
            ),
            task_id="task-42",
        )
        assert result.success is True
        assert result.new_record is not None
        assert result.new_record.lineage.content_snapshot is not None  # pyright: ignore[reportOptionalMemberAccess]
        assert result.new_record.lineage.content_snapshot["source_task_id"] == "task-42"


class TestProcessAnalysis:
    def test_processes_all_suggestions(self):
        parent = _skill()
        store = _make_store()
        store.get_skill_record.return_value = parent
        analysis = AnalysisResult(
            task_id="t1", agent_name="a1", analysis_text="",
            suggestions=[EvolutionSuggestion(
                evolution_type=EvolutionType.FIX, target_skill_ids=["sk-1"], direction="fix it",
            )],
        )
        results = SkillEvolver(store).process_analysis(analysis)
        assert len(results) == 1
        assert results[0].success is True

    def test_empty_suggestions_returns_empty(self):
        store = _make_store()
        analysis = AnalysisResult(
            task_id="t1", agent_name="a1", analysis_text="",
            suggestions=[],
        )
        results = SkillEvolver(store).process_analysis(analysis)
        assert results == []

    def test_multiple_suggestions_each_processed(self):
        parent = _skill()
        store = _make_store()
        store.get_skill_record.return_value = parent
        analysis = AnalysisResult(
            task_id="t1", agent_name="a1", analysis_text="",
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX, target_skill_ids=["sk-1"],
                    direction="fix",
                ),
                EvolutionSuggestion(
                    evolution_type=EvolutionType.DERIVED, target_skill_ids=["sk-1"],
                    direction="enhance",
                ),
            ],
        )
        results = SkillEvolver(store).process_analysis(analysis)
        assert len(results) == 2


class TestProcessToolDegradation:
    def test_skips_already_addressed_skills(self):
        skill = _skill(id="sk-1")
        store = _make_store([skill])
        store.get_skill_record.return_value = skill
        evolver = SkillEvolver(store)
        evolver.process_tool_degradation("tool-1", "broken", {"sk-1"})
        results = evolver.process_tool_degradation("tool-1", "still broken", {"sk-1"})
        assert len(results) == 0

    def test_filters_by_affected_skill_ids(self):
        s1, s2 = _skill(id="s1"), _skill(id="s2")
        store = _make_store([s1, s2])
        store.get_skill_record.return_value = s1
        results = SkillEvolver(store).process_tool_degradation("t", "bad", {"s1"})
        assert len(results) == 1

    def test_no_filter_evolves_all_active_skills(self):
        """When affected_skill_ids is None, all active skills are considered."""
        s1, s2 = _skill(id="s1"), _skill(id="s2")
        store = _make_store([s1, s2])
        store.get_skill_record.side_effect = lambda sid: {"s1": s1, "s2": s2}.get(sid)
        results = SkillEvolver(store).process_tool_degradation("t", "bad", None)
        assert len(results) == 2

    def test_failed_evolution_not_marked_addressed(self):
        """Only successful evolutions are added to the addressed set."""
        skill = _skill(id="sk-1")
        store = _make_store([skill])
        store.get_skill_record.return_value = skill
        # Make the evolution fail
        store.evolve_skill.return_value = EvolveResult(success=False, error="write failed")
        evolver = SkillEvolver(store)
        evolver.process_tool_degradation("tool-1", "broken", {"sk-1"})
        # Skill should NOT be in addressed set
        assert "sk-1" not in evolver._addressed.get("tool-1", set())

    def test_addressed_prevents_re_evolution_on_success(self):
        """After a successful evolution, the skill is in addressed and skipped."""
        skill = _skill(id="sk-1")
        store = _make_store([skill])
        store.get_skill_record.return_value = skill
        evolver = SkillEvolver(store)
        # First call succeeds
        results1 = evolver.process_tool_degradation("tool-1", "broken", {"sk-1"})
        assert len(results1) == 1
        assert results1[0].success is True
        # Second call skips because addressed
        results2 = evolver.process_tool_degradation("tool-1", "still broken", {"sk-1"})
        assert len(results2) == 0

    def test_different_tool_keys_tracked_separately(self):
        """Addressed sets are per-tool-key."""
        skill = _skill(id="sk-1")
        store = _make_store([skill])
        store.get_skill_record.return_value = skill
        evolver = SkillEvolver(store)
        evolver.process_tool_degradation("tool-1", "bad", {"sk-1"})
        # tool-2 is a different tool, should still evolve sk-1
        results = evolver.process_tool_degradation("tool-2", "also bad", {"sk-1"})
        assert len(results) == 1

    def test_empty_active_skills_returns_empty(self):
        store = _make_store([])
        evolver = SkillEvolver(store)
        results = evolver.process_tool_degradation("t", "bad", {"s1"})
        assert results == []


class TestProcessMetricCheck:
    def test_skips_skills_below_min_selections(self):
        skill = _skill(selections=3, applied=2, completions=0, fallbacks=2)
        store = _make_store([skill])
        results = SkillEvolver(store).process_metric_check(min_selections=5)
        assert len(results) == 0

    def test_evolves_unhealthy_skills(self):
        skill = _skill(selections=10, applied=6, completions=0, fallbacks=6)
        store = _make_store([skill])
        store.get_skill_record.return_value = skill
        results = SkillEvolver(store).process_metric_check(min_selections=5)
        assert len(results) >= 1


class TestPruneRecoveredTools:
    def test_removes_recovered_tool_entries(self):
        evolver = SkillEvolver(_make_store())
        evolver._addressed = {"tool-a": {"s1"}, "tool-b": {"s2"}}
        evolver.prune_recovered_tools({"tool-a"})
        assert "tool-a" in evolver._addressed
        assert "tool-b" not in evolver._addressed

    def test_prune_with_empty_addressed_set(self):
        """Pruning with no addressed entries is a no-op."""
        evolver = SkillEvolver(_make_store())
        evolver.prune_recovered_tools({"tool-a"})
        assert evolver._addressed == {}

    def test_prune_with_all_recovered(self):
        """When all tools recover, addressed dict becomes empty."""
        evolver = SkillEvolver(_make_store())
        evolver._addressed = {"tool-a": {"s1"}, "tool-b": {"s2"}}
        evolver.prune_recovered_tools(set())
        assert evolver._addressed == {}


class TestEvolveUnknownType:
    def test_unknown_evolution_type_returns_error(self):
        store = _make_store()
        # Create a suggestion with an invalid type by patching
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["sk-1"],
            direction="test",
        )
        # Mutate the type to something invalid
        object.__setattr__(suggestion, "evolution_type", "bogus")
        result = SkillEvolver(store).evolve(suggestion)
        assert result.success is False
        assert "Unknown evolution type" in result.error


class TestDiagnoseSkillHealth:
    def test_healthy_skill_returns_none(self):
        skill = _skill(selections=10, applied=9, completions=8, fallbacks=1)
        assert SkillEvolver._diagnose_skill_health(skill) is None

    def test_zero_selections_returns_none(self):
        assert SkillEvolver._diagnose_skill_health(_skill()) is None

    def test_high_fallback_triggers_fix(self):
        skill = _skill(id="s1", selections=10, applied=6, completions=0, fallbacks=6)
        suggestion = SkillEvolver._diagnose_skill_health(skill)
        assert suggestion is not None
        assert suggestion.evolution_type == EvolutionType.FIX


class TestStoreProperty:
    def test_store_returns_underlying_store(self):
        store = _make_store()
        assert SkillEvolver(store).store is store
