"""Context Provider — dynamic context injection for the agency pipeline.

Provides a Protocol-based extension point so pipeline stages can receive
runtime-generated context (e.g. planner summaries, expert lists, reflection
feedback) without hard-coding knowledge of each other.

Usage::

    registry = ProviderRegistry()
    registry.register("task_summary", TaskSummaryProvider())

    # Later, in any stage:
    provider = registry.get("task_summary")
    if provider:
        ctx = provider.get_context()
        if ctx:
            prompt += f"\\n## {provider.title}\\n{ctx}"
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ContextProvider(Protocol):
    """Dynamic context injection protocol.

    Implementations provide runtime-generated context that gets injected
    into LLM prompts.  Each call to ``get_context()`` may return different
    content.
    """

    title: str

    def get_context(self) -> str:
        """Return current context content.  Called dynamically each time."""
        ...


# ---------------------------------------------------------------------------
# Built-in providers
# ---------------------------------------------------------------------------


class TaskSummaryProvider:
    """Injects Planner output summary into subsequent stages."""

    title = "已规划任务摘要"

    def __init__(self) -> None:
        self._tasks: list[str] = []

    def update(self, tasks: list[str]) -> None:
        self._tasks = tasks

    def get_context(self) -> str:
        if not self._tasks:
            return ""
        return "\n".join(f"- {t}" for t in self._tasks)


class ExpertListProvider:
    """Injects available expert list into Planner context."""

    title = "可用专家"

    def __init__(self, experts: list[dict[str, Any]]) -> None:
        self._experts = experts

    def get_context(self) -> str:
        if not self._experts:
            return ""
        lines: list[str] = []
        for e in self._experts:
            name = e.get("name", e.get("id", "unknown"))
            caps = e.get("capabilities", [])
            lines.append(f"- {name}: {', '.join(caps) if caps else 'general'}")
        return "\n".join(lines)


class ReflectionFeedbackProvider:
    """Injects Reflector feedback into next Executor round (P4 integration)."""

    title = "改进建议"

    def __init__(self) -> None:
        self._feedback: str = ""

    def update(self, feedback: str) -> None:
        self._feedback = feedback

    def get_context(self) -> str:
        return self._feedback


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """Pipeline-level registry for Context Providers.

    Providers are shared across all pipeline stages.  Each provider has an
    associated priority for use with ``StructuredPrompt`` (P2).
    """

    def __init__(self) -> None:
        self._providers: dict[str, ContextProvider] = {}
        self._priorities: dict[str, int] = {}

    def register(self, name: str, provider: ContextProvider, priority: int = 7) -> None:
        """Register a provider with a priority (default 7 = low)."""
        self._providers[name] = provider
        self._priorities[name] = priority

    def unregister(self, name: str) -> None:
        """Remove a provider."""
        self._providers.pop(name, None)
        self._priorities.pop(name, None)

    @property
    def providers(self) -> dict[str, ContextProvider]:
        """Read-only access to registered providers."""
        return dict(self._providers)

    def get_priority(self, name: str) -> int:
        """Get priority for a provider (default 7)."""
        return self._priorities.get(name, 7)

    def get(self, name: str) -> ContextProvider | None:
        """Get a specific provider by name."""
        return self._providers.get(name)
