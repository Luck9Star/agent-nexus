"""Shared platform-level utilities."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Agent name pattern: starts with alphanumeric, then alphanumeric/hyphen/underscore
AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def agent_name_to_package(agent_name: str) -> str:
    """Convert agent name to Python package directory name.

    Examples: ``code-reviewer`` -> ``agent_code_reviewer``,
    ``test-suite-generator`` -> ``agent_test_suite_generator``.
    """
    return "agent_" + agent_name.replace("-", "_")


def to_class_name(agent_name: str) -> str:
    """Convert agent name to PascalCase class name (without Agent suffix).

    Examples: ``code-reviewer`` -> ``CodeReviewer``,
    ``test-suite-generator`` -> ``TestSuiteGenerator``.
    """
    return "".join(part.capitalize() for part in agent_name.split("-"))


def atomic_write(
    path: Path, content: str, *, prefix: str = ".write-", suffix: str = ".tmp"
) -> None:
    """Write *content* to *path* atomically via temp file + ``os.replace``.

    Prevents corrupted files if the process crashes mid-write.
    """
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=prefix,
        suffix=suffix,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def now_iso() -> str:
    """Return current UTC time as ISO format string."""
    return datetime.now(UTC).isoformat()


# Error types indicating agent process death (IPC/connection failures).
# Shared between Router (producer) and Gateway (consumer).
IPC_FATAL_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "IPCConnectionError",
        "IPCTimeoutError",
        "IPCError",
        "BrokenPipeError",
        "ConnectionResetError",
        "ProcessNotAliveError",
    }
)


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


# ---------------------------------------------------------------------------
# SQLite connection management (shared by TaskGraph, EvolutionStore, etc.)
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


@contextmanager
def sqlite_connection(
    db_path: str | Path,
    *,
    immediate: bool = False,
    persistent_conn: sqlite3.Connection | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a SQLite connection with standard setup.

    Handles both ``:memory:`` and file-based databases:

    * **In-memory**: reuses *persistent_conn* (supplied by the caller, who
      owns its lifecycle).  ``sqlite3.connect(":memory:")`` creates a fresh
      database each time, so sharing is mandatory.
    * **File-based**: opens a fresh connection per invocation and closes it
      on exit.

    Standard pragmas (``foreign_keys=ON``) are applied automatically.
    ``journal_mode=WAL`` should be set separately during schema init because
    it is a persistent database-level setting, not a per-connection one.

    Transaction semantics:

    * If *immediate* is ``True``, ``BEGIN IMMEDIATE`` is issued before
      yielding — this serialises concurrent writers under WAL mode and
    prevents TOCTOU races.
    * On normal exit the connection is committed.
    * On exception the connection is rolled back.

    Args:
        db_path: ``":memory:"`` or a file-system path.
        immediate: Issue ``BEGIN IMMEDIATE`` before yielding.
        persistent_conn: For ``:memory:`` databases — the long-lived
            connection to reuse.  Ignored for file-based databases.
    """
    db_str = str(db_path)
    is_memory = db_str == ":memory:"

    if is_memory:
        # In-memory DB: reuse the persistent connection supplied by the caller.
        if persistent_conn is None:
            raise ValueError("persistent_conn is required for :memory: databases")
        conn = persistent_conn
        if immediate and not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            _logger.debug("DB commit failed in memory-DB context", exc_info=True)
            conn.rollback()
            raise
        return  # EARLY RETURN — don't fall through to file-based cleanup

    # File-based DB: open a fresh connection per operation.
    conn = sqlite3.connect(db_str)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        _logger.debug("DB commit failed in file-DB context", exc_info=True)
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_composition_path(caller_file: str) -> Path | None:
    """Resolve composition.toml using platform protocol.

    Resolution order:
    1. ``AGENT_DIR`` env var (platform-injected install root).
    2. ``<caller_dir>/composition.toml`` (bundled in wheel).
    3. ``<caller_parent>/composition.toml`` (dev mode).

    Args:
        caller_file: The ``__file__`` of the calling coordinator module.
    """
    import os

    caller_dir = Path(caller_file).parent
    agent_dir = os.environ.get("AGENT_DIR")
    if agent_dir:
        candidate = Path(agent_dir) / "composition.toml"
        if candidate.exists():
            return candidate
    pkg_path = caller_dir / "composition.toml"
    if pkg_path.exists():
        return pkg_path
    dev_path = caller_dir.parent / "composition.toml"
    if dev_path.exists():
        return dev_path
    return None
