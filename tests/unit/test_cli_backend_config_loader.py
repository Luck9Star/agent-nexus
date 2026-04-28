"""Unit tests for ConfigLoader CLI backend config integration."""
from __future__ import annotations

from pathlib import Path

from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config.loader import ConfigLoader

CLI_CONFIG = """
[models]
default = "claude-code:sonnet"

[models.providers.claude-code]
api = "cli"
command = "claude"
args = ["-p"]
output_format = "json"
output_format_flag = "--output-format"

[models.providers.claude-code.json_paths]
text = "result"
session_id = "session_id"
model = "model"
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"

[models.providers.openai]
api = "openai-compatible"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[cli_routing]
default = "claude-code"
fallback_enabled = true
fallback_chain = ["gemini-cli", "codex-cli"]

[cli_routing.model_rules]
"anthropic:*" = "claude-code"
"google:*" = "gemini-cli"
"""


class TestConfigLoaderCLIBackends:
    def test_cli_provider_produces_backend_config(self, tmp_path: Path):
        (tmp_path / "config.toml").write_text(CLI_CONFIG)
        loader = ConfigLoader(config_dir=tmp_path)
        backends = loader.load_cli_backends()
        assert "claude-code" in backends
        assert backends["claude-code"].command == "claude"
        assert backends["claude-code"].json_paths.text == "result"
        assert backends["claude-code"].output_format == "json"

    def test_regular_provider_not_in_cli_backends(self, tmp_path: Path):
        (tmp_path / "config.toml").write_text(CLI_CONFIG)
        loader = ConfigLoader(config_dir=tmp_path)
        backends = loader.load_cli_backends()
        assert "openai" not in backends
        config = loader.load_config()
        assert "openai" in config.models.providers
        assert config.models.providers["openai"].api == ProviderApiType.OPENAI_COMPATIBLE

    def test_cli_routing_loaded(self, tmp_path: Path):
        (tmp_path / "config.toml").write_text(CLI_CONFIG)
        loader = ConfigLoader(config_dir=tmp_path)
        routing = loader.load_cli_routing()
        assert routing is not None
        assert routing.default == "claude-code"
        assert routing.fallback_enabled is True
        assert routing.fallback_chain == ["gemini-cli", "codex-cli"]
        assert "anthropic:*" in routing.model_rules

    def test_no_cli_providers_returns_empty(self, tmp_path: Path):
        (tmp_path / "config.toml").write_text("""
[models]
default = "openai:gpt-4o"

[models.providers.openai]
api = "openai-compatible"
""")
        loader = ConfigLoader(config_dir=tmp_path)
        assert len(loader.load_cli_backends()) == 0
        assert loader.load_cli_routing() is None
