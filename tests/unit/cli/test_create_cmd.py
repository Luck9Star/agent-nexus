"""Tests for create_cmd — agent scaffold generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from agent_nexus.platform.local.cli.create_cmd import (
    _gen_main_entry,
    _gen_manifest,
    _gen_mcp_adapter,
    _gen_pkg_agent,
    _gen_pkg_init,
    _gen_pyproject,
    _gen_skill_md,
    _gen_top_level_agent,
    _to_entry_fn,
    create_app,
    scaffold_agent,
)

# ---------------------------------------------------------------------------
# _to_entry_fn
# ---------------------------------------------------------------------------


class TestToEntryFn:
    def test_kebab_to_underscore(self):
        assert _to_entry_fn("code-reviewer") == "code_reviewer"

    def test_no_hyphens(self):
        assert _to_entry_fn("myagent") == "myagent"

    def test_multiple_hyphens(self):
        assert _to_entry_fn("my-cool-agent") == "my_cool_agent"


# ---------------------------------------------------------------------------
# _gen_manifest
# ---------------------------------------------------------------------------


class TestGenManifest:
    def test_simple_tools(self):
        result = _gen_manifest("test-agent", "A test agent", ["run"])
        data = yaml.safe_load(result)
        assert data["name"] == "test-agent"
        assert data["type"] == "atomic"
        assert data["version"] == "0.1.0"
        assert data["mcp"]["tools"] == ["run"]
        assert data["model_config"]["recommended"] == "standard"
        assert data["model_config"]["fallback"] == "economy"

    def test_pipeline_tools(self):
        result = _gen_manifest("p-agent", "Pipeline agent", ["analyze", "execute", "report"])
        data = yaml.safe_load(result)
        assert data["mcp"]["tools"] == ["analyze", "execute", "report"]

    def test_custom_model_tiers(self):
        result = _gen_manifest(
            "agent", "desc", ["run"],
            recommended_model="premium",
            fallback_model="lightweight",
        )
        data = yaml.safe_load(result)
        assert data["model_config"]["recommended"] == "premium"
        assert data["model_config"]["fallback"] == "lightweight"

    def test_permissions_structure(self):
        result = _gen_manifest("a", "d", ["run"])
        data = yaml.safe_load(result)
        assert data["permissions"]["mode"] == "default"
        assert "bash" in data["permissions"]["denied_tools"]

    def test_capabilities_always_general_purpose(self):
        result = _gen_manifest("a", "d", ["run"])
        data = yaml.safe_load(result)
        assert data["capabilities"] == ["general-purpose"]


# ---------------------------------------------------------------------------
# _gen_top_level_agent
# ---------------------------------------------------------------------------


class TestGenTopLevelAgent:
    def test_simple_generates_run_function(self):
        result = _gen_top_level_agent("my-agent", ["run"])
        assert "async def run(" in result
        assert "from agent_my_agent.agent import my_agent_run" in result

    def test_pipeline_generates_multiple_functions(self):
        tools = ["analyze", "execute", "report"]
        result = _gen_top_level_agent("p-agent", tools)
        for t in tools:
            assert f"async def {t}(" in result
            assert f"from agent_p_agent.agent import p_agent_{t}" in result

    def test_includes_future_annotations(self):
        result = _gen_top_level_agent("a", ["run"])
        assert "from __future__ import annotations" in result


# ---------------------------------------------------------------------------
# _gen_skill_md
# ---------------------------------------------------------------------------


class TestGenSkillMd:
    def test_simple_tool_format(self):
        result = _gen_skill_md("test-agent", "Does testing", ["run"])
        assert "# test-agent -- Does testing" in result
        assert "- **run**:" in result
        assert '"task": "example task description"' in result

    def test_pipeline_tool_format(self):
        tools = ["analyze", "execute", "report"]
        result = _gen_skill_md("p-agent", "Pipeline", tools)
        for t in tools:
            assert f"### {t}" in result
            assert f"example {t} input" in result

    def test_error_handling_table(self):
        result = _gen_skill_md("a", "d", ["run"])
        assert "## Error Handling" in result
        assert "| Invalid input |" in result


# ---------------------------------------------------------------------------
# _gen_pyproject
# ---------------------------------------------------------------------------


class TestGenPyproject:
    def test_contains_project_name(self):
        result = _gen_pyproject("my-agent")
        assert 'name = "agent-my-agent"' in result

    def test_contains_package_reference(self):
        result = _gen_pyproject("my-agent")
        assert '"agent_my_agent"' in result

    def test_python_version(self):
        result = _gen_pyproject("a")
        assert ">=3.12" in result

    def test_ruff_config(self):
        result = _gen_pyproject("a")
        assert '"py312"' in result
        assert "line-length = 100" in result

    def test_pytest_asyncio_mode(self):
        result = _gen_pyproject("a")
        assert 'asyncio_mode = "auto"' in result


# ---------------------------------------------------------------------------
# _gen_pkg_init
# ---------------------------------------------------------------------------


class TestGenPkgInit:
    def test_exports_agent_class(self):
        result = _gen_pkg_init("my-agent")
        assert "from agent_my_agent.agent import MyAgentAgent" in result
        assert '"MyAgentAgent"' in result

    def test_single_word_name(self):
        result = _gen_pkg_init("scanner")
        assert "from agent_scanner.agent import ScannerAgent" in result


# ---------------------------------------------------------------------------
# _gen_pkg_agent
# ---------------------------------------------------------------------------


class TestGenPkgAgent:
    def test_simple_generates_class_with_run(self):
        result = _gen_pkg_agent("my-agent", ["run"])
        assert "class MyAgentAgent:" in result
        assert "async def run(self, task:" in result
        assert "async def my_agent_run(" in result

    def test_pipeline_generates_class_with_methods(self):
        tools = ["analyze", "execute", "report"]
        result = _gen_pkg_agent("p-agent", tools)
        assert "class PAgentAgent:" in result
        for t in tools:
            assert f"async def {t}(self, task:" in result
            assert f"async def p_agent_{t}(" in result

    def test_simple_has_todo_placeholder(self):
        result = _gen_pkg_agent("a", ["run"])
        assert "# TODO: Implement agent logic" in result

    def test_pipeline_has_todo_placeholders(self):
        result = _gen_pkg_agent("a", ["analyze", "execute"])
        assert result.count("# TODO:") == 2


# ---------------------------------------------------------------------------
# _gen_main_entry
# ---------------------------------------------------------------------------


class TestGenMainEntry:
    def test_imports_mcp_adapter(self):
        result = _gen_main_entry("my-agent")
        assert "from agent_my_agent.mcp_adapter import create_mcp_server" in result

    def test_transport_env_var(self):
        result = _gen_main_entry("a")
        assert "MCP_TRANSPORT" in result

    def test_sse_host_default(self):
        result = _gen_main_entry("a")
        assert "127.0.0.1" in result


# ---------------------------------------------------------------------------
# _gen_mcp_adapter
# ---------------------------------------------------------------------------


class TestGenMcpAdapter:
    def test_simple_generates_run_tool(self):
        result = _gen_mcp_adapter("my-agent", ["run"])
        assert "FastMCP(" in result
        assert "async def run(task:" in result
        assert "from agent_my_agent.agent import my_agent_run" in result

    def test_pipeline_generates_all_tools(self):
        tools = ["analyze", "execute", "report"]
        result = _gen_mcp_adapter("p-agent", tools)
        for t in tools:
            assert f"async def {t}(task:" in result
            assert f"from agent_p_agent.agent import p_agent_{t}" in result

    def test_includes_fastmcp_import(self):
        result = _gen_mcp_adapter("a", ["run"])
        assert "from fastmcp import FastMCP" in result


# ---------------------------------------------------------------------------
# scaffold_agent — integration of all generators
# ---------------------------------------------------------------------------


class TestScaffoldAgent:
    def test_creates_directory_structure(self, tmp_path: Path):
        agent_dir = scaffold_agent(
            "my-agent", "A test agent", "simple", output_dir=tmp_path,
        )
        assert agent_dir == tmp_path / "my-agent"
        assert agent_dir.is_dir()
        assert (agent_dir / "agent_my_agent").is_dir()

    def test_creates_all_files(self, tmp_path: Path):
        agent_dir = scaffold_agent(
            "my-agent", "desc", "simple", output_dir=tmp_path,
        )
        expected_files = [
            "agent-manifest.yaml",
            "main.py",
            "agent.py",
            "SKILL.md",
            "pyproject.toml",
            "agent_my_agent/__init__.py",
            "agent_my_agent/agent.py",
            "agent_my_agent/mcp_adapter.py",
        ]
        for f in expected_files:
            assert (agent_dir / f).is_file(), f"Missing file: {f}"

    def test_manifest_is_valid_yaml(self, tmp_path: Path):
        agent_dir = scaffold_agent(
            "test-agent", "desc", "simple", output_dir=tmp_path,
        )
        manifest = yaml.safe_load((agent_dir / "agent-manifest.yaml").read_text())
        assert manifest["name"] == "test-agent"
        assert manifest["mcp"]["tools"] == ["run"]

    def test_pipeline_creates_pipeline_tools(self, tmp_path: Path):
        agent_dir = scaffold_agent(
            "p-agent", "Pipeline", "pipeline", output_dir=tmp_path,
        )
        manifest = yaml.safe_load((agent_dir / "agent-manifest.yaml").read_text())
        assert manifest["mcp"]["tools"] == ["analyze", "execute", "report"]
        agent_code = (agent_dir / "agent_p_agent" / "agent.py").read_text()
        assert "async def analyze(" in agent_code
        assert "async def execute(" in agent_code
        assert "async def report(" in agent_code

    def test_rejects_invalid_name(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Invalid agent name"):
            scaffold_agent("bad name!", "desc", "simple", output_dir=tmp_path)

    def test_rejects_name_starting_with_hyphen(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Invalid agent name"):
            scaffold_agent("-agent", "desc", "simple", output_dir=tmp_path)

    def test_rejects_existing_directory(self, tmp_path: Path):
        scaffold_agent("my-agent", "desc", "simple", output_dir=tmp_path)
        with pytest.raises(FileExistsError, match="already exists"):
            scaffold_agent("my-agent", "desc", "simple", output_dir=tmp_path)

    def test_default_output_dir_uses_cwd(self, tmp_path: Path):
        with patch.object(Path, "cwd", return_value=tmp_path):
            agent_dir = scaffold_agent("agent-x", "desc", "simple")
            expected = tmp_path / "agents" / "atomic" / "agent-x"
            assert agent_dir == expected
            assert expected.is_dir()

    def test_custom_model_tiers_in_manifest(self, tmp_path: Path):
        scaffold_agent(
            "m-agent", "desc", "simple",
            recommended_model="premium",
            fallback_model="lightweight",
            output_dir=tmp_path,
        )
        manifest = yaml.safe_load(
            (tmp_path / "m-agent" / "agent-manifest.yaml").read_text(),
        )
        assert manifest["model_config"]["recommended"] == "premium"
        assert manifest["model_config"]["fallback"] == "lightweight"


# ---------------------------------------------------------------------------
# create_agent — CLI command
#
# create_app is a single-command Typer app ("agent" command).
# When invoked directly, the command name is implicit — pass args directly.
# ---------------------------------------------------------------------------


class TestCreateAgentCli:
    runner = CliRunner()

    def test_creates_agent_with_description(self, tmp_path: Path):
        result = self.runner.invoke(
            create_app,
            ["cli-test", "-d", "CLI test agent", "-o", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "Created agent:" in result.output
        assert (tmp_path / "cli-test").is_dir()

    def test_fails_without_description(self, tmp_path: Path):
        result = self.runner.invoke(
            create_app,
            ["cli-test", "-o", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "--description is required" in result.output

    def test_fails_with_invalid_tools(self, tmp_path: Path):
        result = self.runner.invoke(
            create_app,
            ["cli-test", "-d", "desc", "-t", "invalid", "-o", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "--tools must be one of" in result.output

    def test_pipeline_option(self, tmp_path: Path):
        result = self.runner.invoke(
            create_app,
            ["cli-pipe", "-d", "Pipeline", "-t", "pipeline", "-o", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        manifest = yaml.safe_load(
            (tmp_path / "cli-pipe" / "agent-manifest.yaml").read_text(),
        )
        assert manifest["mcp"]["tools"] == ["analyze", "execute", "report"]

    def test_rejects_invalid_name_via_cli(self, tmp_path: Path):
        result = self.runner.invoke(
            create_app,
            ["bad name!", "-d", "desc", "-o", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "Error:" in result.output

    def test_shows_next_steps(self, tmp_path: Path):
        result = self.runner.invoke(
            create_app,
            ["step-agent", "-d", "desc", "-o", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "Next steps:" in result.output
        assert "SKILL.md" in result.output

    def test_wizard_mode(self, tmp_path: Path):
        result = self.runner.invoke(
            create_app,
            ["wiz-agent", "-w", "-o", str(tmp_path)],
            input="Wizard description\n2\n3\n",
        )
        assert result.exit_code == 0, result.output
        manifest = yaml.safe_load(
            (tmp_path / "wiz-agent" / "agent-manifest.yaml").read_text(),
        )
        assert manifest["description"] == "Wizard description"
        assert manifest["mcp"]["tools"] == ["analyze", "execute", "report"]
        assert manifest["model_config"]["recommended"] == "premium"
