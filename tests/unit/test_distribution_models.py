"""Unit tests for agent_nexus.models.distribution module."""

import pytest
from pydantic import ValidationError

from agent_nexus.models.agent import AgentType
from agent_nexus.models.distribution import (
    IndexEntry,
    LockfileEntry,
    PackageSource,
    SourceEntry,
)

# ---------------------------------------------------------------------------
# SourceEntry
# ---------------------------------------------------------------------------


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


class TestLockfileEntryCommitShaValidation:
    """commit_sha must be a valid 40-char or 64-char hex string, or 'latest'/'head'."""

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


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PackageSource
# ---------------------------------------------------------------------------


class TestPackageSourceNameValidation:
    """PackageSource.name must be non-empty (min_length=1)."""

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            PackageSource(name="", url="https://github.com/user/repo.git")


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


# ---------------------------------------------------------------------------
# LockfileEntry uppercase hex commit_sha (iter88)
# ---------------------------------------------------------------------------
