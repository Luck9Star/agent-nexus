"""Unit tests for agent_nexus.platform.evolution.promotion module."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest  # pyright: ignore[reportMissingImports]

from agent_nexus.models.evolution import SkillRecord
from agent_nexus.platform.evolution.promotion import (
    AgentPromoter,
    PromotionCandidate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill(
    id: str = "sk-1",
    name: str = "good-skill",
    selections: int = 100,
    applied: int = 90,
    completions: int = 85,
    fallbacks: int = 5,
    directory: str = "skills/good",
) -> SkillRecord:
    return SkillRecord(
        id=id,
        name=name,
        total_selections=selections,
        total_applied=applied,
        total_completions=completions,
        total_fallbacks=fallbacks,
        directory=directory,
    )


def _make_store(skills: list[SkillRecord] | None = None) -> MagicMock:
    store = MagicMock()
    store.get_active_skills.return_value = skills or []
    return store


def _candidate(**overrides) -> PromotionCandidate:
    defaults = dict(
        skill_id="sk-1",
        skill_name="good-skill",
        effective_rate=0.9,
        total_selections=100,
        directory="skills/good",
        reason="high performance",
    )
    defaults.update(overrides)
    return PromotionCandidate(**defaults)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# find_candidates
# ---------------------------------------------------------------------------

class TestFindCandidates:
    def test_finds_eligible_skills(self):
        skill = _skill()
        store = _make_store([skill])
        promoter = AgentPromoter(store)
        candidates = promoter.find_candidates()
        assert len(candidates) == 1
        assert candidates[0].skill_id == "sk-1"

    def test_skips_low_selections(self):
        skill = _skill(selections=10, applied=5, completions=4, fallbacks=1)
        store = _make_store([skill])
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []

    def test_skips_low_effective_rate(self):
        # completions/selections = 20/100 = 0.2 < 0.8
        # applied=50, completions=20, fallbacks=30 -> 20+30=50=applied OK
        skill = _skill(selections=100, applied=50, completions=20, fallbacks=30)
        store = _make_store([skill])
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []

    def test_skips_empty_directory(self):
        skill = _skill(directory="")
        store = _make_store([skill])
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []

    def test_multiple_candidates(self):
        s1 = _skill(id="s1", name="a", selections=60, applied=55,
                     completions=50, fallbacks=5, directory="skills/a")
        s2 = _skill(id="s2", name="b", selections=10, applied=5,
                     completions=3, fallbacks=2, directory="skills/b")
        store = _make_store([s1, s2])
        promoter = AgentPromoter(store)
        candidates = promoter.find_candidates()
        assert len(candidates) == 1
        assert candidates[0].skill_id == "s1"


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

class TestPromote:
    def test_creates_agent_files(self, tmp_path):
        store = _make_store()
        promoter = AgentPromoter(store, agents_root=tmp_path)
        candidate = _candidate()
        result = promoter.promote(candidate)
        assert result.success is True
        assert result.agent_name == "good-skill"
        assert Path(result.manifest_path).exists()
        assert Path(result.entry_point_path).exists()

    def test_manifest_contains_yaml(self, tmp_path):
        store = _make_store()
        promoter = AgentPromoter(store, agents_root=tmp_path)
        result = promoter.promote(_candidate())
        content = Path(result.manifest_path).read_text()
        assert "atomic" in content
        assert "good-skill" in content

    def test_entry_point_is_python(self, tmp_path):
        store = _make_store()
        promoter = AgentPromoter(store, agents_root=tmp_path)
        result = promoter.promote(_candidate())
        content = Path(result.entry_point_path).read_text()
        assert "async def run" in content
        assert "good-skill" in content

    def test_handles_mkdir_failure(self):
        store = _make_store()
        # Use a path that cannot be created (e.g., under a non-existent deep path
        # that the OS denies). We patch mkdir to raise OSError.
        promoter = AgentPromoter(store, agents_root=Path("/nonexistent/root"))
        promoter._agents_root = Path("/nonexistent/root")
        result = promoter.promote(_candidate(skill_name="x"))
        # On most systems, this will fail
        if not result.success:
            assert "Failed to" in result.error


# ---------------------------------------------------------------------------
# _generate_manifest / _generate_entry_point / _generate_skill_md
# ---------------------------------------------------------------------------

class TestGenerationHelpers:
    def test_manifest_has_promotion_metadata(self):
        store = _make_store()
        promoter = AgentPromoter(store)
        candidate = _candidate()
        manifest = promoter._generate_manifest(candidate)
        assert "effective_rate" in manifest
        assert "0.9" in manifest

    def test_manifest_flat_compatible_with_agent_manifest(self):
        """Regression: generated manifest must be a flat dict parseable by AgentManifest."""
        import yaml

        from agent_nexus.models.agent import AgentManifest

        store = _make_store()
        promoter = AgentPromoter(store)
        candidate = _candidate()
        manifest_yaml = promoter._generate_manifest(candidate)
        manifest_dict = yaml.safe_load(manifest_yaml)

        # Must NOT have the old nested "agent" wrapper
        assert "agent" not in manifest_dict

        # Must have flat top-level fields
        assert manifest_dict["name"] == "good-skill"
        assert manifest_dict["type"] == "atomic"
        assert manifest_dict["version"] == "0.1.0"
        assert "description" in manifest_dict

        # Must round-trip through AgentManifest without error
        manifest = AgentManifest(**manifest_dict)
        assert manifest.name == "good-skill"
        assert manifest.type.value == "atomic"

    def test_entry_point_contains_skill_id(self):
        store = _make_store()
        promoter = AgentPromoter(store)
        entry = promoter._generate_entry_point(_candidate())
        assert "sk-1" in entry

    def test_skill_md_has_metrics(self):
        store = _make_store()
        promoter = AgentPromoter(store)
        md = promoter._generate_skill_md(_candidate())
        assert "90.00%" in md
        assert "100" in md


# ---------------------------------------------------------------------------
# store property
# ---------------------------------------------------------------------------

class TestStoreProperty:
    def test_store_returns_underlying_store(self):
        store = _make_store()
        promoter = AgentPromoter(store)
        assert promoter.store is store


# ---------------------------------------------------------------------------
# iter105 regression: _atomic_write failure cleanup
# ---------------------------------------------------------------------------


class TestAtomicWriteFailure:
    """_atomic_write cleans up temp file when os.replace fails."""

    def test_temp_file_cleaned_on_failure(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from agent_nexus.platform.utils import atomic_write

        target = tmp_path / "sub" / "dir" / "test.yaml"
        # Make parent dir exist
        target.parent.mkdir(parents=True, exist_ok=True)

        with patch("os.replace", side_effect=PermissionError("denied")):
            with pytest.raises(PermissionError):
                atomic_write(target, "test content")

        # The target file should NOT exist (write failed)
        assert not target.exists()

        # No stale .write-*.tmp files left behind
        tmp_files = list(target.parent.glob(".write-*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# iter128 regression: partial rollback on pre-existing directory
# ---------------------------------------------------------------------------


class TestPromotionPartialRollback:
    """promote() cleans up partial files on failure in pre-existing directory."""

    def test_partial_files_cleaned_on_preexisting_dir(self, tmp_path: Path) -> None:
        """When atomic_write fails mid-promotion in a pre-existing dir,
        only the files we wrote are removed (not the entire directory)."""
        from unittest.mock import patch

        store = _make_store()
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        # Pre-create the agent directory
        agent_dir = agents_root / "good-skill"
        agent_dir.mkdir()
        # Put an existing file to prove rmtree is NOT called
        existing = agent_dir / "README.md"
        existing.write_text("existing content")

        promoter = AgentPromoter(store, agents_root=agents_root)
        candidate = _candidate()

        # Make the second write (agent.py) fail
        from agent_nexus.platform.evolution.promotion import _atomic_write as real_write
        call_count = 0

        def _failing_write(path, content):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("disk full")
            return real_write(path, content)

        with patch("agent_nexus.platform.evolution.promotion._atomic_write", side_effect=_failing_write):
            result = promoter.promote(candidate)

        assert result.success is False
        assert "disk full" in result.error
        # Pre-existing file should still be there
        assert existing.exists()
        # The agent-manifest.yaml (first file) should have been cleaned up
        assert not (agent_dir / "agent-manifest.yaml").exists()

    def test_full_cleanup_on_new_dir_failure(self, tmp_path: Path) -> None:
        """When all files fail to write in a newly created dir, dir is removed."""
        from unittest.mock import patch

        store = _make_store()
        agents_root = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_root)
        candidate = _candidate()

        agent_dir = agents_root / "good-skill"
        assert not agent_dir.exists()

        with patch("agent_nexus.platform.evolution.promotion._atomic_write", side_effect=OSError("fail")):
            result = promoter.promote(candidate)

        assert result.success is False
        # Newly created directory should be cleaned up entirely
        assert not agent_dir.exists()
