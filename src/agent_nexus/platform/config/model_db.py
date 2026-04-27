"""Optional network layer that fetches model capability info from models.dev.

This is an enrichment layer on top of the built-in ModelCapabilityRegistry.
Failures are silent -- the system falls back to local data when the remote
is unreachable.

Usage::

    from agent_nexus.platform.config.model_db import ModelDBClient

    client = ModelDBClient()
    model = client.fetch_model("claude-sonnet-4")
    results = client.fetch_search("claude sonnet")
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://models.dev"
_DEFAULT_TIMEOUT = 5.0


class ModelDBClient:
    """Lightweight client for the models.dev API with memory + optional disk cache.

    Parameters
    ----------
    cache_ttl:
        Time-to-live in seconds for in-memory cache entries (default 300).
    disk_cache_path:
        Optional directory path for persisting responses as JSON files.
        If ``None``, no disk caching is performed.
    """

    def __init__(
        self,
        cache_ttl: int = 300,
        disk_cache_path: str | Path | None = None,
    ) -> None:
        self._cache_ttl = cache_ttl
        self._disk_cache_path = (
            Path(disk_cache_path).resolve() if disk_cache_path else None
        )
        # Memory cache: normalized model_id -> (timestamp, data)
        self._mem_cache: dict[str, tuple[float, Any]] = {}
        self._http_client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_model(self, model_id: str) -> dict | None:
        """Fetch capability info for a single model by its ID.

        Returns the parsed JSON dict on success, or ``None`` on any failure
        (network error, timeout, non-200 status).
        """
        # Normalise cache key
        key = model_id.strip().lower()

        # Check memory cache
        cached = self._check_mem_cache(key)
        if cached is not None:
            return cached

        # Check disk cache
        if self._disk_cache_path is not None:
            disk_data = self._read_disk_cache(key)
            if disk_data is not None:
                self._mem_cache[key] = (time.time(), disk_data)
                return disk_data

        # Fetch from remote
        data = self._do_get(f"{_BASE_URL}/api/models/{key}")
        if data is not None:
            self._mem_cache[key] = (time.time(), data)
            self._write_disk_cache(key, data)
        return data

    def fetch_search(self, query: str) -> list[dict]:
        """Search models by query string.

        Returns a list of model dicts on success, or an empty list on failure.
        """
        # Cache key for search queries
        key = f"__search__:{query.strip().lower()}"

        cached = self._check_mem_cache(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        if self._disk_cache_path is not None:
            disk_data = self._read_disk_cache(key)
            if disk_data is not None:
                self._mem_cache[key] = (time.time(), disk_data)
                return disk_data  # type: ignore[return-value]

        data = self._do_get(f"{_BASE_URL}/api/search", params={"q": query})
        if data is not None:
            self._mem_cache[key] = (time.time(), data)
            self._write_disk_cache(key, data)
            return data  # type: ignore[return-value]
        return []

    def clear_cache(self) -> None:
        """Clear both memory and disk caches."""
        self._mem_cache.clear()
        if self._disk_cache_path is not None and self._disk_cache_path.is_dir():
            for path in self._disk_cache_path.iterdir():
                if path.suffix == ".json":
                    path.unlink(missing_ok=True)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            self._http_client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_http_client(self) -> httpx.Client:
        """Return a persistent ``httpx.Client``, creating on first use."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.Client(timeout=_DEFAULT_TIMEOUT)
        return self._http_client

    def _do_get(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> Any | None:
        """Perform a GET request and return the parsed JSON.

        Returns ``None`` on any failure -- never raises.
        """
        client = self._get_http_client()
        try:
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "ModelDB request failed: %s (status=%d)",
                    url,
                    resp.status_code,
                )
                return None
            return resp.json()
        except httpx.ConnectError:
            logger.warning("ModelDB connection error: %s", url, exc_info=True)
        except httpx.TimeoutException:
            logger.warning("ModelDB timeout: %s", url, exc_info=True)
        except Exception:
            logger.warning("ModelDB request error: %s", url, exc_info=True)
        return None

    def _check_mem_cache(self, key: str) -> Any | None:
        """Return cached data if present and not expired."""
        entry = self._mem_cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.time() - ts < self._cache_ttl:
            return data
        # Expired -- remove lazily
        del self._mem_cache[key]
        return None

    def _disk_cache_path_for(self, key: str) -> Path | None:
        """Return the file path for a disk cache entry, or ``None`` if disabled."""
        if self._disk_cache_path is None:
            return None
        return self._disk_cache_path / f"{key.replace('/', '_')}.json"

    def _read_disk_cache(self, key: str) -> Any | None:
        """Read and return cached data from disk, or ``None`` on miss/error."""
        path = self._disk_cache_path_for(key)
        if path is None or not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            return json.loads(raw)
        except Exception:
            logger.debug("Failed to read disk cache: %s", path, exc_info=True)
            return None

    def _write_disk_cache(self, key: str, data: Any) -> None:
        """Persist data to disk cache. Failures are silently ignored."""
        if self._disk_cache_path is None:
            return
        path = self._disk_cache_path_for(key)
        if path is None:
            return
        try:
            self._disk_cache_path.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.debug("Failed to write disk cache: %s", path, exc_info=True)
