"""CLIBackendRegistry — backend discovery, registration, and health check."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend

logger = logging.getLogger(__name__)


class CLIBackendRegistry:
    """Registry of available CLI backends with health check support."""

    def __init__(self) -> None:
        self._backends: dict[str, GenericCLIBackend] = {}

    def register(self, name: str, backend: GenericCLIBackend) -> None:
        self._backends[name] = backend
        logger.debug("Registered CLI backend: %s -> %s", name, backend.name)

    def get(self, name: str) -> GenericCLIBackend:
        if name not in self._backends:
            raise KeyError(
                f"CLI backend '{name}' not registered. Available: {list(self._backends.keys())}"
            )
        return self._backends[name]

    def available_backends(self) -> list[GenericCLIBackend]:
        return [b for b in self._backends.values() if b.is_available()]

    def all_backends(self) -> dict[str, GenericCLIBackend]:
        return dict(self._backends)

    def refresh_all(self) -> None:
        for backend in self._backends.values():
            backend.refresh_availability()

    def __len__(self) -> int:
        return len(self._backends)
