"""OrchestrationDSL -- TOML DAG parser for composite agent workflows.

Parses TOML files defining:
- Goal description
- Agent pool (name, description, role, tool_loading)
- Task DAG (id, description, agent, blocked_by, vars)
- Global tool loading strategy

Produces TaskItem list compatible with TaskGraph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import toml

from agent_nexus.models.task import TaskItem, TaskState

# ---------------------------------------------------------------------------
# DSL data types (immutable)
# ---------------------------------------------------------------------------

_VALID_ROLES = frozenset({"explore", "plan", "worker", "verification"})
_VALID_TOOL_LOADING = frozenset({"eager", "lazy", "manifest_only"})


@dataclass
class ValidationResult:
    """Structured result from DSL validation.

    Separates fatal errors (cycles, unknown refs) from informational
    warnings (unused agents), avoiding fragile substring matching.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


@dataclass(frozen=True)
class DSLAgent:
    """Agent declaration within an orchestration."""

    name: str
    description: str
    role: str = "worker"  # explore | plan | worker | verification
    tool_loading: str = "lazy"  # eager | lazy | manifest_only

    def __post_init__(self) -> None:
        if self.role not in _VALID_ROLES:
            raise ValueError(
                f"Invalid agent role '{self.role}', expected one of {sorted(_VALID_ROLES)}"
            )
        if self.tool_loading not in _VALID_TOOL_LOADING:
            raise ValueError(
                f"Invalid tool_loading '{self.tool_loading}', "
                f"expected one of {sorted(_VALID_TOOL_LOADING)}"
            )


@dataclass(frozen=True)
class DSLTask:
    """Parsed task with dependency info."""

    id: str
    description: str
    agent: str
    blocked_by: list[str] = field(default_factory=list)
    vars: dict[str, Any] = field(default_factory=dict)

    def to_task_item(self) -> TaskItem:
        """Convert to TaskItem for TaskGraph.add_task()."""
        now = datetime.now(UTC)
        return TaskItem(
            id=self.id,
            description=self.description,
            agent=self.agent,
            blocked_by=list(self.blocked_by),
            vars=dict(self.vars),
            state=TaskState.PENDING,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True)
class DSLToolLoading:
    """Global tool loading strategy."""

    strategy: str = "lazy"  # eager | lazy | manifest_only
    preload_agents: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.strategy not in _VALID_TOOL_LOADING:
            raise ValueError(
                f"Invalid tool_loading strategy '{self.strategy}', "
                f"expected one of {sorted(_VALID_TOOL_LOADING)}"
            )


@dataclass(frozen=True)
class OrchestrationDefinition:
    """Complete parsed orchestration definition."""

    goal: str
    agent_name: str  # Composite agent's own name
    agents: dict[str, DSLAgent]  # name -> DSLAgent
    tasks: list[DSLTask]  # Ordered task list
    tool_loading: DSLToolLoading

    def get_root_tasks(self) -> list[DSLTask]:
        """Get tasks with no dependencies (entry points)."""
        return [t for t in self.tasks if not t.blocked_by]

    def get_agent_tasks(self, agent_name: str) -> list[DSLTask]:
        """Get all tasks assigned to a specific agent."""
        return [t for t in self.tasks if t.agent == agent_name]

    def get_task_depth(self, task_id: str) -> int:
        """Get the depth of a task in the dependency graph (0 = root).

        Depth is the length of the longest path from any root task to this task.
        Returns -1 if the task_id is not found.
        """
        task_map = {t.id: t for t in self.tasks}
        if task_id not in task_map:
            return -1

        # Memoized DFS to compute max depth (with cycle detection)
        depth_cache: dict[str, int] = {}
        visiting: set[str] = set()

        def _depth(tid: str) -> int:
            if tid in depth_cache:
                return depth_cache[tid]
            if tid in visiting:
                # Cycle detected — return -1 to signal invalid graph.
                # get_task_depth is a query method and should not raise.
                depth_cache[tid] = -1
                return -1
            visiting.add(tid)
            task = task_map.get(tid)
            if task is None or not task.blocked_by:
                depth_cache[tid] = 0
                visiting.discard(tid)
                return 0
            dep_depths = [_depth(dep) for dep in task.blocked_by]
            if -1 in dep_depths:
                # At least one dependency is cyclic — depth undefined.
                depth_cache[tid] = -1
                visiting.discard(tid)
                return -1
            d = 1 + max(dep_depths)
            depth_cache[tid] = d
            visiting.discard(tid)
            return d

        return _depth(task_id)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DSLError(Exception):
    """Base DSL parsing error."""


class DSLSyntaxError(DSLError):
    """TOML syntax or structure error."""


class DSLValidationError(DSLError):
    """Semantic validation error (missing refs, cycles, etc.)."""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class OrchestrationDSL:
    """Parse and validate TOML orchestration definitions.

    Usage::

        dsl = OrchestrationDSL()
        definition = dsl.parse_file(Path("composition.toml"))
        # or
        definition = dsl.parse_string(toml_content)

        # Feed to TaskGraph:
        for task in definition.tasks:
            task_graph.add_task(task.to_task_item())
    """

    # -- public API ---------------------------------------------------------

    def parse_file(self, path: str | Path) -> OrchestrationDefinition:
        """Parse TOML file into OrchestrationDefinition.

        Args:
            path: Path to TOML file. Accepts both ``str`` and ``Path``.

        Raises:
            DSLSyntaxError: on TOML syntax or structural errors.
            DSLValidationError: on semantic validation errors.
        """
        path = Path(path)
        if not path.exists():
            raise DSLSyntaxError(f"TOML file not found: {path}")
        try:
            raw = toml.load(str(path))
        except toml.TomlDecodeError as exc:
            raise DSLSyntaxError(f"Invalid TOML in {path}: {exc}") from exc
        return self._parse(raw)

    def parse_string(self, content: str) -> OrchestrationDefinition:
        """Parse TOML string into OrchestrationDefinition.

        Raises:
            DSLSyntaxError: on TOML syntax or structural errors.
            DSLValidationError: on semantic validation errors.
        """
        try:
            raw = toml.loads(content)
        except toml.TomlDecodeError as exc:
            raise DSLSyntaxError(f"Invalid TOML: {exc}") from exc
        return self._parse(raw)

    def validate(self, definition: OrchestrationDefinition) -> ValidationResult:
        """Validate the definition.

        Checks:
        1. All task.agent references exist in agents dict
        2. All task.blocked_by references exist in tasks
        3. No cycles in the dependency graph
        4. All agents have at least one task (warning)
        5. Global preload_agents reference valid agent names

        Returns:
            ValidationResult with separate errors and warnings lists.
        """
        errors: list[str] = []
        warnings: list[str] = []
        task_ids = {t.id for t in definition.tasks}
        agent_names = set(definition.agents.keys())

        # 1. task.agent references
        for task in definition.tasks:
            if task.agent not in agent_names:
                errors.append(f"Task '{task.id}' references unknown agent '{task.agent}'")

        # 2. task.blocked_by references
        for task in definition.tasks:
            for dep_id in task.blocked_by:
                if dep_id not in task_ids:
                    errors.append(f"Task '{task.id}' blocked_by unknown task '{dep_id}'")

        # 2b. Self-reference check
        for task in definition.tasks:
            if task.id in task.blocked_by:
                errors.append(f"Task '{task.id}' cannot block itself")

        # 3. Cycle detection (DFS with visiting/visited sets)
        task_map = {t.id: t for t in definition.tasks}
        cycles = self._detect_cycles(task_map)
        for cycle in cycles:
            errors.append(f"Dependency cycle detected: {' -> '.join(cycle)}")

        # 4. Agents with no tasks (warning only)
        tasked_agents = {t.agent for t in definition.tasks}
        for name in agent_names:
            if name not in tasked_agents:
                warnings.append(f"Agent '{name}' has no tasks assigned")

        # 5. Global preload_agents references
        for agent_name in definition.tool_loading.preload_agents:
            if agent_name not in agent_names:
                errors.append(f"preload_agents references unknown agent '{agent_name}'")

        return ValidationResult(errors=errors, warnings=warnings)

    # -- internal -----------------------------------------------------------

    def _parse(self, raw: dict[str, Any]) -> OrchestrationDefinition:
        """Parse raw TOML dict into OrchestrationDefinition.

        Supports two TOML formats:
        - **Canonical** (``[goal]``, ``[[agents]]``, ``[[tasks]]``): used by
          OrchestrationDSL templates and the platform router.
        - **Composition** (``[composition]``, ``[tasks.X]``): used by the
          five official composite agents.  Auto-detected by the presence
          of a top-level ``[composition]`` table.

        Structural validation happens here (required sections).
        Semantic validation is deferred to validate().
        """
        # Auto-detect composition format ([composition] table present)
        if isinstance(raw.get("composition"), dict):
            return self._parse_composition_format(raw)

        # -- [goal] --
        goal_section = raw.get("goal")
        if not isinstance(goal_section, dict):
            raise DSLSyntaxError("Missing [goal] section with 'description'")
        goal = goal_section.get("description")
        if not goal or not isinstance(goal, str):
            raise DSLSyntaxError("[goal].description must be a non-empty string")

        # -- [agent_name] --
        agent_name_section = raw.get("agent_name")
        if not isinstance(agent_name_section, dict):
            raise DSLSyntaxError("Missing [agent_name] section with 'value'")
        agent_name = agent_name_section.get("value")
        if not agent_name or not isinstance(agent_name, str):
            raise DSLSyntaxError("[agent_name].value must be a non-empty string")

        # -- [[agents]] --
        raw_agents = raw.get("agents", [])
        if not isinstance(raw_agents, list):
            raise DSLSyntaxError("[[agents]] must be an array of tables")
        agents: dict[str, DSLAgent] = {}
        for idx, raw_agent in enumerate(raw_agents):
            if not isinstance(raw_agent, dict):
                raise DSLSyntaxError(f"agents[{idx}] must be a table")
            name = raw_agent.get("name")
            if not name or not isinstance(name, str):
                raise DSLSyntaxError(f"agents[{idx}].name must be a non-empty string")
            if name in agents:
                raise DSLSyntaxError(f"Duplicate agent name: '{name}'")
            description = raw_agent.get("description", "")
            role = raw_agent.get("role", "worker")
            tool_loading = raw_agent.get("tool_loading", "lazy")
            try:
                agents[name] = DSLAgent(
                    name=name,
                    description=description,
                    role=role,
                    tool_loading=tool_loading,
                )
            except ValueError as exc:
                raise DSLSyntaxError(f"agents[{idx}] ({name}): {exc}") from exc

        # -- [[tasks]] --
        raw_tasks = raw.get("tasks", [])
        if not isinstance(raw_tasks, list):
            raise DSLSyntaxError("[[tasks]] must be an array of tables")
        tasks: list[DSLTask] = []
        seen_task_ids: set[str] = set()
        for idx, raw_task in enumerate(raw_tasks):
            if not isinstance(raw_task, dict):
                raise DSLSyntaxError(f"tasks[{idx}] must be a table")
            task_id = raw_task.get("id")
            if not task_id or not isinstance(task_id, str):
                raise DSLSyntaxError(f"tasks[{idx}].id must be a non-empty string")
            if task_id in seen_task_ids:
                raise DSLSyntaxError(f"Duplicate task id: '{task_id}'")
            seen_task_ids.add(task_id)

            description = raw_task.get("description", "")
            if not description or not isinstance(description, str):
                raise DSLSyntaxError(
                    f"tasks[{idx}] ({task_id}): .description must be a non-empty string"
                )
            agent = raw_task.get("agent")
            if not agent or not isinstance(agent, str):
                raise DSLSyntaxError(f"tasks[{idx}] ({task_id}): .agent must be a non-empty string")
            blocked_by = raw_task.get("blocked_by", [])
            if not isinstance(blocked_by, list):
                raise DSLSyntaxError(f"tasks[{idx}] ({task_id}): .blocked_by must be a list")
            for dep_idx, dep in enumerate(blocked_by):
                if not isinstance(dep, str) or not dep:
                    raise DSLSyntaxError(
                        f"tasks[{idx}] ({task_id}): .blocked_by[{dep_idx}] must be a "
                        f"non-empty string, got {dep!r}"
                    )
            task_vars = raw_task.get("vars", {})
            if not isinstance(task_vars, dict):
                raise DSLSyntaxError(f"tasks[{idx}] ({task_id}): .vars must be a table")
            tasks.append(
                DSLTask(
                    id=task_id,
                    description=description,
                    agent=agent,
                    blocked_by=list(blocked_by),
                    vars=dict(task_vars),
                )
            )

        # -- [tool_loading] (optional) --
        tool_loading = self._parse_tool_loading(raw)

        definition = OrchestrationDefinition(
            goal=goal,
            agent_name=agent_name,
            agents=agents,
            tasks=tasks,
            tool_loading=tool_loading,
        )

        # Run validation, raise on errors (cycles, bad refs)
        result = self.validate(definition)
        if result.errors:
            raise DSLValidationError(
                "DSL validation failed:\n" + "\n".join(f"  - {e}" for e in result.errors)
            )

        return definition

    def _parse_composition_format(self, raw: dict[str, Any]) -> OrchestrationDefinition:
        """Parse the ``[composition]`` / ``[tasks.X]`` TOML format.

        This is the format used by the five official composite agents::

            [composition]
            name = "feature-delivery-pipeline"
            description = "..."

            [tasks.task1]
            name = "requirements-analysis"
            agent = "requirements-analyzer"
            blocked_by = []

        It is auto-detected when a top-level ``[composition]`` table is
        present.  The method converts it to the canonical
        :class:`OrchestrationDefinition` by:

        1. Mapping ``[composition].description`` → *goal*,
           ``[composition].name`` → *agent_name*.
        2. Converting the ``[tasks.X]`` dict-of-tables into a flat list
           of :class:`DSLTask` objects (key *X* becomes ``task.id``,
           ``name`` becomes ``task.description``).
        3. Inferring :class:`DSLAgent` entries from unique ``agent``
           values across all tasks.

        Raises:
            DSLSyntaxError: on structural errors.
            DSLValidationError: on semantic validation errors.
        """
        composition = raw.get("composition", {})
        if not isinstance(composition, dict):
            raise DSLSyntaxError("[composition] must be a table")

        goal = composition.get("description", "")
        if not goal or not isinstance(goal, str):
            raise DSLSyntaxError("[composition].description must be a non-empty string")
        agent_name = composition.get("name", "")
        if not agent_name or not isinstance(agent_name, str):
            raise DSLSyntaxError("[composition].name must be a non-empty string")

        # -- [tasks.X] (dict-of-tables) → list of DSLTask --
        raw_tasks = raw.get("tasks", {})
        if not isinstance(raw_tasks, dict):
            raise DSLSyntaxError("[tasks] must be a table of task definitions")

        tasks: list[DSLTask] = []
        seen_task_ids: set[str] = set()
        agent_names_seen: set[str] = set()

        for task_id, task_def in raw_tasks.items():
            if not isinstance(task_def, dict):
                raise DSLSyntaxError(f"tasks.{task_id} must be a table")
            if task_id in seen_task_ids:
                raise DSLSyntaxError(f"Duplicate task id: '{task_id}'")
            seen_task_ids.add(task_id)

            name = task_def.get("name", task_id)
            if not isinstance(name, str) or not name:
                name = task_id
            agent = task_def.get("agent", "")
            if not isinstance(agent, str) or not agent:
                raise DSLSyntaxError(f"tasks.{task_id}.agent must be a non-empty string")
            agent_names_seen.add(agent)

            blocked_by = task_def.get("blocked_by", [])
            if not isinstance(blocked_by, list):
                raise DSLSyntaxError(f"tasks.{task_id}.blocked_by must be a list")
            for dep_idx, dep in enumerate(blocked_by):
                if not isinstance(dep, str) or not dep:
                    raise DSLSyntaxError(
                        f"tasks.{task_id}.blocked_by[{dep_idx}] must be a "
                        f"non-empty string, got {dep!r}"
                    )

            tasks.append(
                DSLTask(
                    id=task_id,
                    description=name,
                    agent=agent,
                    blocked_by=list(blocked_by),
                )
            )

        # -- Infer [[agents]] from unique agent references --
        agents: dict[str, DSLAgent] = {}
        for aname in sorted(agent_names_seen):
            agents[aname] = DSLAgent(name=aname, description="")

        # -- [tool_loading] (optional, rare in composition format) --
        tool_loading = self._parse_tool_loading(raw)

        definition = OrchestrationDefinition(
            goal=goal,
            agent_name=agent_name,
            agents=agents,
            tasks=tasks,
            tool_loading=tool_loading,
        )

        # Run validation (same as canonical path)
        result = self.validate(definition)
        if result.errors:
            raise DSLValidationError(
                "DSL validation failed:\n" + "\n".join(f"  - {e}" for e in result.errors)
            )

        return definition

    @staticmethod
    def _parse_tool_loading(raw: dict[str, Any]) -> DSLToolLoading:
        """Parse [tool_loading] section from raw TOML dict."""
        raw_tl = raw.get("tool_loading", {})
        if not isinstance(raw_tl, dict):
            raise DSLSyntaxError("[tool_loading] must be a table")
        tl_strategy = raw_tl.get("strategy", "lazy")
        tl_preload = raw_tl.get("preload_agents", [])
        if not isinstance(tl_preload, list):
            raise DSLSyntaxError("[tool_loading].preload_agents must be a list")
        try:
            return DSLToolLoading(
                strategy=tl_strategy,
                preload_agents=list(tl_preload),
            )
        except ValueError as exc:
            raise DSLSyntaxError(f"[tool_loading]: {exc}") from exc

    @staticmethod
    def _detect_cycles(task_map: dict[str, DSLTask]) -> list[list[str]]:
        """Detect cycles in the task dependency graph."""
        from agent_nexus.platform.utils import detect_cycles_dfs

        return detect_cycles_dfs(
            nodes=task_map.keys(),
            get_deps=lambda name: [dep for dep in task_map[name].blocked_by if dep in task_map],
        )
