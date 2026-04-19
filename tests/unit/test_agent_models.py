"""Unit tests for agent_nexus.models.agent module."""

import json

import pytest
from pydantic import ValidationError

from agent_nexus.models.agent import (
    AgentDependencies,
    AgentDefinition,
    AgentManifest,
    AgentModelConfig,
    AgentPackage,
    AgentRole,
    AgentType,
    CommandDef,
    HookDef,
    McpServerConfig,
    ModelTier,
    RunMode,
    SkillDefinition,
)
from agent_nexus.models.hooks import HookEvent, HookType
from agent_nexus.models.permission import PermissionConfig, PermissionMode


# ---------------------------------------------------------------------------
# AgentType enum
# ---------------------------------------------------------------------------

class TestAgentType:
    def test_members(self):
        assert set(AgentType) == {
            AgentType.ATOMIC,
            AgentType.COMPOSITE,
        }

    def test_values(self):
        assert AgentType.ATOMIC == "atomic"
        assert AgentType.COMPOSITE == "composite"

    def test_from_string(self):
        assert AgentType("atomic") is AgentType.ATOMIC
        assert AgentType("composite") is AgentType.COMPOSITE

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            AgentType("unknown")


# ---------------------------------------------------------------------------
# RunMode enum
# ---------------------------------------------------------------------------

class TestRunMode:
    def test_members(self):
        assert set(RunMode) == {
            RunMode.MCP_STANDALONE,
            RunMode.PLATFORM_ROUTER,
            RunMode.CLI_STANDALONE,
        }

    def test_values(self):
        assert RunMode.MCP_STANDALONE == "mcp"
        assert RunMode.PLATFORM_ROUTER == "local"
        assert RunMode.CLI_STANDALONE == "cli"


# ---------------------------------------------------------------------------
# AgentRole enum
# ---------------------------------------------------------------------------

class TestAgentRole:
    def test_members(self):
        assert set(AgentRole) == {
            AgentRole.EXPLORE,
            AgentRole.PLAN,
            AgentRole.WORKER,
            AgentRole.VERIFICATION,
        }

    def test_values(self):
        assert AgentRole.EXPLORE == "explore"
        assert AgentRole.PLAN == "plan"
        assert AgentRole.WORKER == "worker"
        assert AgentRole.VERIFICATION == "verification"


# ---------------------------------------------------------------------------
# ModelTier enum
# ---------------------------------------------------------------------------

class TestModelTier:
    def test_members(self):
        assert set(ModelTier) == {
            ModelTier.LIGHTWEIGHT,
            ModelTier.STANDARD,
            ModelTier.POWERFUL,
            ModelTier.PREMIUM,
        }

    def test_values(self):
        assert ModelTier.LIGHTWEIGHT == "lightweight"
        assert ModelTier.STANDARD == "standard"
        assert ModelTier.POWERFUL == "powerful"
        assert ModelTier.PREMIUM == "premium"


# ---------------------------------------------------------------------------
# AgentModelConfig
# ---------------------------------------------------------------------------

class TestAgentModelConfig:
    def test_defaults(self):
        cfg = AgentModelConfig()
        assert cfg.recommended is None
        assert cfg.fallback is None

    def test_with_values(self):
        cfg = AgentModelConfig(recommended="gpt-4o", fallback="gpt-3.5-turbo")
        assert cfg.recommended == "gpt-4o"
        assert cfg.fallback == "gpt-3.5-turbo"

    def test_frozen(self):
        cfg = AgentModelConfig(recommended="gpt-4o")
        with pytest.raises(ValidationError):
            cfg.recommended = "claude"

    def test_serialization(self):
        cfg = AgentModelConfig(recommended="gpt-4o")
        data = cfg.model_dump()
        assert data == {"recommended": "gpt-4o", "fallback": None}


# ---------------------------------------------------------------------------
# McpServerConfig
# ---------------------------------------------------------------------------

class TestMcpServerConfig:
    def test_defaults(self):
        cfg = McpServerConfig(command="uvx")
        assert cfg.transport == "stdio"
        assert cfg.command == "uvx"
        assert cfg.args == []
        assert cfg.url is None

    def test_with_command(self):
        cfg = McpServerConfig(command="uvx", args=["mcp-server-docx"])
        assert cfg.command == "uvx"
        assert cfg.args == ["mcp-server-docx"]

    def test_with_url(self):
        cfg = McpServerConfig(transport="sse", url="http://localhost:8080/mcp")
        assert cfg.url == "http://localhost:8080/mcp"

    def test_frozen(self):
        cfg = McpServerConfig(command="uvx")
        with pytest.raises(ValidationError):
            cfg.transport = "sse"


# ---------------------------------------------------------------------------
# AgentManifest
# ---------------------------------------------------------------------------

class TestAgentManifest:
    def test_minimal_construction(self):
        m = AgentManifest(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fill documents",
        )
        assert m.name == "doc-filler"
        assert m.version == "1.0.0"
        assert m.type is AgentType.ATOMIC
        assert m.description == "Fill documents"
        assert m.role is None
        assert m.dependencies == AgentDependencies()
        assert m.mcp_servers == {}
        assert m.background is False
        assert m.initial_prompt is None

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

    def test_frozen(self):
        m = AgentManifest(
            name="test", version="1.0.0", type=AgentType.ATOMIC, description="t"
        )
        with pytest.raises(ValidationError):
            m.name = "changed"

    def test_serialization_round_trip(self):
        m = AgentManifest(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fill documents",
        )
        data = m.model_dump()
        m2 = AgentManifest(**data)
        assert m2 == m

    def test_json_serialization(self):
        m = AgentManifest(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fill documents",
        )
        json_str = m.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["name"] == "doc-filler"
        m2 = AgentManifest.model_validate_json(json_str)
        assert m2 == m

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            AgentManifest()

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

class TestSkillDefinition:
    def test_construction(self):
        s = SkillDefinition(
            name="fill-template",
            agent_type=AgentType.ATOMIC,
            description="Fill a template",
            triggers=["fill", "template"],
            compatible_agents=["doc-filler"],
            capabilities=["docx", "template"],
        )
        assert s.name == "fill-template"
        assert len(s.triggers) == 2
        assert "docx" in s.capabilities

    def test_defaults(self):
        s = SkillDefinition(
            name="test", agent_type=AgentType.ATOMIC, description="test"
        )
        assert s.triggers == []
        assert s.compatible_agents == []
        assert s.capabilities == []
        assert s.body is None
        assert s.resources is None

    def test_frozen(self):
        s = SkillDefinition(
            name="test", agent_type=AgentType.ATOMIC, description="test"
        )
        with pytest.raises(ValidationError):
            s.name = "changed"


# ---------------------------------------------------------------------------
# CommandDef
# ---------------------------------------------------------------------------

class TestCommandDef:
    def test_construction(self):
        c = CommandDef(name="fill", description="Fill a document")
        assert c.name == "fill"
        assert c.parameters == {}

    def test_with_parameters(self):
        c = CommandDef(
            name="fill",
            description="Fill a document",
            parameters={"path": {"type": "string"}},
        )
        assert "path" in c.parameters

    def test_frozen(self):
        c = CommandDef(name="fill", description="Fill")
        with pytest.raises(ValidationError):
            c.name = "changed"


# ---------------------------------------------------------------------------
# AgentDefinition
# ---------------------------------------------------------------------------

class TestAgentDefinition:
    def test_construction(self):
        a = AgentDefinition(name="reviewer", description="Code reviewer")
        assert a.role is None
        assert a.model is None
        assert a.tools == []

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

class TestHookDef:
    def test_construction(self):
        h = HookDef(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        assert h.enabled is True
        assert h.block_on_failure is False
        assert h.timeout_seconds == 10.0
        assert h.matcher is None
        assert h.config == {}


# ---------------------------------------------------------------------------
# AgentPackage
# ---------------------------------------------------------------------------

class TestAgentPackage:
    def test_minimal_construction(self):
        manifest = AgentManifest(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fill documents",
        )
        pkg = AgentPackage(manifest=manifest)
        assert pkg.manifest is manifest
        assert pkg.skills == []
        assert pkg.commands == []
        assert pkg.agents == []
        assert pkg.hooks == {}
        assert pkg.mcp_servers == {}

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

    def test_frozen(self):
        manifest = AgentManifest(
            name="test", version="1.0.0", type=AgentType.ATOMIC, description="t"
        )
        pkg = AgentPackage(manifest=manifest)
        with pytest.raises(ValidationError):
            pkg.skills = []

    def test_serialization_round_trip(self):
        manifest = AgentManifest(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fill documents",
        )
        pkg = AgentPackage(manifest=manifest)
        data = pkg.model_dump()
        pkg2 = AgentPackage(**data)
        assert pkg2 == pkg


# ============================================================================
# HookDef typed enums -- was str, now HookType / HookEvent (from iter20)
# ============================================================================


class TestHookDefEnumTypes:
    def test_type_must_be_hook_type_enum(self) -> None:
        h = HookDef(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        assert isinstance(h.type, HookType)
        assert h.type == HookType.COMMAND

    def test_event_must_be_hook_event_enum(self) -> None:
        h = HookDef(type=HookType.HTTP, event=HookEvent.POST_EXECUTION)
        assert isinstance(h.event, HookEvent)

    def test_all_hook_type_values(self) -> None:
        for ht in HookType:
            h = HookDef(type=ht, event=HookEvent.PRE_EXECUTION)
            assert h.type is ht

    def test_all_hook_event_values(self) -> None:
        for he in HookEvent:
            h = HookDef(type=HookType.COMMAND, event=he)
            assert h.event is he

    def test_string_coerced_to_enum(self) -> None:
        """String literals are auto-coerced by Pydantic's StrEnum handling."""
        h = HookDef(type="command", event="pre_execution")
        assert isinstance(h.type, HookType)
        assert isinstance(h.event, HookEvent)
        assert h.type is HookType.COMMAND
        assert h.event is HookEvent.PRE_EXECUTION

    def test_serialization_round_trip(self) -> None:
        h = HookDef(type=HookType.AGENT, event=HookEvent.ON_ERROR)
        data = h.model_dump()
        h2 = HookDef(**data)
        assert h2 == h


# ---------------------------------------------------------------------------
# Validation constraint tests (iter22)
# ---------------------------------------------------------------------------


class TestAgentManifestMaxTurnsValidation:
    """Field constraint tests for AgentManifest.max_turns."""

    def test_max_turns_rejects_zero(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            AgentManifest(
                name="test",
                version="1.0.0",
                type=AgentType.ATOMIC,
                description="test",
                max_turns=0,
            )

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

    def test_max_turns_accepts_none(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="test",
        )
        assert m.max_turns is None


class TestHookDefTimeoutValidation:
    """Field constraint tests for agent.models.HookDef.timeout_seconds."""

    def test_timeout_rejects_zero(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            HookDef(
                type=HookType.COMMAND,
                event=HookEvent.PRE_EXECUTION,
                timeout_seconds=0,
            )

    def test_timeout_rejects_negative(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            HookDef(
                type=HookType.COMMAND,
                event=HookEvent.PRE_EXECUTION,
                timeout_seconds=-10,
            )

    def test_timeout_accepts_positive(self):
        h = HookDef(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
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

    def test_agent_manifest_empty_version(self):
        with pytest.raises(ValidationError):
            AgentManifest(name="a", version="", type=AgentType.ATOMIC, description="d")

    def test_agent_manifest_empty_description(self):
        with pytest.raises(ValidationError):
            AgentManifest(name="a", version="1.0", type=AgentType.ATOMIC, description="")

    def test_skill_definition_empty_name(self):
        with pytest.raises(ValidationError):
            SkillDefinition(name="", agent_type=AgentType.ATOMIC, description="d")

    def test_command_def_empty_name(self):
        with pytest.raises(ValidationError):
            CommandDef(name="", description="d")

    def test_agent_definition_empty_name(self):
        with pytest.raises(ValidationError):
            AgentDefinition(name="", description="d")


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

    def test_valid_name_with_underscores_and_digits(self):
        m = AgentManifest(
            name="my_agent_42", version="1.0.0", type=AgentType.ATOMIC, description="d"
        )
        assert m.name == "my_agent_42"

    def test_valid_name_at_max_length(self):
        name = "a" * 128
        m = AgentManifest(
            name=name, version="1.0.0", type=AgentType.ATOMIC, description="d"
        )
        assert len(m.name) == 128

    def test_rejects_name_too_long(self):
        with pytest.raises(ValidationError, match="at most 128 characters"):
            AgentManifest(
                name="a" * 129, version="1.0.0", type=AgentType.ATOMIC, description="d"
            )

    def test_rejects_path_traversal_characters(self):
        with pytest.raises(ValidationError, match="should match"):
            AgentManifest(
                name="../../../etc/passwd",
                version="1.0.0",
                type=AgentType.ATOMIC,
                description="d",
            )

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError, match="should match"):
            AgentManifest(
                name="my agent", version="1.0.0", type=AgentType.ATOMIC, description="d"
            )

    def test_rejects_special_characters(self):
        with pytest.raises(ValidationError, match="should match"):
            AgentManifest(
                name="agent!@#", version="1.0.0", type=AgentType.ATOMIC, description="d"
            )


# ---------------------------------------------------------------------------
# Issue 2: McpServerConfig cross-field validation (transport vs command/url)
# ---------------------------------------------------------------------------


class TestMcpServerConfigTransportValidation:
    """McpServerConfig validates that transport type matches required fields."""

    def test_stdio_with_command_is_valid(self):
        cfg = McpServerConfig(transport="stdio", command="uvx")
        assert cfg.transport == "stdio"
        assert cfg.command == "uvx"

    def test_sse_with_url_is_valid(self):
        cfg = McpServerConfig(transport="sse", url="http://localhost:8080/mcp")
        assert cfg.transport == "sse"
        assert cfg.url == "http://localhost:8080/mcp"

    def test_stdio_without_command_rejected(self):
        with pytest.raises(ValidationError, match="transport='stdio' requires 'command'"):
            McpServerConfig(transport="stdio")

    def test_stdio_default_transport_without_command_rejected(self):
        """Default transport is stdio, so no command should fail."""
        with pytest.raises(ValidationError, match="transport='stdio' requires 'command'"):
            McpServerConfig()

    def test_sse_without_url_rejected(self):
        with pytest.raises(ValidationError, match="transport='sse' requires 'url'"):
            McpServerConfig(transport="sse")

    def test_sse_with_url_and_command_is_valid(self):
        """Having both url and command with sse transport is allowed (command ignored)."""
        cfg = McpServerConfig(
            transport="sse", url="http://localhost:8080/mcp", command="helper"
        )
        assert cfg.url == "http://localhost:8080/mcp"


# ---------------------------------------------------------------------------
# AgentManifest.model_config_field alias round-trip (iter88)
# ---------------------------------------------------------------------------


class TestAgentManifestModelConfigRoundTrip:
    """model_config_field alias must survive serialization round-trip."""

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
        assert m2.model_config_field is not None
        assert m2.model_config_field.recommended == "gpt-4o"

    def test_model_dump_json_round_trip_preserves_model_config(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
            model_config=AgentModelConfig(recommended="gpt-4o", fallback="gpt-3.5-turbo"),
        )
        json_str = m.model_dump_json()
        m2 = AgentManifest.model_validate_json(json_str)
        assert m2.model_config_field is not None
        assert m2.model_config_field.recommended == "gpt-4o"
        assert m2.model_config_field.fallback == "gpt-3.5-turbo"

    def test_field_name_construction_works(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
            model_config_field=AgentModelConfig(recommended="gpt-4o"),
        )
        assert m.model_config_field is not None
        assert m.model_config_field.recommended == "gpt-4o"

    def test_none_model_config_round_trips(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
        )
        data = m.model_dump()
        m2 = AgentManifest(**data)
        assert m2.model_config_field is None


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

    def test_permission_mode_with_default_permissions_mode_accepted(self):
        m = AgentManifest(
            name="test",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="d",
            permission_mode=PermissionMode.PLAN,
            permissions=PermissionConfig(),
        )
        assert m.permission_mode is PermissionMode.PLAN
