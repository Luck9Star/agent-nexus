"""Tests for LiteLLM-based unified LLMClient calling layer.

Uses deferred imports to avoid circular import issues through agency/__init__.py.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def _make_capable_model(
    max_output_tokens: int = 4096,
    supports_temperature: bool = True,
    temperature_min: float = 0.0,
    temperature_max: float = 2.0,
):
    """Create a ModelCapability with configurable fields (deferred import)."""
    from agent_nexus.models.capability import ModelCapability

    return ModelCapability(
        model_id="test-model",
        provider="openai",
        max_output_tokens=max_output_tokens,
        context_window=128000,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=supports_temperature,
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        knowledge_cutoff="2024-01",
    )


def _make_llm_client(
    provider_name: str = "openai",
    model_name: str = "test-model",
    provider_api: str | None = None,
    provider_base_url: str = "https://api.test.com/v1",
    provider_api_key_env: str = "TEST_API_KEY",
    capability=None,
):
    """Create an LLMClient with mocked config for testing."""
    from agent_nexus.models.config import (
        ModelConfig,
        PlatformConfig,
        ProviderApiType,
        ProviderConfig,
        RuntimeConfig,
    )

    if provider_api is None:
        api_type = ProviderApiType.OPENAI_COMPATIBLE
    elif provider_api == "anthropic-messages":
        api_type = ProviderApiType.ANTHROPIC_MESSAGES
    elif provider_api == "ollama":
        api_type = ProviderApiType.OLLAMA
    else:
        api_type = ProviderApiType.OPENAI_COMPATIBLE

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
                        api_key_env=provider_api_key_env,
                        api=api_type,
                    ),
                },
            ),
        )
        mock_loader = MagicMock()
        mock_loader.load_config.return_value = platform_cfg
        mock_loader.load_cli_backends.return_value = {}
        MockLoader.return_value = mock_loader

        os.environ[provider_api_key_env] = "test-key-123"
        try:
            from agent_nexus.platform.agency.llm_client import LLMClient

            client = LLMClient(model_string=f"{provider_name}:{model_name}")
            if capability is not None:
                client._capability = capability
            return client
        finally:
            os.environ.pop(provider_api_key_env, None)


def _mock_litellm_response(text: str = "Hello", model: str = "test-model"):
    """Create a mock litellm.completion() response."""
    mock_resp = MagicMock()
    mock_resp.model = model
    mock_choice = MagicMock()
    mock_choice.message.content = text
    mock_resp.choices = [mock_choice]
    return mock_resp


# ---------------------------------------------------------------------------
# Model string mapping
# ---------------------------------------------------------------------------


class TestToLitellmModel:
    """Test _to_litellm_model() converts agent-nexus format to litellm format."""

    def test_anthropic_mapping(self):
        client = _make_llm_client(
            provider_name="anthropic",
            model_name="claude-sonnet-4-20250514",
            provider_api="anthropic-messages",
        )
        assert client._to_litellm_model() == "anthropic/claude-sonnet-4-20250514"

    def test_openai_mapping(self):
        client = _make_llm_client(
            provider_name="openai",
            model_name="gpt-4o",
        )
        assert client._to_litellm_model() == "openai/gpt-4o"

    def test_deepseek_mapping(self):
        client = _make_llm_client(
            provider_name="deepseek",
            model_name="deepseek-chat",
        )
        assert client._to_litellm_model() == "deepseek/deepseek-chat"

    def test_ollama_mapping(self):
        client = _make_llm_client(
            provider_name="ollama",
            model_name="llama3",
            provider_api="ollama",
        )
        assert client._to_litellm_model() == "ollama/llama3"

    def test_api_mapping_to_openai_prefix(self):
        """OpenAI-compatible APIs (MiniMax, Qwen) use 'openai/' prefix with api_base."""
        client = _make_llm_client(
            provider_name="api",
            model_name="MiniMax-M2.7-highspeed",
        )
        assert client._to_litellm_model() == "openai/MiniMax-M2.7-highspeed"

    def test_unknown_provider_defaults_to_openai(self):
        client = _make_llm_client(
            provider_name="unknown-provider",
            model_name="some-model",
        )
        assert client._to_litellm_model() == "openai/some-model"


# ---------------------------------------------------------------------------
# litellm.completion() kwargs building
# ---------------------------------------------------------------------------


class TestBuildLitellmKwargs:
    """Test _build_litellm_kwargs() produces correct kwargs."""

    def test_basic_kwargs(self):
        from agent_nexus.platform.agency.hooks import CallContext

        client = _make_llm_client(
            provider_name="openai",
            model_name="gpt-4o",
        )
        ctx = CallContext(
            model="gpt-4o",
            system_prompt="You are helpful",
            user_message="Say hi",
            temperature=None,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=None, top_p=None)

        assert kwargs["model"] == "openai/gpt-4o"
        assert kwargs["messages"] == [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Say hi"},
        ]
        assert kwargs["stream"] is False
        assert "api_key" in kwargs
        assert kwargs["api_key"] == "test-key-123"
        assert kwargs["api_base"] == "https://api.test.com/v1"

    def test_max_tokens_from_capability(self):
        from agent_nexus.platform.agency.hooks import CallContext

        cap = _make_capable_model(max_output_tokens=4096)
        client = _make_llm_client(capability=cap)
        ctx = CallContext(
            model="test-model",
            system_prompt="sys",
            user_message="usr",
            temperature=None,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=None, top_p=None)
        assert kwargs["max_tokens"] == 4096

    def test_max_tokens_explicit_override(self):
        from agent_nexus.platform.agency.hooks import CallContext

        client = _make_llm_client()
        ctx = CallContext(
            model="test-model",
            system_prompt="sys",
            user_message="usr",
            temperature=None,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=2048, top_p=None)
        assert kwargs["max_tokens"] == 2048

    def test_temperature_applied_with_capability_support(self):
        from agent_nexus.platform.agency.hooks import CallContext

        cap = _make_capable_model(
            supports_temperature=True, temperature_min=0.0, temperature_max=2.0
        )
        client = _make_llm_client(capability=cap)
        ctx = CallContext(
            model="test-model",
            system_prompt="sys",
            user_message="usr",
            temperature=0.7,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=None, top_p=None)
        assert kwargs["temperature"] == 0.7

    def test_temperature_clamped_to_range(self):
        from agent_nexus.platform.agency.hooks import CallContext

        cap = _make_capable_model(
            supports_temperature=True, temperature_min=0.0, temperature_max=1.0
        )
        client = _make_llm_client(capability=cap)
        ctx = CallContext(
            model="test-model",
            system_prompt="sys",
            user_message="usr",
            temperature=1.5,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=None, top_p=None)
        assert kwargs["temperature"] == 1.0  # clamped to max

    def test_temperature_skipped_when_not_supported(self):
        from agent_nexus.platform.agency.hooks import CallContext

        cap = _make_capable_model(supports_temperature=False)
        client = _make_llm_client(capability=cap)
        ctx = CallContext(
            model="test-model",
            system_prompt="sys",
            user_message="usr",
            temperature=0.7,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=None, top_p=None)
        assert "temperature" not in kwargs

    def test_top_p_applied(self):
        from agent_nexus.platform.agency.hooks import CallContext

        client = _make_llm_client()
        ctx = CallContext(
            model="test-model",
            system_prompt="sys",
            user_message="usr",
            temperature=None,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=None, top_p=0.9)
        assert kwargs["top_p"] == 0.9

    def test_no_base_url_when_empty(self):
        from agent_nexus.platform.agency.hooks import CallContext

        client = _make_llm_client(
            provider_base_url="",
        )
        ctx = CallContext(
            model="test-model",
            system_prompt="sys",
            user_message="usr",
            temperature=None,
            response_format=None,
            timeout=None,
        )
        kwargs = client._build_litellm_kwargs(ctx, max_tokens=None, top_p=None)
        assert "api_base" not in kwargs


# ---------------------------------------------------------------------------
# call() method -- LiteLLM path
# ---------------------------------------------------------------------------


class TestCallLiteLLM:
    """Test call() uses litellm.completion() for API providers."""

    def test_basic_call(self):
        import agent_nexus.platform.agency.llm_client as lc_mod

        client = _make_llm_client(provider_name="openai", model_name="gpt-4o")

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.return_value = _mock_litellm_response(
                text="Hello from LiteLLM", model="gpt-4o"
            )
            result = client.call("You are helpful", "Say hi")

        assert result.text == "Hello from LiteLLM"
        assert result.model == "gpt-4o"
        assert result.provider == "openai"
        mock_completion.assert_called_once()

    def test_response_format_json(self):
        import agent_nexus.platform.agency.llm_client as lc_mod

        client = _make_llm_client(provider_name="openai", model_name="gpt-4o")

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.return_value = _mock_litellm_response(
                text='{"key": "value"}', model="gpt-4o"
            )
            result = client.call("You are helpful", "Return JSON", response_format="json")

        call_kwargs = mock_completion.call_args
        assert call_kwargs.kwargs.get("response_format") == {"type": "json_object"}
        assert result.text == '{"key": "value"}'

    def test_error_propagates(self):
        import agent_nexus.platform.agency.llm_client as lc_mod

        client = _make_llm_client()

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.side_effect = Exception("API error")
            with pytest.raises(Exception, match="API error"):
                client.call("You are helpful", "Say hi")

    def test_empty_response_content(self):
        import agent_nexus.platform.agency.llm_client as lc_mod

        client = _make_llm_client()

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.return_value = _mock_litellm_response(text=None, model="test-model")
            result = client.call("You are helpful", "Say hi")

        assert result.text == ""

    def test_api_base_forwarded(self):
        import agent_nexus.platform.agency.llm_client as lc_mod

        client = _make_llm_client(
            provider_base_url="https://custom.api.com/v1",
        )

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.return_value = _mock_litellm_response()
            client.call("sys", "usr")

        call_kwargs = mock_completion.call_args
        assert call_kwargs.kwargs["api_base"] == "https://custom.api.com/v1"


# ---------------------------------------------------------------------------
# HookManager events
# ---------------------------------------------------------------------------


class TestHooksPreserved:
    """HookManager events still fire correctly with LiteLLM path."""

    def test_before_and_after_call_hooks(self):
        import agent_nexus.platform.agency.llm_client as lc_mod
        from agent_nexus.platform.agency.hooks import HookEvent

        client = _make_llm_client()

        events_seen = []

        def before_handler(**kwargs):
            events_seen.append("before_call")

        def after_handler(**kwargs):
            events_seen.append("after_call")

        client.hooks.register(HookEvent.BEFORE_CALL, before_handler)
        client.hooks.register(HookEvent.AFTER_CALL, after_handler)

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.return_value = _mock_litellm_response()
            client.call("sys", "usr")

        assert "before_call" in events_seen
        assert "after_call" in events_seen

    def test_on_error_hook_on_failure(self):
        import agent_nexus.platform.agency.llm_client as lc_mod
        from agent_nexus.platform.agency.hooks import HookEvent

        client = _make_llm_client()

        errors_seen = []

        def error_handler(**kwargs):
            errors_seen.append(kwargs.get("error"))

        client.hooks.register(HookEvent.ON_ERROR, error_handler)

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.side_effect = Exception("boom")
            with pytest.raises(Exception, match="boom"):
                client.call("sys", "usr")

        assert len(errors_seen) == 1
        assert str(errors_seen[0]) == "boom"

    def test_after_call_receives_result(self):
        import agent_nexus.platform.agency.llm_client as lc_mod
        from agent_nexus.platform.agency.hooks import HookEvent

        client = _make_llm_client()

        results = []

        def after_handler(**kwargs):
            results.append(kwargs.get("result"))

        client.hooks.register(HookEvent.AFTER_CALL, after_handler)

        with patch.object(lc_mod.litellm, "completion") as mock_completion:
            mock_completion.return_value = _mock_litellm_response(
                text="Hello world", model="test-model"
            )
            client.call("sys", "usr")

        assert len(results) == 1
        assert results[0].content == "Hello world"
        assert results[0].model == "test-model"
