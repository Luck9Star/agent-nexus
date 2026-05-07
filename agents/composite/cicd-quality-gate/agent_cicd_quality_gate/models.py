"""Data models for cicd-quality-gate Composite Agent.

Pydantic v2 frozen models for CI/CD quality gate checking.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GateCheck(BaseModel):
    """Result of a single quality gate check.

    Attributes:
        agent: The Atomic Agent that performed this check.
        passed: Whether this check passed.
        findings: List of findings from this check.
        score: Numeric score for this check (0-100).
    """

    model_config = ConfigDict(frozen=True)

    agent: str
    passed: bool = True
    findings: list[str] = Field(default_factory=list)
    score: float = 100.0


class GateResult(BaseModel):
    """Result of the full quality gate evaluation.

    Attributes:
        checks: Individual check results.
        overall_passed: Whether the gate passed (all critical checks passed).
        gate_score: Weighted average score across all checks.
        blockers: Items that must be resolved before passing.
        warnings: Items that should be addressed but don't block.
    """

    model_config = ConfigDict(frozen=True)

    checks: list[GateCheck] = Field(default_factory=list)
    overall_passed: bool = False
    gate_score: float = 0.0
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
