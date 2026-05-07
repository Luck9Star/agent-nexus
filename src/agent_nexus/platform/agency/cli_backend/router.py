"""CLIRouter — 4-strategy priority routing with fallback chain."""

from __future__ import annotations

import fnmatch
import logging

from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
from agent_nexus.platform.agency.cli_backend.registry import CLIBackendRegistry
from agent_nexus.platform.agency.cli_backend.types import RoutingConfig

logger = logging.getLogger(__name__)


class CLIRouter:
    """Resolve which CLI backend to use for a given request.

    Strategy priority (highest first):
    1. Explicit backend name
    2. Model string pattern matching (model_rules)
    3. Default backend
    """

    def __init__(self, config: RoutingConfig, registry: CLIBackendRegistry) -> None:
        self._config = config
        self._registry = registry

    def resolve(
        self,
        model_string: str | None = None,
        explicit_backend: str | None = None,
    ) -> GenericCLIBackend:
        if explicit_backend:
            return self._registry.get(explicit_backend)

        if model_string:
            for pattern, backend_name in self._config.model_rules.items():
                if fnmatch.fnmatch(model_string, pattern):
                    try:
                        return self._registry.get(backend_name)
                    except KeyError:
                        logger.warning(
                            "Model rule '%s' -> '%s' but backend not registered",
                            pattern,
                            backend_name,
                        )

        return self._registry.get(self._config.default)

    def resolve_with_fallback(
        self,
        model_string: str | None = None,
        explicit_backend: str | None = None,
    ) -> GenericCLIBackend:
        try:
            primary = self.resolve(model_string, explicit_backend)
            if primary.is_available():
                return primary
        except KeyError:
            pass

        if not self._config.fallback_enabled:
            raise RuntimeError(
                "Fallback disabled — primary backend unavailable. "
                "Enable via [cli_routing] fallback_enabled = true"
            )

        for name in self._config.fallback_chain:
            try:
                backend = self._registry.get(name)
                if backend.is_available():
                    logger.info("Fallback: using backend '%s'", name)
                    return backend
            except KeyError:
                logger.warning("Fallback backend '%s' not registered, skipping", name)

        raise RuntimeError(
            f"All backends unavailable. Primary: {explicit_backend or self._config.default}, "
            f"Fallback chain: {self._config.fallback_chain}"
        )
