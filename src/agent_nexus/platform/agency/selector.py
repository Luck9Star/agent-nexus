"""Deterministic specialist selector based on capability matching and ranking.

Supports two selection strategies:
1. **Single-agent**: If one agent has ALL required capabilities, select it directly.
2. **Multi-agent (greedy set-cover)**: When no single agent covers all required
   capabilities, find the minimum combination of agents whose capabilities
   collectively cover the full required set.  This prevents compound tasks
   (e.g. "review architecture AND write documentation") from silently failing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .registry import ExpertRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission hierarchy: higher index = more permissive
# ---------------------------------------------------------------------------
_PERMISSION_LEVELS = ["plan", "default", "full_auto"]


def _permission_level(mode: str) -> int:
    """Return numeric level for a permission mode."""
    try:
        return _PERMISSION_LEVELS.index(mode)
    except ValueError:
        return 0


def _capability_overlap(agent_caps: set[str], required_caps: set[str]) -> float:
    """Fraction of required capabilities that the agent has (0.0 .. 1.0).

    Returns 0.0 when required_caps is empty — no requirements means no agent
    should be rewarded on the capability axis.
    """
    if not required_caps:
        return 0.0
    return len(agent_caps & required_caps) / len(required_caps)


def _optional_overlap(agent_caps: set[str], optional_caps: set[str]) -> float:
    """Fraction of optional capabilities that the agent has (0.0 .. 1.0).

    Returns 0.0 when optional_caps is empty — consistent with _capability_overlap:
    an empty requirement set contributes zero rather than inflating all scores.
    """
    if not optional_caps:
        return 0.0
    return len(agent_caps & optional_caps) / len(optional_caps)


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two sets (0.0 .. 1.0)."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


@dataclass
class SelectionRequest:
    """Input to the specialist selector."""

    task_type: str
    required_capabilities: list[str]
    optional_capabilities: list[str]
    max_agents: int
    permissions: str  # "plan", "full_auto", "default"


@dataclass
class SelectionResult:
    """Output for a single selected agent."""

    agent_id: str
    score: float
    reasons: list[str] = field(default_factory=list)


class SpecialistSelector:
    """Deterministic specialist selector based on capability matching and ranking.

    Scoring weights:
        - Required capability overlap:  0.40
        - Optional capability overlap:  0.15
        - Task type match:              0.25
        - Permission fit:               0.10
        - Diversity adjustment:         0.10
    """

    WEIGHT_REQUIRED: float = 0.40
    WEIGHT_OPTIONAL: float = 0.15
    WEIGHT_TASK_TYPE: float = 0.25
    WEIGHT_PERMISSION: float = 0.10
    WEIGHT_DIVERSITY: float = 0.10
    DIVERSITY_THRESHOLD: float = 0.80

    def __init__(self, registry: ExpertRegistry) -> None:
        self.registry = registry

    def select(self, request: SelectionRequest) -> list[SelectionResult]:
        """Select the best agents for a given request.

        Steps:
        1. Get all experts from registry.
        2. Filter by permission fit.
        3. Try single-agent selection (agent has ALL required caps).
        4. If no single agent covers all, use greedy set-cover to compose
           a multi-agent team that collectively covers all required capabilities.
        5. Score and rank selected agents.
        6. Apply diversity dedup.
        7. Return up to max_agents results.
        """
        required_set = set(request.required_capabilities)
        optional_set = set(request.optional_capabilities)
        request_perm_level = _permission_level(request.permissions)

        # 1. Get all experts and filter by permission
        all_ids = self.registry.list_all()
        eligible: list[dict[str, Any]] = []
        for pid in all_ids:
            profile = self.registry.get(pid)
            if profile is None:
                continue
            profile.setdefault("id", pid)
            agent_perm = profile.get("permissions", {}).get("mode", "plan")
            if _permission_level(agent_perm) > request_perm_level:
                continue
            eligible.append(profile)

        if not eligible:
            return []

        # 2. Fast path: find agents that individually cover ALL required caps
        full_match = [
            p
            for p in eligible
            if not required_set or required_set.issubset(set(p.get("capabilities", [])))
        ]

        if full_match:
            return self._score_and_rank(full_match, required_set, optional_set, request)

        # 3. Slow path: no single agent covers all required caps.
        #    Use greedy set-cover to find a multi-agent team.
        if not required_set:
            return self._score_and_rank(eligible, required_set, optional_set, request)

        team = self._greedy_set_cover(eligible, required_set)
        if not team:
            logger.warning(
                "No agent combination covers required capabilities: %s",
                sorted(required_set),
            )
            return []

        return self._score_and_rank(
            team, required_set, optional_set, request, protect_coverage=True
        )

    @staticmethod
    def _is_better_candidate(
        profile: dict[str, Any],
        coverage: set[str],
        best_profile: dict[str, Any] | None,
        best_coverage: set[str],
        best_score: float,
    ) -> bool:
        """Determine if *profile* is a strictly better greedy choice than the current best."""
        coverage_len = len(coverage)
        if coverage_len > len(best_coverage):
            return True
        total_caps = len(set(profile.get("capabilities", [])))
        if coverage_len == len(best_coverage) and total_caps > best_score:
            return True
        return bool(
            coverage_len == len(best_coverage)
            and total_caps == best_score
            and best_profile is not None
            and profile["id"] < best_profile["id"]
        )

    def _greedy_set_cover(
        self,
        candidates: list[dict[str, Any]],
        required: set[str],
    ) -> list[dict[str, Any]]:
        """Greedy set-cover: pick agents that collectively cover all required caps.

        At each step, pick the agent that covers the most uncovered capabilities.
        Stop when all required capabilities are covered or we run out of candidates.
        """
        remaining = set(required)
        selected: list[dict[str, Any]] = []
        used_ids: set[str] = set()

        while remaining:
            best_profile: dict[str, Any] | None = None
            best_coverage: set[str] = set()
            best_score = -1.0

            for profile in candidates:
                pid = profile["id"]
                if pid in used_ids:
                    continue
                coverage = set(profile.get("capabilities", [])) & remaining
                if self._is_better_candidate(
                    profile,
                    coverage,
                    best_profile,
                    best_coverage,
                    best_score,
                ):
                    best_profile = profile
                    best_coverage = coverage
                    best_score = len(set(profile.get("capabilities", [])))

            if best_profile is None or not best_coverage:
                break  # No agent can cover remaining caps

            selected.append(best_profile)
            used_ids.add(best_profile["id"])
            remaining -= best_coverage

        if remaining:
            logger.warning("Greedy set-cover could not cover capabilities: %s", remaining)
        return selected

    def _score_candidate(
        self,
        profile: dict[str, Any],
        required_set: set[str],
        optional_set: set[str],
        request: SelectionRequest,
        request_perm_level: int,
    ) -> tuple[float, dict[str, Any], list[str]]:
        """Score a single candidate profile and return (raw_score, profile, reasons)."""
        agent_caps = set(profile.get("capabilities", []))
        reasons: list[str] = []

        req_overlap = _capability_overlap(agent_caps, required_set)
        if req_overlap > 0:
            matched = agent_caps & required_set
            reasons.append(f"Required capability match: {sorted(matched)}")

        opt_overlap = _optional_overlap(agent_caps, optional_set)

        task_types = set(profile.get("routing", {}).get("task_types", []))
        task_match = 1.0 if request.task_type in task_types else 0.0
        if task_match > 0:
            reasons.append(f"Task type match: {request.task_type}")

        agent_perm = profile.get("permissions", {}).get("mode", "plan")
        agent_perm_level = _permission_level(agent_perm)
        perm_fit = (agent_perm_level + 1) / (request_perm_level + 1)
        if perm_fit >= 1.0:
            reasons.append(f"Permission fit: {agent_perm} (exact match)")
        else:
            reasons.append(
                f"Permission fit: {agent_perm} (less permissive than request {request.permissions})"  # noqa: E501
            )

        raw_score = (
            self.WEIGHT_REQUIRED * req_overlap
            + self.WEIGHT_OPTIONAL * opt_overlap
            + self.WEIGHT_TASK_TYPE * task_match
            + self.WEIGHT_PERMISSION * perm_fit
        )
        return (raw_score, profile, reasons)

    def _apply_diversity_dedup(
        self,
        scored: list[tuple[float, dict[str, Any], list[str]]],
    ) -> list[tuple[float, dict[str, Any], list[str]]]:
        """Filter out candidates with >DIVERSITY_THRESHOLD Jaccard similarity."""
        selected: list[tuple[float, dict[str, Any], list[str]]] = []
        for raw_score, profile, reasons in scored:
            agent_caps = set(profile.get("capabilities", []))
            is_duplicate = any(
                _jaccard_similarity(agent_caps, set(sel.get("capabilities", [])))
                > self.DIVERSITY_THRESHOLD
                for _, sel, _ in selected
            )
            if not is_duplicate:
                selected.append((raw_score, profile, reasons))
        return selected

    def _normalize_and_build_results(
        self,
        selected: list[tuple[float, dict[str, Any], list[str]]],
    ) -> list[SelectionResult]:
        """Normalize raw scores and build SelectionResult objects."""
        max_possible = (
            self.WEIGHT_REQUIRED
            + self.WEIGHT_OPTIONAL
            + self.WEIGHT_TASK_TYPE
            + self.WEIGHT_PERMISSION
        )
        results: list[SelectionResult] = []
        for raw_score, profile, reasons in selected:
            normalized = raw_score / max_possible if max_possible > 0 else 0.0
            final_score = normalized * (1.0 - self.WEIGHT_DIVERSITY) + self.WEIGHT_DIVERSITY
            final_score = max(final_score, 0.01)
            results.append(
                SelectionResult(
                    agent_id=profile["id"],
                    score=round(final_score, 4),
                    reasons=reasons,
                )
            )
        return results

    def _score_and_rank(
        self,
        candidates: list[dict[str, Any]],
        required_set: set[str],
        optional_set: set[str],
        request: SelectionRequest,
        *,
        protect_coverage: bool = False,
    ) -> list[SelectionResult]:
        """Score, rank, deduplicate, and return top max_agents.

        Args:
            protect_coverage: When True (set-cover path), skip diversity
                dedup and do not truncate below the candidate count, because
                every member was selected for unique capability coverage.
        """
        request_perm_level = _permission_level(request.permissions)

        scored = [
            self._score_candidate(p, required_set, optional_set, request, request_perm_level)
            for p in candidates
        ]
        scored.sort(key=lambda t: (-t[0], t[1]["id"]))

        if protect_coverage:
            selected = scored[: request.max_agents] if request.max_agents else scored
        else:
            selected = self._apply_diversity_dedup(scored)[: request.max_agents]

        return self._normalize_and_build_results(selected)
