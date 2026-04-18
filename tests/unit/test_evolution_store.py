"""Unit tests for EvolutionStore agent_records table and methods."""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: F401 — needed for tmp_path fixture

from agent_nexus.platform.evolution.store import EvolutionStore


def _make_store(tmp_path: Path) -> EvolutionStore:
    return EvolutionStore(tmp_path / "test.db")


# ============================================================================
# save_agent_record + get_agent_record round-trip
# ============================================================================


class TestSaveAndGetAgentRecord:
    def test_save_and_get_round_trip(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="agent-1",
            name="feature-delivery",
            type="composite",
            skill_ids=["s1", "s2"],
            orchestration_toml="[pipeline]\nsteps = []",
        )
        record = store.get_agent_record("agent-1")
        assert record is not None
        assert record["agent_id"] == "agent-1"
        assert record["name"] == "feature-delivery"
        assert record["type"] == "composite"
        assert record["skill_ids"] == ["s1", "s2"]
        assert record["orchestration_toml"] == "[pipeline]\nsteps = []"
        assert record["is_active"] is True
        assert record["effective_rate"] == 0.0
        assert record["avg_steps"] is None
        assert record["avg_duration_ms"] is None
        assert record["created_at"] is not None
        assert record["updated_at"] is not None

    def test_save_minimal(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1",
            name="simple-agent",
            type="atomic",
            skill_ids=["s1"],
        )
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["orchestration_toml"] is None
        assert record["skill_ids"] == ["s1"]

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_agent_record("no-such-id") is None

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1", name="original", type="atomic", skill_ids=["s1"],
        )
        store.save_agent_record(
            agent_id="a1", name="updated", type="composite", skill_ids=["s1", "s2"],
        )
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["name"] == "updated"
        assert record["type"] == "composite"
        assert record["skill_ids"] == ["s1", "s2"]

    def test_save_preserves_metrics_on_overwrite(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1", name="agent", type="atomic", skill_ids=["s1"],
        )
        store.update_agent_metrics("a1", 0.85, 5.0, 1200.0)
        store.save_agent_record(
            agent_id="a1", name="agent", type="atomic", skill_ids=["s1", "s2"],
        )
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["effective_rate"] == 0.85
        assert record["avg_steps"] == 5.0
        assert record["avg_duration_ms"] == 1200.0
        assert record["skill_ids"] == ["s1", "s2"]

    def test_save_preserves_created_at(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1", name="agent", type="atomic", skill_ids=[],
        )
        original = store.get_agent_record("a1")
        assert original is not None
        original_created = original["created_at"]
        store.save_agent_record(
            agent_id="a1", name="agent-v2", type="atomic", skill_ids=[],
        )
        updated = store.get_agent_record("a1")
        assert updated is not None
        assert updated["created_at"] == original_created
        assert updated["name"] == "agent-v2"


# ============================================================================
# get_active_agents
# ============================================================================


class TestGetActiveAgents:
    def test_returns_only_active(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "active", "atomic", ["s1"])
        store.save_agent_record("a2", "also-active", "atomic", ["s2"])
        store.save_agent_record("a3", "inactive", "composite", ["s3"])
        store.deactivate_agent("a3")
        active = store.get_active_agents()
        assert len(active) == 2
        assert {a["agent_id"] for a in active} == {"a1", "a2"}

    def test_empty_store(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_active_agents() == []

    def test_all_inactive(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "x", "atomic", [])
        store.deactivate_agent("a1")
        assert store.get_active_agents() == []


# ============================================================================
# update_agent_metrics
# ============================================================================


class TestUpdateAgentMetrics:
    def test_update_metrics(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        result = store.update_agent_metrics("a1", 0.92, 3.5, 850.0)
        assert result is True
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["effective_rate"] == 0.92
        assert record["avg_steps"] == 3.5
        assert record["avg_duration_ms"] == 850.0

    def test_update_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.update_agent_metrics("nope", 0.5, 1.0, 100.0) is False

    def test_update_preserves_other_fields(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1",
            name="my-agent",
            type="composite",
            skill_ids=["s1", "s2"],
            orchestration_toml="[pipeline]",
        )
        store.update_agent_metrics("a1", 0.75, 4.0, 2000.0)
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["name"] == "my-agent"
        assert record["type"] == "composite"
        assert record["skill_ids"] == ["s1", "s2"]
        assert record["orchestration_toml"] == "[pipeline]"
        assert record["is_active"] is True


# ============================================================================
# deactivate_agent
# ============================================================================


class TestDeactivateAgent:
    def test_deactivate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        result = store.deactivate_agent("a1")
        assert result is True
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["is_active"] is False

    def test_deactivate_nonexistent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.deactivate_agent("nope") is False

    def test_deactivate_updates_timestamp(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", [])
        original = store.get_agent_record("a1")
        assert original is not None
        store.deactivate_agent("a1")
        updated = store.get_agent_record("a1")
        assert updated is not None
        assert updated["updated_at"] != original["updated_at"]


# ============================================================================
# clear() cleans up agent_records
# ============================================================================


class TestClearAgentRecords:
    def test_clear_removes_agent_records(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        store.save_agent_record("a2", "other", "composite", ["s2"])
        store.clear()
        assert store.get_agent_record("a1") is None
        assert store.get_agent_record("a2") is None
        assert store.get_active_agents() == []
