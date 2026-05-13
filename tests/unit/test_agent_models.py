"""Unit tests for agent_nexus.models.agent module."""

import pytest
from pydantic import ValidationError

from agent_nexus.models.agent import (
    AgentDefinition,
    AgentDependencies,
    AgentManifest,
    AgentModelConfig,
    AgentPackage,
    AgentRole,
    AgentType,
    CommandDef,
    HookDef,
    McpServerConfig,
    SkillDefinition,
)
from agent_nexus.models.hooks import HookEvent, HookType
from agent_nexus.models.permission import PermissionConfig, PermissionMode

# ---------------------------------------------------------------------------
# AgentModelConfig
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# McpServerConfig
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AgentManifest
# ---------------------------------------------------------------------------


class TestAgentManifest:
    def test_full_construction(self):
        m = AgentManifest(
            name="feature-pipeline",
            version="2.0.0",
            type=AgentType.COMPOSITE,
            description="Full feature delivery pipeline",
            role=AgentRole.WORKER,
            dependencies=AgentDependencies(atomic_agents=["doc-filler", "code-reviewer"]),
            mcp_servers={
                "docx": McpServerConfig(command="uvx", args=["mcp-docx"]),
            },
            effort="high",
            max_turns=50,
            memory_scope="session",
            isolation="full",
            color="#FF5733",
            background=True,
            initial_prompt="Start working on the feature",
        )
        assert m.type is AgentType.COMPOSITE
        assert m.role is AgentRole.WORKER
        assert len(m.dependencies.atomic_agents) == 2
        assert "docx" in m.mcp_servers
        assert m.background is True
        assert m.max_turns == 50

    def test_string_enum_type(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type="atomic",
            description="test",
        )
        assert m.type is AgentType.ATOMIC


# ---------------------------------------------------------------------------
# SkillDefinition
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CommandDef
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AgentDefinition
# ---------------------------------------------------------------------------


class TestAgentDefinition:
    def test_full_construction(self):
        a = AgentDefinition(
            name="reviewer",
            description="Code reviewer",
            role=AgentRole.VERIFICATION,
            model="gpt-4o",
            tools=["file_read", "bash"],
        )
        assert a.role is AgentRole.VERIFICATION
        assert len(a.tools) == 2


# ---------------------------------------------------------------------------
# HookDef
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AgentPackage
# ---------------------------------------------------------------------------


class TestAgentPackage:
    def test_full_construction(self):
        manifest = AgentManifest(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fill documents",
        )
        skill = SkillDefinition(
            name="fill",
            agent_type=AgentType.ATOMIC,
            description="Fill template",
            triggers=["fill"],
        )
        cmd = CommandDef(name="fill", description="Fill a document")
        pkg = AgentPackage(
            manifest=manifest,
            skills=[skill],
            commands=[cmd],
        )
        assert len(pkg.skills) == 1
        assert len(pkg.commands) == 1


# ============================================================================
# HookDef typed enums -- was str, now HookType / HookEvent (from iter20)
# ============================================================================


class TestHookDefEnumTypes:
    def test_string_coerced_to_enum(self) -> None:
        """String literals are auto-coerced by Pydantic's StrEnum handling."""
        h = HookDef(type="command", event="pre_execution", command="echo test")
        assert isinstance(h.type, HookType)
        assert isinstance(h.event, HookEvent)
        assert h.type is HookType.COMMAND
        assert h.event is HookEvent.PRE_EXECUTION


# ---------------------------------------------------------------------------
# Validation constraint tests (iter22)
# ---------------------------------------------------------------------------


class TestAgentManifestMaxTurnsValidation:
    """Field constraint tests for AgentManifest.max_turns."""

    def test_max_turns_rejects_negative(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            AgentManifest(
                name="test",
                version="1.0.0",
                type=AgentType.ATOMIC,
                description="test",
                max_turns=-5,
            )

    def test_max_turns_accepts_positive(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="test",
            max_turns=50,
        )
        assert m.max_turns == 50


class TestHookDefTimeoutValidation:
    """Field constraint tests for agent.models.HookDef.timeout_seconds."""

    def test_timeout_rejects_zero(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            HookDef(
                type=HookType.COMMAND,
                event=HookEvent.PRE_EXECUTION,
                command="echo test",
                timeout_seconds=0,
            )

    def test_timeout_rejects_negative(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            HookDef(
                type=HookType.COMMAND,
                event=HookEvent.PRE_EXECUTION,
                command="echo test",
                timeout_seconds=-10,
            )

    def test_timeout_accepts_positive(self):
        h = HookDef(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command="echo test",
            timeout_seconds=5.0,
        )
        assert h.timeout_seconds == 5.0


# ---------------------------------------------------------------------------
# min_length=1 validation tests (iter30)
# ---------------------------------------------------------------------------


class TestMinLengthValidation:
    """Required string fields reject empty strings (min_length=1)."""

    def test_agent_manifest_empty_name(self):
        with pytest.raises(ValidationError):
            AgentManifest(name="", version="1.0", type=AgentType.ATOMIC, description="d")

    def test_skill_definition_empty_name(self):
        with pytest.raises(ValidationError):
            SkillDefinition(name="", agent_type=AgentType.ATOMIC, description="d")


# ---------------------------------------------------------------------------
# Issue 1: AgentManifest name max_length and pattern validation
# ---------------------------------------------------------------------------


class TestAgentManifestNameValidation:
    """AgentManifest.name must be 1-128 chars and match ^[a-zA-Z0-9_-]+$."""

    def test_valid_simple_name(self):
        m = AgentManifest(
            name="doc-filler", version="1.0.0", type=AgentType.ATOMIC, description="d"
        )
        assert m.name == "doc-filler"

    def test_rejects_name_too_long(self):
        with pytest.raises(ValidationError, match="at most 128 characters"):
            AgentManifest(name="a" * 129, version="1.0.0", type=AgentType.ATOMIC, description="d")

    def test_rejects_path_traversal_characters(self):
        with pytest.raises(ValidationError, match="should match"):
            AgentManifest(
                name="../../../etc/passwd",
                version="1.0.0",
                type=AgentType.ATOMIC,
                description="d",
            )


# ---------------------------------------------------------------------------
# Issue 2: McpServerConfig cross-field validation (transport vs command/url)
# ---------------------------------------------------------------------------


class TestMcpServerConfigTransportValidation:
    """McpServerConfig validates that transport type matches required fields."""

    def test_stdio_without_command_rejected(self):
        with pytest.raises(ValidationError, match="transport='stdio' requires 'command'"):
            McpServerConfig(transport="stdio")

    def test_sse_without_url_rejected(self):
        with pytest.raises(ValidationError, match="transport='sse' requires 'url'"):
            McpServerConfig(transport="sse")


# ---------------------------------------------------------------------------
# AgentManifest.model_preferences alias round-trip (iter88)
# ---------------------------------------------------------------------------


class TestAgentManifestModelConfigRoundTrip:
    """model_preferences alias must survive serialization round-trip."""

    def test_model_dump_round_trip_preserves_model_config(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
            model_config=AgentModelConfig(recommended="gpt-4o"),
        )
        data = m.model_dump()
        m2 = AgentManifest(**data)
        assert m2.model_preferences is not None
        assert m2.model_preferences.recommended == "gpt-4o"


# ---------------------------------------------------------------------------
# AgentManifest permission_mode vs permissions.mode consistency (iter88)
# ---------------------------------------------------------------------------


class TestAgentManifestPermissionConsistency:
    """permission_mode and permissions.mode must not diverge."""

    def test_divergent_modes_rejected(self):
        with pytest.raises(ValidationError, match="conflicts"):
            AgentManifest(
                name="test",
                version="1.0.0",
                type=AgentType.ATOMIC,
                description="d",
                permission_mode=PermissionMode.PLAN,
                permissions=PermissionConfig(mode=PermissionMode.FULL_AUTO),
            )

    def test_consistent_modes_accepted(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
            permission_mode=PermissionMode.PLAN,
            permissions=PermissionConfig(mode=PermissionMode.PLAN),
        )
        assert m.permission_mode is PermissionMode.PLAN

    def test_only_permission_mode_accepted(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
            permission_mode=PermissionMode.FULL_AUTO,
        )
        assert m.permission_mode is PermissionMode.FULL_AUTO
        assert m.permissions is None

    def test_only_permissions_accepted(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
            permissions=PermissionConfig(mode=PermissionMode.FULL_AUTO),
        )
        assert m.permissions.mode is PermissionMode.FULL_AUTO
        assert m.permission_mode is None
