"""Unit tests for agent-nexus check command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_atomic_agent(base: Path, name: str = "test-agent") -> Path:
    """Create a minimal valid atomic agent package at base/name."""
    agent_dir = base / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir = agent_dir / f"agent_{name.replace('-', '_')}"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # agent-manifest.yaml
    manifest = {
        "name": name,
        "version": "0.1.0",
        "type": "atomic",
        "description": "Test agent",
    }
    (agent_dir / "agent-manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

    # SKILL.md with YAML frontmatter
    skill_md = (
        "---\n"
        f"name: {name}\n"
        "agent_type: atomic\n"
        "description: Test agent\n"
        "---\n\n# Test Agent\n\nTest body.\n"
    )
    (agent_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # pyproject.toml
    (agent_dir / "pyproject.toml").write_text(
        "[project]\nname = 'test-agent'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )

    # agent.py
    (agent_dir / "agent.py").write_text(
        "async def run(task: str, _context=None) -> str:\n    return task\n",
        encoding="utf-8",
    )

    # mcp_adapter.py
    (pkg_dir / "mcp_adapter.py").write_text("def create_mcp_server(): pass\n", encoding="utf-8")
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    return agent_dir


def _write_composite_agent(base: Path, name: str = "test-composite") -> Path:
    """Create a minimal valid composite agent package."""
    agent_dir = base / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": name,
        "version": "0.1.0",
        "type": "composite",
        "description": "Test composite agent",
    }
    (agent_dir / "agent-manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

    composition_toml = (
        '[composition]\nname = "test-composite"\ndescription = "test"\n\n'
        "[tasks.task1]\nname = 'step1'\nagent = 'code-reviewer'\nblocked_by = []\n"
    )
    (agent_dir / "composition.toml").write_text(composition_toml, encoding="utf-8")

    (agent_dir / "SKILL.md").write_text(
        "---\nname: test-composite\nagent_type: composite\ndescription: test\n---\n\n# Test\n",
        encoding="utf-8",
    )
    (agent_dir / "pyproject.toml").write_text(
        "[project]\nname = 'test-composite'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )
    return agent_dir


# ---------------------------------------------------------------------------
# Valid packages
# ---------------------------------------------------------------------------


class TestCheckValid:
    def test_valid_atomic_agent(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 0
        assert "All checks passed" in result.output

    def test_valid_composite_agent(self, tmp_path: Path) -> None:
        agent_dir = _write_composite_agent(tmp_path)
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 0
        assert "All checks passed" in result.output


# ---------------------------------------------------------------------------
# Missing files
# ---------------------------------------------------------------------------


class TestCheckMissingFiles:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "broken"
        agent_dir.mkdir()
        (agent_dir / "SKILL.md").write_text("---\nname: x\n---\n")
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "agent-manifest.yaml" in result.output

    def test_missing_skill_md(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "SKILL.md").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "SKILL.md" in result.output

    def test_missing_pyproject(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "pyproject.toml").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output

    def test_missing_agent_py_for_atomic(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "agent.py").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "agent.py" in result.output

    def test_missing_composition_toml_for_composite(self, tmp_path: Path) -> None:
        agent_dir = _write_composite_agent(tmp_path)
        (agent_dir / "composition.toml").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "composition.toml" in result.output


# ---------------------------------------------------------------------------
# Invalid content
# ---------------------------------------------------------------------------


class TestCheckInvalidContent:
    def test_invalid_manifest_yaml(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "agent-manifest.yaml").write_text(":: invalid yaml {{{")
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "agent-manifest.yaml" in result.output

    def test_manifest_name_mismatch(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path, name="agent-a")
        # Overwrite manifest with a different name
        manifest = {
            "name": "different-name",
            "version": "0.1.0",
            "type": "atomic",
            "description": "Test",
        }
        (agent_dir / "agent-manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "name" in result.output.lower()

    def test_composition_cycle(self, tmp_path: Path) -> None:
        agent_dir = _write_composite_agent(tmp_path)
        cycle_toml = (
            '[composition]\nname = "cycle"\ndescription = "cycle test"\n\n'
            "[tasks.t1]\nname = 'a'\nagent = 'x'\nblocked_by = ['t2']\n\n"
            "[tasks.t2]\nname = 'b'\nagent = 'y'\nblocked_by = ['t1']\n"
        )
        (agent_dir / "composition.toml").write_text(cycle_toml, encoding="utf-8")
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "cycle" in result.output.lower() or "circular" in result.output.lower()

    def test_nonexistent_path(self) -> None:
        result = runner.invoke(app, ["check", "/nonexistent/path"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "does not exist" in result.output.lower()
