"""Phase D tests: SpecialistSelector — capability matching, ranking, dedup, permission filtering."""

from __future__ import annotations

from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.selector import SelectionRequest, SelectionResult, SpecialistSelector


# ---------------------------------------------------------------------------
# Helpers — build a minimal expert profile for testing
# ---------------------------------------------------------------------------
def _make_profile(
    profile_id: str,
    name: str = "Test Agent",
    capabilities: list[str] | None = None,
    task_types: list[str] | None = None,
    permission_mode: str = "plan",
    output_contract: str = "review_report",
) -> dict:
    """Return a profile dict matching the structure produced by AgencyImporter."""
    caps = capabilities or []
    return {
        "id": profile_id,
        "name": name,
        "capabilities": caps,
        "routing": {
            "task_types": task_types if task_types is not None else caps,
            "positive_signals": [name.lower()],
            "negative_signals": [],
        },
        "permissions": {
            "mode": permission_mode,
            "allowed_tools": [],
            "denied_tools": [],
        },
        "output_contract": {
            "artifact_type": output_contract,
            "required_sections": ["summary"],
        },
    }


def _registry_with(*profiles: dict) -> ExpertRegistry:
    """Create an ExpertRegistry pre-loaded with the given profiles."""
    reg = ExpertRegistry()
    for p in profiles:
        reg.add(p["id"], p, p["capabilities"])
    return reg


# ===================================================================
# 1. Filter by capability
# ===================================================================
def test_selector_filters_by_capability():
    """Selecting for system_design returns only the software-architect."""
    architect = _make_profile(
        "agency.software-architect",
        name="Software Architect",
        capabilities=["system_design", "architecture_review"],
    )
    reviewer = _make_profile(
        "agency.code-reviewer",
        name="Code Reviewer",
        capabilities=["code_review", "security_review"],
    )
    registry = _registry_with(architect, reviewer)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture",
            required_capabilities=["system_design"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    assert len(results) == 1
    assert results[0].agent_id == "agency.software-architect"


# ===================================================================
# 2. Filter by permission
# ===================================================================
def test_selector_filters_by_permission():
    """An expert with full_auto mode is excluded when request needs plan-only."""
    planner = _make_profile(
        "agency.planner",
        name="Planner",
        capabilities=["system_design"],
        permission_mode="plan",
    )
    auto_agent = _make_profile(
        "agency.auto-agent",
        name="Auto Agent",
        capabilities=["system_design"],
        permission_mode="full_auto",
    )
    registry = _registry_with(planner, auto_agent)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture",
            required_capabilities=["system_design"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    ids = [r.agent_id for r in results]
    assert "agency.planner" in ids
    assert "agency.auto-agent" not in ids


# ===================================================================
# 3. Rank by capability overlap
# ===================================================================
def test_selector_ranks_by_capability_overlap():
    """Agents with ALL required caps are selected; optional caps differentiate scores."""
    full_match = _make_profile(
        "agency.full-match",
        name="Full Match",
        capabilities=["system_design", "architecture_review", "tradeoff_analysis"],
    )
    partial_match = _make_profile(
        "agency.partial-match",
        name="Partial Match",
        # Has 2 of 3 required caps — should be filtered out under ALL-required mode
        capabilities=["system_design", "architecture_review", "extra_unique_cap"],
    )
    registry = _registry_with(full_match, partial_match)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture",
            required_capabilities=["system_design", "architecture_review", "tradeoff_analysis"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    # Only full_match has ALL required capabilities
    assert len(results) == 1
    assert results[0].agent_id == "agency.full-match"


# ===================================================================
# 4. Task type match boost
# ===================================================================
def test_selector_task_type_match():
    """Expert whose routing.task_types includes the task_type gets a boosted score."""
    matched = _make_profile(
        "agency.matched",
        name="Matched Agent",
        capabilities=["system_design", "unique_a"],
        task_types=["architecture_review"],
    )
    unmatched = _make_profile(
        "agency.unmatched",
        name="Unmatched Agent",
        capabilities=["system_design", "unique_b"],
        task_types=["code_review"],
    )
    registry = _registry_with(matched, unmatched)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture_review",
            required_capabilities=["system_design"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    assert len(results) == 2
    assert results[0].agent_id == "agency.matched"
    assert results[0].score > results[1].score


# ===================================================================
# 5. Diversity dedup
# ===================================================================
def test_selector_diversity_dedup():
    """Two experts with identical capabilities: only the higher-ranked one is kept."""
    agent_a = _make_profile(
        "agency.agent-a",
        name="Agent A",
        capabilities=["code_review", "security_review"],
        task_types=["code_review"],
    )
    agent_b = _make_profile(
        "agency.agent-b",
        name="Agent B",
        capabilities=["code_review", "security_review"],
        task_types=["other"],
    )
    registry = _registry_with(agent_a, agent_b)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="code_review",
            required_capabilities=["code_review"],
            optional_capabilities=["security_review"],
            max_agents=5,
            permissions="plan",
        )
    )

    # Both have identical capabilities (>80% overlap), so only the top one is kept
    assert len(results) == 1
    assert results[0].agent_id == "agency.agent-a"


# ===================================================================
# 6. Max agents limit
# ===================================================================
def test_selector_max_agents():
    """Request max_agents=2 with 5 candidates returns exactly 2."""
    profiles = [
        _make_profile(f"agency.agent-{i}", name=f"Agent {i}", capabilities=["system_design"])
        for i in range(5)
    ]
    # Give them diverse capabilities to avoid dedup
    for i, p in enumerate(profiles):
        p["capabilities"] = ["system_design", f"extra_cap_{i}"]

    registry = _registry_with(*profiles)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture",
            required_capabilities=["system_design"],
            optional_capabilities=[],
            max_agents=2,
            permissions="plan",
        )
    )

    assert len(results) == 2


# ===================================================================
# 7. Output includes reasons
# ===================================================================
def test_selector_output_includes_reasons():
    """Each selected agent has a score (float) and reasons (list of strings)."""
    profile = _make_profile(
        "agency.test-agent",
        name="Test Agent",
        capabilities=["system_design"],
        task_types=["architecture_review"],
    )
    registry = _registry_with(profile)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture_review",
            required_capabilities=["system_design"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    assert len(results) == 1
    result = results[0]
    assert isinstance(result.score, float)
    assert isinstance(result.reasons, list)
    assert len(result.reasons) > 0
    for reason in result.reasons:
        assert isinstance(reason, str)


# ===================================================================
# 8. No match returns empty
# ===================================================================
def test_selector_no_match_returns_empty():
    """Selecting for a capability that no expert has returns empty list."""
    profile = _make_profile(
        "agency.code-reviewer",
        name="Code Reviewer",
        capabilities=["code_review"],
    )
    registry = _registry_with(profile)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture",
            required_capabilities=["nonexistent_capability"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    assert results == []


# ===================================================================
# 9. Uses registry
# ===================================================================
def test_selector_uses_registry():
    """Selector loads experts from ExpertRegistry (the class in registry.py)."""
    registry = ExpertRegistry()
    profile = _make_profile(
        "agency.test-agent",
        name="Test Agent",
        capabilities=["system_design"],
    )
    registry.add("agency.test-agent", profile, ["system_design"])
    selector = SpecialistSelector(registry)

    # The selector holds the registry instance
    assert selector.registry is registry

    results = selector.select(
        SelectionRequest(
            task_type="architecture",
            required_capabilities=["system_design"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    assert len(results) == 1
    assert results[0].agent_id == "agency.test-agent"


# ===================================================================
# 10. Empty required capabilities — I1 fix
# ===================================================================
def test_selector_empty_required_caps_no_free_score():
    """When required_capabilities is empty, agents should not get free capability points.

    Before I1 fix, _capability_overlap returned 1.0 for empty required caps,
    giving every agent a free 0.40 score bonus on the required-caps axis.
    Now it returns 0.0, so only task_type and permission fit contribute.
    """
    # Agent with capabilities but no task_type match
    agent_a = _make_profile(
        "agency.agent-a",
        name="Agent A",
        capabilities=["system_design"],
        task_types=["architecture"],
    )
    # Agent with no capabilities at all
    agent_b = _make_profile(
        "agency.agent-b",
        name="Agent B",
        capabilities=[],
        task_types=["architecture"],
    )
    registry = _registry_with(agent_a, agent_b)
    selector = SpecialistSelector(registry)

    results = selector.select(
        SelectionRequest(
            task_type="architecture",
            required_capabilities=[],  # empty — no free points
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
    )

    # Both agents should be selectable (empty required_set skips the filter)
    assert len(results) == 2

    # Scores should be lower than when required caps match,
    # since the required-caps axis (0.40 weight) contributes 0.0
    for r in results:
        assert r.score < 1.0
