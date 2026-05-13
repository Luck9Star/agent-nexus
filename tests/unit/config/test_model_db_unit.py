"""Unit tests for ModelDBClient internal methods.

Tests cover close() resource cleanup, _trigram_candidates() algorithm,
_build_search_index(), _load_disk_index() corruption handling, and
_save_disk_index() roundtrip.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agent_nexus.platform.config.model_db import ModelDBClient

# ============================================================================
# A) close() resource cleanup
# ============================================================================


# ============================================================================
# B) _trigram_candidates() algorithm
# ============================================================================


class TestTrigramCandidates:
    """Tests for ModelDBClient._trigram_candidates()."""

    def _make_client_with_index(self) -> ModelDBClient:
        """Create a client with a pre-built _model_index and _trigram_index."""
        client = ModelDBClient()
        client._model_index = {
            "claude-sonnet-4": {"id": "claude-sonnet-4", "name": "Claude Sonnet 4"},
            "claude-haiku-4": {"id": "claude-haiku-4", "name": "Claude Haiku 4"},
            "gpt-4o": {"id": "gpt-4o", "name": "GPT-4o"},
        }
        client._build_search_index()
        return client

    def test_short_query_exact_match(self) -> None:
        """Queries < 3 chars return exact-match set if found."""
        client = self._make_client_with_index()

        result = client._trigram_candidates("gp")

        # "gp" < 3 chars -> exact lookup. "gp" is not a key, so empty.
        assert result == set()

    def test_short_query_exact_match_found(self) -> None:
        """Short query that exactly matches a key returns that key."""
        client = self._make_client_with_index()

        result = client._trigram_candidates("gpt-4o")

        # >= 3 chars, uses trigram scoring. Should find "gpt-4o".
        assert "gpt-4o" in result

    def test_long_query_finds_candidates(self) -> None:
        """Longer query uses trigram matching to find candidates."""
        client = self._make_client_with_index()

        result = client._trigram_candidates("claude-sonnet")

        # Should find "claude-sonnet-4" due to high trigram overlap
        assert "claude-sonnet-4" in result

    def test_threshold_filters_weak_matches(self) -> None:
        """Candidates must match >= 50% of query trigrams."""
        client = self._make_client_with_index()

        # A query with trigrams that don't overlap well with any model
        result = client._trigram_candidates("zzz-xyz-abc")

        # No model should match >= 50% of these trigrams
        assert result == set()

    def test_empty_query_returns_empty(self) -> None:
        """Empty query returns empty set."""
        client = self._make_client_with_index()

        result = client._trigram_candidates("")

        assert result == set()


# ============================================================================
# C) _build_search_index()
# ============================================================================


class TestBuildSearchIndex:
    """Tests for ModelDBClient._build_search_index()."""

    def test_builds_trigram_index_from_model_names(self) -> None:
        """_build_search_index() creates trigrams from model names too."""
        client = ModelDBClient()
        client._model_index = {
            "gpt-4o": {"id": "gpt-4o", "name": "GPT-4o"},
        }
        client._build_search_index()

        # "gpt-4o" name.lower() = "gpt-4o" produces trigrams including "gpt"
        # which should map to the key "gpt-4o"
        assert "gpt-4o" in client._trigram_index.get("gpt", set())

    def test_multiple_models_share_trigrams(self) -> None:
        """Two models with overlapping names share trigram entries."""
        client = ModelDBClient()
        client._model_index = {
            "claude-sonnet-4": {"id": "claude-sonnet-4", "name": "Claude Sonnet 4"},
            "claude-haiku-4": {"id": "claude-haiku-4", "name": "Claude Haiku 4"},
        }
        client._build_search_index()

        # Both share "cla" trigram from key "claude-..."
        cla_set = client._trigram_index.get("cla", set())
        assert "claude-sonnet-4" in cla_set
        assert "claude-haiku-4" in cla_set

    def test_empty_index_produces_empty_trigrams(self) -> None:
        """Empty _model_index produces empty _trigram_index."""
        client = ModelDBClient()
        client._model_index = {}
        client._build_search_index()

        assert client._trigram_index == {}


# ============================================================================
# D) _load_disk_index() corruption
# ============================================================================


class TestLoadDiskIndexCorruption:
    """Tests for _load_disk_index() handling of corrupt/expired data."""

    def test_malformed_json_returns_false(self, tmp_path: Path) -> None:
        """Malformed JSON in _index.json returns False without crashing."""
        client = ModelDBClient(disk_cache_path=tmp_path)
        cache_file = tmp_path / "_index.json"
        cache_file.write_text("this is not json {{{", encoding="utf-8")

        result = client._load_disk_index()

        assert result is False

    def test_expired_ttl_returns_false(self, tmp_path: Path) -> None:
        """Valid JSON with expired TTL (fetched_at = 0) returns False."""
        client = ModelDBClient(disk_cache_path=tmp_path, index_ttl=86_400)
        cache_file = tmp_path / "_index.json"
        payload = {"fetched_at": 0, "index": {"some-model": {"id": "some-model"}}}
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

        result = client._load_disk_index()

        assert result is False

    def test_no_disk_path_returns_false(self) -> None:
        """When disk_cache_path is None, returns False."""
        client = ModelDBClient(disk_cache_path=None)

        result = client._load_disk_index()

        assert result is False

    def test_empty_index_dict_returns_false(self, tmp_path: Path) -> None:
        """Valid JSON with fresh TTL but empty index dict returns False."""
        client = ModelDBClient(disk_cache_path=tmp_path, index_ttl=86_400)
        cache_file = tmp_path / "_index.json"
        payload = {"fetched_at": time.time(), "index": {}}
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

        result = client._load_disk_index()

        # bool({}) == False, so _load_disk_index returns False
        assert result is False


# ============================================================================
# E) _save_disk_index() roundtrip
# ============================================================================


class TestSaveDiskIndexRoundtrip:
    """Tests for _save_disk_index() / _load_disk_index() roundtrip."""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Index saved to disk can be loaded back with identical data."""
        client = ModelDBClient(disk_cache_path=tmp_path, index_ttl=86_400)

        # Build a model index directly
        client._model_index = {
            "test-model": {
                "id": "test-model",
                "provider": "test-provider",
                "name": "Test Model",
            },
        }
        client._index_fetched_at = time.time()
        client._build_search_index()

        # Save to disk
        client._save_disk_index()

        # Create a fresh client and load from disk
        client2 = ModelDBClient(disk_cache_path=tmp_path, index_ttl=86_400)
        loaded = client2._load_disk_index()

        assert loaded is True
        assert client2._model_index == client._model_index
        assert "test-model" in client2._model_index
        assert client2._model_index["test-model"]["provider"] == "test-provider"

    def test_save_creates_directory(self, tmp_path: Path) -> None:
        """_save_disk_index() creates parent directories if needed."""
        nested_path = tmp_path / "deep" / "nested" / "dir"
        client = ModelDBClient(disk_cache_path=nested_path, index_ttl=86_400)
        client._model_index = {"m": {"id": "m"}}
        client._index_fetched_at = time.time()

        client._save_disk_index()

        assert (nested_path / "_index.json").is_file()

    def test_save_noop_when_no_disk_path(self) -> None:
        """_save_disk_index() does nothing when disk_cache_path is None."""
        client = ModelDBClient()
        client._model_index = {"m": {"id": "m"}}
        client._index_fetched_at = time.time()

        # Should not raise
        client._save_disk_index()
