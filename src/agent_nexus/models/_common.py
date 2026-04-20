"""Shared utilities for Agent Nexus data models.

Centralizes patterns used across all model modules to enforce DRY.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def _utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware).

    Used as ``Field(default_factory=...)`` for timestamp fields.
    Centralized here so all models share a single source of truth.
    """
    return datetime.now(timezone.utc)


class FrozenModel(BaseModel):
    """Base class for immutable Pydantic models.

    All value-object models in agent-nexus are frozen (hashable, no mutation).
    Inherit from this instead of repeating ``model_config = ConfigDict(frozen=True)``.
    """

    model_config = ConfigDict(frozen=True)


# Agent name validation regex — shared across installer, supervisor, promotion.
# Valid names: start with alphanumeric, then alphanumeric/dot/hyphen/underscore.
_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

# Sentinel for missing values in runtime — shared across executor, runtime, describer.
_MISSING = object()
