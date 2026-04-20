"""Evolution health threshold constants (from docs/04 Section 6).

Single source of truth for all evolution health thresholds.
Used by health.py, analyzer.py, and evolver.py to avoid duplication.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fallback rate > 0.4 triggers FIX
_FALLBACK_THRESHOLD = 0.4

# Applied rate > 0.4 AND completion rate < 0.35 triggers FIX
_HIGH_APPLIED_FOR_FIX = 0.4
_LOW_COMPLETION_THRESHOLD = 0.35

# Effective rate < 0.55 AND applied rate > 0.25 triggers DERIVED
_MODERATE_EFFECTIVE_THRESHOLD = 0.55
_MIN_APPLIED_FOR_DERIVED = 0.25


@dataclass(frozen=True)
class SkillRates:
    """Computed quality rates for a single skill."""

    fallback_rate: float
    applied_rate: float
    completion_rate: float
    effective_rate: float


# Rule identifiers returned by evaluate_skill_health.
RULE_HIGH_FALLBACK = 1        # fallback_rate > 0.4
RULE_LOW_COMPLETION = 2       # applied > 0.4 AND completion < 0.35
RULE_MODERATE_EFFECTIVE = 3   # effective < 0.55 AND applied > 0.25


@dataclass(frozen=True)
class HealthEvaluation:
    """Result of evaluating a skill's health against the 3-rule decision tree.

    ``action`` is ``"FIX"``, ``"DERIVED"``, or ``""`` (no action).
    ``rules`` is a tuple of all matching rule IDs (both FIX rules can
    match simultaneously — the caller picks the highest-confidence one).
    """

    action: str
    rules: tuple[int, ...]


def evaluate_skill_health(rates: SkillRates) -> HealthEvaluation:
    """Evaluate a skill's health and return the recommended evolution type.

    Implements the 3-rule decision tree from docs/04 Section 6:
    1. fallback_rate > 0.4 -> FIX
    2. applied_rate > 0.4 AND completion_rate < 0.35 -> FIX
    3. effective_rate < 0.55 AND applied_rate > 0.25 -> DERIVED

    All FIX rules are checked (not short-circuited) so the caller can
    compare confidences when multiple rules match.  DERIVED is only
    considered when no FIX rules matched.

    Returns a :class:`HealthEvaluation` with ``action`` and ``rules``.
    """
    fix_rules: list[int] = []

    # Rule 1: High fallback rate
    if rates.fallback_rate > _FALLBACK_THRESHOLD:
        fix_rules.append(RULE_HIGH_FALLBACK)

    # Rule 2: Applied often but rarely completes
    if (
        rates.applied_rate > _HIGH_APPLIED_FOR_FIX
        and rates.completion_rate < _LOW_COMPLETION_THRESHOLD
    ):
        fix_rules.append(RULE_LOW_COMPLETION)

    if fix_rules:
        return HealthEvaluation(action="FIX", rules=tuple(fix_rules))

    # Rule 3: Moderate effectiveness (lower priority than FIX)
    if (
        rates.effective_rate < _MODERATE_EFFECTIVE_THRESHOLD
        and rates.applied_rate > _MIN_APPLIED_FOR_DERIVED
    ):
        return HealthEvaluation(action="DERIVED", rules=(RULE_MODERATE_EFFECTIVE,))

    return HealthEvaluation(action="", rules=())
