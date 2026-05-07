"""Unit tests for CLI config template loading from config.toml."""

from __future__ import annotations

from agent_nexus.platform.agency.cli_backend.config_templates import (
    load_backend_configs_from_providers,
    load_routing_config,
)


class TestLoadBackendConfigs:
    def test_loads_cli_provider_only(self):
        providers = {
            "claude-code": {
                "api": "cli",
                "command": "claude",
                "args": ["-p"],
                "system_prompt_flag": "--system-prompt",
                "session_flag": "--resume",
                "output_format": "json",
                "output_format_flag": "--output-format",
                "model_map": {"sonnet": "claude-sonnet-4-20250514"},
                "json_paths": {
                    "text": "result",
                    "session_id": "session_id",
                    "model": "model",
                    "input_tokens": "usage.input_tokens",
                    "output_tokens": "usage.output_tokens",
                },
            },
            "openai": {
                "api": "openai-compatible",
                "base_url": "https://api.openai.com/v1",
            },
        }
        configs = load_backend_configs_from_providers(providers)
        assert "claude-code" in configs
        assert "openai" not in configs
        assert configs["claude-code"].command == "claude"
        assert configs["claude-code"].json_paths.text == "result"

    def test_ignores_non_cli_providers(self):
        providers = {"openai": {"api": "openai-compatible"}}
        configs = load_backend_configs_from_providers(providers)
        assert len(configs) == 0

    def test_text_mode_provider(self):
        providers = {
            "openclaw": {
                "api": "cli",
                "command": "openclaw",
                "args": ["agent", "-m"],
                "output_format": "text",
                "text_patterns": {
                    "session_id": r"session[:\s]+([a-f0-9-]+)",
                },
            },
        }
        configs = load_backend_configs_from_providers(providers)
        assert configs["openclaw"].output_format == "text"
        assert configs["openclaw"].text_patterns.session_id is not None


class TestLoadRoutingConfig:
    def test_loads_routing(self):
        raw = {
            "default": "claude-code",
            "fallback_enabled": False,
            "fallback_chain": ["gemini-cli"],
            "model_rules": {"anthropic:*": "claude-code"},
        }
        config = load_routing_config(raw)
        assert config.default == "claude-code"
        assert config.fallback_enabled is False
        assert config.model_rules["anthropic:*"] == "claude-code"

    def test_defaults(self):
        raw = {"default": "claude-code"}
        config = load_routing_config(raw)
        assert config.fallback_enabled is True
        assert config.fallback_chain == []
