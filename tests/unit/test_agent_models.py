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
        cfg = McpServerConfig()
        assert cfg.transport == "stdio"
        assert cfg.command is None
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
        cfg = McpServerConfig()
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
