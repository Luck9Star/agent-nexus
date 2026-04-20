"""Shared platform-level utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    """Return current UTC time as ISO format string."""
    return datetime.now(timezone.utc).isoformat()


def make_error_result(error: str, error_type: str) -> dict[str, Any]:
    """Construct a standardized error result dict.

    Used by McpToolAdapter, PlatformRouter, and any other component
    that needs to return a uniform error payload.
    """
    return {"output": "", "success": False, "error": error, "error_type": error_type}
