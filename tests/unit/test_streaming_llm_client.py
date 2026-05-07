"""Tests for LLMClient using LiteLLM unified calling layer.

Replaces the old SDK-specific tests (OpenAI/Anthropic streaming) with
LiteLLM-based tests that verify the unified path works correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_llm_client(
    provider_api="openai-compatible",
    provider_name="openai",
    model_name="test-model",
    provider_base_url="https://api.test.com/v1",
):
    """Create an LLMClient with mocked config for testing."""
    from agent_nexus.models.config import (
        ModelConfig,
        PlatformConfig,
        ProviderApiType,
        ProviderConfig,
        RuntimeConfig,
    )

    api_type = ProviderApiType.OPENAI_COMPATIBLE
    if provider_api == "anthropic-messages":
        api_type = ProviderApiType.ANTHROPIC_MESSAGES

    with (
        patch("agent_nexus.platform.config.loader.ConfigLoader") as MockLoader,
        patch("agent_nexus.platform.config.model_db.ModelDBClient"),
    ):
        platform_cfg = PlatformConfig(
            runtime=RuntimeConfig(),
            models=ModelConfig(
                default=f"{provider_name}:{model_name}",
                providers={
                    provider_name: ProviderConfig(
                        base_url=provider_base_url,
                        api_key_env="TEST_API_KEY",
                        api=api_type,
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

            client = LLMClient(model_string=f"{provider_name}:{model_name}")
            return client
        finally:
            os.environ.pop("TEST_API_KEY", None)


def _mock_litellm_response(text="Hello from test", model="test-model"):
    mock_resp = MagicMock()
    mock_resp.model = model
    mock_choice = MagicMock()
    mock_choice.message.content = text
    mock_resp.choices = [mock_choice]
    return mock_resp


class TestOpenAIProviderViaLiteLLM:
    """Test OpenAI provider uses litellm.completion()."""

    @patch("agent_nexus.platform.agency.llm_client.litellm")
    def test_call_uses_litellm_completion(self, mock_litellm):
        mock_litellm.completion.return_value = _mock_litellm_response()

        client = _make_llm_client(provider_name="openai", model_name="gpt-4o")
        result = client.call("You are helpful", "Say hi")

        assert result.text == "Hello from test"
        mock_litellm.completion.assert_called_once()

        call_kwargs = mock_litellm.completion.call_args.kwargs
        assert call_kwargs["model"] == "openai/gpt-4o"
        assert call_kwargs["stream"] is False

    @patch("agent_nexus.platform.agency.llm_client.litellm")
    def test_response_format_json(self, mock_litellm):
        mock_litellm.completion.return_value = _mock_litellm_response(text='{"result": "ok"}')

        client = _make_llm_client(provider_name="openai", model_name="gpt-4o")
        result = client.call("sys", "usr", response_format="json")

        call_kwargs = mock_litellm.completion.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert result.text == '{"result": "ok"}'


class TestAnthropicProviderViaLiteLLM:
    """Test Anthropic provider uses litellm.completion()."""

    @patch("agent_nexus.platform.agency.llm_client.litellm")
    def test_call_uses_litellm_completion(self, mock_litellm):
        mock_litellm.completion.return_value = _mock_litellm_response(
            text="Hello from Claude", model="claude-sonnet-4-20250514"
        )

        client = _make_llm_client(
            provider_api="anthropic-messages",
            provider_name="anthropic",
            model_name="claude-sonnet-4-20250514",
        )
        result = client.call("You are helpful", "Say hi")

        assert result.text == "Hello from Claude"
        call_kwargs = mock_litellm.completion.call_args.kwargs
        assert call_kwargs["model"] == "anthropic/claude-sonnet-4-20250514"


class TestDeepSeekProviderViaLiteLLM:
    """Test DeepSeek provider uses litellm.completion()."""

    @patch("agent_nexus.platform.agency.llm_client.litellm")
    def test_call_uses_litellm_completion(self, mock_litellm):
        mock_litellm.completion.return_value = _mock_litellm_response(
            text="Hello from DeepSeek", model="deepseek-chat"
        )

        client = _make_llm_client(
            provider_name="deepseek",
            model_name="deepseek-chat",
        )
        result = client.call("You are helpful", "Say hi")

        assert result.text == "Hello from DeepSeek"
        call_kwargs = mock_litellm.completion.call_args.kwargs
        assert call_kwargs["model"] == "deepseek/deepseek-chat"
