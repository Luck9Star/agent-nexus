"""Shared utilities for Agent Nexus data models.

Centralizes patterns used across all model modules to enforce DRY.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


def _utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware).

    Used as ``Field(default_factory=...)`` for timestamp fields.
    Centralized here so all models share a single source of truth.
    """
    return datetime.now(UTC)


class FrozenModel(BaseModel):
    """Base class for immutable Pydantic models.

    All value-object models in agent-nexus are frozen (hashable, no mutation).
    Inherit from this instead of repeating ``model_config = ConfigDict(frozen=True)``.
    """

    model_config = ConfigDict(frozen=True)


# Sentinel for missing values in runtime — shared across executor, runtime, describer.
# Uses a singleton class so that identity comparison survives pickle round-trips.
class _MissingSentinel:
    """Singleton sentinel for missing values — preserves identity across pickle."""

    _instance: _MissingSentinel | None = None

    def __new__(cls) -> _MissingSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<MISSING>"

    def __reduce__(self) -> tuple:
        # Return (_restore_missing,) so unpickling calls the module-level
        # helper and always gets back the same singleton.
        return (_restore_missing, ())


def _restore_missing() -> _MissingSentinel:
    """Pickle restore helper — returns the _MISSING singleton."""
    return _MISSING


_MISSING = _MissingSentinel()
