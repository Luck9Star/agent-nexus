"""SourceManager: manage sources.yaml and resolve agent locations.

Three source types are supported:
- **official**: built-in monorepo with ``index.yaml`` + ``packages/`` directory
- **private**: user/team repos registered in ``sources.yaml``
- **direct**: ephemeral ``--git-url`` CLI parameter

Sources are searched by priority (official first, then by order in the file).
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from agent_nexus.models.distribution import IndexEntry, SourceEntry

logger = logging.getLogger(__name__)

# Default official source (used when sources.yaml does not exist)
_OFFICIAL_SOURCE = SourceEntry(
    name="official",
    type="git",
    url="https://github.com/anthropics/agent-nexus-packages.git",
    branch="main",
)


class SourceManager:
    """Manage package sources (``sources.yaml``) and resolve agent locations.

    Parameters
    ----------
    sources_path:
        Absolute path to ``sources.yaml`` (typically
        ``~/.agent-nexus/sources.yaml``).
    """

    def __init__(self, sources_path: Path) -> None:
        self._path = sources_path
        self._sources: list[SourceEntry] = []
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_source(self, entry: SourceEntry) -> None:
        """Add or update a source entry and save."""
        # Remove existing entry with same name
        self._sources = [s for s in self._sources if s.name != entry.name]
        self._sources.append(entry)
        self.save()
        logger.info("Source added/updated: %s (%s)", entry.name, entry.url)

    def remove_source(self, name: str) -> bool:
        """Remove a source by *name*.

        Returns ``True`` if the source existed (and was removed).
        """
        before = len(self._sources)
        self._sources = [s for s in self._sources if s.name != name]
        if len(self._sources) < before:
            self.save()
            logger.info("Source removed: %s", name)
            return True
        return False

    def list_sources(self) -> list[SourceEntry]:
        """Return all sources sorted by priority (official first)."""
        return sorted(self._sources, key=self._source_priority)

    def save(self) -> None:
        """Persist sources to ``sources.yaml`` atomically.

        Writes to a temporary file first, then uses ``os.replace`` to
        atomically swap it into place.  This avoids corruption if the
        process crashes mid-write.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, Any] = {
            "sources": [
                {
                    "name": s.name,
                    "type": s.type,
                    "url": s.url,
                    "branch": s.branch,
                }
                for s in self._sources
            ],
        }

        content = yaml.dump(payload, default_flow_style=False, allow_unicode=True)

        # Atomic write: tempfile in same directory so os.replace is atomic.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=".sources-",
            suffix=".yaml.tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(self._path))
        except BaseException:
            # Clean up the temp file on any failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.debug("Sources saved to %s", self._path)

    def search_agents(self, query: str) -> list[tuple[SourceEntry, IndexEntry]]:
        """Search all source indexes for agents matching *query*.

        Matches against agent name, description, and tags (case-insensitive).

        Returns a list of ``(source, index_entry)`` tuples for each match.
        """
        results: list[tuple[SourceEntry, IndexEntry]] = []
        for source in self.list_sources():
            index = self._load_source_index(source)
            if index is None:
                continue
            for entry in index:
                searchable = " ".join(
                    [entry.name, entry.description] + entry.tags
                ).lower()
                if query.lower() in searchable:
                    results.append((source, entry))
        return results

    def resolve_agent_source(self, agent_name: str) -> tuple[SourceEntry, str] | None:
        """Find which source contains *agent_name*.

        Returns ``(source_entry, relative_path)`` where *relative_path* is
        the path within the repo (e.g. ``packages/doc-filler``).  Returns
        ``None`` when no source lists the agent in its index.

        Searches sources in priority order (official first).
        """
        for source in self.list_sources():
            index = self._load_source_index(source)
            if index is None:
                continue

            for entry in index:
                if entry.name == agent_name:
                    # Use explicit path override if set, else standard convention
                    relative_path = entry.path or f"packages/{agent_name}"
                    return source, relative_path

        return None

    def get_official_source(self) -> SourceEntry | None:
        """Return the official source entry, or ``None`` if not configured."""
        for source in self._sources:
            if source.name == "official":
                return source
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load ``sources.yaml``.  Create with defaults if absent."""
        if not self._path.exists():
            logger.debug("sources.yaml not found at %s, creating defaults", self._path)
            self._sources = [_OFFICIAL_SOURCE]
            return

        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", self._path, exc)
            self._sources = [_OFFICIAL_SOURCE]
            return

        if not raw or "sources" not in raw:
            logger.warning("sources.yaml empty or missing 'sources' key")
            self._sources = [_OFFICIAL_SOURCE]
            return

        sources_list = raw["sources"]
        if not isinstance(sources_list, list):
            logger.warning("sources.yaml 'sources' key is not a list, using defaults")
            self._sources = [_OFFICIAL_SOURCE]
            return

        entries: list[SourceEntry] = []
        for item in sources_list:
            if not isinstance(item, dict):
                logger.warning("Skipping non-mapping source entry: %r", item)
                continue
            try:
                entry = SourceEntry(
                    name=item["name"],
                    type=item.get("type", "git"),
                    url=item.get("url", ""),
                    branch=item.get("branch", "main"),
                )
                entries.append(entry)
            except Exception as exc:
                logger.warning("Skipping invalid source entry %s: %s", item, exc)

        self._sources = entries if entries else [_OFFICIAL_SOURCE]

    def _get_cache_path(self, source: SourceEntry) -> Path:
        """Compute cache path matching GitInstaller._get_cache_path."""
        digest = hashlib.sha256(source.url.encode()).hexdigest()[:12]
        return self._path.parent / "cache" / "repos" / digest

    def _load_source_index(self, source: SourceEntry) -> list[IndexEntry] | None:
        """Load the ``index.yaml`` for *source* from its local cache.

        Returns ``None`` if the cache does not exist or cannot be parsed.
        The cache directory is ``<config_dir>/cache/repos/<url_hash>/``.
        """
        cache_dir = self._get_cache_path(source)
        index_path = cache_dir / "index.yaml"

        if not index_path.exists():
            logger.debug("Index not found at %s", index_path)
            return None

        try:
            raw = yaml.safe_load(index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse index %s: %s", index_path, exc)
            return None

        if not raw or "agents" not in raw:
            return None

        if not isinstance(raw["agents"], list):
            logger.warning("index.yaml 'agents' key is not a list")
            return None

        from agent_nexus.models.agent import AgentType

        entries: list[IndexEntry] = []
        for item in raw["agents"]:
            if not isinstance(item, dict):
                logger.warning("Skipping non-mapping index entry: %r", item)
                continue
            try:
                entries.append(IndexEntry(
                    name=item["name"],
                    version=item["version"],
                    type=AgentType(item["type"]),
                    description=item.get("description", ""),
                    tags=item.get("tags", []),
                    dependencies=item.get("dependencies", []),
                    path=item.get("path", ""),
                ))
            except Exception as exc:
                logger.warning("Skipping invalid index entry %s: %s", item, exc)

        return entries

    @staticmethod
    def _source_priority(source: SourceEntry) -> int:
        """Sort key: official=0 (highest), then by list position."""
        if source.name == "official":
            return 0
        return 1
