"""Unit tests for agent_nexus.platform.evolution.analyzer module."""

from unittest.mock import MagicMock

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionType,
    SkillRecord,
)
from agent_nexus.platform.evolution.analyzer import (
    AnalysisResult,
    EvolutionSuggestion,
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
    return EvolutionContext(**defaults)


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
        assert any(
            s.evolution_type == EvolutionType.CAPTURED
            for s in result.suggestions
        )

    def test_no_suggestions_for_healthy_skill(self):
        skill = SkillRecord(
            id="sk-1", name="s1",
            total_selections=10, total_applied=9,
            total_completions=8, total_fallbacks=1,
        )
        store = _make_store([skill])
        analyzer = ExecutionAnalyzer(store)
        ctx = _make_ctx()
        result = analyzer.analyze_execution(ctx)
        assert result.suggestions == []


# ---------------------------------------------------------------------------
# store property
# ---------------------------------------------------------------------------

class TestStoreProperty:
    def test_store_returns_underlying_store(self):
        store = _make_store()
        analyzer = ExecutionAnalyzer(store)
        assert analyzer.store is store
