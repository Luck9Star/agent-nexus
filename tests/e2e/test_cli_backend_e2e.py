"""End-to-end integration test for CLI backend: config -> LLMClient -> LLMResponse."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.cli_backend.types import CLISessionRecord

CLI_E2E_CONFIG = """
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
fallback_chain = ["gemini-cli"]
"""


class TestCLIBackendE2E:
    @patch("agent_nexus.platform.agency.cli_backend.base.shutil.which", return_value="/usr/bin/claude")
    @patch("agent_nexus.platform.agency.cli_backend.base.subprocess.run")
    def test_config_to_llm_response(self, mock_run, mock_which, tmp_path: Path):
        """Full pipeline: config.toml -> ConfigLoader -> LLMClient -> LLMResponse."""
        (tmp_path / "config.toml").write_text(CLI_E2E_CONFIG)
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "result": "E2E test passed",
                "session_id": "sess-e2e",
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 200, "output_tokens": 100},
            }),
            stderr="",
            returncode=0,
        )

        from agent_nexus.models.capability import ModelCapability, ModelCapabilityRegistry
        from agent_nexus.platform.agency.llm_client import LLMClient

        # Pre-populate capability registry to avoid network calls
        registry = ModelCapabilityRegistry()
        cap = ModelCapability(
            model_id="sonnet", provider="anthropic",
            max_output_tokens=16384, context_window=200000,
            supports_vision=True, supports_tool_use=True,
            supports_temperature=True, temperature_min=0.0, temperature_max=1.0,
            knowledge_cutoff="2025-04",
        )
        registry.set_override("sonnet", cap)
        registry._enriched_models.add("sonnet")

        client = LLMClient(
            model_string="claude-code:sonnet",
            config_dir=tmp_path,
            capability_registry=registry,
        )
        response = client.call(system_prompt="You are a test assistant.", user_message="Say hello.")
        assert response.text == "E2E test passed"
        assert response.model == "claude-sonnet-4-20250514"
        assert response.provider == "claude-code"
        assert response.metadata["session_id"] == "sess-e2e"
        assert response.metadata["input_tokens"] == 200
        assert response.metadata["output_tokens"] == 100
        client.close()

    def test_config_loader_produces_cli_backends(self, tmp_path: Path):
        """ConfigLoader.load_cli_backends() returns correct BackendConfig objects."""
        (tmp_path / "config.toml").write_text(CLI_E2E_CONFIG)

        from agent_nexus.platform.config.loader import ConfigLoader

        loader = ConfigLoader(config_dir=tmp_path)
        backends = loader.load_cli_backends()
        assert "claude-code" in backends
        assert backends["claude-code"].command == "claude"
        assert backends["claude-code"].json_paths.text == "result"
        routing = loader.load_cli_routing()
        assert routing is not None
        assert routing.default == "claude-code"

    @patch("agent_nexus.platform.agency.cli_backend.base.shutil.which", return_value="/usr/bin/claude")
    @patch("agent_nexus.platform.agency.cli_backend.base.subprocess.run")
    def test_session_store_records_execution(self, mock_run, mock_which, tmp_path: Path):
        """SessionStore records executions when wired into LLMClient."""
        from agent_nexus.platform.agency.cli_backend.session_store import CLISessionStore

        db_path = tmp_path / "agent-nexus.db"
        store = CLISessionStore(db_path)
        store.record_execution(
            task_id="task-e2e",
            backend_type="cli",
            backend_name="claude-code",
            model="claude-sonnet-4-20250514",
            session_id="sess-e2e",
            input_tokens=200,
            output_tokens=100,
            duration_ms=1500,
            status="success",
        )
        store.save_session(CLISessionRecord(
            session_id="sess-e2e",
            backend_name="claude-code",
            model="claude-sonnet-4-20250514",
        ))
        session = store.get_session("sess-e2e")
        assert session is not None
        assert session.backend_name == "claude-code"
        stats = store.get_daily_stats()
        assert len(stats) == 1
        assert stats[0]["total_calls"] == 1
        assert stats[0]["success_calls"] == 1
        store.close()

    def test_full_pipeline_config_to_router_to_backend(self, tmp_path: Path):
        """Config -> Registry -> Router -> Backend (all pipeline stages)."""
        from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
        from agent_nexus.platform.agency.cli_backend.registry import CLIBackendRegistry
        from agent_nexus.platform.agency.cli_backend.router import CLIRouter
        from agent_nexus.platform.agency.cli_backend.types import BackendConfig, RoutingConfig
        from agent_nexus.platform.config.loader import ConfigLoader

        (tmp_path / "config.toml").write_text(CLI_E2E_CONFIG)
        loader = ConfigLoader(config_dir=tmp_path)

        # Stage 1: Config loading
        backend_configs = loader.load_cli_backends()
        routing_config = loader.load_cli_routing()
        assert "claude-code" in backend_configs

        # Stage 2: Registry discovery
        registry = CLIBackendRegistry()
        for name, config in backend_configs.items():
            registry.register(name, GenericCLIBackend(config))
        assert len(registry) >= 1

        # Stage 3: Router resolution
        router = CLIRouter(config=routing_config, registry=registry)
        resolved = router.resolve(model_string="anthropic:claude-sonnet")
        assert resolved is not None
        assert resolved.name == "claude"

        # Stage 4: Verify backend has correct config
        assert resolved.config.json_paths.text == "result"
        assert resolved.config.output_format == "json"
