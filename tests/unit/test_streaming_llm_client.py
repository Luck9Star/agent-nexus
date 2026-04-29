"""Tests for LLMClient SDK-based streaming and non-streaming calls."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)


def _make_llm_client(provider_api=ProviderApiType.OPENAI_COMPATIBLE,
                      streaming=True,
                      provider_base_url="https://api.test.com/v1"):
    """Create an LLMClient with mocked config for testing."""
    with patch("agent_nexus.platform.agency.llm_client.ConfigLoader") as MockLoader, \
         patch("agent_nexus.platform.agency.llm_client.ModelDBClient"):
        platform_cfg = PlatformConfig(
            runtime=RuntimeConfig(),
            models=ModelConfig(
                default="openai:test-model",
                providers={
                    "openai": ProviderConfig(
                        base_url=provider_base_url,
                        api_key_env="TEST_API_KEY",
                        api=provider_api,
                        streaming=streaming,
                    ),
                },
            ),
        )
        mock_loader = MagicMock()
        mock_loader.load_config.return_value = platform_cfg
        MockLoader.return_value = mock_loader

        import os
        os.environ["TEST_API_KEY"] = "test-key-123"

        from agent_nexus.platform.agency.llm_client import LLMClient
        client = LLMClient(model_string="openai:test-model")
        return client


class TestCallOpenaiSDK:
    """Test OpenAI SDK path (streaming and non-streaming)."""

    def test_non_streaming_uses_sdk(self):
        """Non-streaming call uses OpenAI SDK .create() without stream=True."""
        client = _make_llm_client(streaming=False)

        mock_response = MagicMock()
        mock_response.model = "test-model"
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from test"
        mock_response.choices = [mock_choice]

        with patch.object(client, "_get_openai_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.return_value = mock_response
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from test"
            mock_sdk.chat.completions.create.assert_called_once()
            call_kwargs = mock_sdk.chat.completions.create.call_args
            assert call_kwargs.kwargs.get("stream") is None or call_kwargs.kwargs.get("stream") is False

    def test_streaming_uses_sdk_with_stream_true(self):
        """Streaming call uses OpenAI SDK .create(stream=True)."""
        from contextlib import contextmanager

        client = _make_llm_client(streaming=True)

        # Build mock stream chunks
        chunks = []
        for text in ["Hello", " from", " stream"]:
            chunk = MagicMock()
            chunk.model = "test-model"
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = text
            chunks.append(chunk)

        @contextmanager
        def _mock_stream(*args, **kwargs):
            yield iter(chunks)

        with patch.object(client, "_get_openai_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = _mock_stream
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from stream"
            call_kwargs = mock_sdk.chat.completions.create.call_args
            assert call_kwargs.kwargs.get("stream") is True
