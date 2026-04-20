"""Shared platform-level utilities."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Agent name pattern: starts with alphanumeric, then alphanumeric/dot/hyphen/underscore
AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def now_iso() -> str:
    """Return current UTC time as ISO format string."""
    return datetime.now(timezone.utc).isoformat()


def make_error_result(error: str, error_type: str) -> dict[str, Any]:
    """Construct a standardized error result dict.

    Used by McpToolAdapter, PlatformRouter, and any other component
    that needs to return a uniform error payload.
    """
    return {"output": "", "success": False, "error": error, "error_type": error_type}


def cache_path_for_url(base_dir: Path, url: str) -> Path:
    """Derive a deterministic cache path from a source URL.

    Uses SHA-256 truncated to 12 hex chars.  Used by both
    GitInstaller and SourceManager -- keeping it in one place
    ensures they stay in sync.
    """
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    return base_dir / "cache" / "repos" / digest


def detect_cycles_dfs(
    nodes: Iterable[str],
    get_deps: Callable[[str], Iterable[str]],
) -> list[list[str]]:
    """DFS cycle detection over a directed graph.

    Args:
        nodes: All node identifiers.
        get_deps: Returns dependencies (successors) for a given node.

    Returns:
        List of cycles, each cycle as a list of node names.
    """
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: list[list[str]] = []

    def _dfs(node: str, path: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            # Found a cycle — extract the cycle portion of the path
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:] + [node])
            return
        visiting.add(node)
        path.append(node)
        for dep in get_deps(node):
            _dfs(dep, path)
        path.pop()
        visiting.discard(node)
        visited.add(node)

    for node in nodes:
        _dfs(node, [])

    return cycles
