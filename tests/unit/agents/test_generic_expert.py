"""Tests for generic-expert-agent: profile loading, prompt assembly, output contract, permissions, manifest."""

import os
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AGENT_DIR = Path(__file__).resolve().parents[3] / "agents" / "atomic" / "generic-expert-agent"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PROFILE: dict = {
    "id": "agency.software-architect",
    "name": "Software Architect",
    "source": {
        "kind": "git",
        "repo": "https://github.com/example/agency-agents",
        "ref": "abc123",
        "path": "experts/software-architect.md",
        "license": "MIT",
    },
    "profile": {
        "category": "engineering",
        "description": "Designs system architecture and produces technical blueprints",
        "normalized_prompt_path": "prompts/software-architect.md",
    },
    "capabilities": ["system-design", "architecture-review", "technical-planning"],
    "routing": {
        "task_types": ["architecture", "system-design"],
        "positive_signals": ["design", "architecture", "blueprint"],
        "negative_signals": ["bug-fix", "testing"],
    },
    "runtime": {
        "mode": "persona_only",
        "runner": "nexus.generic-expert-agent",
        "implementation": "python-pydanticai",
        "model_tier": "heavyweight",
    },
    "permissions": {
        "mode": "plan",
        "allowed_tools": [],
        "denied_tools": ["bash", "file_write", "file_delete", "network"],
    },
    "output_contract": {
        "artifact_type": "architecture_document",
        "required_sections": ["overview", "components", "data-flow", "decisions"],
    },
    "quality": {"status": "stable"},
}


SAMPLE_PROMPT_BODY = textwrap.dedent("""\
    You are a senior software architect. Your job is to analyze requirements
    and produce clear, actionable architecture documents.

    Focus on:
    - Component boundaries and responsibilities
    - Data flow between components
    - Key technical decisions and trade-offs
""")


def _write_profile_yaml(tmp_path: Path, profile: dict | None = None) -> Path:
    """Write a sample profile YAML to a temp file and return its path."""
    data = profile or SAMPLE_PROFILE
    # Attach a prompt body under profile.vibe and profile.body for the loader.
    data.setdefault("profile", {})
    data["profile"]["vibe"] = "precise and thorough"
    data["profile"]["body"] = SAMPLE_PROMPT_BODY

    path = tmp_path / "expert_profile.yaml"
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Profile loading
# ---------------------------------------------------------------------------


def test_profile_loading(tmp_path: Path):
    """load_expert_profile reads a YAML file and returns expected fields."""
    _write_profile_yaml(tmp_path)
    from agent_generic_expert_agent.profile_loader import load_expert_profile

    profile = load_expert_profile(str(tmp_path / "expert_profile.yaml"))

    assert profile["id"] == "agency.software-architect"
    assert profile["name"] == "Software Architect"
    assert profile["capabilities"] == ["system-design", "architecture-review", "technical-planning"]
    assert profile["output_contract"]["artifact_type"] == "architecture_document"
    assert "overview" in profile["output_contract"]["required_sections"]


# ---------------------------------------------------------------------------
# 2. Prompt assembly
# ---------------------------------------------------------------------------


def test_prompt_assembly(tmp_path: Path):
    """assemble_prompt includes the role name and body content."""
    _write_profile_yaml(tmp_path)
    from agent_generic_expert_agent.profile_loader import assemble_prompt, load_expert_profile

    profile = load_expert_profile(str(tmp_path / "expert_profile.yaml"))
    prompt = assemble_prompt(profile)

    prompt_lower = prompt.lower()
    assert "Software Architect" in prompt
    assert "senior software architect" in prompt_lower
    assert "component boundaries" in prompt_lower


# ---------------------------------------------------------------------------
# 3. Output contract enforcement — valid
# ---------------------------------------------------------------------------


def test_output_contract_enforcement_valid():
    """A complete output with all required sections should pass validation."""
    from agent_generic_expert_agent.contract import validate_output_contract

    output = textwrap.dedent("""\
        ## overview

        This system handles user authentication.

        ## components

        - Auth Service
        - Token Manager

        ## data-flow

        Requests flow through the API gateway.

        ## decisions

        We chose JWT for token-based auth.
    """)

    contract = {
        "required_sections": ["overview", "components", "data-flow", "decisions"],
    }

    result = validate_output_contract(output, contract)

    assert result["valid"] is True
    assert result["missing_sections"] == []


# ---------------------------------------------------------------------------
# 4. Output contract enforcement — missing
# ---------------------------------------------------------------------------


def test_output_contract_enforcement_missing():
    """An output missing required sections should fail and report which ones."""
    from agent_generic_expert_agent.contract import validate_output_contract

    output = textwrap.dedent("""\
        ## overview

        This system handles user authentication.

        ## components

        - Auth Service
    """)

    contract = {
        "required_sections": ["overview", "components", "data-flow", "decisions"],
    }

    result = validate_output_contract(output, contract)

    assert result["valid"] is False
    assert set(result["missing_sections"]) == {"data-flow", "decisions"}


# ---------------------------------------------------------------------------
# 5. Permissions — plan only
# ---------------------------------------------------------------------------


def test_permissions_plan_only(tmp_path: Path):
    """The agent's permission config should deny bash, file_write, network tools."""
    _write_profile_yaml(tmp_path)
    from agent_generic_expert_agent.runner import ExpertAgentRunner

    runner = ExpertAgentRunner(str(tmp_path / "expert_profile.yaml"))
    perms = runner.get_permissions()

    assert perms["mode"] == "plan"
    for tool in ("bash", "file_write", "network"):
        assert tool in perms["denied_tools"]


# ---------------------------------------------------------------------------
# 6. Agent manifest exists and is valid
# ---------------------------------------------------------------------------


def test_agent_manifest_exists():
    """agent-manifest.yaml exists, is valid YAML, has type=atomic and correct runner."""
    manifest_path = AGENT_DIR / "agent-manifest.yaml"
    assert manifest_path.is_file(), f"Manifest not found at {manifest_path}"

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    assert manifest["type"] == "atomic"
    assert manifest["runtime"]["runner"] == "nexus.generic-expert-agent"


# ---------------------------------------------------------------------------
# 7. SKILL.md exists and is non-empty
# ---------------------------------------------------------------------------


def test_skill_md_exists():
    """SKILL.md exists and is non-empty."""
    skill_path = AGENT_DIR / "SKILL.md"
    assert skill_path.is_file(), f"SKILL.md not found at {skill_path}"
    content = skill_path.read_text(encoding="utf-8").strip()
    assert len(content) > 0


# ---------------------------------------------------------------------------
# 8. pyproject.toml has pydantic-ai dependency
# ---------------------------------------------------------------------------


def test_pyproject_has_pydantic_ai():
    """pyproject.toml lists pydantic-ai in dependencies."""
    pyproject_path = AGENT_DIR / "pyproject.toml"
    assert pyproject_path.is_file(), f"pyproject.toml not found at {pyproject_path}"

    content = pyproject_path.read_text(encoding="utf-8")
    assert "pydantic-ai" in content
