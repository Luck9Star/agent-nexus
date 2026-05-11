"""Shared composition data models for Composite Agent coordinators."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import toml

from agent_nexus.models.errors import AgentNexusError


class CompositionError(AgentNexusError):
    """Error in composition.toml parsing or validation."""


class CompositionTask:
    """A single task parsed from composition.toml."""

    def __init__(self, task_id: str, name: str, agent: str, blocked_by: list[str]) -> None:
        self.id = task_id
        self.name = name
        self.agent = agent
        self.blocked_by = blocked_by

    def __repr__(self) -> str:
        return f"CompositionTask({self.id!r}, agent={self.agent!r})"


class Composition:
    """Parsed composition.toml definition."""

    def __init__(self, name: str, description: str, tasks: dict[str, CompositionTask]) -> None:
        self.name = name
        self.description = description
        self.tasks = tasks

    def get_root_tasks(self) -> list[CompositionTask]:
        """Get tasks with no dependencies (entry points)."""
        return [t for t in self.tasks.values() if not t.blocked_by]

    def get_dependents(self, task_id: str) -> list[CompositionTask]:
        """Get tasks that depend on the given task."""
        return [t for t in self.tasks.values() if task_id in t.blocked_by]

    def get_execution_order(self) -> list[list[str]]:
        """Compute parallel execution groups (BFS by dependency depth).

        Raises CompositionError if some tasks cannot be scheduled (cyclic
        or unresolved dependencies).
        """
        groups: list[list[str]] = []
        completed: set[str] = set()
        remaining = set(self.tasks.keys())

        while remaining:
            ready = [
                tid
                for tid in remaining
                if all(dep in completed for dep in self.tasks[tid].blocked_by)
            ]
            if not ready:
                unscheduled = sorted(remaining)
                raise CompositionError(
                    f"Cannot schedule tasks (cyclic or unresolved deps): {unscheduled}"
                )
            groups.append(sorted(ready))
            completed.update(ready)
            remaining -= set(ready)

        return groups

    @staticmethod
    def _parse_tasks(tasks_raw: dict[str, Any]) -> dict[str, CompositionTask]:
        """Parse the [tasks] section of a composition TOML."""
        tasks: dict[str, CompositionTask] = {}
        for task_id, task_def in tasks_raw.items():
            if not isinstance(task_def, dict):
                raise CompositionError(f"tasks.{task_id} must be a table")
            tasks[task_id] = CompositionTask(
                task_id=task_id,
                name=task_def.get("name", task_id),
                agent=task_def.get("agent", ""),
                blocked_by=list(task_def.get("blocked_by", [])),
            )
        return tasks

    @staticmethod
    def _validate_deps(tasks: dict[str, CompositionTask]) -> None:
        """Validate that all blocked_by references point to existing tasks."""
        for tid, task in tasks.items():
            for dep in task.blocked_by:
                if dep not in tasks:
                    raise CompositionError(f"Task '{tid}' blocked_by unknown task '{dep}'")

    @classmethod
    def from_toml(cls, path: Path | str) -> Composition:
        """Parse composition.toml file."""
        if isinstance(path, str):
            path = Path(path)
        if not path.exists():
            raise CompositionError(f"composition.toml not found: {path}")

        try:
            raw = toml.load(str(path))
        except Exception as exc:
            raise CompositionError(f"Invalid TOML in {path}: {exc}") from exc

        meta = raw.get("composition", {})
        name = meta.get("name", "unknown")
        description = meta.get("description", "")

        tasks_raw = raw.get("tasks", {})
        if not isinstance(tasks_raw, dict):
            raise CompositionError("[tasks] must be a table of task definitions")

        tasks = cls._parse_tasks(tasks_raw)
        cls._validate_deps(tasks)
        _detect_cycles(tasks)

        return cls(name=name, description=description, tasks=tasks)


def _detect_cycles(tasks: dict[str, CompositionTask]) -> None:
    """Detect dependency cycles. Raises CompositionError if found."""
    cycles = detect_cycles_dfs(
        nodes=tasks.keys(),
        get_deps=lambda name: [dep for dep in tasks[name].blocked_by if dep in tasks],
    )
    if cycles:
        # Format the first cycle for the error message
        cycle = cycles[0]
        raise CompositionError(f"Dependency cycle: {' -> '.join(cycle)}")


def detect_cycles_dfs(
    nodes: Iterable[str],
    get_deps: Callable[[str], Iterable[str]],
) -> list[list[str]]:
    """DFS cycle detection over a directed graph.

    Args:
        nodes: All node identifiers.
        get_deps: Returns dependencies (successors) for a given node.

    Returns:
        List of cycles, each cycle as a list of node names.
    """
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: list[list[str]] = []

    def _dfs(node: str, path: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            # Found a cycle — extract the cycle portion of the path
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:] + [node])
            return
        visiting.add(node)
        path.append(node)
        for dep in get_deps(node):
            _dfs(dep, path)
        path.pop()
        visiting.discard(node)
        visited.add(node)

    for node in nodes:
        _dfs(node, [])

    return cycles
