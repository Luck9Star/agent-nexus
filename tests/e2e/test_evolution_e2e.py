"""E2E tests for Self-Evolution Engine: skill lifecycle, lineage, and evolution.

Tests the evolution store using real SQLite databases.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_nexus.models.evolution import SkillLineage, SkillOrigin, SkillRecord


@pytest.fixture
def evo_store(tmp_path: Path):
    """Create an EvolutionStore with a real SQLite database."""
    from agent_nexus.platform.evolution.store import EvolutionStore

    db_path = tmp_path / "evolution.db"
    return EvolutionStore(db_path)


class TestEvolutionE2E:
    """E2E evolution scenarios."""

    def test_store_save_and_query_skill(self, evo_store):
        """Store saves a skill record and queries it back."""
        now = datetime.now(UTC)
        record = SkillRecord(
            id="skill-001",
            name="test-skill",
            version="1.0.0",
            lineage=SkillLineage(origin=SkillOrigin.CAPTURED),
            first_seen=now,
            last_updated=now,
        )
        evo_store.save_skill_record(record)

        skill = evo_store.get_skill_record("skill-001")
        assert skill is not None
        assert skill.name == "test-skill"
        assert skill.version == "1.0.0"

    def test_store_increment_counters(self, evo_store):
        """Store increments quality counters atomically."""
        now = datetime.now(UTC)
        record = SkillRecord(
            id="skill-002",
            name="counter-skill",
            version="1.0.0",
            lineage=SkillLineage(origin=SkillOrigin.CAPTURED),
            first_seen=now,
            last_updated=now,
        )
        evo_store.save_skill_record(record)

        evo_store.increment_counters("skill-002", selected=True, applied=True, completed=True)

        skill = evo_store.get_skill_record("skill-002")
        assert skill is not None
        assert skill.total_selections == 1
        assert skill.total_completions == 1

    def test_store_evolution_creates_new_version(self, evo_store):
        """Store evolves a skill creating a new version with lineage."""
        now = datetime.now(UTC)
        parent = SkillRecord(
            id="parent-001",
            name="evo-skill",
            version="1.0.0",
            lineage=SkillLineage(origin=SkillOrigin.CAPTURED),
            first_seen=now,
            last_updated=now,
        )
        evo_store.save_skill_record(parent)

        child = SkillRecord(
            id="child-001",
            name="evo-skill",
            version="1.0.1",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_ids=["parent-001"],
            ),
            first_seen=now,
            last_updated=now,
        )

        result = evo_store.evolve_skill(child, parent_skill_ids=["parent-001"])
        assert result.success
        assert result.new_record is not None
        assert result.new_record.id == "child-001"

        child_record = evo_store.get_skill_record("child-001")
        assert child_record is not None
        assert child_record.version == "1.0.1"

        # Parent should be deactivated for FIX evolution
        parent_record = evo_store.get_skill_record("parent-001")
        assert parent_record is not None
        assert parent_record.is_active is False
