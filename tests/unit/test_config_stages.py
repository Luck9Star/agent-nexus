"""Tests for ModelConfig.stages and resolve_stage_model()."""

from agent_nexus.models.config import ModelConfig, PlatformConfig
from agent_nexus.platform.config.model_config import ModelConfigManager


def test_resolve_stage_model_returns_stage_override():
    config = PlatformConfig(
        models=ModelConfig(
            default="openai:gpt-4o",
            stages={"planning": "anthropic:claude-sonnet-4-20250514"},
        )
    )
    mgr = ModelConfigManager(config)
    assert mgr.resolve_stage_model("planning") == "anthropic:claude-sonnet-4-20250514"


def test_resolve_stage_model_falls_back_to_default():
    config = PlatformConfig(
        models=ModelConfig(default="openai:gpt-4o", stages={})
    )
    mgr = ModelConfigManager(config)
    assert mgr.resolve_stage_model("planning") == "openai:gpt-4o"


def test_resolve_stage_model_returns_none_when_no_default():
    config = PlatformConfig(models=ModelConfig(default="", stages={}))
    mgr = ModelConfigManager(config)
    assert mgr.resolve_stage_model("planning") is None


def test_resolve_stage_model_unknown_stage_uses_default():
    config = PlatformConfig(
        models=ModelConfig(
            default="openai:gpt-4o",
            stages={"planning": "anthropic:claude-sonnet-4-20250514"},
        )
    )
    mgr = ModelConfigManager(config)
    assert mgr.resolve_stage_model("integration") == "openai:gpt-4o"
