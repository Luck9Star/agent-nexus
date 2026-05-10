"""Unit tests for AgentDirectory — In-memory agent registry for A2A discovery.

Covers:
- register / deregister
- resolve (found and not found)
- find_by_capability (single and multiple matches)
- find_by_role
- list_active
- duplicate register overwrites
"""

from __future__ import annotations

import pytest

from agent_nexus.models.ipc import AgentAddress
from agent_nexus.platform.orchestration.agent_directory import AgentDirectory


class TestRegisterDeregister:
    def test_register_and_resolve(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", ["code-review", "testing"], "worker")

        addr = directory.resolve("agent-a")
        assert addr is not None
        assert addr.agent_id == "agent-a"
        assert addr.role == "worker"

    def test_deregister_removes_agent(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", ["code-review"], "worker")
        directory.deregister("agent-a")

        assert directory.resolve("agent-a") is None

    def test_deregister_unknown_idempotent(self) -> None:
        """Deregistering a non-existent agent does not raise."""
        directory = AgentDirectory()
        directory.deregister("ghost")  # should not raise

    def test_register_clean_ups_old_capabilities(self) -> None:
        """Re-registering with different capabilities removes old ones from index."""
        directory = AgentDirectory()
        directory.register("agent-a", ["code-review"], "worker")
        # Re-register with different capabilities
        directory.register("agent-a", ["testing"], "reviewer")

        # "code-review" should no longer match agent-a
        assert directory.find_by_capability("code-review") == []
        # "testing" should now match
        matches = directory.find_by_capability("testing")
        assert len(matches) == 1
        assert matches[0].agent_id == "agent-a"

    def test_duplicate_register_overwrites(self) -> None:
        """Second register for same agent_id updates role and capabilities."""
        directory = AgentDirectory()
        directory.register("agent-a", ["code-review"], "worker")
        directory.register("agent-a", ["testing"], "coordinator")

        addr = directory.resolve("agent-a")
        assert addr is not None
        assert addr.role == "coordinator"

        # Old capability gone
        assert directory.find_by_capability("code-review") == []
        # New capability present
        assert len(directory.find_by_capability("testing")) == 1


class TestResolve:
    def test_resolve_found(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-x", [], "worker")
        addr = directory.resolve("agent-x")
        assert addr is not None
        assert addr.agent_id == "agent-x"

    def test_resolve_not_found(self) -> None:
        directory = AgentDirectory()
        assert directory.resolve("nonexistent") is None


class TestFindByCapability:
    def test_single_match(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", ["code-review"], "worker")
        directory.register("agent-b", ["testing"], "worker")

        matches = directory.find_by_capability("code-review")
        assert len(matches) == 1
        assert matches[0].agent_id == "agent-a"

    def test_multiple_matches(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", ["code-review", "testing"], "worker")
        directory.register("agent-b", ["testing"], "worker")
        directory.register("agent-c", ["code-review"], "reviewer")

        matches = directory.find_by_capability("testing")
        match_ids = {m.agent_id for m in matches}
        assert match_ids == {"agent-a", "agent-b"}

    def test_no_match(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", ["code-review"], "worker")
        assert directory.find_by_capability("nonexistent-cap") == []

    def test_agent_with_multiple_capabilities(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", ["cap-1", "cap-2", "cap-3"], "worker")

        assert len(directory.find_by_capability("cap-1")) == 1
        assert len(directory.find_by_capability("cap-2")) == 1
        assert len(directory.find_by_capability("cap-3")) == 1


class TestFindByRole:
    def test_find_by_role(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", [], "coordinator")
        directory.register("agent-b", [], "worker")
        directory.register("agent-c", [], "coordinator")

        coordinators = directory.find_by_role("coordinator")
        coord_ids = {a.agent_id for a in coordinators}
        assert coord_ids == {"agent-a", "agent-c"}

    def test_find_by_role_no_match(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", [], "worker")
        assert directory.find_by_role("coordinator") == []


class TestListActive:
    def test_list_active_empty(self) -> None:
        directory = AgentDirectory()
        assert directory.list_active() == []

    def test_list_active_returns_all(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", [], "worker")
        directory.register("agent-b", [], "coordinator")

        active = directory.list_active()
        active_ids = {a.agent_id for a in active}
        assert active_ids == {"agent-a", "agent-b"}

    def test_list_active_after_deregister(self) -> None:
        directory = AgentDirectory()
        directory.register("agent-a", [], "worker")
        directory.register("agent-b", [], "worker")
        directory.deregister("agent-a")

        active = directory.list_active()
        assert len(active) == 1
        assert active[0].agent_id == "agent-b"
