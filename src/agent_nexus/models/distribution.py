"""Git-based distribution models: PackageSource, SourceEntry, LockfileEntry, InstallationStatus."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_utc_now = lambda: datetime.now(timezone.utc)

from agent_nexus.models.agent import AgentType


class SourceType(StrEnum):
    """Type of package source."""

    OFFICIAL = "official"
    PRIVATE = "private"
    DIRECT = "direct"


class InstallationStatus(StrEnum):
    """Installation state of an Agent Package."""

    INSTALLED = "installed"
    OUTDATED = "outdated"
    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    FAILED = "failed"


class SourceEntry(BaseModel):
    """A package source entry from sources.yaml.

    Example sources.yaml:
        sources:
          - name: official
            type: git
            url: https://github.com/user/agent-nexus-packages.git
            branch: main
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    type: str = "git"
    url: str = ""
    branch: str = "main"

    @model_validator(mode="after")
    def _validate_git_url(self) -> "SourceEntry":
        """Git-type sources must have a non-empty URL."""
        if self.type == "git" and not self.url.strip():
            raise ValueError(
                "Git-type source requires a non-empty 'url'. "
                f"Source '{self.name}' has type='git' but url is empty."
            )
        return self


class LockfileEntry(BaseModel):
    """A single Agent entry in lockfile.json.

    Records the installed version, source, commit SHA for reproducibility.

    Example lockfile.json:
        {
          "version": 1,
          "agents": {
            "doc-filler": {
              "version": "1.2.0",
              "source": "official",
              "commit_sha": "abc123def456...",
              "agent_type": "atomic",
              "installed_at": "2026-04-18T12:00:00Z",
              "venv_path": "~/.agent-nexus/venvs/doc-filler"
            }
          }
        }
    """

    model_config = ConfigDict(frozen=True)

    version: str
    source: str
    commit_sha: str = Field(
        pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$|^latest$|^head$",
        description="Hex SHA-1 (40 chars), SHA-256 (64 chars), or sentinel 'latest'/'head'",
    )
    agent_type: AgentType
    installed_at: datetime = Field(default_factory=_utc_now)
    venv_path: str = ""
    dependencies: list[str] = Field(default_factory=list)


class Lockfile(BaseModel):
    """The complete lockfile.json structure.

    Tracks all installed Agent Packages with their exact versions
    and source commit SHAs for reproducible installations.
    """

    model_config = ConfigDict(frozen=True)

    version: int = 1
    agents: dict[str, LockfileEntry] = Field(default_factory=dict)


class PackageSource(BaseModel):
    """Git package source with local cache path.

    Extends SourceEntry with runtime state (local cache directory).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    type: str = "git"
    url: str = ""
    branch: str = "main"
    local_cache: str = ""


class IndexEntry(BaseModel):
    """A single Agent entry from a source's index.yaml.

    Used for search and discovery across all configured sources.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    type: AgentType
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    path: str = ""  # Override for non-standard repo layouts (e.g. "agents/doc-filler")
