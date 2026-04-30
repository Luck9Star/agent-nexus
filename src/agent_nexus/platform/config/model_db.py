"""Optional network layer that fetches model capability info from models.dev.

This is an enrichment layer on top of the built-in ModelCapabilityRegistry.
Failures are silent -- the system falls back to local data when the remote
is unreachable.

Strategy: fetch the full ``api.json`` catalogue (~1.8 MB, 116+ providers)
once, build a flat local index, and cache to disk with a configurable TTL.
Individual model lookups are resolved against the local index -- no per-model
HTTP requests.

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
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_INDEX_TTL = 86_400  # 24 hours


def _transform_model(raw: dict, provider_id: str) -> dict:
    """Transform a models.dev model entry into the format LLMClient expects."""
    limit = raw.get("limit", {}) or {}
    modalities = raw.get("modalities", []) or []
    cost = raw.get("cost", {}) or {}
    return {
        "id": raw.get("id", ""),
        "provider": provider_id,
        "name": raw.get("name", ""),
        "max_output_tokens": limit.get("output", 0),
        "context_window": limit.get("context", 0),
        "supports_vision": "image" in modalities,
        "supports_tool_use": bool(raw.get("tool_call", False)),
        "supports_temperature": bool(raw.get("temperature", False)),
        "temperature_min": 0.0,
        "temperature_max": 2.0,
        "knowledge_cutoff": "",
        "cost_input": cost.get("input", 0),
        "cost_output": cost.get("output", 0),
    }


class ModelDBClient:
    """Client for the models.dev API with full-index + local-cache strategy.

    On first use, fetches ``api.json`` (the complete model catalogue) and
    builds a flat index mapping ``model_id`` → model data.  The index is
    cached to disk and refreshed after *index_ttl* seconds (default 24 h).

    Parameters
    ----------
    index_ttl:
        Seconds before the full index is re-fetched (default 86 400 = 24 h).
    disk_cache_path:
        Optional directory path for persisting the index as a JSON file.
    """

    def __init__(
        self,
        index_ttl: int = _DEFAULT_INDEX_TTL,
        disk_cache_path: str | Path | None = None,
        # Legacy params kept for API compat — ignored
        cache_ttl: int = 0,
    ) -> None:
        self._index_ttl = index_ttl
        self._disk_cache_path = (
            Path(disk_cache_path).resolve() if disk_cache_path else None
        )
        # Flat index: normalized_model_id → transformed dict
        self._model_index: dict[str, dict] = {}
        self._index_fetched_at: float = 0.0
        self._trigram_index: dict[str, set[str]] = {}
        self._http_client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_model(self, model_id: str) -> dict | None:
        """Look up a model by ID in the local index.

        Returns a dict with keys matching what ``LLMClient`` expects
        (``max_output_tokens``, ``context_window``, etc.), or ``None`` if
        the model is not found.
        """
        self._ensure_index()
        key = model_id.strip().lower()

        # Exact match
        result = self._model_index.get(key)
        if result is not None:
            return result

        # Try stripping common prefixes (e.g. "zai-org/glm-4.7" → "glm-4.7")
        if "/" in key:
            short = key.rsplit("/", 1)[-1]
            result = self._model_index.get(short)
            if result is not None:
                return result

        # Fuzzy: trigram-accelerated substring search
        for idx_key in self._trigram_candidates(key):
            val = self._model_index.get(idx_key)
            if val is not None and (key in idx_key or idx_key.endswith(key)):
                logger.info("ModelDB: fuzzy match '%s' → '%s'", key, idx_key)
                return val
        return None

    def fetch_search(self, query: str) -> list[dict]:
        """Search models by query string against the local index.

        Matches against model id and name (case-insensitive substring).
        """
        self._ensure_index()
        q = query.strip().lower()
        if not q:
            return []
        results: list[dict] = []
        for idx_key in self._trigram_candidates(q):
            val = self._model_index.get(idx_key)
            if val is not None and (
                q in val.get("id", "").lower() or q in val.get("name", "").lower()
            ):
                results.append(val)
        return results

    def list_providers(self) -> list[str]:
        """Return the list of provider IDs in the current index."""
        self._ensure_index()
        return sorted({v["provider"] for v in self._model_index.values()})

    def clear_cache(self) -> None:
        """Clear the in-memory index and delete the disk cache file."""
        self._model_index.clear()
        self._trigram_index.clear()
        self._index_fetched_at = 0.0
        if self._disk_cache_path is not None:
            cache_file = self._disk_cache_path / "_index.json"
            cache_file.unlink(missing_ok=True)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            self._http_client.close()

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _ensure_index(self) -> None:
        """Load index from memory/disk/remote as needed."""
        if self._model_index and time.time() - self._index_fetched_at < self._index_ttl:
            return

        # Try disk cache first
        if self._load_disk_index():
            return

        # Fetch from remote
        self._fetch_full_index()

    def _fetch_full_index(self) -> None:
        """Fetch api.json and build the flat model index."""
        data = self._do_get(f"{_BASE_URL}/api.json")
        if data is None:
            return
        self._build_index(data)
        self._save_disk_index()

    def _build_index(self, data: dict[str, Any]) -> None:
        """Parse the full api.json into a flat {normalized_id: transformed} dict."""
        index: dict[str, dict] = {}
        for provider_id, provider in data.items():
            if not isinstance(provider, dict):
                continue
            models = provider.get("models", {})
            if not isinstance(models, dict):
                continue
            for model_id, model_data in models.items():
                if not isinstance(model_data, dict):
                    continue
                transformed = _transform_model(model_data, provider_id)
                # Index by normalized id (lowercase)
                key = model_id.strip().lower()
                index[key] = transformed
                # Also index short name if model_id has a prefix
                if "/" in key:
                    short = key.rsplit("/", 1)[-1]
                    if short not in index:
                        index[short] = transformed
        self._model_index = index
        self._index_fetched_at = time.time()
        self._build_search_index()
        logger.info(
            "ModelDB: index built — %d models from %d providers",
            len(index), len(data),
        )

    def _build_search_index(self) -> None:
        """Build trigram index for fast substring matching."""
        trigrams: dict[str, set[str]] = {}
        for key, val in self._model_index.items():
            for i in range(max(len(key) - 2, 0)):
                trigrams.setdefault(key[i:i + 3], set()).add(key)
            name = val.get("name", "").lower()
            for i in range(max(len(name) - 2, 0)):
                trigrams.setdefault(name[i:i + 3], set()).add(key)
        self._trigram_index = trigrams

    def _trigram_candidates(self, query: str) -> set[str]:
        """Return candidate model keys that likely contain *query*.

        Uses frequency-based scoring instead of strict intersection:
        a candidate only needs to match >= 50% of query trigrams.
        This tolerates partial matches that strict intersection would miss.
        """
        if len(query) < 3:
            exact = self._model_index.get(query)
            return {query} if exact is not None else set()
        query_trigrams = [query[i:i + 3] for i in range(len(query) - 2)]
        candidate_scores: dict[str, int] = {}
        for tri in query_trigrams:
            for key in self._trigram_index.get(tri, set()):
                candidate_scores[key] = candidate_scores.get(key, 0) + 1
        threshold = max(len(query_trigrams) // 2, 1)
        return {k for k, v in candidate_scores.items() if v >= threshold}

    def _load_disk_index(self) -> bool:
        """Load the index from disk cache if fresh enough."""
        if self._disk_cache_path is None:
            return False
        cache_file = self._disk_cache_path / "_index.json"
        if not cache_file.is_file():
            return False
        try:
            raw = cache_file.read_text(encoding="utf-8")
            payload = json.loads(raw)
            fetched_at = payload.get("fetched_at", 0)
            if time.time() - fetched_at > self._index_ttl:
                return False
            self._model_index = payload.get("index", {})
            self._index_fetched_at = fetched_at
            self._build_search_index()
            logger.debug("ModelDB: loaded index from disk (%d models)", len(self._model_index))
            return bool(self._model_index)
        except Exception:
            logger.debug("ModelDB: failed to load disk cache", exc_info=True)
            return False

    def _save_disk_index(self) -> None:
        """Persist the index to disk."""
        if self._disk_cache_path is None:
            return
        cache_file = self._disk_cache_path / "_index.json"
        try:
            self._disk_cache_path.mkdir(parents=True, exist_ok=True)
            payload = {
                "fetched_at": self._index_fetched_at,
                "index": self._model_index,
            }
            cache_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8",
            )
        except Exception:
            logger.debug("ModelDB: failed to save disk cache", exc_info=True)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get_http_client(self) -> httpx.Client:
        """Return a persistent ``httpx.Client``, creating on first use."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.Client(
                timeout=_DEFAULT_TIMEOUT, follow_redirects=True,
            )
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
                logger.debug("ModelDB request failed: %s (status=%d)", url, resp.status_code)
                return None
            ct = resp.headers.get("content-type", "")
            if "json" not in ct and "javascript" not in ct:
                logger.debug("ModelDB non-JSON response: %s (content-type=%s)", url, ct)
                return None
            return resp.json()
        except httpx.ConnectError:
            logger.debug("ModelDB connection error: %s", url, exc_info=True)
        except httpx.TimeoutException:
            logger.debug("ModelDB timeout: %s", url, exc_info=True)
        except Exception:
            logger.warning("ModelDB request error: %s", url, exc_info=True)
        return None
