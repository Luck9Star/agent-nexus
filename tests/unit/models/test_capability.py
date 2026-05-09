"""Unit tests for agent_nexus.models.capability module."""

import pytest

from agent_nexus.models.capability import (
    PROVIDER_DEFAULTS,
    ModelCapability,
    ModelCapabilityRegistry,
)

# ---------------------------------------------------------------------------
# ModelCapability dataclass basics
# ---------------------------------------------------------------------------


class TestModelCapability:
    def test_fields_present(self):
        """All fields should be present and typed."""
        cap = ModelCapability(
            model_id="test",
            provider="test",
            max_output_tokens=100,
            context_window=1000,
            supports_vision=False,
            supports_tool_use=True,
            supports_temperature=True,
            temperature_min=0.0,
            temperature_max=1.0,
            knowledge_cutoff="2025-01",
        )
        assert cap.model_id == "test"
        assert isinstance(cap.provider, str)
        assert isinstance(cap.max_output_tokens, int)
        assert isinstance(cap.context_window, int)
        assert isinstance(cap.supports_vision, bool)
        assert isinstance(cap.supports_tool_use, bool)
        assert isinstance(cap.supports_temperature, bool)
        assert isinstance(cap.temperature_min, float)
        assert isinstance(cap.temperature_max, float)
        assert isinstance(cap.knowledge_cutoff, str)


# ---------------------------------------------------------------------------
# ModelCapabilityRegistry — exact matching
# ---------------------------------------------------------------------------


class TestExactMatch:
    def test_claude_sonnet_4(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-sonnet-4-20250514")
        assert cap.provider == "anthropic"
        assert cap.max_output_tokens == 8192
        assert cap.context_window == 200000
        assert cap.supports_vision is True
        assert cap.supports_tool_use is True
        assert cap.knowledge_cutoff == "2025-04"

    def test_claude_opus_4(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-opus-4-20250116")
        assert cap.provider == "anthropic"
        assert cap.max_output_tokens == 8192
        assert cap.context_window == 200000

    def test_claude_3_5_sonnet(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-3-5-sonnet-20241022")
        assert cap.provider == "anthropic"
        assert cap.knowledge_cutoff == "2024-10"

    def test_claude_3_5_haiku(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-3-5-haiku-20241022")
        assert cap.provider == "anthropic"
        assert cap.supports_vision is True

    def test_claude_3_opus(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-3-opus-20240229")
        assert cap.provider == "anthropic"
        assert cap.max_output_tokens == 4096
        assert cap.context_window == 200000

    def test_gpt_4o(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("gpt-4o")
        assert cap.provider == "openai"
        assert cap.max_output_tokens == 16384
        assert cap.context_window == 128000
        assert cap.temperature_max == 2.0
        assert cap.supports_vision is True

    def test_gpt_4o_mini(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("gpt-4o-mini")
        assert cap.provider == "openai"
        assert cap.max_output_tokens == 16384

    def test_gpt_4_turbo(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("gpt-4-turbo")
        assert cap.provider == "openai"
        assert cap.context_window == 128000
        assert cap.knowledge_cutoff == "2024-04"

    def test_gpt_4(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("gpt-4")
        assert cap.provider == "openai"
        assert cap.supports_vision is False
        assert cap.context_window == 8192

    def test_gpt_3_5_turbo(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("gpt-3.5-turbo")
        assert cap.provider == "openai"
        assert cap.supports_tool_use is False
        assert cap.max_output_tokens == 4096

    def test_deepseek_chat(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("deepseek-chat")
        assert cap.provider == "deepseek"
        assert cap.context_window == 128000
        assert cap.supports_vision is False

    def test_deepseek_reasoner(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("deepseek-reasoner")
        assert cap.provider == "deepseek"
        assert cap.max_output_tokens == 8192

    def test_qwen_72b(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("qwen2.5-72b-instruct")
        assert cap.provider == "qwen"
        assert cap.context_window == 131072

    def test_qwen_32b(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("qwen2.5-32b-instruct")
        assert cap.provider == "qwen"
        assert cap.context_window == 131072

    def test_qwen_7b(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("qwen2.5-7b-instruct")
        assert cap.provider == "qwen"
        assert cap.context_window == 32768

    def test_minimax_m1(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("minimax-m1-0519")
        assert cap.provider == "minimax"
        assert cap.context_window == 1048576
        assert cap.supports_vision is True
        assert cap.temperature_max == 1.0

    def test_minimax_t1(self):
        reg = ModelCapabilityRegistry()
        cap = reg.get("minimax-t1-0519")
        assert cap.provider == "minimax"
        assert cap.context_window == 1048576
        assert cap.knowledge_cutoff == "2025-05"


# ---------------------------------------------------------------------------
# ModelCapabilityRegistry — fuzzy matching
# ---------------------------------------------------------------------------


class TestFuzzyMatch:
    def test_strip_date_suffix_to_base(self):
        """claude-sonnet-4-20250514 -> fuzzy match claude-sonnet-4 base."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-sonnet-4-20250514")
        assert cap.model_id == "claude-sonnet-4-20250514"
        assert cap.provider == "anthropic"
        assert cap.max_output_tokens == 8192

    def test_claude_3_5_sonnet_stripped_date(self):
        """claude-3-5-sonnet-20241022 -> exact match exists."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-3-5-sonnet-20241022")
        assert cap.model_id == "claude-3-5-sonnet-20241022"
        assert cap.provider == "anthropic"

    def test_unknown_model_falls_to_provider_default(self):
        """Unknown model like claude-4-experimental should get anthropic default."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-4-experimental")
        assert cap.provider == "anthropic"
        assert cap.max_output_tokens == 8192

    def test_unknown_openai_model_falls_to_provider_default(self):
        """Unknown GPT variant should get openai default."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("gpt-5-unknown")
        assert cap.provider == "openai"
        assert cap.max_output_tokens == 16384

    def test_unknown_deepseek_model(self):
        """Unknown deepseek model should get deepseek default."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("deepseek-coder-v3")
        assert cap.provider == "deepseek"
        assert cap.max_output_tokens == 8192

    def test_unknown_qwen_model(self):
        """Unknown qwen model should get qwen default."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("qwen3-110b-instruct")
        assert cap.provider == "qwen"
        assert cap.context_window == 131072

    def test_unknown_minimax_model(self):
        """Unknown minimax model should get minimax default."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("minimax-m2-9999")
        assert cap.provider == "minimax"
        assert cap.context_window == 1048576

    def test_ollama_fallback(self):
        """ollama model should get ollama default."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("ollama/llama3.3-70b")
        assert cap.provider == "ollama"
        assert cap.max_output_tokens == 4096

    def test_totally_unknown_provider_falls_to_generic(self):
        """A model id with no recognisable provider gets a safe generic default."""
        reg = ModelCapabilityRegistry()
        cap = reg.get("some-future-model-v2")
        # Falls to openai default based on provider heuristic
        assert cap.provider == "openai"
        assert cap.max_output_tokens >= 4096


# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------


class TestProviderDefaults:
    def test_anthropic_default(self):
        cap = ModelCapabilityRegistry().get_provider_default("anthropic")
        assert cap.max_output_tokens == 8192
        assert cap.context_window == 200000
        assert cap.supports_vision is True
        assert cap.temperature_max == 1.0

    def test_openai_default(self):
        cap = ModelCapabilityRegistry().get_provider_default("openai")
        assert cap.max_output_tokens == 16384
        assert cap.context_window == 128000
        assert cap.temperature_max == 2.0

    def test_deepseek_default(self):
        cap = ModelCapabilityRegistry().get_provider_default("deepseek")
        assert cap.max_output_tokens == 8192
        assert cap.context_window == 128000
        assert cap.supports_vision is False

    def test_qwen_default(self):
        cap = ModelCapabilityRegistry().get_provider_default("qwen")
        assert cap.max_output_tokens == 8192
        assert cap.context_window == 131072
        assert cap.supports_vision is False

    def test_minimax_default(self):
        cap = ModelCapabilityRegistry().get_provider_default("minimax")
        assert cap.max_output_tokens == 16384
        assert cap.context_window == 1048576
        assert cap.supports_vision is True

    def test_ollama_default(self):
        cap = ModelCapabilityRegistry().get_provider_default("ollama")
        assert cap.max_output_tokens == 4096
        assert cap.context_window == 8192
        assert cap.supports_vision is False

    def test_unknown_provider_returns_generic_default(self):
        """An unrecognised provider name should still return a sensible default."""
        cap = ModelCapabilityRegistry().get_provider_default("nonexistent_provider")
        assert cap.max_output_tokens == 4096
        assert cap.provider == "nonexistent_provider"


# ---------------------------------------------------------------------------
# Overrides support
# ---------------------------------------------------------------------------


class TestOverrides:
    def test_override_builtin(self):
        custom = ModelCapability(
            model_id="claude-sonnet-4-20250514",
            provider="anthropic",
            max_output_tokens=99999,
            context_window=500000,
            supports_vision=True,
            supports_tool_use=True,
            supports_temperature=True,
            temperature_min=0.0,
            temperature_max=1.0,
            knowledge_cutoff="2025-04",
        )
        reg = ModelCapabilityRegistry(overrides={"claude-sonnet-4-20250514": custom})
        cap = reg.get("claude-sonnet-4-20250514")
        assert cap.max_output_tokens == 99999
        assert cap.context_window == 500000

    def test_override_new_model(self):
        custom = ModelCapability(
            model_id="my-custom-model",
            provider="custom",
            max_output_tokens=4096,
            context_window=32000,
            supports_vision=False,
            supports_tool_use=False,
            supports_temperature=True,
            temperature_min=0.0,
            temperature_max=1.5,
            knowledge_cutoff="2025-06",
        )
        reg = ModelCapabilityRegistry(overrides={"my-custom-model": custom})
        cap = reg.get("my-custom-model")
        assert cap.model_id == "my-custom-model"
        assert cap.max_output_tokens == 4096

    def test_override_does_not_affect_other_models(self):
        custom = ModelCapability(
            model_id="gpt-4o",
            provider="openai",
            max_output_tokens=99999,
            context_window=128000,
            supports_vision=True,
            supports_tool_use=True,
            supports_temperature=True,
            temperature_min=0.0,
            temperature_max=2.0,
            knowledge_cutoff="2024-10",
        )
        reg = ModelCapabilityRegistry(overrides={"gpt-4o": custom})
        cap = reg.get("gpt-4o-mini")
        assert cap.max_output_tokens == 16384  # unchanged


# ---------------------------------------------------------------------------
# All built-in models have realistic values
# ---------------------------------------------------------------------------


class TestBuiltinCompleteness:
    """Verify that every built-in entry has non-zero / non-default values."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250116",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
            "deepseek-chat",
            "deepseek-reasoner",
            "qwen2.5-72b-instruct",
            "qwen2.5-32b-instruct",
            "qwen2.5-7b-instruct",
            "minimax-m1-0519",
            "minimax-t1-0519",
        ],
    )
    def test_builtin_has_realistic_values(self, model_id: str):
        reg = ModelCapabilityRegistry()
        cap = reg.get(model_id)
        assert cap.max_output_tokens > 0, f"{model_id}: max_output_tokens is 0"
        assert cap.context_window > 0, f"{model_id}: context_window is 0"
        assert 0.0 <= cap.temperature_min <= cap.temperature_max
        assert cap.temperature_max > 0.0
        assert len(cap.knowledge_cutoff) >= 7  # "YYYY-MM" or "YYYY-MM-DD"

    @pytest.mark.parametrize(
        "provider",
        ["anthropic", "openai", "deepseek", "qwen", "minimax", "ollama"],
    )
    def test_provider_defaults_have_realistic_values(self, provider: str):
        cap = PROVIDER_DEFAULTS[provider]
        assert cap.max_output_tokens > 0
        assert cap.context_window > 0
        assert 0.0 <= cap.temperature_min <= cap.temperature_max
        assert cap.temperature_max > 0.0
        assert len(cap.knowledge_cutoff) >= 7
