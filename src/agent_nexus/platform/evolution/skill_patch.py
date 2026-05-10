"""SkillPatcher -- LLM-driven skill content evolution.

Generates improved SKILL.md content by prompting LLM with the current
skill content and diagnosis/suggestions, then validates the result.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import TYPE_CHECKING

from pydantic import Field

from agent_nexus.models._common import FrozenModel
from agent_nexus.models.evolution import EvolutionType, SkillRecord

if TYPE_CHECKING:
    from agent_nexus.platform.agency.llm_client import LLMClient

logger = logging.getLogger(__name__)


class ValidationResult(FrozenModel):
    """Result of validating a patched skill content."""

    syntax_valid: bool = True
    security_pass: bool = True
    test_pass: bool | None = None
    regression_risk: float = Field(default=0.0, ge=0.0, le=1.0)


class PatchResult(FrozenModel):
    """Result of an LLM-driven patch operation."""

    original_content: str = ""
    patched_content: str = ""
    diff: str = ""
    patch_type: EvolutionType = EvolutionType.FIX
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    validation: ValidationResult = Field(default_factory=ValidationResult)


class SkillPatcher:
    """LLM-driven skill content modification.

    Usage::

        patcher = SkillPatcher(llm_client)
        result = patcher.generate_fix(skill, diagnosis)
        if result.validation.syntax_valid and result.confidence > 0.6:
            # apply the patch
    """

    def __init__(self, llm_client: LLMClient, *, max_tokens: int = 4096) -> None:
        self._llm = llm_client
        self._max_tokens = max_tokens

    def generate_fix(self, skill: SkillRecord, diagnosis: str) -> PatchResult:
        """Generate a FIX patch for a broken/outdated skill."""
        original = self._get_skill_content(skill)
        prompt = self._build_fix_prompt(skill, original, diagnosis)
        patched = self._call_llm(prompt)
        return self._build_result(original, patched, EvolutionType.FIX)

    def generate_derived(self, skill: SkillRecord, insights: list[str]) -> PatchResult:
        """Generate a DERIVED patch for skill enhancement."""
        original = self._get_skill_content(skill)
        prompt = self._build_derived_prompt(skill, original, insights)
        patched = self._call_llm(prompt)
        return self._build_result(original, patched, EvolutionType.DERIVED)

    def validate_patch(self, original: str, patched: str) -> ValidationResult:
        """Validate patched skill content.

        Checks:
        1. Syntax: non-empty, has markdown sections
        2. Security: no dangerous patterns (exec, eval, subprocess)
        3. Regression risk: similarity ratio (too different = high risk)
        """
        if not patched or not patched.strip():
            return ValidationResult(
                syntax_valid=False,
                security_pass=True,
                regression_risk=1.0,
            )

        syntax_valid = self._check_syntax(patched)
        security_pass = self._check_security(patched)
        regression_risk = self._compute_regression_risk(original, patched)

        return ValidationResult(
            syntax_valid=syntax_valid,
            security_pass=security_pass,
            regression_risk=regression_risk,
        )

    # --- Internal helpers ---

    @staticmethod
    def _get_skill_content(skill: SkillRecord) -> str:
        """Extract skill content from lineage snapshot or return empty."""
        if skill.lineage.content_snapshot:
            return skill.lineage.content_snapshot.get("content", "")
        return ""

    def _build_fix_prompt(
        self,
        skill: SkillRecord,
        original: str,
        diagnosis: str,
    ) -> str:
        return (
            f"You are a skill content repair specialist.\n\n"
            f"Skill: {skill.name} (v{skill.version})\n"
            f"Diagnosis: {diagnosis}\n\n"
            f"Current content:\n---\n{original}\n---\n\n"
            f"Generate the COMPLETE fixed skill content. "
            f"Preserve the original structure and format. "
            f"Only modify the parts that address the diagnosis. "
            f"Output the full corrected content without any explanation."
        )

    def _build_derived_prompt(
        self,
        skill: SkillRecord,
        original: str,
        insights: list[str],
    ) -> str:
        insights_text = "\n".join(f"- {i}" for i in insights)
        return (
            f"You are a skill enhancement specialist.\n\n"
            f"Base skill: {skill.name} (v{skill.version})\n"
            f"Improvement insights:\n{insights_text}\n\n"
            f"Current content:\n---\n{original}\n---\n\n"
            f"Generate an ENHANCED version of the skill content. "
            f"Incorporate the improvement insights while maintaining "
            f"the original structure. Output the full enhanced content."
        )

    def _call_llm(self, prompt: str) -> str:
        response = self._llm.call(
            system_prompt="You are an expert skill content engineer.",
            user_message=prompt,
            max_tokens=self._max_tokens,
            temperature=0.3,
        )
        return response.text

    def _build_result(
        self,
        original: str,
        patched: str,
        patch_type: EvolutionType,
    ) -> PatchResult:
        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                patched.splitlines(),
                fromfile="original",
                tofile="patched",
                lineterm="",
            )
        )

        # Confidence based on diff size relative to original
        if original:
            diff_lines = sum(
                1
                for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            total_lines = max(len(original.splitlines()), 1)
            change_ratio = diff_lines / total_lines
            confidence = max(0.1, min(1.0, 1.0 - change_ratio * 0.5))
        else:
            confidence = 0.5

        validation = self.validate_patch(original, patched)

        return PatchResult(
            original_content=original,
            patched_content=patched,
            diff=diff,
            patch_type=patch_type,
            confidence=confidence,
            validation=validation,
        )

    @staticmethod
    def _check_syntax(content: str) -> bool:
        has_section = bool(re.search(r"^#{1,3}\s+\S+", content, re.MULTILINE))
        return has_section

    @staticmethod
    def _check_security(content: str) -> bool:
        """Best-effort content safety heuristic for skill instruction text.

        Only scans within fenced code blocks (```) to avoid false positives
        on natural language prose like "Execute the plan".
        """
        code_sections: list[str] = []
        in_block = False
        current: list[str] = []
        for line in content.splitlines():
            if line.strip().startswith("```"):
                if in_block:
                    code_sections.append("\n".join(current))
                    current = []
                in_block = not in_block
                continue
            if in_block:
                current.append(line)
        if current:
            code_sections.append("\n".join(current))

        if not code_sections:
            return True

        code_text = "\n".join(code_sections)
        dangerous = (
            r"\bexec\s*\(",
            r"\beval\s*\(",
            r"\bsubprocess\s*\.",
            r"\bos\.system\s*\(",
            r"\b__import__\s*\(",
            r"\bopen\s*\(.+[\'\"]w",
        )
        return not any(re.search(p, code_text) for p in dangerous)

    @staticmethod
    def _compute_regression_risk(original: str, patched: str) -> float:
        if not original:
            return 0.5
        ratio = difflib.SequenceMatcher(None, original, patched).ratio()
        return round(max(0.0, min(1.0, 1.0 - ratio)), 3)
