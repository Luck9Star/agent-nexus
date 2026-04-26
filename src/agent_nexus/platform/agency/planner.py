"""Dynamic Composite Planner — generates temporary DAGs from specialist subtasks."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubtaskDef:
    """A single specialist subtask to be included in the DAG."""

    id: str
    goal: str
    needed_capabilities: list[str]
    output_contract: str
    assigned_agent: str


@dataclass
class DAGTask:
    """A single task entry in the generated TOML DAG."""

    id: str
    agent: str
    output: str
    blocked_by: list[str] = field(default_factory=list)
    task_type: str = "specialist"
    """Task role: ``"specialist"`` for expert tasks, ``"synthetic"`` for integrate/validate."""


@dataclass
class CompositionDAG:
    """A complete composition DAG with metadata and tasks."""

    name: str
    max_parallel: int
    tasks: list[DAGTask] = field(default_factory=list)

    @property
    def specialist_tasks(self) -> list[DAGTask]:
        """Return tasks that are specialist tasks (not synthetic integrate/validate)."""
        return [t for t in self.tasks if t.task_type == "specialist"]


@dataclass
class PlannerInput:
    """Convenience wrapper for planner inputs."""

    subtasks: list[SubtaskDef]
    composition_name: str
    max_parallel: int = 3


_RESERVED_IDS = {"integrate", "validate"}


class DynamicCompositePlanner:
    """Generates a temporary CompositionDAG from specialist subtasks.

    The planner always appends two fixed tasks:
    - ``integrate`` (agent ``nexus.integrator``) — blocked by ALL specialist tasks
    - ``validate`` (agent ``nexus.qa-gate``) — blocked by ``integrate``
    """

    def plan(
        self,
        subtasks: list[SubtaskDef],
        composition_name: str,
        max_parallel: int = 3,
    ) -> CompositionDAG:
        """Build a CompositionDAG from the given subtasks.

        Raises:
            ValueError: If subtasks is empty or contains duplicate IDs.
        """
        if not subtasks:
            raise ValueError("Need at least one subtask to plan a composition")

        # Validate IDs don't contain TOML-special characters
        _INVALID_ID_CHARS = {'"', "#", "\n", "\t", "["}
        for st in subtasks:
            for ch in _INVALID_ID_CHARS:
                if ch in st.id:
                    raise ValueError(f"Subtask id '{st.id}' contains invalid character: {ch!r}")

        # Validate composition_name for TOML-special characters
        for ch in _INVALID_ID_CHARS:
            if ch in composition_name:
                raise ValueError(f"composition_name contains invalid character: {ch!r}")

        # Validate no duplicate IDs
        seen_ids: set[str] = set()
        for st in subtasks:
            if st.id in seen_ids:
                raise ValueError(f"Duplicate subtask id: '{st.id}'")
            seen_ids.add(st.id)

        # Validate no reserved IDs
        for st in subtasks:
            if st.id in _RESERVED_IDS:
                raise ValueError(
                    f"Subtask id '{st.id}' is reserved for synthetic tasks"
                )

        # Floor at 1, but allow values higher than specialist count
        effective_parallel = max(1, max_parallel)

        # Build specialist DAG tasks (no blocked_by)
        dag_tasks: list[DAGTask] = []
        for st in subtasks:
            dag_tasks.append(
                DAGTask(
                    id=st.id,
                    agent=st.assigned_agent,
                    output=st.output_contract,
                    blocked_by=[],
                )
            )

        specialist_ids = [st.id for st in subtasks]

        # Append integrator task — blocked by ALL specialist tasks
        dag_tasks.append(
            DAGTask(
                id="integrate",
                agent="nexus.integrator",
                output="final_plan",
                blocked_by=specialist_ids,
                task_type="synthetic",
            )
        )

        # Append validate task — blocked by integrate only
        dag_tasks.append(
            DAGTask(
                id="validate",
                agent="nexus.qa-gate",
                output="validated_plan",
                blocked_by=["integrate"],
                task_type="synthetic",
            )
        )

        return CompositionDAG(
            name=composition_name,
            max_parallel=effective_parallel,
            tasks=dag_tasks,
        )

    def resolve_dependencies(
        self,
        subtasks: list[SubtaskDef],
        composition_name: str,
        max_parallel: int = 3,
    ) -> CompositionDAG:
        """Build a DAG with dynamic inter-task dependencies based on capability overlap.

        Unlike ``plan`` (which places all specialist tasks in parallel), this
        method analyses each subtask's ``needed_capabilities`` and creates
        blocked_by edges when a later subtask depends on a capability produced
        by an earlier one.

        Dependency rule:
            Task B depends on Task A if B's needed_capabilities overlap with
            A's needed_capabilities and A appears earlier in the list.

        Raises:
            ValueError: If subtasks is empty or contains duplicate IDs.
        """
        if not subtasks:
            raise ValueError("Need at least one subtask to plan a composition")

        # Validate IDs
        _INVALID_ID_CHARS = {'"', "#", "\n", "\t", "["}
        seen_ids: set[str] = set()
        for st in subtasks:
            for ch in _INVALID_ID_CHARS:
                if ch in st.id:
                    raise ValueError(f"Subtask id '{st.id}' contains invalid character: {ch!r}")
            if st.id in seen_ids:
                raise ValueError(f"Duplicate subtask id: '{st.id}'")
            seen_ids.add(st.id)

        # Validate no reserved IDs
        for st in subtasks:
            if st.id in _RESERVED_IDS:
                raise ValueError(
                    f"Subtask id '{st.id}' is reserved for synthetic tasks"
                )

        for ch in _INVALID_ID_CHARS:
            if ch in composition_name:
                raise ValueError(f"composition_name contains invalid character: {ch!r}")

        effective_parallel = max(1, max_parallel)

        # Build capability -> producing task mapping (first task that declares it wins)
        cap_producer: dict[str, str] = {}
        for st in subtasks:
            for cap in st.needed_capabilities:
                if cap not in cap_producer:
                    cap_producer[cap] = st.id

        # Build specialist tasks with dynamic blocked_by
        dag_tasks: list[DAGTask] = []
        for st in subtasks:
            blocked_by: list[str] = []
            for cap in st.needed_capabilities:
                producer = cap_producer.get(cap)
                if producer and producer != st.id and producer not in blocked_by:
                    blocked_by.append(producer)

            dag_tasks.append(
                DAGTask(
                    id=st.id,
                    agent=st.assigned_agent,
                    output=st.output_contract,
                    blocked_by=blocked_by,
                )
            )

        specialist_ids = [st.id for st in subtasks]

        # Append integrator — blocked by ALL specialist tasks
        dag_tasks.append(
            DAGTask(
                id="integrate",
                agent="nexus.integrator",
                output="final_plan",
                blocked_by=specialist_ids,
                task_type="synthetic",
            )
        )

        # Append validate — blocked by integrate
        dag_tasks.append(
            DAGTask(
                id="validate",
                agent="nexus.qa-gate",
                output="validated_plan",
                blocked_by=["integrate"],
                task_type="synthetic",
            )
        )

        return CompositionDAG(
            name=composition_name,
            max_parallel=effective_parallel,
            tasks=dag_tasks,
        )


def generate_toml(dag: CompositionDAG) -> str:
    """Serialize a CompositionDAG to TOML string.

    Output format matches doc §7.3:
      [composition]
      name = "..."
      max_parallel = N

      [[tasks]]
      id = "..."
      agent = "..."
      output = "..."
      blocked_by = ["..."]
    """
    # Validate all string fields for TOML-special characters
    _INVALID_CHARS = {'"', "#", "\n", "\t", "\r", "\\"}
    for ch in _INVALID_CHARS:
        if ch in dag.name:
            raise ValueError(f"DAG name contains invalid character: {ch!r}")

    for task in dag.tasks:
        for field_name, value in [("id", task.id), ("agent", task.agent), ("output", task.output)]:
            for ch in _INVALID_CHARS:
                if ch in value:
                    raise ValueError(
                        f"Task {field_name} '{value}' contains invalid character: {ch!r}"
                    )
        for b in task.blocked_by:
            for ch in _INVALID_CHARS:
                if ch in b:
                    raise ValueError(
                        f"Task blocked_by '{b}' contains invalid character: {ch!r}"
                    )

    lines: list[str] = []
    lines.append("[composition]")
    lines.append(f'name = "{dag.name}"')
    lines.append(f"max_parallel = {dag.max_parallel}")

    for task in dag.tasks:
        lines.append("")
        lines.append("[[tasks]]")
        lines.append(f'id = "{task.id}"')
        lines.append(f'agent = "{task.agent}"')
        lines.append(f'output = "{task.output}"')
        if task.blocked_by:
            items = ", ".join(f'"{b}"' for b in task.blocked_by)
            lines.append(f"blocked_by = [{items}]")

    lines.append("")  # trailing newline
    return "\n".join(lines)
