"""Tests for SkillPatcher — LLM-driven skill content evolution."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_nexus.models.evolution import (
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.skill_patch import (
    PatchResult,
    SkillPatcher,
    ValidationResult,
)


def _make_skill(
    content: str = "",
    name: str = "test-skill",
    origin: SkillOrigin = SkillOrigin.IMPORTED,
) -> SkillRecord:
    """Create a SkillRecord with optional content snapshot."""
    snapshot = {"content": content} if content else None
    return SkillRecord(
        id=f"{name}__v1",
        name=name,
        lineage=SkillLineage(origin=origin, content_snapshot=snapshot),
    )


def _mock_llm(response_text: str) -> MagicMock:
    """Create a mock LLMClient that returns the given text."""
    mock = MagicMock()
    mock.call.return_value = MagicMock(text=response_text)
    return mock


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_frozen(self) -> None:
        v = ValidationResult()
        try:
            v.syntax_valid = False  # type: ignore[misc]
            assert False, "Should be frozen"
        except Exception:
            pass


# ---------------------------------------------------------------------------
# PatchResult
# ---------------------------------------------------------------------------


class TestPatchResult:
    def test_defaults(self) -> None:
        p = PatchResult()
        assert p.original_content == ""
        assert p.patched_content == ""
        assert p.patch_type == EvolutionType.FIX
        assert p.confidence == 0.0

    def test_with_values(self) -> None:
        p = PatchResult(
            original_content="old",
            patched_content="new",
            diff="--- old\n+++ new",
            patch_type=EvolutionType.DERIVED,
            confidence=0.8,
        )
        assert p.patch_type == EvolutionType.DERIVED
        assert p.confidence == 0.8


# ---------------------------------------------------------------------------
# SkillPatcher — validate_patch
# ---------------------------------------------------------------------------


class TestValidatePatch:
    def setup_method(self) -> None:
        self.patcher = SkillPatcher(_mock_llm(""))

    def test_empty_patched_fails_syntax(self) -> None:
        result = self.patcher.validate_patch("original", "")
        assert result.syntax_valid is False
        assert result.regression_risk == 1.0

    def test_whitespace_only_fails_syntax(self) -> None:
        result = self.patcher.validate_patch("original", "   \n  ")
        assert result.syntax_valid is False

    def test_valid_markdown_passes(self) -> None:
        content = "# My Skill\n\nSome instructions here."
        result = self.patcher.validate_patch("", content)
        assert result.syntax_valid is True

    def test_no_sections_fails_syntax(self) -> None:
        content = "Just plain text without any headers."
        result = self.patcher.validate_patch("", content)
        assert result.syntax_valid is False

    def test_identical_content_low_risk(self) -> None:
        content = "# Skill\n\nDo stuff."
        result = self.patcher.validate_patch(content, content)
        assert result.regression_risk == 0.0

    def test_very_different_high_risk(self) -> None:
        result = self.patcher.validate_patch("aaaa", "xxxx")
        assert result.regression_risk > 0.5

    def test_dangerous_code_in_code_block_fails_security(self) -> None:
        content = "# Skill\n\n```python\nexec(code)\n```"
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is False

    def test_safe_content_passes_security(self) -> None:
        content = "# Skill\n\nRead the file and summarize."
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is True

    def test_dangerous_prose_passes_security(self) -> None:
        """Prose like 'Execute the plan' should NOT trigger security check."""
        content = "# Skill\n\nExecute the plan and evaluate the results."
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is True

    def test_eval_in_code_block_fails_security(self) -> None:
        content = "# Skill\n\n```python\neval(expr)\n```"
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is False

    def test_subprocess_in_code_block_fails_security(self) -> None:
        content = "# Skill\n\n```python\nsubprocess.run(cmd)\n```"
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is False

    def test_no_code_blocks_passes_security(self) -> None:
        content = "# Skill\n\nNo code blocks here."
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is True

    def test_unclosed_code_block_detects_dangerous(self) -> None:
        """Unclosed code block (odd number of ```) still scanned for safety."""
        content = "# Skill\n\n```python\nsubprocess.run(cmd)\n"
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is False

    def test_open_read_not_flagged(self) -> None:
        """open() with read mode or path containing 'w' should not trigger."""
        content = "# Skill\n\n```python\nf = open(os.path.join('data', 'write_log'), 'r')\n```"
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is True

    def test_open_write_flagged(self) -> None:
        """open() with write mode should be flagged."""
        content = "# Skill\n\n```python\nf = open('output.txt', 'w')\n```"
        result = self.patcher.validate_patch("", content)
        assert result.security_pass is False


# ---------------------------------------------------------------------------
# SkillPatcher — generate_fix
# ---------------------------------------------------------------------------


class TestGenerateFix:
    def test_generates_fix_patch(self) -> None:
        llm = _mock_llm("# Fixed Skill\n\nBetter instructions.")
        patcher = SkillPatcher(llm)
        skill = _make_skill("# Old Skill\n\nOld instructions.")

        result = patcher.generate_fix(skill, "Fallback rate too high")

        assert result.patch_type == EvolutionType.FIX
        assert result.patched_content == "# Fixed Skill\n\nBetter instructions."
        assert result.original_content == "# Old Skill\n\nOld instructions."
        assert result.diff
        assert result.validation.syntax_valid is True
        assert result.validation.security_pass is True
        assert 0.0 <= result.confidence <= 1.0
        llm.call.assert_called_once()

    def test_fix_with_empty_original(self) -> None:
        llm = _mock_llm("# New Content\n\nFresh skill.")
        patcher = SkillPatcher(llm)
        skill = _make_skill("")

        result = patcher.generate_fix(skill, "Skill is empty")

        assert result.original_content == ""
        assert result.patched_content == "# New Content\n\nFresh skill."
        assert result.confidence == 0.5

    def test_fix_passes_diagnosis_to_llm(self) -> None:
        llm = _mock_llm("# Fixed\n\nContent.")
        patcher = SkillPatcher(llm)
        skill = _make_skill("# Old\n\nContent.")

        patcher.generate_fix(skill, "specific diagnosis text")

        user_msg = llm.call.call_args.kwargs.get("user_message", "")
        assert "specific diagnosis text" in user_msg


# ---------------------------------------------------------------------------
# SkillPatcher — generate_derived
# ---------------------------------------------------------------------------


class TestGenerateDerived:
    def test_generates_derived_patch(self) -> None:
        llm = _mock_llm("# Enhanced Skill\n\nBetter approach.")
        patcher = SkillPatcher(llm)
        skill = _make_skill("# Base Skill\n\nBasic approach.")

        result = patcher.generate_derived(skill, ["Add error handling", "Support edge cases"])

        assert result.patch_type == EvolutionType.DERIVED
        assert result.patched_content == "# Enhanced Skill\n\nBetter approach."
        assert result.validation.syntax_valid is True
        llm.call.assert_called_once()

    def test_derived_passes_insights_to_llm(self) -> None:
        llm = _mock_llm("# Enhanced\n\nContent.")
        patcher = SkillPatcher(llm)
        skill = _make_skill("# Base\n\nContent.")

        patcher.generate_derived(skill, ["insight A", "insight B"])

        user_msg = llm.call.call_args.kwargs.get("user_message", "")
        assert "insight A" in user_msg
        assert "insight B" in user_msg


# ---------------------------------------------------------------------------
# SkillPatcher — _get_skill_content
# ---------------------------------------------------------------------------


class TestGetSkillContent:
    def test_extracts_from_snapshot(self) -> None:
        skill = _make_skill("my content here")
        content = SkillPatcher._get_skill_content(skill)
        assert content == "my content here"

    def test_returns_empty_for_no_snapshot(self) -> None:
        skill = SkillRecord(id="x", name="y")
        content = SkillPatcher._get_skill_content(skill)
        assert content == ""


# ---------------------------------------------------------------------------
# SkillPatcher — confidence scoring
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_small_change_high_confidence(self) -> None:
        llm = _mock_llm("# Skill\n\nFixed typo.")
        patcher = SkillPatcher(llm)
        skill = _make_skill("# Skill\n\nFixed typ0.")

        result = patcher.generate_fix(skill, "Typo in instructions")
        assert result.confidence > 0.7

    def test_complete_rewrite_moderate_confidence(self) -> None:
        original = "# Skill\n\nLine 1\nLine 2\nLine 3\nLine 4\nLine 5"
        new = "# Skill\n\nDifferent content here\nNew line\nAnother\nMore\nExtra"
        llm = _mock_llm(new)
        patcher = SkillPatcher(llm)
        skill = _make_skill(original)

        result = patcher.generate_fix(skill, "Rewrite needed")
        assert result.confidence < 0.9


# ---------------------------------------------------------------------------
# TaskComposer evolution hook integration
# ---------------------------------------------------------------------------


class TestEvolutionHook:
    def test_evolution_callback_triggered_on_qa_pass(self) -> None:
        from agent_nexus.platform.agency.registry import ExpertRegistry
        from agent_nexus.platform.agency.task_composer import (
            TaskComposer,
            TaskComposerInput,
            TaskComposerResult,
        )

        callback_called = []

        def on_evolution(result: TaskComposerResult) -> None:
            callback_called.append(result)

        registry = ExpertRegistry()
        composer = TaskComposer(registry)
        inp = TaskComposerInput(task="test task")

        result = composer.run(
            inp,
            evolution_callback=on_evolution,
        )
        assert hasattr(result, "evolution_triggered")

    def test_evolution_not_triggered_when_callback_none(self) -> None:
        from agent_nexus.platform.agency.registry import ExpertRegistry
        from agent_nexus.platform.agency.task_composer import (
            TaskComposer,
            TaskComposerInput,
        )

        registry = ExpertRegistry()
        composer = TaskComposer(registry)
        inp = TaskComposerInput(task="test task")

        result = composer.run(inp)
        assert result.evolution_triggered is False

    def test_evolution_callback_exception_does_not_crash(self) -> None:
        from agent_nexus.platform.agency.registry import ExpertRegistry
        from agent_nexus.platform.agency.task_composer import (
            TaskComposer,
            TaskComposerInput,
        )

        def bad_callback(result: object) -> None:
            raise RuntimeError("boom")

        registry = ExpertRegistry()
        composer = TaskComposer(registry)
        inp = TaskComposerInput(task="test task")

        result = composer.run(inp, evolution_callback=bad_callback)
        assert result is not None
