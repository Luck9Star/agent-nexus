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
