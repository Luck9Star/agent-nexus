"""SourceManager: manage package sources via config.toml [sources].

Three source types are supported:
- **official**: built-in monorepo with ``index.yaml`` + ``packages/`` directory
- **private**: user/team repos registered in config.toml ``[sources]``
- **direct**: ephemeral ``--git-url`` CLI parameter

Sources are searched by priority (official first, then by order in the file).
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agent_nexus.models.distribution import IndexEntry, SourceEntry

if TYPE_CHECKING:
    from agent_nexus.platform.config.loader import ConfigLoader

logger = logging.getLogger(__name__)

# Default official source (used when sources.yaml does not exist)
_OFFICIAL_SOURCE = SourceEntry(
    name="official",
    type="git",
    url="https://github.com/anthropics/agent-nexus-packages.git",
    branch="main",
)


def _parse_source_entry(item: dict[str, Any]) -> SourceEntry | None:
    """Parse a single source entry dict, returning None on failure."""
    try:
        raw_name = item.get("name")
        if not raw_name:
            raise ValueError("Source entry missing required 'name' field")
        return SourceEntry(
            name=raw_name,
            type=item.get("type", "git"),
            url=item.get("url", ""),
            branch=item.get("branch", "main"),
        )
    except Exception as exc:
        logger.warning("Skipping invalid source entry %s: %s", item, exc)
        return None


def _parse_index_entry(item: dict[str, Any]) -> IndexEntry | None:
    """Parse a single index entry dict, returning None on failure."""
    from agent_nexus.models.agent import AgentType

    try:
        raw_name = item.get("name")
        raw_version = item.get("version")
        raw_type = item.get("type")
        if not raw_name:
            raise ValueError("Index entry missing required 'name' field")
        if not raw_version:
            raise ValueError("Index entry missing required 'version' field")
        if not raw_type:
            raise ValueError("Index entry missing required 'type' field")
        # Parse optional score sub-dict
        score = None
        raw_score = item.get("score")
        if isinstance(raw_score, dict):
            from agent_nexus.models.distribution import AgentScore

            score = AgentScore(
                quality_gate_score=raw_score.get("quality_gate_score"),
                download_count=raw_score.get("download_count", 0),
                average_rating=raw_score.get("average_rating"),
                rating_count=raw_score.get("rating_count", 0),
                last_updated=raw_score.get("last_updated"),
            )

        return IndexEntry(
            name=raw_name,
            version=raw_version,
            type=AgentType(raw_type),
            description=item.get("description", ""),
            tags=item.get("tags", []),
            dependencies=item.get("dependencies", []),
            path=item.get("path", ""),
            capabilities=item.get("capabilities", []),
            category=item.get("category"),
            download_count=item.get("download_count", 0),
            score=score,
        )
    except Exception as exc:
        logger.warning("Skipping invalid index entry %s: %s", item, exc)
        return None


class SourceManager:
    """Manage package sources and resolve agent locations.

    Two construction modes:

    - **Path mode** (deprecated): ``SourceManager(sources_path)`` —
      reads/writes standalone ``sources.yaml``.
    - **Loader mode** (recommended): ``SourceManager.from_loader(loader)`` —
      reads/writes ``[sources]`` section in ``config.toml``.
    """

    def __init__(self, sources_path: Path) -> None:
        self._path = sources_path
        self._loader: ConfigLoader | None = None
        self._sources: list[SourceEntry] = []
        self._loaded = False
        # mtime-based cache to avoid re-parsing unchanged files
        self._cache_mtime: float = 0.0

    @classmethod
    def from_loader(cls, loader: ConfigLoader) -> SourceManager:
        """Create a SourceManager backed by ``[sources]`` in config.toml."""
        mgr = cls.__new__(cls)
        mgr._path = loader.config_dir / "config.toml"
        mgr._loader = loader
        mgr._sources = []
        mgr._loaded = False
        mgr._cache_mtime = 0.0
        return mgr

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Lazily load sources on first use, or when file mtime changes."""
        if self._loader is not None:
            self._load_from_config()
            return

        try:
            current_mtime = os.path.getmtime(self._path)
        except OSError:
            current_mtime = 0.0

        if self._loaded and current_mtime == self._cache_mtime:
            return

        self._load()
        self._loaded = True
        self._cache_mtime = current_mtime

    def add_source(self, entry: SourceEntry) -> None:
        """Add or update a source entry and save."""
        self._ensure_loaded()
        self._sources = [s for s in self._sources if s.name != entry.name]
        self._sources.append(entry)
        self.save()
        logger.info("Source added/updated: %s (%s)", entry.name, entry.url)

    def remove_source(self, name: str) -> bool:
        """Remove a source by *name*.

        Returns ``True`` if the source existed (and was removed).
        """
        self._ensure_loaded()
        before = len(self._sources)
        self._sources = [s for s in self._sources if s.name != name]
        if len(self._sources) < before:
            self.save()
            logger.info("Source removed: %s", name)
            return True
        return False

    def list_sources(self) -> list[SourceEntry]:
        """Return all sources sorted by priority (official first)."""
        self._ensure_loaded()
        return sorted(self._sources, key=self._source_priority)

    def save(self) -> None:
        """Persist sources atomically.

        In loader mode, writes to config.toml ``[sources]``.
        In path mode, writes to sources.yaml.
        """
        self._ensure_loaded()
        if self._loader is not None:
            self._save_to_config()
            return
        self._save_to_yaml()

    def search_agents(
        self,
        query: str,
        *,
        capability: str | None = None,
        category: str | None = None,
        sort_by: str = "relevance",
    ) -> list[tuple[SourceEntry, IndexEntry]]:
        """Search all source indexes for agents matching *query*.

        Matches against agent name, description, and tags (case-insensitive).
        Optional filters:

        - *capability*: only return agents with this capability.
        - *category*: only return agents in this category.
        - *sort_by*: ``"relevance"`` (keyword match order), ``"downloads"``,
          ``"name"``, or ``"rating"``.

        Returns a list of ``(source, index_entry)`` tuples for each match.
        """
        self._ensure_loaded()
        results: list[tuple[SourceEntry, IndexEntry]] = []
        for source in self.list_sources():
            index = self._load_source_index(source)
            if index is None:
                continue
            for entry in index:
                # --- keyword filter (existing) ---
                searchable = " ".join([entry.name, entry.description] + entry.tags).lower()
                if query.lower() not in searchable:
                    continue
                # --- capability filter (new) ---
                if capability is not None and capability not in entry.capabilities:
                    continue
                # --- category filter (new) ---
                if category is not None and entry.category != category:
                    continue
                results.append((source, entry))

        # --- sorting (new) ---
        if sort_by == "downloads":
            results.sort(key=lambda pair: pair[1].download_count, reverse=True)
        elif sort_by == "name":
            results.sort(key=lambda pair: pair[1].name.lower())
        elif sort_by == "rating":
            results.sort(
                key=lambda pair: (
                    pair[1].score.average_rating
                    if pair[1].score and pair[1].score.average_rating is not None
                    else -1.0
                ),
                reverse=True,
            )
        # "relevance" → keep keyword match order (default)

        return results

    def resolve_agent_source(self, agent_name: str) -> tuple[SourceEntry, str] | None:
        """Find which source contains *agent_name*.

        Returns ``(source_entry, relative_path)`` where *relative_path* is
        the path within the repo (e.g. ``packages/doc-filler``).  Returns
        ``None`` when no source lists the agent in its index.

        Searches sources in priority order (official first).
        """
        self._ensure_loaded()
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
        self._ensure_loaded()
        for source in self._sources:
            if source.name == "official":
                return source
        return None

    # ------------------------------------------------------------------
    # Internal: config.toml mode
    # ------------------------------------------------------------------

    def _load_from_config(self) -> None:
        """Load sources from config.toml ``[sources]``."""
        assert self._loader is not None
        try:
            current_mtime = os.path.getmtime(self._path)
        except OSError:
            current_mtime = 0.0

        if self._loaded and current_mtime == self._cache_mtime:
            return

        config = self._loader.load_config()
        self._sources = list(config.sources)
        self._loaded = True
        self._cache_mtime = current_mtime

    def _save_to_config(self) -> None:
        """Write sources to config.toml ``[sources]`` atomically."""
        import toml

        self._path.parent.mkdir(parents=True, exist_ok=True)

        try:
            raw = toml.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, toml.TomlDecodeError):
            raw = {}

        raw["sources"] = [
            {
                "name": s.name,
                "type": s.type,
                "url": s.url,
                "branch": s.branch,
            }
            for s in self._sources
        ]

        content = toml.dumps(raw)

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=".config-",
            suffix=".toml.tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(self._path))
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

        # Invalidate the loader's config cache so it picks up the changes
        if self._loader is not None:
            self._loader.invalidate_cache()

        try:
            self._cache_mtime = os.path.getmtime(self._path)
        except OSError:
            self._cache_mtime = 0.0
        logger.debug("Sources saved to %s [sources]", self._path)

    # ------------------------------------------------------------------
    # Internal: sources.yaml mode (backward compat)
    # ------------------------------------------------------------------

    def _read_raw_yaml(self) -> list[Any] | None:
        """Read and validate ``sources.yaml``.

        Returns the sources list on success, ``None`` on any failure
        (missing file, parse error, invalid structure).
        """
        if not self._path.exists():
            logger.debug("sources.yaml not found at %s, creating defaults", self._path)
            return None
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", self._path, exc)
            return None
        if not raw or "sources" not in raw:
            logger.warning("sources.yaml empty or missing 'sources' key")
            return None
        sources_list = raw["sources"]
        if not isinstance(sources_list, list):
            logger.warning("sources.yaml 'sources' key is not a list, using defaults")
            return None
        return sources_list

    @staticmethod
    def _parse_entries(sources_list: list[Any]) -> list[SourceEntry]:
        """Parse source entries from raw list, skipping invalid items."""
        entries: list[SourceEntry] = []
        for item in sources_list:
            if not isinstance(item, dict):
                logger.warning("Skipping non-mapping source entry: %r", item)
                continue
            entry = _parse_source_entry(item)
            if entry is not None:
                entries.append(entry)
        return entries

    def _load(self) -> None:
        """Load ``sources.yaml``.  Create with defaults if absent."""
        sources_list = self._read_raw_yaml()
        if sources_list is None:
            self._sources = [_OFFICIAL_SOURCE]
            return

        entries = self._parse_entries(sources_list)
        if sources_list and not entries:
            logger.warning("All source entries invalid, using defaults")
            self._sources = [_OFFICIAL_SOURCE]
        else:
            self._sources = entries

    def _save_to_yaml(self) -> None:
        """Persist sources to ``sources.yaml`` atomically."""
        self._ensure_loaded()
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
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
        try:
            self._cache_mtime = os.path.getmtime(self._path)
        except OSError:
            self._cache_mtime = 0.0
        logger.debug("Sources saved to %s", self._path)

    # ------------------------------------------------------------------
    # Internal: index loading
    # ------------------------------------------------------------------

    def _get_cache_path(self, source: SourceEntry) -> Path:
        """Compute cache path matching GitInstaller._get_cache_path."""
        from agent_nexus.platform.utils import cache_path_for_url

        return cache_path_for_url(self._path.parent, source.url)

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

        return self._parse_agent_entries(raw["agents"])

    def _parse_agent_entries(self, raw_agents: list) -> list[IndexEntry]:
        """Parse and validate index entry dicts from raw YAML data."""
        entries: list[IndexEntry] = []
        for item in raw_agents:
            if not isinstance(item, dict):
                logger.warning("Skipping non-mapping index entry: %r", item)
                continue
            entry = _parse_index_entry(item)
            if entry is not None:
                entries.append(entry)
        return entries

    @staticmethod
    def _source_priority(source: SourceEntry) -> int:
        """Sort key: official=0 (highest), then by list position."""
        if source.name == "official":
            return 0
        return 1
