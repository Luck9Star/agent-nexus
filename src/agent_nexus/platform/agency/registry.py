"""Local expert registry with id and capability-tag indexing."""

import logging
from typing import Any


class ExpertRegistry:
    """In-memory registry for expert profiles, indexed by id and capability tags."""

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_capability: dict[str, list[str]] = {}  # capability -> list of profile ids

    def add(self, profile_id: str, profile: dict[str, Any], capabilities: list[str]) -> None:
        """Register a profile indexed by id and each capability tag."""
        if profile_id in self._by_id:
            # Clean up old capability mappings before overwriting
            for cap_list in self._by_capability.values():
                if profile_id in cap_list:
                    cap_list.remove(profile_id)
            logging.warning("Overwriting existing profile: %s", profile_id)

        self._by_id[profile_id] = profile

        for cap in capabilities:
            if cap not in self._by_capability:
                self._by_capability[cap] = []
            if profile_id not in self._by_capability[cap]:
                self._by_capability[cap].append(profile_id)

    def get(self, profile_id: str) -> dict[str, Any] | None:
        """Retrieve a profile by id, or None if not found."""
        return self._by_id.get(profile_id)

    def search_by_capability(self, capabilities: list[str]) -> list[dict[str, Any]]:
        """Return profiles that match ANY of the given capability tags."""
        result_ids: set[str] = set()
        for cap in capabilities:
            ids = self._by_capability.get(cap, [])
            result_ids.update(ids)

        return [self._by_id[pid] for pid in sorted(result_ids) if pid in self._by_id]

    def list_all(self) -> list[str]:
        """Return a sorted list of all registered profile ids."""
        return sorted(self._by_id.keys())
