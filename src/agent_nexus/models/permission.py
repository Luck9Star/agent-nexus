"""Permission models: PermissionMode, PermissionConfig, PathRule, PermissionDecision."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agent_nexus.models._common import FrozenModel


class PermissionMode(StrEnum):
    """Permission modes controlling how Agents interact with the system.

    - DEFAULT: Mutable operations require user confirmation.
    - PLAN: Read-only; all modifications are blocked.
    - FULL_AUTO: No confirmation needed; used in CI/CD and test environments.
    """

    DEFAULT = "default"
    PLAN = "plan"
    FULL_AUTO = "full_auto"


class PathAccess(StrEnum):
    """Access level for a path rule."""

    READ = "read"
    WRITE = "write"
    READ_WRITE = "read-write"
    DENY = "deny"


class PathRule(FrozenModel):
    """A glob-pattern path access rule.

    Used in PermissionConfig to control which file paths an Agent can access.

    Example:
        - pattern: "*.docx"
          access: read-write
        - pattern: "*.env"
          access: deny
    """

    pattern: str = Field(min_length=1)
    access: PathAccess = PathAccess.READ


class PermissionConfig(FrozenModel):
    """Permission configuration for an Agent.

    Maps to the `permissions` section in agent-manifest.yaml.

    Evaluation order (later steps can override earlier ones):
    1. Built-in sensitive path protection (SSH, AWS, GCP, etc.) -- cannot override
    2. denied_tools (blacklist)
    3. allowed_tools (whitelist)
    4. path_rules (glob pattern access control)
    5. denied_commands (dangerous shell command patterns)
    6. mode (DEFAULT/PLAN/FULL_AUTO baseline)
    7. Read-only tool exemption (file_read, grep, etc. always allowed unless FULL_AUTO)

    Example agent-manifest.yaml:
        permissions:
          mode: default
          allowed_tools: [file_read, file_write, mcp__docx__*]
          denied_tools: [bash]
          path_rules:
            - pattern: "*.docx"
              access: read-write
            - pattern: "*.env"
              access: deny
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    path_rules: list[PathRule] = Field(default_factory=list)
    denied_commands: list[str] = Field(default_factory=list)


class PermissionDecision(FrozenModel):
    """Result of a permission check.

    Attributes:
        allowed: Whether the action is permitted.
        reason: Human-readable explanation (especially for denials).
        requires_confirmation: True when the action is allowed but needs user
            confirmation first (DEFAULT mode for write operations).
    """

    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False
