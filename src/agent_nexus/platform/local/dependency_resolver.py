"""DependencyResolver — parse and validate agent dependencies.

Handles both ``pip_dependencies`` (Python packages) and
``atomic_agents`` (inter-agent dependencies for Composite Agents).

Design spec: docs/roadmap/p1-3-marketplace.md Phase 3.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .manifest import find_manifest, load_manifest_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedDependency:
    """A single resolved dependency."""

    name: str
    version_spec: str
    dep_type: str  # "pip" or "agent"
    resolved_path: str | None = None


@dataclass(frozen=True)
class ConflictReport:
    """A version conflict between two agents for the same dependency."""

    dep_name: str
    agent_a: str
    agent_b: str
    conflict_reason: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Split "package>=1.0,<2.0" into ("package", ">=1.0,<2.0")
_VERSION_SPLIT_RE = re.compile(r"^([A-Za-z0-9._-]+(?:\[[^\]]+\])?)\s*(.*)$")


def _parse_dep_string(dep: str) -> tuple[str, str]:
    """Split a dependency string into (name, version_spec).

    Handles extras like ``requests[security]>=2.0``.
    """
    m = _VERSION_SPLIT_RE.match(dep.strip())
    if m:
        return m.group(1), m.group(2)
    return dep.strip(), ""


# ---------------------------------------------------------------------------
# DependencyResolver
# ---------------------------------------------------------------------------


class DependencyResolver:
    """Parse dependencies from agent manifests and detect conflicts.

    Parameters
    ----------
    agents_dir:
        Path to the directory where installed agents live.  Used to resolve
        ``atomic_agents`` dependencies to their actual paths.  If ``None``,
        agent dependencies will have ``resolved_path=None``.
    """

    def __init__(self, agents_dir: Path | None = None) -> None:
        self._agents_dir = agents_dir

    def resolve(self, agent_dir: Path) -> list[ResolvedDependency]:
        """Parse dependencies from the agent manifest.

        Reads ``pip_dependencies`` and ``atomic_agents`` from the manifest
        and returns a unified list of resolved dependencies.

        Parameters
        ----------
        agent_dir:
            Path to the agent package directory.

        Returns
        -------
        list[ResolvedDependency]
            Resolved dependencies.  Empty if no manifest exists.
        """
        manifest_path = find_manifest(agent_dir)
        if manifest_path is None:
            return []

        _issues, raw = load_manifest_dict(agent_dir)
        if not raw:
            return []

        deps: list[ResolvedDependency] = []

        # pip_dependencies
        for dep_str in raw.get("pip_dependencies", []):
            name, version_spec = _parse_dep_string(dep_str)
            deps.append(
                ResolvedDependency(
                    name=name,
                    version_spec=version_spec,
                    dep_type="pip",
                    resolved_path=None,
                )
            )

        # atomic_agents (from dependencies section)
        dep_section = raw.get("dependencies", {})
        if isinstance(dep_section, dict):
            for agent_name in dep_section.get("atomic_agents", []):
                resolved_path = self._resolve_agent_path(agent_name)
                deps.append(
                    ResolvedDependency(
                        name=agent_name,
                        version_spec="",
                        dep_type="agent",
                        resolved_path=resolved_path,
                    )
                )

        return deps

    def check_conflicts(
        self, deps_by_agent: dict[str, list[ResolvedDependency]]
    ) -> list[ConflictReport]:
        """Check for version conflicts between agents.

        Compares pip dependencies across agents and reports cases where
        the same package has incompatible version specs.

        Parameters
        ----------
        deps_by_agent:
            Mapping of agent name to its list of resolved dependencies.

        Returns
        -------
        list[ConflictReport]
            Conflicts found (empty if none).
        """
        conflicts: list[ConflictReport] = []

        # Collect {pip_name: [(agent_name, version_spec), ...]}
        pip_map: dict[str, list[tuple[str, str]]] = {}
        for agent_name, deps in deps_by_agent.items():
            for dep in deps:
                if dep.dep_type == "pip" and dep.version_spec:
                    pip_map.setdefault(dep.name, []).append((agent_name, dep.version_spec))

        # Check each pip package that appears in multiple agents
        for pip_name, entries in pip_map.items():
            if len(entries) < 2:
                continue

            # Compare each pair
            for i, (agent_a, spec_a) in enumerate(entries):
                for agent_b, spec_b in entries[i + 1 :]:
                    if spec_a != spec_b:
                        # Different version specs for the same package
                        conflicts.append(
                            ConflictReport(
                                dep_name=pip_name,
                                agent_a=agent_a,
                                agent_b=agent_b,
                                conflict_reason=(
                                    f"Version mismatch: {agent_a} requires "
                                    f"{pip_name}{spec_a}, {agent_b} requires "
                                    f"{pip_name}{spec_b}"
                                ),
                            )
                        )

        return conflicts

    def _resolve_agent_path(self, agent_name: str) -> str | None:
        """Try to resolve an atomic agent dependency to its installed path."""
        if self._agents_dir is None:
            return None

        candidate = self._agents_dir / agent_name
        if candidate.is_dir():
            return str(candidate)

        return None
