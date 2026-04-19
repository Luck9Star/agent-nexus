"""AgentPromoter -- promote a skill to a standalone agent.

Conditions (from docs/04):
  - effective_rate > 0.8
  - total_selections > 50
  - Covers an independent workflow (not a fragment)

Actions:
  1. Create new agent manifest from skill metadata
  2. Generate minimal agent.py skeleton
  3. Register as Atomic Agent
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent_nexus.platform.evolution.store import EvolutionStore

# Same pattern as installer.py _AGENT_NAME_RE and supervisor.py _SAFE_NAME_RE
_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


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
        self._agents_root = (agents_root or Path("agents/atomic")).resolve()

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

        Generates:
          1. Agent manifest (agent.toml)
          2. Entry point (agent.py)
          3. Skill file copy (SKILL.md)

        Args:
            candidate: The promotion candidate.

        Returns:
            PromotionResult with paths to generated files.
        """
        agent_name = candidate.skill_name
        if not _AGENT_NAME_RE.match(agent_name):
            return PromotionResult(
                success=False,
                error=f"Invalid skill name for promotion: {agent_name!r}",
            )

        agent_dir = self._agents_root / agent_name

        # Track whether the directory existed BEFORE we started so we
        # can decide cleanup scope on failure.  A pre-existing directory
        # from a crashed promotion (SIGKILL) should still be cleaned up
        # if only partial files were written.
        preexisting = agent_dir.exists()
        try:
            agent_dir.mkdir(parents=True, exist_ok=True)
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
            self._atomic_write(manifest_path, manifest_content)
            written_files.append(manifest_path)

            # Generate entry point
            entry_content = self._generate_entry_point(candidate)
            entry_path = agent_dir / "agent.py"
            self._atomic_write(entry_path, entry_content)
            written_files.append(entry_path)

            # Generate skill file
            skill_content = self._generate_skill_md(candidate)
            skill_path = agent_dir / "SKILL.md"
            self._atomic_write(skill_path, skill_content)
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

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write *content* to *path* atomically via temp file + os.replace.

        Prevents corrupted files if the process crashes mid-write.
        """
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".promo-", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

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
            "model_config": {
                "recommended": "standard",
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
        return (
            f'"""Auto-promoted agent: {candidate.skill_name}.\n'
            f'\n'
            f'Promoted from skill {candidate.skill_id} with\n'
            f'effective_rate={candidate.effective_rate:.2f} and\n'
            f'total_selections={candidate.total_selections}.\n'
            f'"""\n'
            f'\n'
            f'\n'
            f'async def run(task: str, context: dict | None = None) -> str:\n'
            f'    """Execute the agent task.\n'
            f'\n'
            f'    Args:\n'
            f'        task: Task description.\n'
            f'        context: Optional context dictionary.\n'
            f'\n'
            f'    Returns:\n'
            f'        Task result as string.\n'
            f'    """\n'
            f'    # NOTE: Implement agent logic based on promoted skill\n'
            f'    return f"Agent {candidate.skill_name!r} executed: {{task}}"\n'
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
