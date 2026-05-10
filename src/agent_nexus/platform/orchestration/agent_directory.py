"""AgentDirectory — In-memory agent registry for A2A discovery.

Provides capability-based and role-based lookup so that MessageBroker and
the Platform Router can route messages to the right agent without knowing
its identity up-front.

Internal storage:
- ``_registry``: ``dict[str, AgentAddress]`` keyed by agent_id
- ``_cap_index``: inverted index ``dict[str, set[str]]`` mapping capability
  to the set of agent_ids that declare it.
"""

from __future__ import annotations

from agent_nexus.models.ipc import AgentAddress


class AgentDirectory:
    """In-memory agent registry for A2A discovery."""

    def __init__(self) -> None:
        self._registry: dict[str, AgentAddress] = {}
        self._cap_index: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, agent_id: str, capabilities: list[str], role: str) -> None:
        """Register (or re-register) an agent with capabilities and role.

        If the agent was previously registered, its old capabilities are
        removed from the inverted index before the new ones are added.
        """
        # Clean up old capability index entries if re-registering
        old = self._registry.get(agent_id)
        if old is not None:
            old_caps = getattr(old, "_capabilities", [])
            for cap in old_caps:
                ids = self._cap_index.get(cap)
                if ids is not None:
                    ids.discard(agent_id)
                    if not ids:
                        self._cap_index.pop(cap, None)

        # Store address with role metadata
        addr = AgentAddress(agent_id=agent_id, role=role)
        # Stash capabilities on the address object for re-registration cleanup.
        # We use a private attribute since AgentAddress is a BaseModel.
        addr._capabilities = capabilities  # type: ignore[attr-defined]
        self._registry[agent_id] = addr

        # Update inverted index
        for cap in capabilities:
            if cap not in self._cap_index:
                self._cap_index[cap] = set()
            self._cap_index[cap].add(agent_id)

    def deregister(self, agent_id: str) -> None:
        """Remove agent from directory.

        Silently ignores unknown agent_ids (idempotent).
        """
        old = self._registry.pop(agent_id, None)
        if old is None:
            return
        old_caps = getattr(old, "_capabilities", [])
        for cap in old_caps:
            ids = self._cap_index.get(cap)
            if ids is not None:
                ids.discard(agent_id)
                if not ids:
                    self._cap_index.pop(cap, None)

    def resolve(self, agent_id: str) -> AgentAddress | None:
        """Look up agent by ID, return AgentAddress or None."""
        return self._registry.get(agent_id)

    def find_by_capability(self, capability: str) -> list[AgentAddress]:
        """Find all agents with given capability."""
        ids = self._cap_index.get(capability)
        if ids is None:
            return []
        return [self._registry[aid] for aid in ids if aid in self._registry]

    def find_by_role(self, role: str) -> list[AgentAddress]:
        """Find all agents with given role."""
        return [addr for addr in self._registry.values() if addr.role == role]

    def list_active(self) -> list[AgentAddress]:
        """Return all registered agents."""
        return list(self._registry.values())
