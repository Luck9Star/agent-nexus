"""Agent system models: AgentManifest, AgentPackage, AgentType, RunMode, AgentRole, ModelTier."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from agent_nexus.models._common import FrozenModel

from agent_nexus.models.hooks import HookDefinition
from agent_nexus.models.permission import PermissionConfig, PermissionMode


class AgentType(StrEnum):
    """Agent type classification."""

    ATOMIC = "atomic"
    COMPOSITE = "composite"


class RunMode(StrEnum):
    """How an Agent is executed."""

    MCP_STANDALONE = "mcp"
    PLATFORM_ROUTER = "local"
    CLI_STANDALONE = "cli"


class AgentRole(StrEnum):
    """Preset role types for Atomic Agents within Composite Agent orchestration.

    Roles constrain the tool set and recommend a model tier.
    Not mandatory -- a generic Agent leaves role unset and gets the full tool set.
    """

    EXPLORE = "explore"
    PLAN = "plan"
    WORKER = "worker"
    VERIFICATION = "verification"


class ModelTier(StrEnum):
    """Model capability tiers for Agent model configuration."""

    LIGHTWEIGHT = "lightweight"
    STANDARD = "standard"
    POWERFUL = "powerful"
    PREMIUM = "premium"


class AgentModelConfig(FrozenModel):
    """Model tier preferences for an Agent."""

    recommended: str | None = None
    fallback: str | None = None


class McpServerConfig(FrozenModel):
    """Configuration for an external MCP Server dependency."""

    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> "McpServerConfig":
        """Ensure transport type matches the required fields.

        - stdio transport requires command to be set
        - sse transport requires url to be set
        """
        if self.transport == "stdio" and not self.command:
            raise ValueError(
                "McpServerConfig with transport='stdio' requires 'command' to be set"
            )
        if self.transport == "sse" and not self.url:
            raise ValueError(
                "McpServerConfig with transport='sse' requires 'url' to be set"
            )
        return self


class AgentDependencies(FrozenModel):
    """Agent dependency specification for composite agents."""

    atomic_agents: list[str] = Field(default_factory=list)


class AgentManifest(FrozenModel):
    """Agent metadata parsed from agent-manifest.yaml.

    This is the identity card of an Agent -- name, version, type, description,
    model preferences, permissions, dependencies, MCP servers, and hooks.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    version: str = Field(min_length=1)
    type: AgentType
    description: str = Field(min_length=1)
    model_config_field: AgentModelConfig | None = Field(default=None, alias="model_config")
    role: AgentRole | None = None
    dependencies: AgentDependencies = Field(default_factory=AgentDependencies)
    permissions: PermissionConfig | None = None
    tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    permission_mode: PermissionMode | None = None
    skills: list[str] = Field(default_factory=list)
    hooks: dict[str, list[HookDef]] = Field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    pip_dependencies: list[str] = Field(default_factory=list)
    effort: str | None = None
    max_turns: int | None = Field(default=None, gt=0)
    memory_scope: str | None = None
    isolation: str | None = None
    color: str | None = None
    background: bool = False
    initial_prompt: str | None = None

    @model_validator(mode="after")
    def _validate_permission_consistency(self) -> "AgentManifest":
        """Ensure permission_mode and permissions.mode do not diverge."""
        if (
            self.permission_mode is not None
            and self.permissions is not None
            and self.permissions.mode is not PermissionMode.DEFAULT
            and self.permissions.mode != self.permission_mode
        ):
            raise ValueError(
                f"permission_mode ({self.permission_mode.value}) conflicts "
                f"with permissions.mode ({self.permissions.mode.value}). "
                "Set them consistently or use only one."
            )
        return self


class SkillDefinition(FrozenModel):
    """A SKILL.md file parsed into structured form.

    SKILL.md follows a three-layer progressive loading pattern:
    - Metadata (YAML frontmatter): always loaded
    - Body (markdown content): loaded on first interaction
    - Resources (examples/templates): loaded on demand
    """

    name: str = Field(min_length=1)
    agent_type: AgentType
    description: str = Field(min_length=1)
    triggers: list[str] = Field(default_factory=list)
    compatible_agents: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    body: str | None = None
    resources: str | None = None


class CommandDef(FrozenModel):
    """A slash command or tool definition exposed by an Agent."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentDefinition(FrozenModel):
    """Sub-agent definition used by Composite Agents."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    role: AgentRole | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)


# Alias for backward compatibility — use HookDefinition from hooks.py
HookDef = HookDefinition


class AgentPackage(FrozenModel):
    """Agent Package = Plugin aggregation container.

    Each Package is a self-contained plugin unit that aggregates skills,
    commands, agents, hooks, MCP servers, and permissions.
    """

    manifest: AgentManifest
    skills: list[SkillDefinition] = Field(default_factory=list)
    commands: list[CommandDef] = Field(default_factory=list)
    agents: list[AgentDefinition] = Field(default_factory=list)
    hooks: dict[str, list[HookDef]] = Field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
