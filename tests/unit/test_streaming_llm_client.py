"""Tests for LLMClient SDK-based streaming and non-streaming calls."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)


def _make_llm_client(
    provider_api=ProviderApiType.OPENAI_COMPATIBLE,
    streaming=True,
    provider_base_url="https://api.test.com/v1",
):
    """Create an LLMClient with mocked config for testing."""
    with (
        patch("agent_nexus.platform.config.loader.ConfigLoader") as MockLoader,  # noqa: N806
        patch("agent_nexus.platform.config.model_db.ModelDBClient"),
    ):
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
        try:

            from agent_nexus.platform.agency.llm_client import LLMClient

            client = LLMClient(model_string="openai:test-model")
            return client
        finally:
            os.environ.pop("TEST_API_KEY", None)


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
            assert (
                call_kwargs.kwargs.get("stream") is None
                or call_kwargs.kwargs.get("stream") is False
            )

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


class TestCallAnthropicSDK:
    """Test Anthropic SDK path (streaming and non-streaming)."""

    def _make_anthropic_client(self, streaming=True):
        with (
            patch("agent_nexus.platform.config.loader.ConfigLoader") as MockLoader,  # noqa: N806
            patch("agent_nexus.platform.config.model_db.ModelDBClient"),
        ):
            platform_cfg = PlatformConfig(
                runtime=RuntimeConfig(),
                models=ModelConfig(
                    default="anthropic:test-model",
                    providers={
                        "anthropic": ProviderConfig(
                            base_url="https://api.anthropic.com",
                            api_key_env="TEST_API_KEY",
                            api=ProviderApiType.ANTHROPIC_MESSAGES,
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
            try:

                from agent_nexus.platform.agency.llm_client import LLMClient

                return LLMClient(model_string="anthropic:test-model")
            finally:
                os.environ.pop("TEST_API_KEY", None)

    def test_non_streaming_uses_sdk(self):
        client = self._make_anthropic_client(streaming=False)

        mock_response = MagicMock()
        mock_response.model = "test-model"
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Hello from Claude"
        mock_response.content = [mock_block]

        with patch.object(client, "_get_anthropic_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.messages.create.return_value = mock_response
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from Claude"
            mock_sdk.messages.create.assert_called_once()
            call_kwargs = mock_sdk.messages.create.call_args
            assert (
                call_kwargs.kwargs.get("stream") is None
                or call_kwargs.kwargs.get("stream") is False
            )

    def test_streaming_uses_sdk_with_stream_true(self):
        client = self._make_anthropic_client(streaming=True)

        # Anthropic stream events
        events = []
        # message_start
        start_event = MagicMock()
        start_event.type = "message_start"
        start_event.message = MagicMock()
        start_event.message.model = "test-model"
        events.append(start_event)
        # content_block_delta
        for text in ["Hello", " from", " Claude"]:
            delta_event = MagicMock()
            delta_event.type = "content_block_delta"
            delta_event.delta = MagicMock()
            delta_event.delta.text = text
            events.append(delta_event)
        # message_stop
        stop_event = MagicMock()
        stop_event.type = "message_stop"
        events.append(stop_event)

        @contextmanager
        def _mock_anthropic_stream(*args, **kwargs):
            yield iter(events)

        with patch.object(client, "_get_anthropic_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.messages.create.side_effect = _mock_anthropic_stream
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Hello from Claude"
            call_kwargs = mock_sdk.messages.create.call_args
            assert call_kwargs.kwargs.get("stream") is True

    def test_non_streaming_falls_back_to_stream_when_sdk_requires(self):
        """When Anthropic SDK rejects non-streaming (long timeout), falls back to streaming internally."""
        client = self._make_anthropic_client(streaming=False)

        # Anthropic stream events for the fallback path
        events = []
        start_event = MagicMock()
        start_event.type = "message_start"
        start_event.message = MagicMock()
        start_event.message.model = "test-model"
        events.append(start_event)
        for text in ["Fallback", " stream"]:
            delta_event = MagicMock()
            delta_event.type = "content_block_delta"
            delta_event.delta = MagicMock()
            delta_event.delta.text = text
            events.append(delta_event)
        stop_event = MagicMock()
        stop_event.type = "message_stop"
        events.append(stop_event)

        @contextmanager
        def _mock_stream(*args, **kwargs):
            yield iter(events)

        call_count = 0

        def _create_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("stream") is False or kwargs.get("stream") is None:
                raise ValueError("Streaming is required for operations that may take longer than 10 minutes.")
            return _mock_stream(**kwargs)

        with patch.object(client, "_get_anthropic_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.messages.create.side_effect = _create_side_effect
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi", timeout=900)

            assert result.text == "Fallback stream"
            assert call_count == 2  # First attempt (non-stream) + second attempt (stream)

    def test_streaming_skips_thinking_delta(self):
        """ThinkingDelta events (no .text attr) are silently skipped."""
        client = self._make_anthropic_client(streaming=True)

        events = []
        start_event = MagicMock()
        start_event.type = "message_start"
        start_event.message = MagicMock()
        start_event.message.model = "test-model"
        events.append(start_event)
        # ThinkingDelta — has .thinking but no .text
        thinking_event = MagicMock()
        thinking_event.type = "content_block_delta"
        thinking_event.delta = MagicMock(spec=[])
        thinking_event.delta.thinking = "internal reasoning"
        events.append(thinking_event)
        # Normal TextDelta
        for text in ["Real", " output"]:
            delta_event = MagicMock()
            delta_event.type = "content_block_delta"
            delta_event.delta = MagicMock()
            delta_event.delta.text = text
            events.append(delta_event)
        stop_event = MagicMock()
        stop_event.type = "message_stop"
        events.append(stop_event)

        @contextmanager
        def _mock_anthropic_stream(*args, **kwargs):
            yield iter(events)

        with patch.object(client, "_get_anthropic_sdk") as mock_get_sdk:
            mock_sdk = MagicMock()
            mock_sdk.messages.create.side_effect = _mock_anthropic_stream
            mock_get_sdk.return_value = mock_sdk

            result = client.call("You are helpful", "Say hi")

            assert result.text == "Real output"
