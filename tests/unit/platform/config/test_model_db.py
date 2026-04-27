"""Unit tests for ModelDBClient: API interaction, caching, and error handling.

Tests cover fetch_model (success, 404, network error, timeout), fetch_search,
memory cache hit/expiry, and cache clearing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from agent_nexus.platform.config.model_db import ModelDBClient

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_client(
    return_value: httpx.Response,
) -> httpx.Client:
    """Build an httpx.Client whose send() returns *return_value*.

    The mock accepts all keyword arguments that httpx internally passes
    to ``Client.send()`` (e.g. *auth*, *follow_redirects*).
    """
    client = httpx.Client()

    def _send(request: httpx.Request, **kwargs: object) -> httpx.Response:
        return return_value

    client.send = _send  # type: ignore[method-assign]
    return client


def _make_raising_client(exception: Exception) -> httpx.Client:
    """Build an httpx.Client whose send() raises *exception*."""
    client = httpx.Client()

    def _send(request: httpx.Request, **kwargs: object) -> httpx.Response:
        raise exception

    client.send = _send  # type: ignore[method-assign]
    return client


def _patch_http(return_value: httpx.Response):
    """Patch ``_get_http_client`` to return a mock that returns *return_value*."""
    return patch.object(
        ModelDBClient,
        "_get_http_client",
        return_value=_make_mock_client(return_value),
    )


# ============================================================================
# fetch_model
# ============================================================================


class TestFetchModel:
    """Tests for ModelDBClient.fetch_model()."""

    def test_fetch_model_success(self) -> None:
        """200 response returns parsed JSON dict."""
        expected = {"id": "claude-sonnet-4", "capabilities": ["reasoning"]}

        with _patch_http(httpx.Response(200, json=expected)):
            client = ModelDBClient()
            result = client.fetch_model("claude-sonnet-4")

        assert result == expected

    def test_fetch_model_404(self) -> None:
        """Non-200 response returns None."""
        with _patch_http(httpx.Response(404)):
            client = ModelDBClient()
            result = client.fetch_model("unknown-model")

        assert result is None

    def test_fetch_model_network_error(self) -> None:
        """ConnectError returns None."""
        err = httpx.ConnectError("Connection refused")
        with patch.object(
            ModelDBClient, "_get_http_client", return_value=_make_raising_client(err)
        ):
            client = ModelDBClient()
            result = client.fetch_model("claude-sonnet-4")

        assert result is None

    def test_fetch_model_timeout(self) -> None:
        """TimeoutException returns None."""
        err = httpx.TimeoutException("Request timed out")
        with patch.object(
            ModelDBClient,
            "_get_http_client",
            return_value=_make_raising_client(err),
        ):
            client = ModelDBClient()
            result = client.fetch_model("claude-sonnet-4")

        assert result is None


# ============================================================================
# Cache
# ============================================================================


class TestCache:
    """Tests for ModelDBClient caching behavior."""

    def test_cache_hit(self) -> None:
        """Second call uses memory cache and does not trigger HTTP request."""
        expected = {"id": "claude-sonnet-4", "capabilities": ["reasoning"]}
        call_count = 0
        resp = httpx.Response(200, json=expected)
        mock_client = _make_mock_client(resp)
        original_send = mock_client.send

        def counting_send(request: httpx.Request, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return original_send(request, **kwargs)

        mock_client.send = counting_send  # type: ignore[method-assign]

        with patch.object(
            ModelDBClient, "_get_http_client", return_value=mock_client
        ):
            client = ModelDBClient()
            result1 = client.fetch_model("claude-sonnet-4")
            result2 = client.fetch_model("claude-sonnet-4")

        assert result1 == expected
        assert result2 == expected
        # Only the first call should hit the network
        assert call_count == 1

    def test_cache_hit_normalized_key(self) -> None:
        """Cache key is case-insensitive: 'Claude-Sonnet-4' hits same cache as 'claude-sonnet-4'."""
        expected = {"id": "claude-sonnet-4"}
        call_count = 0
        resp = httpx.Response(200, json=expected)
        mock_client = _make_mock_client(resp)
        original_send = mock_client.send

        def counting_send(request: httpx.Request, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return original_send(request, **kwargs)

        mock_client.send = counting_send  # type: ignore[method-assign]

        with patch.object(
            ModelDBClient, "_get_http_client", return_value=mock_client
        ):
            client = ModelDBClient()
            result1 = client.fetch_model("Claude-Sonnet-4")
            result2 = client.fetch_model("claude-sonnet-4")

        assert result1 == expected
        assert result2 == expected
        assert call_count == 1

    def test_cache_expired(self) -> None:
        """After TTL expires, the client makes a new HTTP request."""
        expected = {"id": "claude-sonnet-4"}
        call_count = 0
        resp = httpx.Response(200, json=expected)
        mock_client = _make_mock_client(resp)
        original_send = mock_client.send

        def counting_send(request: httpx.Request, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return original_send(request, **kwargs)

        mock_client.send = counting_send  # type: ignore[method-assign]

        with patch.object(
            ModelDBClient, "_get_http_client", return_value=mock_client
        ):
            client = ModelDBClient(cache_ttl=1)
            client.fetch_model("claude-sonnet-4")
            # Manually advance time past TTL
            client._mem_cache["claude-sonnet-4"] = (time.time() - 2, expected)
            client.fetch_model("claude-sonnet-4")

        # Two calls because cache expired
        assert call_count == 2

    def test_clear_cache(self) -> None:
        """clear_cache() empties memory and disk cache."""
        expected = {"id": "claude-sonnet-4"}
        call_count = 0
        resp = httpx.Response(200, json=expected)
        mock_client = _make_mock_client(resp)
        original_send = mock_client.send

        def counting_send(request: httpx.Request, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return original_send(request, **kwargs)

        mock_client.send = counting_send  # type: ignore[method-assign]

        with patch.object(
            ModelDBClient, "_get_http_client", return_value=mock_client
        ):
            client = ModelDBClient()
            client.fetch_model("claude-sonnet-4")
            client.clear_cache()
            client.fetch_model("claude-sonnet-4")

        # Two calls because cache was cleared
        assert call_count == 2

    def test_clear_cache_with_disk(self, tmp_path: Path) -> None:
        """clear_cache() removes disk cache files."""
        expected = {"id": "claude-sonnet-4"}
        resp = httpx.Response(200, json=expected)

        with _patch_http(resp):
            client = ModelDBClient(disk_cache_path=tmp_path)
            client.fetch_model("claude-sonnet-4")

            # Verify disk cache file exists
            cache_files = list(tmp_path.iterdir())
            assert len(cache_files) == 1

            client.clear_cache()

            # Disk cache files should be gone
            assert len(list(tmp_path.iterdir())) == 0


# ============================================================================
# fetch_search
# ============================================================================


class TestFetchSearch:
    """Tests for ModelDBClient.fetch_search()."""

    def test_fetch_search_success(self) -> None:
        """200 response returns list of model dicts."""
        expected = [
            {"id": "claude-sonnet-4"},
            {"id": "claude-haiku-4"},
        ]

        with _patch_http(httpx.Response(200, json=expected)):
            client = ModelDBClient()
            results = client.fetch_search("claude")

        assert results == expected

    def test_fetch_search_failure_returns_empty_list(self) -> None:
        """Non-200 response returns empty list."""
        with _patch_http(httpx.Response(500)):
            client = ModelDBClient()
            results = client.fetch_search("claude")

        assert results == []

    def test_fetch_search_network_error_returns_empty_list(self) -> None:
        """ConnectError returns empty list."""
        err = httpx.ConnectError("Connection refused")
        with patch.object(
            ModelDBClient, "_get_http_client", return_value=_make_raising_client(err)
        ):
            client = ModelDBClient()
            results = client.fetch_search("claude")

        assert results == []


# ============================================================================
# Disk cache
# ============================================================================


class TestDiskCache:
    """Tests for ModelDBClient disk caching."""

    def test_disk_cache_read_on_miss(self, tmp_path: Path) -> None:
        """When mem cache is empty but disk cache exists, data is loaded from disk."""
        expected = {"id": "claude-sonnet-4", "capabilities": ["reasoning"]}
        cache_file = tmp_path / "claude-sonnet-4.json"
        cache_file.write_text(json.dumps(expected), encoding="utf-8")

        # Create client without mocking httpx -- disk cache should serve the data
        client = ModelDBClient(disk_cache_path=tmp_path)
        result = client.fetch_model("claude-sonnet-4")

        assert result == expected

    def test_disk_cache_writes_on_fetch(self, tmp_path: Path) -> None:
        """After a successful fetch, data is written to disk cache."""
        expected = {"id": "claude-sonnet-4", "capabilities": ["reasoning"]}

        with _patch_http(httpx.Response(200, json=expected)):
            client = ModelDBClient(disk_cache_path=tmp_path)
            client.fetch_model("claude-sonnet-4")

        cache_files = list(tmp_path.iterdir())
        assert len(cache_files) == 1
        assert json.loads(cache_files[0].read_text(encoding="utf-8")) == expected
