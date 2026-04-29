"""Tests for streaming config fields on Pydantic models."""

from agent_nexus.models.config import ModelConfig, ProviderConfig


def test_provider_config_streaming_default_is_none():
    """ProviderConfig.streaming defaults to None (use global default)."""
    cfg = ProviderConfig()
    assert cfg.streaming is None


def test_provider_config_streaming_can_be_set():
    cfg = ProviderConfig(streaming=True)
    assert cfg.streaming is True


def test_model_config_streaming_default_is_true():
    """ModelConfig.streaming_default defaults to True."""
    cfg = ModelConfig()
    assert cfg.streaming_default is True


def test_model_config_streaming_default_can_be_set():
    cfg = ModelConfig(streaming_default=False)
    assert cfg.streaming_default is False


class TestStreamingResolution:
    """Test the 3-tier streaming resolution: provider -> global -> True."""

    def test_provider_streaming_true(self):
        pc = ProviderConfig(streaming=True)
        mc = ModelConfig(streaming_default=False)
        assert _resolve_streaming(pc, mc) is True

    def test_provider_streaming_false(self):
        pc = ProviderConfig(streaming=False)
        mc = ModelConfig(streaming_default=True)
        assert _resolve_streaming(pc, mc) is False

    def test_provider_none_uses_global_true(self):
        pc = ProviderConfig(streaming=None)
        mc = ModelConfig(streaming_default=True)
        assert _resolve_streaming(pc, mc) is True

    def test_provider_none_uses_global_false(self):
        pc = ProviderConfig(streaming=None)
        mc = ModelConfig(streaming_default=False)
        assert _resolve_streaming(pc, mc) is False


def _resolve_streaming(provider_config: ProviderConfig, model_config: ModelConfig) -> bool:
    """Extract resolution logic for testing -- mirrors LLMClient._should_stream."""
    if provider_config.streaming is not None:
        return provider_config.streaming
    return model_config.streaming_default
