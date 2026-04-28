"""AgentPromoter -- promote a skill to a standalone agent.

Conditions (from docs/04):
  - effective_rate > 0.8
  - total_selections > 50
  - Covers an independent workflow (not a fragment)

Actions:
  1. Create new agent manifest from skill metadata
  2. Generate proper Python package with __init__.py, agent.py, mcp_adapter.py
  3. Generate pyproject.toml with hatch build config
  4. Register as Atomic Agent
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.utils import (
    AGENT_NAME_RE,
    agent_name_to_package,
    to_class_name,
)
from agent_nexus.platform.utils import (
    atomic_write as _atomic_write,
)


@dataclass
class PromotionCandidate:
    """A skill that is eligible for promotion to an agent."""

    skill_id: str
    skill_name: str
    effective_rate: float
    total_selections: int
    directory: str
    reason: str


@dataclass
class PromotionResult:
    """Outcome of a promotion attempt."""

    success: bool
    agent_name: str = ""
    agent_directory: str = ""
    manifest_path: str = ""
    entry_point_path: str = ""
    error: str = ""


# Promotion thresholds
_MIN_EFFECTIVE_RATE = 0.8
_MIN_TOTAL_SELECTIONS = 50


class AgentPromoter:
    """Promote well-performing skills to standalone agents.

    When a CAPTURED skill consistently performs well (high effective_rate,
    many selections, independent workflow), it can be "promoted" to a
    first-class Atomic Agent with its own manifest, entry point, and
    MCP server.

    Args:
        store: EvolutionStore for reading skill records.
        agents_root: Root directory for agent packages.
    """

    def __init__(
        self,
        store: EvolutionStore,
        agents_root: Path | None = None,
    ) -> None:
        self._store = store
        self._agents_root = Path(agents_root or "agents/atomic").resolve()

    def find_candidates(self) -> list[PromotionCandidate]:
        """Scan active skills and find promotion candidates.

        Returns skills that meet all three conditions:
          1. effective_rate > 0.8
          2. total_selections > 50
          3. Has a non-empty directory (implies independent workflow)
        """
        active_skills = self._store.get_active_skills()
        candidates: list[PromotionCandidate] = []

        for skill in active_skills:
            if skill.total_selections < _MIN_TOTAL_SELECTIONS:
                continue

            # total_selections >= _MIN_TOTAL_SELECTIONS (>= 50) guaranteed here
            effective_rate = skill.total_completions / skill.total_selections

            if effective_rate < _MIN_EFFECTIVE_RATE:
                continue

            # Must have a directory (implies it has content)
            if not skill.directory:
                continue

            candidates.append(PromotionCandidate(
                skill_id=skill.id,
                skill_name=skill.name,
                effective_rate=effective_rate,
                total_selections=skill.total_selections,
                directory=skill.directory,
                reason=(
                    f"effective_rate={effective_rate:.2f}, "
                    f"total_selections={skill.total_selections}, "
                    f"independent workflow"
                ),
            ))

        return candidates

    def promote(
        self,
        candidate: PromotionCandidate,
    ) -> PromotionResult:
        """Promote a skill to a standalone Atomic Agent.

        Generates the full package structure matching other agents:
          1. Agent manifest (agent-manifest.yaml)
          2. Python package directory with __init__.py, agent.py, mcp_adapter.py
          3. pyproject.toml with hatch build config
          4. SKILL.md

        Args:
            candidate: The promotion candidate.

        Returns:
            PromotionResult with paths to generated files.
        """
        agent_name = candidate.skill_name
        if not AGENT_NAME_RE.match(agent_name):
            return PromotionResult(
                success=False,
                error=f"Invalid skill name for promotion: {agent_name!r}",
            )

        agent_dir = self._agents_root / agent_name
        pkg_name = agent_name_to_package(agent_name)
        pkg_dir = agent_dir / pkg_name

        # Track whether the directory existed BEFORE we started so we
        # can decide cleanup scope on failure.  A pre-existing directory
        # from a crashed promotion (SIGKILL) should still be cleaned up
        # if only partial files were written.
        preexisting = agent_dir.exists()
        try:
            agent_dir.mkdir(parents=True, exist_ok=True)
            pkg_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return PromotionResult(
                success=False,
                error=f"Failed to create agent directory: {e}",
            )

        # Track files written so we can clean up on partial failure.
        written_files: list[Path] = []
        try:
            # Generate manifest
            manifest_content = self._generate_manifest(candidate)
            manifest_path = agent_dir / "agent-manifest.yaml"
            _atomic_write(manifest_path, manifest_content)
            written_files.append(manifest_path)

            # Generate __init__.py (package root)
            init_content = self._generate_init_py(candidate, pkg_name)
            init_path = pkg_dir / "__init__.py"
            _atomic_write(init_path, init_content)
            written_files.append(init_path)

            # Generate agent.py inside package (entry point)
            entry_content = self._generate_entry_point(candidate)
            entry_path = pkg_dir / "agent.py"
            _atomic_write(entry_path, entry_content)
            written_files.append(entry_path)

            # Generate mcp_adapter.py inside package
            mcp_content = self._generate_mcp_adapter(candidate, pkg_name)
            mcp_path = pkg_dir / "mcp_adapter.py"
            _atomic_write(mcp_path, mcp_content)
            written_files.append(mcp_path)

            # Generate pyproject.toml
            pyproject_content = self._generate_pyproject(candidate, pkg_name)
            pyproject_path = agent_dir / "pyproject.toml"
            _atomic_write(pyproject_path, pyproject_content)
            written_files.append(pyproject_path)

            # Generate skill file
            skill_content = self._generate_skill_md(candidate)
            skill_path = agent_dir / "SKILL.md"
            _atomic_write(skill_path, skill_content)
            written_files.append(skill_path)
        except OSError as e:
            # Clean up partial files.
            if not preexisting:
                # We created the directory — safe to remove entirely.
                shutil.rmtree(agent_dir, ignore_errors=True)
            else:
                # Pre-existing directory — only remove files we wrote.
                for f in written_files:
                    try:
                        f.unlink()
                    except OSError:
                        pass
            return PromotionResult(
                success=False,
                error=f"Failed to write agent files: {e}",
            )

        return PromotionResult(
            success=True,
            agent_name=agent_name,
            agent_directory=str(agent_dir),
            manifest_path=str(manifest_path),
            entry_point_path=str(entry_path),
        )

    def _generate_init_py(
        self, candidate: PromotionCandidate, pkg_name: str
    ) -> str:
        """Generate __init__.py for the promoted agent package."""
        class_name = to_class_name(candidate.skill_name)
        return (
            f'"""agent-{candidate.skill_name} — Auto-promoted agent.\n'
            f'\n'
            f'{candidate.skill_name} agent, auto-promoted from skill '
            f'{candidate.skill_id}.\n'
            f'"""\n'
            f'\n'
            f'from {pkg_name}.agent import {class_name}Agent\n'
            f'\n'
            f'__all__ = [\n'
            f'    "{class_name}Agent",\n'
            f']\n'
        )

    def _generate_mcp_adapter(
        self, candidate: PromotionCandidate, pkg_name: str
    ) -> str:
        """Generate mcp_adapter.py for the promoted agent."""
        return (
            f'"""MCP adapter — expose {candidate.skill_name} as an MCP Server.\n'
            f'\n'
            f'Requires the ``fastmcp`` package.\n'
            f'"""\n'
            f'\n'
            f'from __future__ import annotations\n'
            f'\n'
            f'\n'
            f'def create_mcp_server() -> object:\n'
            f'    """Create and return a FastMCP server for {candidate.skill_name}."""\n'
            f'    from fastmcp import FastMCP\n'
            f'\n'
            f'    mcp = FastMCP("{candidate.skill_name}")\n'
            f'\n'
            f'    @mcp.tool()\n'
            f'    async def run(task: str, context: dict | None = None) -> str:\n'
            f'        """Execute the {candidate.skill_name} agent task."""\n'
            f'        from {pkg_name}.agent import {candidate.skill_name.replace("-", "_")}_run\n'
            f'        return await {candidate.skill_name.replace("-", "_")}_run(task, context)\n'
            f'\n'
            f'    return mcp\n'
        )

    def _generate_pyproject(
        self, candidate: PromotionCandidate, pkg_name: str
    ) -> str:
        """Generate pyproject.toml with hatch build config."""
        return (
            f'[project]\n'
            f'name = "agent-{candidate.skill_name}"\n'
            f'version = "0.1.0"\n'
            f'description = "Auto-promoted from skill {candidate.skill_id}"\n'
            f'requires-python = ">=3.12"\n'
            f'dependencies = [\n'
            f'    "pydantic>=2.0",\n'
            f']\n'
            f'\n'
            f'[project.optional-dependencies]\n'
            f'full = [\n'
            f'    "fastmcp>=2.0",\n'
            f']\n'
            f'dev = [\n'
            f'    "pytest>=8.0",\n'
            f'    "pytest-asyncio>=0.23",\n'
            f']\n'
            f'\n'
            f'[build-system]\n'
            f'requires = ["hatchling"]\n'
            f'build-backend = "hatchling.build"\n'
            f'\n'
            f'[tool.hatch.build.targets.wheel]\n'
            f'packages = ["{pkg_name}"]\n'
            f'\n'
            f'[tool.ruff]\n'
            f'target-version = "py312"\n'
            f'line-length = 100\n'
            f'\n'
            f'[tool.ruff.lint]\n'
            f'select = ["E", "F", "I", "N", "UP", "B", "SIM"]\n'
        )

    def _generate_manifest(
        self, candidate: PromotionCandidate
    ) -> str:
        """Generate an agent-manifest.yaml for the promoted agent.

        Produces a flat dict compatible with ``AgentManifest(**data)``.
        The ``promotion`` key is extra metadata (ignored by Pydantic v2).
        """
        manifest_data = {
            "name": candidate.skill_name,
            "type": "atomic",
            "description": f"Auto-promoted from skill {candidate.skill_id}",
            "version": "0.1.0",
            "capabilities": ["general-purpose"],
            "mcp": {
                "tools": ["run"],
            },
            "permissions": {
                "mode": "default",
                "allowed_tools": ["file_read", "grep", "glob"],
                "denied_tools": ["bash"],
            },
            "model_config": {
                "recommended": "standard",
                "fallback": "economy",
            },
            "promotion": {
                "from_skill": candidate.skill_id,
                "effective_rate": round(candidate.effective_rate, 2),
                "total_selections": candidate.total_selections,
            },
        }
        return yaml.safe_dump(manifest_data, default_flow_style=False, sort_keys=False)

    def _generate_entry_point(
        self, candidate: PromotionCandidate
    ) -> str:
        """Generate a minimal agent.py skeleton for the promoted agent."""
        class_name = to_class_name(candidate.skill_name)
        return (
            f'"""Auto-promoted agent: {candidate.skill_name}.\n'
            f'\n'
            f'Promoted from skill {candidate.skill_id} with\n'
            f'effective_rate={candidate.effective_rate:.2f} and\n'
            f'total_selections={candidate.total_selections}.\n'
            f'"""\n'
            f'\n'
            f'\n'
            f'class {class_name}Agent:\n'
            f'    """Auto-promoted agent from skill {candidate.skill_id}."""\n'
            f'\n'
            f'    async def run(self, task: str, context: dict | None = None) -> str:\n'
            f'        """Execute the agent task.\n'
            f'\n'
            f'        Args:\n'
            f'            task: Task description.\n'
            f'            context: Optional context dictionary.\n'
            f'\n'
            f'        Returns:\n'
            f'            Task result as string.\n'
            f'        """\n'
            f'        # NOTE: Implement agent logic based on promoted skill\n'
            f'        return f"Agent {candidate.skill_name!r} executed: {{task}}"\n'
            f'\n'
            f'\n'
            f'async def {candidate.skill_name.replace("-", "_")}_run('
            f'task: str, context: dict | None = None) -> str:\n'
            f'    """Module-level entry point for MCP adapter."""\n'
            f'    agent = {class_name}Agent()\n'
            f'    return await agent.run(task, context)\n'
        )

    def _generate_skill_md(
        self, candidate: PromotionCandidate
    ) -> str:
        """Generate a SKILL.md for the promoted agent."""
        return (
            f'# {candidate.skill_name}\n'
            f'\n'
            f'Auto-promoted from skill `{candidate.skill_id}`.\n'
            f'\n'
            f'## Metrics\n'
            f'\n'
            f'- Effective rate: {candidate.effective_rate:.2%}\n'
            f'- Total selections: {candidate.total_selections}\n'
            f'- Original directory: `{candidate.directory}`\n'
            f'\n'
            f'## Workflow\n'
            f'\n'
            f'NOTE: Document the workflow that was captured and promoted.\n'
        )

    @property
    def store(self) -> EvolutionStore:
        """Access the underlying EvolutionStore."""
        return self._store
