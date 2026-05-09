"""Unit tests for ModelDBClient internal methods.

Tests cover close() resource cleanup, _trigram_candidates() algorithm,
_build_search_index(), _load_disk_index() corruption handling, and
_save_disk_index() roundtrip.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_nexus.platform.config.model_db import ModelDBClient


# ============================================================================
# A) close() resource cleanup
# ============================================================================


class TestClose:
    """Tests for ModelDBClient.close() resource cleanup."""

    def test_close_closes_http_client(self) -> None:
        """close() closes the internal httpx.Client when it exists."""
        client = ModelDBClient()
        mock_http = MagicMock()
        mock_http.is_closed = False
        client._http_client = mock_http

        client.close()

        mock_http.close.assert_called_once()

    def test_close_noop_when_http_client_is_none(self) -> None:
        """close() is a no-op when _http_client is None."""
        client = ModelDBClient()
        assert client._http_client is None

        # Should not raise
        client.close()

    def test_close_idempotent(self) -> None:
        """Calling close() twice does not error."""
        client = ModelDBClient()
        mock_http = MagicMock()
        mock_http.is_closed = False
        client._http_client = mock_http

        client.close()
        # After first close, the real code checks is_closed. Simulate
        # httpx.Client.is_closed returning True after close.
        mock_http.is_closed = True
        client.close()

        # close() should have been called only once (second call skipped
        # because is_closed is True).
        mock_http.close.assert_called_once()

    def test_close_with_mock_patch(self) -> None:
        """close() delegates to httpx.Client.close via mocked module."""
        with patch("agent_nexus.platform.config.model_db.httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_instance.is_closed = False
            MockClient.return_value = mock_instance

            client = ModelDBClient()
            # Simulate lazy creation
            client._http_client = mock_instance
            client.close()

            mock_instance.close.assert_called_once()


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

    def test_single_char_not_in_index(self) -> None:
        """Single-char query not in index returns empty set."""
        client = self._make_client_with_index()

        result = client._trigram_candidates("x")

        assert result == set()


# ============================================================================
# C) _build_search_index()
# ============================================================================


class TestBuildSearchIndex:
    """Tests for ModelDBClient._build_search_index()."""

    def test_builds_trigram_index_from_model_keys(self) -> None:
        """_build_search_index() creates trigrams for model keys."""
        client = ModelDBClient()
        client._model_index = {
            "gpt-4o": {"id": "gpt-4o", "name": "GPT-4o"},
        }
        client._build_search_index()

        # "gpt-4o" produces trigrams: "gpt", "pt-", "t-4", "-4o"
        assert "gpt" in client._trigram_index
        assert "gpt-4o" in client._trigram_index.get("gpt", set())

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

    def test_missing_cache_file_returns_false(self, tmp_path: Path) -> None:
        """When _index.json does not exist, returns False."""
        client = ModelDBClient(disk_cache_path=tmp_path)

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
