"""Unit tests for agent_nexus.models.distribution module."""


import pytest
from pydantic import ValidationError

from agent_nexus.models.agent import AgentType
from agent_nexus.models.distribution import (
    IndexEntry,
    Lockfile,
    LockfileEntry,
    PackageSource,
    SourceEntry,
)

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


class TestSourceEntryValidation:
    """SourceEntry cross-field and field-level validation."""

    def test_git_type_requires_url(self):
        """Git-type source with empty URL must raise ValueError."""
        with pytest.raises(ValidationError, match="non-empty"):
            SourceEntry(name="official", type="git", url="")

    def test_git_type_with_url_succeeds(self):
        se = SourceEntry(name="official", url="https://github.com/user/repo.git")
        assert se.type == "git"
        assert se.url == "https://github.com/user/repo.git"

    def test_empty_name_rejected(self):
        """Empty name must raise ValidationError (min_length=1)."""
        with pytest.raises(ValidationError):
            SourceEntry(name="", url="https://github.com/user/repo.git")


# ---------------------------------------------------------------------------
# LockfileEntry
# ---------------------------------------------------------------------------


class TestLockfileEntry:
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


class TestLockfileEntrySourceValidation:
    """LockfileEntry.source must be non-empty (min_length=1)."""

    def test_empty_source_rejected(self):
        with pytest.raises(ValidationError):
            LockfileEntry(
                version="1.0.0",
                source="",
                commit_sha="a" * 40,
                agent_type=AgentType.ATOMIC,
            )

    def test_valid_source_accepted(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
        )
        assert le.source == "official"


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

    def test_valid_latest_sentinel(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="latest",
            agent_type=AgentType.ATOMIC,
        )
        assert le.commit_sha == "latest"

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

    def test_uppercase_hex_accepted(self):
        """Uppercase hex is accepted (git SHAs are case-insensitive)."""
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="A" * 40,
            agent_type=AgentType.ATOMIC,
        )
        assert le.commit_sha == "A" * 40


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------


class TestLockfile:
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


# ---------------------------------------------------------------------------
# PackageSource
# ---------------------------------------------------------------------------


class TestPackageSourceNameValidation:
    """PackageSource.name must be non-empty (min_length=1)."""

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            PackageSource(name="", url="https://github.com/user/repo.git")

    def test_valid_name_accepted(self):
        ps = PackageSource(name="official", url="https://github.com/user/repo.git")
        assert ps.name == "official"


class TestPackageSourceValidation:
    """PackageSource git-URL validation (mirrors SourceEntry)."""

    def test_git_type_empty_url_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            PackageSource(name="official", type="git", url="")

    def test_non_git_type_allows_empty_url(self):
        ps = PackageSource(name="local", type="local", url="")
        assert ps.type == "local"


# ---------------------------------------------------------------------------
# IndexEntry
# ---------------------------------------------------------------------------


class TestIndexEntry:
    def test_rejects_path_traversal(self):
        """IndexEntry.path must not contain '..'."""
        with pytest.raises(ValidationError, match=r"\.\."):
            IndexEntry(
                name="test",
                version="1.0.0",
                type=AgentType.ATOMIC,
                path="../../etc/passwd",
            )


# ---------------------------------------------------------------------------
# IndexEntry.name min_length=1 validation (iter88)
# ---------------------------------------------------------------------------


class TestIndexEntryNameValidation:
    """IndexEntry.name must reject empty strings."""

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            IndexEntry(name="", version="1.0.0", type=AgentType.ATOMIC)

    def test_valid_name_accepted(self):
        ie = IndexEntry(name="doc-filler", version="1.0.0", type=AgentType.ATOMIC)
        assert ie.name == "doc-filler"


# ---------------------------------------------------------------------------
# LockfileEntry uppercase hex commit_sha (iter88)
# ---------------------------------------------------------------------------


class TestLockfileEntryUppercaseSha:
    """LockfileEntry.commit_sha accepts uppercase hex (git SHAs are case-insensitive)."""

    def test_uppercase_sha1_accepted(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="A" * 40,
            agent_type=AgentType.ATOMIC,
        )
        assert le.commit_sha == "A" * 40

    def test_mixed_case_sha1_accepted(self):
        le = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="AbCdEf" + "0" * 34,
            agent_type=AgentType.ATOMIC,
        )
        assert le.commit_sha[:6] == "AbCdEf"
