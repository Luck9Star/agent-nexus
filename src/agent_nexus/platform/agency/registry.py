"""Local expert registry with id and capability-tag indexing."""

import logging
import threading
from typing import Any


class ExpertRegistry:
    """In-memory registry for expert profiles, indexed by id and capability tags."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_capability: dict[str, list[str]] = {}  # capability -> list of profile ids
        # profile_id -> capabilities for targeted cleanup
        self._caps_by_id: dict[str, list[str]] = {}

    def add(self, profile_id: str, profile: dict[str, Any], capabilities: list[str]) -> None:
        """Register a profile indexed by id and each capability tag."""
        with self._lock:
            if profile_id in self._by_id:
                # Targeted cleanup: only remove from capability lists the old profile was in
                for old_cap in self._caps_by_id.get(profile_id, []):
                    cap_list = self._by_capability.get(old_cap)
                    if cap_list and profile_id in cap_list:
                        cap_list.remove(profile_id)
                logging.warning("Overwriting existing profile: %s", profile_id)

            self._by_id[profile_id] = profile
            self._caps_by_id[profile_id] = capabilities

            for cap in capabilities:
                if cap not in self._by_capability:
                    self._by_capability[cap] = []
                if profile_id not in self._by_capability[cap]:
                    self._by_capability[cap].append(profile_id)

    def get(self, profile_id: str) -> dict[str, Any] | None:
        """Retrieve a profile by id, or None if not found."""
        with self._lock:
            return self._by_id.get(profile_id)

    def search_by_capability(self, capabilities: list[str]) -> list[dict[str, Any]]:
        """Return profiles that match ANY of the given capability tags."""
        with self._lock:
            result_ids: set[str] = set()
            for cap in capabilities:
                ids = self._by_capability.get(cap, [])
                result_ids.update(ids)

            return [self._by_id[pid] for pid in sorted(result_ids) if pid in self._by_id]

    def list_all(self) -> list[str]:
        """Return a sorted list of all registered profile ids."""
        with self._lock:
            return sorted(self._by_id.keys())

    def remove(self, profile_id: str) -> bool:
        """Remove a profile by id. Returns True if found and removed."""
        with self._lock:
            if profile_id not in self._by_id:
                return False
            for cap in self._caps_by_id.pop(profile_id, []):
                cap_list = self._by_capability.get(cap)
                if cap_list and profile_id in cap_list:
                    cap_list.remove(profile_id)
            del self._by_id[profile_id]
            return True

    def clear(self) -> None:
        """Remove all registered profiles."""
        with self._lock:
            self._by_id.clear()
            self._by_capability.clear()
            self._caps_by_id.clear()
