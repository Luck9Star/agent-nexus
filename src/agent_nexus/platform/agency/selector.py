"""Deterministic specialist selector based on capability matching and ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import ExpertRegistry

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
    """Fraction of optional capabilities that the agent has (0.0 .. 1.0)."""
    if not optional_caps:
        return 1.0
    return len(agent_caps & optional_caps) / len(optional_caps)


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two sets (0.0 .. 1.0)."""
    if not set_a and not set_b:
        return 1.0
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
        2. Filter by required_capabilities (agent must have ALL required caps).
        3. Filter by permission fit (agent permission level must not exceed request).
        4. Score each candidate on capability overlap, task type, and permission fit.
        5. Sort by score descending.
        6. Apply diversity dedup (remove agents with >80% capability overlap with
           already-selected).
        7. Take top max_agents.
        8. Return SelectionResult for each with score and reasons.
        """
        required_set = set(request.required_capabilities)
        optional_set = set(request.optional_capabilities)
        request_perm_level = _permission_level(request.permissions)

        # 1. Get all experts
        all_ids = self.registry.list_all()

        # 2 + 3. Filter by required capabilities and permission fit
        candidates: list[dict[str, Any]] = []
        for pid in all_ids:
            profile = self.registry.get(pid)
            if profile is None:
                continue

            agent_caps = set(profile.get("capabilities", []))

            # Must have at least one required capability to be considered
            if required_set and not agent_caps & required_set:
                continue

            # Permission fit: agent's mode must not exceed requested permission level
            agent_perm = profile.get("permissions", {}).get("mode", "plan")
            if _permission_level(agent_perm) > request_perm_level:
                continue

            candidates.append(profile)

        if not candidates:
            return []

        # 4. Score each candidate
        scored: list[tuple[float, dict[str, Any], list[str]]] = []
        for profile in candidates:
            agent_caps = set(profile.get("capabilities", []))
            reasons: list[str] = []

            # Required capability overlap
            req_overlap = _capability_overlap(agent_caps, required_set)
            if req_overlap > 0:
                matched = agent_caps & required_set
                reasons.append(f"Required capability match: {sorted(matched)}")

            # Optional capability overlap
            opt_overlap = _optional_overlap(agent_caps, optional_set)

            # Task type match
            task_types = set(profile.get("routing", {}).get("task_types", []))
            task_match = 1.0 if request.task_type in task_types else 0.0
            if task_match > 0:
                reasons.append(f"Task type match: {request.task_type}")

            # Permission fit
            perm_fit = 1.0  # already filtered; all remaining are fits
            reasons.append("Permission fit")

            raw_score = (
                self.WEIGHT_REQUIRED * req_overlap
                + self.WEIGHT_OPTIONAL * opt_overlap
                + self.WEIGHT_TASK_TYPE * task_match
                + self.WEIGHT_PERMISSION * perm_fit
            )

            scored.append((raw_score, profile, reasons))

        # 5. Sort by score descending, break ties by id for determinism
        scored.sort(key=lambda t: (-t[0], t[1]["id"]))

        # 6. Apply diversity dedup
        selected: list[tuple[float, dict[str, Any], list[str]]] = []
        for raw_score, profile, reasons in scored:
            agent_caps = set(profile.get("capabilities", []))

            # Check against already-selected agents
            is_duplicate = False
            for _, sel_profile, _ in selected:
                sel_caps = set(sel_profile.get("capabilities", []))
                if _jaccard_similarity(agent_caps, sel_caps) > self.DIVERSITY_THRESHOLD:
                    is_duplicate = True
                    break

            if is_duplicate:
                continue

            selected.append((raw_score, profile, reasons))

        # 7. Take top max_agents
        selected = selected[: request.max_agents]

        # 8. Build results with adjusted score including diversity weight
        results: list[SelectionResult] = []
        for raw_score, profile, reasons in selected:
            # Final score: scale raw to 0..1 range then apply diversity bonus
            # Raw max is WEIGHT_REQUIRED + WEIGHT_OPTIONAL + WEIGHT_TASK_TYPE + WEIGHT_PERMISSION
            max_possible = (
                self.WEIGHT_REQUIRED
                + self.WEIGHT_OPTIONAL
                + self.WEIGHT_TASK_TYPE
                + self.WEIGHT_PERMISSION
            )
            normalized = raw_score / max_possible if max_possible > 0 else 0.0

            # Apply full diversity bonus to selected agents
            final_score = normalized * (1.0 - self.WEIGHT_DIVERSITY) + self.WEIGHT_DIVERSITY

            results.append(
                SelectionResult(
                    agent_id=profile["id"],
                    score=round(final_score, 4),
                    reasons=reasons,
                )
            )

        return results
