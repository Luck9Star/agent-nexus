"""Unit tests for ModelDBClient: full-index strategy, caching, and error handling.

Tests cover fetch_model (exact, prefix-strip, fuzzy), fetch_search, index
caching (memory + disk), TTL expiry, and error resilience.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from agent_nexus.platform.config.model_db import ModelDBClient

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_MOCK_API_JSON: dict = {
    "anthropic": {
        "models": {
            "claude-sonnet-4": {
                "id": "claude-sonnet-4",
                "name": "Claude Sonnet 4",
                "limit": {"output": 8192, "context": 200000},
                "modalities": ["text", "image"],
                "tool_call": True,
                "temperature": True,
                "cost": {"input": 3.0, "output": 15.0},
            },
            "claude-haiku-4": {
                "id": "claude-haiku-4",
                "name": "Claude Haiku 4",
                "limit": {"output": 8192, "context": 200000},
                "modalities": ["text"],
                "tool_call": True,
                "temperature": True,
                "cost": {"input": 0.8, "output": 4.0},
            },
        },
    },
    "zai-org": {
        "models": {
            "zai-org/glm-5": {
                "id": "zai-org/glm-5",
                "name": "GLM-5",
                "limit": {"output": 4096, "context": 128000},
                "modalities": ["text"],
                "tool_call": True,
                "temperature": True,
                "cost": {"input": 1.0, "output": 2.0},
            },
        },
    },
}


def _patch_do_get(data: dict | None):
    """Patch ``_do_get`` to return *data* (simulating a successful api.json fetch)."""
    return patch.object(ModelDBClient, "_do_get", return_value=data)


def _patch_do_get_none():
    """Patch ``_do_get`` to return None (simulating a failed fetch)."""
    return patch.object(ModelDBClient, "_do_get", return_value=None)


# ============================================================================
# fetch_model
# ============================================================================


class TestFetchModel:
    """Tests for ModelDBClient.fetch_model()."""

    def test_fetch_model_exact_match(self) -> None:
        """Exact model ID lookup returns transformed dict."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            result = client.fetch_model("claude-sonnet-4")

        assert result is not None
        assert result["id"] == "claude-sonnet-4"
        assert result["provider"] == "anthropic"
        assert result["max_output_tokens"] == 8192
        assert result["context_window"] == 200000
        assert result["supports_vision"] is True
        assert result["supports_tool_use"] is True
        assert result["cost_input"] == 3.0

    def test_fetch_model_case_insensitive(self) -> None:
        """Model lookup is case-insensitive."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            result = client.fetch_model("Claude-Sonnet-4")

        assert result is not None
        assert result["id"] == "claude-sonnet-4"

    def test_fetch_model_prefix_strip(self) -> None:
        """'provider/model' resolves by stripping the provider prefix."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            result = client.fetch_model("zai-org/glm-5")

        assert result is not None
        assert result["id"] == "zai-org/glm-5"

    def test_fetch_model_fuzzy_substring(self) -> None:
        """Substring match finds models even with partial ID."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            result = client.fetch_model("glm-5")

        assert result is not None
        assert "glm" in result["id"].lower()

    def test_fetch_model_not_found(self) -> None:
        """Unknown model ID returns None."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            result = client.fetch_model("nonexistent-model-xyz")

        assert result is None

    def test_fetch_model_network_failure(self) -> None:
        """Network failure (None from _do_get) returns None gracefully."""
        with _patch_do_get_none():
            client = ModelDBClient()
            result = client.fetch_model("claude-sonnet-4")

        assert result is None


# ============================================================================
# Index caching
# ============================================================================


class TestIndexCache:
    """Tests for in-memory index caching behavior."""

    def test_cache_hit_no_second_fetch(self) -> None:
        """Second lookup uses in-memory index — no second HTTP call."""
        call_count = 0

        def _mock_do_get(url: str, **kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            return _MOCK_API_JSON

        with patch.object(ModelDBClient, "_do_get", side_effect=_mock_do_get):
            client = ModelDBClient()
            result1 = client.fetch_model("claude-sonnet-4")
            result2 = client.fetch_model("claude-sonnet-4")

        assert result1 is not None
        assert result2 is not None
        assert result1["id"] == result2["id"]
        assert call_count == 1

    def test_cache_expired_re_fetches(self) -> None:
        """After index TTL expires, client re-fetches."""
        call_count = 0

        def _mock_do_get(url: str, **kw: object) -> dict:
            nonlocal call_count
            call_count += 1
            return _MOCK_API_JSON

        with patch.object(ModelDBClient, "_do_get", side_effect=_mock_do_get):
            client = ModelDBClient(index_ttl=1)
            client.fetch_model("claude-sonnet-4")
            # Expire the index
            client._index_fetched_at = time.time() - 2
            client.fetch_model("claude-sonnet-4")

        assert call_count == 2

    def test_clear_cache_removes_disk_file(self, tmp_path: Path) -> None:
        """clear_cache() deletes the _index.json disk cache."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient(disk_cache_path=tmp_path)
            client.fetch_model("claude-sonnet-4")

            cache_file = tmp_path / "_index.json"
            assert cache_file.is_file()

            client.clear_cache()
            assert not cache_file.is_file()


# ============================================================================
# fetch_search
# ============================================================================


class TestFetchSearch:
    """Tests for ModelDBClient.fetch_search()."""

    def test_fetch_search_finds_models(self) -> None:
        """Search by substring returns matching models."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            results = client.fetch_search("claude")

        ids = [r["id"] for r in results]
        assert "claude-sonnet-4" in ids
        assert "claude-haiku-4" in ids

    def test_fetch_search_no_match(self) -> None:
        """Search with no matches returns empty list."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            results = client.fetch_search("nonexistent-xyz")

        assert results == []

    def test_fetch_search_empty_query(self) -> None:
        """Empty query returns empty list without fetching."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            results = client.fetch_search("")

        assert results == []

    def test_fetch_search_network_failure(self) -> None:
        """Network failure returns empty list."""
        with _patch_do_get_none():
            client = ModelDBClient()
            results = client.fetch_search("claude")

        assert results == []


# ============================================================================
# Disk cache
# ============================================================================


class TestDiskCache:
    """Tests for disk-based index caching."""

    def test_disk_cache_writes_on_fetch(self, tmp_path: Path) -> None:
        """After index fetch, _index.json is written to disk."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient(disk_cache_path=tmp_path)
            client.fetch_model("claude-sonnet-4")

        cache_file = tmp_path / "_index.json"
        assert cache_file.is_file()

        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "fetched_at" in payload
        assert "index" in payload
        assert "claude-sonnet-4" in payload["index"]

    def test_disk_cache_read_on_miss(self, tmp_path: Path) -> None:
        """When in-memory index is empty but disk cache is fresh, load from disk."""
        # Write a disk cache with the index
        cache_file = tmp_path / "_index.json"
        index_data = {
            "claude-sonnet-4": {
                "id": "claude-sonnet-4",
                "provider": "anthropic",
                "name": "Claude Sonnet 4",
                "max_output_tokens": 8192,
                "context_window": 200000,
                "supports_vision": True,
                "supports_tool_use": True,
                "supports_temperature": True,
                "temperature_min": 0.0,
                "temperature_max": 2.0,
                "knowledge_cutoff": "",
                "cost_input": 3.0,
                "cost_output": 15.0,
            },
        }
        payload = {"fetched_at": time.time(), "index": index_data}
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

        # No HTTP mock needed — should load from disk
        client = ModelDBClient(disk_cache_path=tmp_path)
        result = client.fetch_model("claude-sonnet-4")

        assert result is not None
        assert result["id"] == "claude-sonnet-4"
        assert result["max_output_tokens"] == 8192

    def test_disk_cache_expired_re_fetches(self, tmp_path: Path) -> None:
        """Stale disk cache triggers a new remote fetch."""
        # Write a stale disk cache
        cache_file = tmp_path / "_index.json"
        stale_index = {"old-model": {"id": "old-model", "provider": "test"}}
        payload = {"fetched_at": time.time() - 100_000, "index": stale_index}
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient(disk_cache_path=tmp_path, index_ttl=86_400)
            result = client.fetch_model("claude-sonnet-4")

        assert result is not None
        assert result["id"] == "claude-sonnet-4"


# ============================================================================
# list_providers
# ============================================================================


class TestListProviders:
    """Tests for ModelDBClient.list_providers()."""

    def test_list_providers(self) -> None:
        """Returns sorted list of provider IDs from the index."""
        with _patch_do_get(_MOCK_API_JSON):
            client = ModelDBClient()
            providers = client.list_providers()

        assert providers == ["anthropic", "zai-org"]
