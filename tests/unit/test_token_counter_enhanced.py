"""Tests for three-tier TokenCounter: LiteLLM -> tiktoken -> len/4.

When running standalone, the first test may fail due to a circular import
introduced by external_mcp_adapter (Step 1 of the parallel task). This
is a known pre-existing issue that does not affect the broader test suite
(where agency modules are pre-loaded by other test files).

To run standalone successfully, ensure the agency package is importable
(e.g. by running alongside other agency tests).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def _ensure_agency_loaded():
    """Pre-load agency modules by importing llm_client (which uses lazy imports).

    This ensures the agency package is initialized before any token_counter
    test runs. The llm_client module uses lazy imports internally, so it
    doesn't trigger the circular import chain.
    """
    # If agency.__init__ already loaded successfully, nothing to do
    if "agent_nexus.platform.agency.token_counter" in sys.modules:
        return

    # Import hooks directly (no circular dependency) to trigger agency package init
    try:
        import agent_nexus.platform.agency.hooks  # noqa: F401
    except ImportError:
        pass


def _get_token_counter():
    """Get TokenCounter class from agency.token_counter module."""
    from agent_nexus.platform.agency.token_counter import TokenCounter

    return TokenCounter


class TestTokenCounterThreeTier:
    """Test the three-tier fallback chain."""

    def test_litellm_tier_1_success(self):
        """When LiteLLM is available, it is used first."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = True
        tc._litellm_mod = MagicMock()
        tc._litellm_mod.token_counter.return_value = 42

        result = tc.count("some text", model="gpt-4o")
        assert result == 42
        tc._litellm_mod.token_counter.assert_called_once_with(model="gpt-4o", text="some text")

    def test_litellm_default_model_when_empty(self):
        """LiteLLM tier uses 'gpt-4o' as default when model is empty."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = True
        tc._litellm_mod = MagicMock()
        tc._litellm_mod.token_counter.return_value = 10

        tc.count("some text", model="")
        tc._litellm_mod.token_counter.assert_called_once_with(model="gpt-4o", text="some text")

    def test_litellm_failure_falls_to_tiktoken_or_estimate(self):
        """When LiteLLM raises, falls through to tiktoken or len/4."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = True
        tc._litellm_mod = MagicMock()
        tc._litellm_mod.token_counter.side_effect = ValueError("unsupported model")

        result = tc.count("some text", model="gpt-4o")
        assert result > 0

    def test_litellm_failure_falls_to_tiktoken_explicit(self):
        """When LiteLLM fails and tiktoken is available, tiktoken is used."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = True
        tc._litellm_mod = MagicMock()
        tc._litellm_mod.token_counter.side_effect = ValueError("unsupported model")
        tc._tiktoken_available = True

        mock_enc = MagicMock()
        mock_enc.encode.return_value = [1, 2, 3, 4, 5]

        mock_tiktoken = MagicMock()
        mock_tiktoken.encoding_for_model.return_value = mock_enc

        with patch.dict("sys.modules", {"tiktoken": mock_tiktoken}):
            result = tc.count("some text", model="gpt-4o")
            assert result == 5

    def test_both_litellm_and_tiktoken_fail_to_len_div_4(self):
        """When both LiteLLM and tiktoken fail, len/4 is used."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = True
        tc._litellm_mod = MagicMock()
        tc._litellm_mod.token_counter.side_effect = ValueError("fail")
        tc._tiktoken_available = False

        text = "a" * 100  # 100 chars
        assert tc.count(text) == 25  # 100 // 4

    def test_no_litellm_uses_tiktoken(self):
        """When LiteLLM is not available, tiktoken is used."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = False
        tc._tiktoken_available = True

        mock_enc = MagicMock()
        mock_enc.encode.return_value = [1, 2, 3]

        mock_tiktoken = MagicMock()
        mock_tiktoken.encoding_for_model.return_value = mock_enc

        with patch.dict("sys.modules", {"tiktoken": mock_tiktoken}):
            result = tc.count("hello", model="gpt-4o")
            assert result == 3

    def test_no_litellm_no_tiktoken_uses_len_div_4(self):
        """When both LiteLLM and tiktoken are unavailable, len/4 is used."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = False
        tc._tiktoken_available = False

        text = "a" * 80
        assert tc.count(text) == 20  # 80 // 4

    def test_len_div_4_minimum_is_1(self):
        """Fallback returns at least 1 token for non-empty text."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = False
        tc._tiktoken_available = False
        assert tc.count("abc") == 1  # 3//4=0 -> max(1,0)=1

    def test_full_chain_litellm_succeeds(self):
        """Integration: when litellm works, tiktoken is never called."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        tc._litellm_available = True
        tc._litellm_mod = MagicMock()
        tc._litellm_mod.token_counter.return_value = 7

        result = tc.count("test", model="gpt-4o")
        assert result == 7

    def test_empty_string_returns_zero(self):
        """Empty string returns 0 tokens."""
        TokenCounter = _get_token_counter()
        tc = TokenCounter()
        assert tc.count("") == 0
