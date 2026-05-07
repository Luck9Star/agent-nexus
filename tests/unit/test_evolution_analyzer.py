"""Unit tests for agent_nexus.platform.evolution.analyzer module."""

from unittest.mock import MagicMock

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionType,
    SkillRecord,
)
from agent_nexus.platform.evolution.analyzer import (
    AnalysisResult,
    ExecutionAnalyzer,
    _edit_distance,
    _correct_skill_ids,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(skills: list[SkillRecord] | None = None) -> MagicMock:
    store = MagicMock()
    store.get_active_skills.return_value = skills or []
    store.record_analysis.return_value = "analysis-1"
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
# _edit_distance
# ---------------------------------------------------------------------------


class TestEditDistance:
    def test_identical_strings(self):
        assert _edit_distance("abc", "abc") == 0

    def test_empty_strings(self):
        assert _edit_distance("", "") == 0
        assert _edit_distance("abc", "") == 3

    def test_insertion(self):
        assert _edit_distance("abc", "abxc") == 1

    def test_unicode_strings(self):
        assert _edit_distance("café", "cafe") == 1

    def test_special_chars(self):
        assert _edit_distance("a-b_c", "a-b_d") == 1

    def test_asymmetric_lengths(self):
        assert _edit_distance("a", "abcde") == 4


# ---------------------------------------------------------------------------
# _correct_skill_ids
# ---------------------------------------------------------------------------


class TestCorrectSkillIds:
    def test_returns_same_if_known(self):
        known = {"sk__abc", "sk__def"}
        assert _correct_skill_ids(["sk__abc"], known) == ["sk__abc"]

    def test_corrects_typo_within_threshold(self):
        known = {"sk__abcde"}
        # suffix "abcdf" len=5 <=8, max_dist=2
        # distance("sk__abcdf","sk__abcde")=1 < max_dist=2 -> corrected
        result = _correct_skill_ids(["sk__abcdf"], known)
        assert result == ["sk__abcde"]

    def test_passes_through_if_no_prefix_separator(self):
        known = {"sk__abc"}
        result = _correct_skill_ids(["nope"], known)
        assert result == ["nope"]

    def test_returns_same_if_ambiguous(self):
        # Two candidates at equal distance
        known = {"sk__abx", "sk__aby"}
        result = _correct_skill_ids(["sk__abc"], known)
        assert result == ["sk__abc"]  # can't resolve, keeps original

    def test_empty_known_set_passes_through(self):
        assert _correct_skill_ids(["x"], set()) == ["x"]

    def test_many_candidates_uses_tighter_threshold(self):
        """When >20 candidates exist, max_dist=2 regardless of suffix length."""
        # Create 21 candidates all sharing the same prefix
        known = {f"ns__{chr(i)}" for i in range(ord("a"), ord("a") + 21)}
        # "ns__abc" has distance 1 from some candidates but with 21 candidates
        # the tighter max_dist=2 still allows correction to nearest
        raw_id = "ns__abz"
        result = _correct_skill_ids([raw_id], known)
        # Should correct to the nearest candidate if unambiguous
        assert len(result) == 1

    def test_short_suffix_max_dist_1(self):
        """Suffix length <=4 gets max_dist=1; distance-1 correction works."""
        known = {"sk__ab", "sk__cd"}
        # "sk__ac" is distance 1 from "sk__ab" and distance 2 from "sk__cd"
        # -> unambiguous best, corrected to "sk__ab"
        result = _correct_skill_ids(["sk__ac"], known)
        assert result == ["sk__ab"]

    def test_short_suffix_ambiguous_kept(self):
        """When two candidates tie at the same distance within threshold, original kept."""
        known = {"sk__ab", "sk__ad"}
        # "sk__ac" is distance 1 from both "sk__ab" and "sk__ad" -> ambiguous
        result = _correct_skill_ids(["sk__ac"], known)
        assert result == ["sk__ac"]

    def test_short_suffix_exact_match(self):
        """Short suffix that is an exact match passes through."""
        known = {"sk__ab"}
        result = _correct_skill_ids(["sk__ab"], known)
        assert result == ["sk__ab"]

    def test_unambiguous_best_wins(self):
        """When one candidate is clearly closer, it wins."""
        # Use suffixes > 4 chars so max_dist=2, allowing distance-1 correction.
        known = {"sk__hello-there", "sk__world-peace"}
        # "sk__hello-thers" is distance 1 from "sk__hello-there"
        result = _correct_skill_ids(["sk__hello-thers"], known)
        assert result == ["sk__hello-there"]

    def test_mixed_known_and_unknown_ids(self):
        """IDs already known pass through; only unknown ones get corrected."""
        # Use suffixes > 4 chars so max_dist=2, allowing distance-1 correction.
        known = {"sk__abcdef", "sk__defghij"}
        result = _correct_skill_ids(["sk__abcdef", "sk__abcdeg"], known)
        assert result[0] == "sk__abcdef"  # known, kept
        assert result[1] == "sk__abcdef"  # corrected typo (dist 1, within max_dist=2)

    def test_empty_input_list(self):
        assert _correct_skill_ids([], {"sk__abc"}) == []

    def test_suffix_beyond_max_dist(self):
        """Suffix too far from any candidate is kept as-is."""
        known = {"sk__abc"}  # suffix "abc", len=3, max_dist=1
        # "sk__xyz" is distance 3 from "sk__abc", beyond max_dist=1
        result = _correct_skill_ids(["sk__xyz"], known)
        assert result == ["sk__xyz"]

    def test_short_suffix_exact_max_dist_accepted(self):
        """Regression: candidates at exactly max_dist distance must be accepted.

        Before the fix, best_dist was initialized to max_dist, so the
        comparison d < best_dist excluded candidates at d == max_dist.
        With max_dist=1, a typo at distance 1 was never corrected.
        """
        known = {"sk__ab"}  # suffix len=2, max_dist=1
        # "sk__ac" is distance 1 from "sk__ab" — exactly at max_dist
        result = _correct_skill_ids(["sk__ac"], known)
        assert result == ["sk__ab"]

    def test_medium_suffix_exact_max_dist_accepted(self):
        """Regression: max_dist=2 should accept distance-2 corrections."""
        known = {"sk__abcde"}  # suffix len=5, max_dist=2
        # "sk__abcdf" is distance 1 — should always work
        result = _correct_skill_ids(["sk__abcdf"], known)
        assert result == ["sk__abcde"]


# ---------------------------------------------------------------------------
# analyze_execution
# ---------------------------------------------------------------------------


class TestAnalyzeExecution:
    def test_returns_analysis_result_with_task_id(self):
        skill = SkillRecord(id="sk-1", name="skill-1")
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx()
        result = analyzer.analyze_execution(ctx)
        assert isinstance(result, AnalysisResult)
        assert result.task_id == "task-1"
        assert result.analysis_id == "analysis-1"

    def test_records_analysis_in_store(self):
        skill = SkillRecord(id="sk-1", name="skill-1")
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx()
        analyzer.analyze_execution(ctx)
        store.record_analysis.assert_called_once()

    def test_judgments_for_applied_skill(self):
        skill = SkillRecord(id="sk-1", name="skill-1")
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(task_completed=True)
        result = analyzer.analyze_execution(ctx)
        assert len(result.judgments) == 1
        j = result.judgments[0]
        assert j["skill_id"] == "sk-1"
        assert j["applied"] is True
        assert j["completed"] is True

    def test_no_judgments_for_unknown_skill(self):
        store = _make_store([])  # no active skills
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(skill_ids_used=["ghost"])
        result = analyzer.analyze_execution(ctx)
        assert result.judgments == []

    def test_analysis_text_contains_task_info(self):
        skill = SkillRecord(id="sk-1", name="skill-1")
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(task_description="Do the thing", execution_error="oops")
        result = analyzer.analyze_execution(ctx)
        assert "task-1" in result.analysis_text
        assert "Do the thing" in result.analysis_text
        assert "oops" in result.analysis_text


# ---------------------------------------------------------------------------
# _generate_suggestions -- evolution rules
# ---------------------------------------------------------------------------


class TestGenerateSuggestions:
    def test_captured_suggestion_when_task_ok_no_skills(self):
        store = _make_store([])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(skill_ids_used=[], skills_applied=[], task_completed=True)
        result = analyzer.analyze_execution(ctx)
        assert any(s.evolution_type == EvolutionType.CAPTURED for s in result.suggestions)

    def test_no_captured_when_task_not_completed(self):
        """CAPTURED only fires when task succeeded with no skills."""
        store = _make_store([])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(skill_ids_used=[], skills_applied=[], task_completed=False)
        result = analyzer.analyze_execution(ctx)
        assert not any(s.evolution_type == EvolutionType.CAPTURED for s in result.suggestions)

    def test_no_suggestions_for_healthy_skill(self):
        skill = SkillRecord(
            id="sk-1",
            name="s1",
            total_selections=10,
            total_applied=9,
            total_completions=8,
            total_fallbacks=1,
        )
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx()
        result = analyzer.analyze_execution(ctx)
        assert result.suggestions == []

    def test_zero_selections_skill_skipped(self):
        """Skills with total_selections=0 should not produce suggestions."""
        skill = SkillRecord(id="sk-1", name="s1")
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx()
        result = analyzer.analyze_execution(ctx)
        assert result.suggestions == []

    def test_all_skills_zero_selections_no_suggestions(self):
        """When all skills have zero selections, no FIX/DERIVED suggestions."""
        skills = [
            SkillRecord(id="sk-1", name="s1"),
            SkillRecord(id="sk-2", name="s2"),
        ]
        store = _make_store(skills)
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(
            skill_ids_used=["sk-1", "sk-2"],
            skills_applied=["sk-1", "sk-2"],
        )
        result = analyzer.analyze_execution(ctx)
        # Only FIX/DERIVED should be absent; CAPTURED could appear if task completed + no skills
        fix_or_derived = [
            s
            for s in result.suggestions
            if s.evolution_type in (EvolutionType.FIX, EvolutionType.DERIVED)
        ]
        assert fix_or_derived == []

    def test_fallback_skill_triggers_fix(self):
        """High fallback rate triggers FIX suggestion."""
        skill = SkillRecord(
            id="sk-1",
            name="s1",
            total_selections=10,
            total_applied=6,
            total_completions=0,
            total_fallbacks=6,
        )
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(skills_applied=["sk-1"])
        result = analyzer.analyze_execution(ctx)
        assert any(s.evolution_type == EvolutionType.FIX for s in result.suggestions)

    def test_dedup_keeps_highest_confidence_across_skills(self):
        """When the same (type, skill_id) appears from different thresholds,
        the dedup keeps the highest confidence."""
        skill = SkillRecord(
            id="sk-1",
            name="s1",
            # Triggers both Rule 1 (fallback > 0.4) and Rule 2 (applied > 0.4, completion < 0.35)
            total_selections=10,
            total_applied=7,
            total_completions=1,
            total_fallbacks=6,
        )
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx()
        result = analyzer.analyze_execution(ctx)
        fix_suggestions = [s for s in result.suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) == 1  # deduped to one


# ---------------------------------------------------------------------------
# store property
# ---------------------------------------------------------------------------


class TestStoreProperty:
    def test_store_returns_underlying_store(self):
        store = _make_store()
        analyzer = ExecutionAnalyzer(store)
        assert analyzer.store is store


# ---------------------------------------------------------------------------
# _build_analysis_text -- edge cases
# ---------------------------------------------------------------------------


class TestBuildAnalysisText:
    def test_minimal_context_no_optional_fields(self):
        """Analysis text with no description, no error, no judgments."""
        store = _make_store([])
        analyzer = ExecutionAnalyzer(store)
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-1",
            task_completed=False,
        )
        result = analyzer.analyze_execution(ctx)
        assert "task-1" in result.analysis_text
        assert "Completed: False" in result.analysis_text

    def test_no_judgments_no_suggestions(self):
        """When context references unknown skills, text has no judgment/suggestion sections."""
        store = _make_store([])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(skill_ids_used=["ghost"], task_completed=True)
        result = analyzer.analyze_execution(ctx)
        assert "Skill Judgments" not in result.analysis_text

    def test_fell_back_skill_in_judgments(self):
        """Skills in fell_back set show fell_back=True."""
        skill = SkillRecord(id="sk-1", name="s1")
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx(
            skill_ids_used=["sk-1"],
            skills_applied=[],
        )
        result = analyzer.analyze_execution(ctx)
        assert len(result.judgments) == 1
        assert result.judgments[0]["fell_back"] is False
        assert result.judgments[0]["applied"] is False
