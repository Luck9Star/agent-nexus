"""Unit tests for agent_nexus.models.distribution module."""

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_nexus.models.agent import AgentType
from agent_nexus.models.distribution import (
    IndexEntry,
    InstallationStatus,
    Lockfile,
    LockfileEntry,
    PackageSource,
    SourceEntry,
    SourceType,
)


# ---------------------------------------------------------------------------
# SourceType enum
# ---------------------------------------------------------------------------

class TestSourceType:
    def test_members(self):
        assert set(SourceType) == {
            SourceType.OFFICIAL,
            SourceType.PRIVATE,
            SourceType.DIRECT,
        }

    def test_values(self):
        assert SourceType.OFFICIAL == "official"
        assert SourceType.PRIVATE == "private"
        assert SourceType.DIRECT == "direct"

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            SourceType("unknown")


# ---------------------------------------------------------------------------
# InstallationStatus enum
# ---------------------------------------------------------------------------

class TestInstallationStatus:
    def test_members(self):
        assert set(InstallationStatus) == {
            InstallationStatus.INSTALLED,
            InstallationStatus.OUTDATED,
            InstallationStatus.NOT_INSTALLED,
            InstallationStatus.INSTALLING,
            InstallationStatus.FAILED,
        }

    def test_values(self):
        assert InstallationStatus.INSTALLED == "installed"
        assert InstallationStatus.OUTDATED == "outdated"
        assert InstallationStatus.NOT_INSTALLED == "not_installed"
        assert InstallationStatus.INSTALLING == "installing"
        assert InstallationStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# SourceEntry
# ---------------------------------------------------------------------------

class TestSourceEntry:
    def test_full_construction(self):
        se = SourceEntry(
            name="private-repo",
            type="git",
            url="https://github.com/user/agents.git",
            branch="develop",
        )
        assert se.url == "https://github.com/user/agents.git"
        assert se.branch == "develop"

    def test_frozen(self):
        se = SourceEntry(name="official", url="https://github.com/user/repo.git")
        with pytest.raises(ValidationError):
            se.name = "changed"

    def test_serialization_round_trip(self):
        se = SourceEntry(name="test", url="https://example.com/repo.git")
        data = se.model_dump()
        se2 = SourceEntry(**data)
        assert se2 == se


class TestSourceEntryValidation:
    """SourceEntry cross-field and field-level validation."""

    def test_git_type_requires_url(self):
        """Git-type source with empty URL must raise ValueError."""
        with pytest.raises(ValidationError, match="non-empty"):
            SourceEntry(name="official", type="git", url="")

    def test_git_type_requires_non_whitespace_url(self):
        """Git-type source with whitespace-only URL must raise ValueError."""
        with pytest.raises(ValidationError, match="non-empty"):
            SourceEntry(name="official", type="git", url="   ")

    def test_git_type_with_url_succeeds(self):
        se = SourceEntry(name="official", url="https://github.com/user/repo.git")
        assert se.type == "git"
        assert se.url == "https://github.com/user/repo.git"

    def test_non_git_type_allows_empty_url(self):
        """Non-git type with empty URL is allowed."""
        se = SourceEntry(name="local", type="local", url="")
        assert se.type == "local"

    def test_empty_name_rejected(self):
        """Empty name must raise ValidationError (min_length=1)."""
        with pytest.raises(ValidationError):
            SourceEntry(name="", url="https://github.com/user/repo.git")

    def test_whitespace_name_accepted(self):
        """Whitespace-only name passes min_length=1 (field-level check)."""
        se = SourceEntry(name=" ", url="https://github.com/user/repo.git")
        assert se.name == " "


# ---------------------------------------------------------------------------
# LockfileEntry
# ---------------------------------------------------------------------------

class TestLockfileEntry:
    def test_construction_with_required_fields(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="abc123def4560000000000000000000000000000",
            agent_type=AgentType.ATOMIC,
        )
        assert le.version == "1.0.0"
        assert le.source == "official"
        assert le.commit_sha == "abc123def4560000000000000000000000000000"
        assert le.agent_type is AgentType.ATOMIC

    def test_defaults(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
        )
        assert isinstance(le.installed_at, datetime)
        assert le.venv_path == ""
        assert le.dependencies == []

    def test_full_construction(self):
        le = LockfileEntry(
            version="2.0.0",
            source="private",
            commit_sha="deadbeef" * 5,
            agent_type=AgentType.COMPOSITE,
            venv_path="~/.agent-nexus/venvs/my-agent",
            dependencies=["numpy", "pandas"],
        )
        assert le.venv_path == "~/.agent-nexus/venvs/my-agent"
        assert len(le.dependencies) == 2

    def test_frozen(self):
        le = LockfileEntry(
            version="1.0.0", source="official", commit_sha="a" * 40, agent_type=AgentType.ATOMIC
        )
        with pytest.raises(ValidationError):
            le.version = "2.0.0"

    def test_serialization_round_trip(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
            venv_path="/venvs/test",
        )
        data = le.model_dump()
        le2 = LockfileEntry(**data)
        assert le2 == le


class TestLockfileEntryCommitShaValidation:
    """commit_sha must be a valid 40-char or 64-char hex string, or 'latest'/'head'."""

    def test_valid_sha1_hash(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
        )
        assert len(le.commit_sha) == 40

    def test_valid_sha256_hash(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="c" * 64,
            agent_type=AgentType.ATOMIC,
        )
        assert len(le.commit_sha) == 64

    def test_valid_latest_sentinel(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="latest",
            agent_type=AgentType.ATOMIC,
        )
        assert le.commit_sha == "latest"

    def test_valid_head_sentinel(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="head",
            agent_type=AgentType.ATOMIC,
        )
        assert le.commit_sha == "head"

    def test_invalid_short_sha_rejected(self):
        with pytest.raises(ValidationError):
            LockfileEntry(
                version="1.0.0",
                source="official",
                commit_sha="abc123",
                agent_type=AgentType.ATOMIC,
            )

    def test_invalid_non_hex_rejected(self):
        with pytest.raises(ValidationError):
            LockfileEntry(
                version="1.0.0",
                source="official",
                commit_sha="g" * 40,  # 'g' is not hex
                agent_type=AgentType.ATOMIC,
            )

    def test_invalid_mixed_case_rejected(self):
        """Only lowercase hex is accepted."""
        with pytest.raises(ValidationError):
            LockfileEntry(
                version="1.0.0",
                source="official",
                commit_sha="A" * 40,
                agent_type=AgentType.ATOMIC,
            )

    def test_invalid_unknown_sentinel_rejected(self):
        with pytest.raises(ValidationError):
            LockfileEntry(
                version="1.0.0",
                source="official",
                commit_sha="tip",
                agent_type=AgentType.ATOMIC,
            )


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------

class TestLockfile:
    def test_defaults(self):
        lf = Lockfile()
        assert lf.version == 1
        assert lf.agents == {}

    def test_with_entries(self):
        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
        )
        lf = Lockfile(agents={"doc-filler": entry})
        assert "doc-filler" in lf.agents
        assert lf.agents["doc-filler"].version == "1.0.0"

    def test_multiple_agents(self):
        e1 = LockfileEntry(
            version="1.0.0", source="official", commit_sha="a" * 40, agent_type=AgentType.ATOMIC
        )
        e2 = LockfileEntry(
            version="2.0.0", source="private", commit_sha="b" * 40, agent_type=AgentType.COMPOSITE
        )
        lf = Lockfile(agents={"agent-a": e1, "agent-b": e2})
        assert len(lf.agents) == 2

    def test_frozen(self):
        lf = Lockfile()
        with pytest.raises(ValidationError):
            lf.agents = {}

    def test_serialization_round_trip(self):
        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
        )
        lf = Lockfile(agents={"test-agent": entry})
        data = lf.model_dump()
        lf2 = Lockfile(**data)
        assert lf2 == lf

    def test_json_serialization(self):
        lf = Lockfile()
        json_str = lf.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["version"] == 1
        assert parsed["agents"] == {}
        lf2 = Lockfile.model_validate_json(json_str)
        assert lf2 == lf


# ---------------------------------------------------------------------------
# PackageSource
# ---------------------------------------------------------------------------

class TestPackageSource:
    def test_construction(self):
        ps = PackageSource(
            name="official",
            url="https://github.com/user/packages.git",
            local_cache="/tmp/cache",
        )
        assert ps.local_cache == "/tmp/cache"

    def test_defaults(self):
        ps = PackageSource(name="test", url="https://github.com/user/repo.git")
        assert ps.type == "git"
        assert ps.url == "https://github.com/user/repo.git"
        assert ps.branch == "main"
        assert ps.local_cache == ""

    def test_frozen(self):
        ps = PackageSource(name="test", url="https://github.com/user/repo.git")
        with pytest.raises(ValidationError):
            ps.local_cache = "changed"


class TestPackageSourceValidation:
    """PackageSource git-URL validation (mirrors SourceEntry)."""

    def test_git_type_with_url_succeeds(self):
        ps = PackageSource(
            name="official", url="https://github.com/user/repo.git"
        )
        assert ps.url == "https://github.com/user/repo.git"

    def test_git_type_empty_url_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            PackageSource(name="official", type="git", url="")

    def test_git_type_whitespace_url_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            PackageSource(name="official", type="git", url="   ")

    def test_non_git_type_allows_empty_url(self):
        ps = PackageSource(name="local", type="local", url="")
        assert ps.type == "local"


# ---------------------------------------------------------------------------
# IndexEntry
# ---------------------------------------------------------------------------

class TestIndexEntry:
    def test_construction(self):
        ie = IndexEntry(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fill documents",
            tags=["docx", "template"],
        )
        assert ie.name == "doc-filler"
        assert ie.tags == ["docx", "template"]

    def test_defaults(self):
        ie = IndexEntry(name="test", version="1.0.0", type=AgentType.ATOMIC)
        assert ie.description == ""
        assert ie.tags == []
        assert ie.dependencies == []

    def test_frozen(self):
        ie = IndexEntry(name="test", version="1.0.0", type=AgentType.ATOMIC)
        with pytest.raises(ValidationError):
            ie.name = "changed"

    def test_serialization_round_trip(self):
        ie = IndexEntry(
            name="test",
            version="1.0.0",
            type=AgentType.COMPOSITE,
            tags=["a", "b"],
        )
        data = ie.model_dump()
        ie2 = IndexEntry(**data)
        assert ie2 == ie

    def test_path_override(self):
        """IndexEntry.path allows non-standard repo layouts."""
        ie = IndexEntry(
            name="custom-agent",
            version="1.0.0",
            type=AgentType.ATOMIC,
            path="agents/custom-agent",
        )
        assert ie.path == "agents/custom-agent"

    def test_path_default_empty(self):
        ie = IndexEntry(name="test", version="1.0.0", type=AgentType.ATOMIC)
        assert ie.path == ""
