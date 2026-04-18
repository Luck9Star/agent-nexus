"""Agent system models: AgentManifest, AgentPackage, AgentType, RunMode, AgentRole, ModelTier."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


class AgentModelConfig(BaseModel):
    """Model tier preferences for an Agent."""

    model_config = ConfigDict(frozen=True)

    recommended: str | None = None
    fallback: str | None = None


class McpServerConfig(BaseModel):
    """Configuration for an external MCP Server dependency."""

    model_config = ConfigDict(frozen=True)

    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None


class AgentDependencies(BaseModel):
    """Agent dependency specification for composite agents."""

    model_config = ConfigDict(frozen=True)

    atomic_agents: list[str] = Field(default_factory=list)


class AgentManifest(BaseModel):
    """Agent metadata parsed from agent-manifest.yaml.

    This is the identity card of an Agent -- name, version, type, description,
    model preferences, permissions, dependencies, MCP servers, and hooks.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    type: AgentType
    description: str
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
    max_turns: int | None = None
    memory_scope: str | None = None
    isolation: str | None = None
    color: str | None = None
    background: bool = False
    initial_prompt: str | None = None


class SkillDefinition(BaseModel):
    """A SKILL.md file parsed into structured form.

    SKILL.md follows a three-layer progressive loading pattern:
    - Metadata (YAML frontmatter): always loaded
    - Body (markdown content): loaded on first interaction
    - Resources (examples/templates): loaded on demand
    """

    model_config = ConfigDict(frozen=True)

    name: str
    agent_type: AgentType
    description: str
    triggers: list[str] = Field(default_factory=list)
    compatible_agents: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    body: str | None = None
    resources: str | None = None


class CommandDef(BaseModel):
    """A slash command or tool definition exposed by an Agent."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentDefinition(BaseModel):
    """Sub-agent definition used by Composite Agents."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    role: AgentRole | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)


class HookDef(BaseModel):
    """A single lifecycle hook entry."""

    model_config = ConfigDict(frozen=True)

    type: str  # command | http | prompt | agent
    event: str  # pre_execution | post_execution | pre_tool_use | post_tool_use | on_error | on_evolution
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    block_on_failure: bool = False
    timeout_seconds: float = 10.0
    matcher: str | None = None


class AgentPackage(BaseModel):
    """Agent Package = Plugin aggregation container.

    Each Package is a self-contained plugin unit that aggregates skills,
    commands, agents, hooks, MCP servers, and permissions.
    """

    model_config = ConfigDict(frozen=True)

    manifest: AgentManifest
    skills: list[SkillDefinition] = Field(default_factory=list)
    commands: list[CommandDef] = Field(default_factory=list)
    agents: list[AgentDefinition] = Field(default_factory=list)
    hooks: dict[str, list[HookDef]] = Field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
