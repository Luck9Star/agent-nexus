"""Tests for iteration 19 — supervisor env forwarding."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agent_nexus.models.config import ModelConfig, ProviderConfig
from agent_nexus.models.distribution import LockfileEntry
from agent_nexus.platform.local.supervisor import AgentSupervisor


def _make_entry(name: str = "test-agent") -> LockfileEntry:
    return LockfileEntry(
        name=name,
        source="git+https://example.com/test-agent",
        version="1.0.0",
        commit_sha="abc123",
        agent_type="atomic",
    )


class TestSupervisorEnvForwarding:
    """_build_env must forward API keys from configured providers."""

    def test_forwards_api_keys(self, monkeypatch: object) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")  # type: ignore[attr-defined]
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key-456")  # type: ignore[attr-defined]

        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()

        model_config = ModelConfig(
            default="openai:gpt-4o",
            providers={
                "openai": ProviderConfig(
                    base_url="https://api.openai.com/v1",
                    api_key_env="OPENAI_API_KEY",
                ),
                "anthropic": ProviderConfig(
                    base_url="https://api.anthropic.com",
                    api_key_env="ANTHROPIC_API_KEY",
                ),
            },
        )
        config_loader.load_config.return_value = MagicMock(models=model_config)

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", _make_entry())

        assert env["AGENT_MODEL"] == "openai:gpt-4o"
        assert env["OPENAI_API_KEY"] == "sk-test-123"
        assert env["ANTHROPIC_API_KEY"] == "ant-key-456"

    def test_skips_empty_keys(self, monkeypatch: object) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # type: ignore[attr-defined]

        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()

        model_config = ModelConfig(
            default="openai:gpt-4o",
            providers={
                "openai": ProviderConfig(
                    base_url="https://api.openai.com/v1",
                    api_key_env="OPENAI_API_KEY",
                ),
            },
        )
        config_loader.load_config.return_value = MagicMock(models=model_config)

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", _make_entry())

        assert "OPENAI_API_KEY" not in env
        assert env["AGENT_MODEL"] == "openai:gpt-4o"

    def test_config_load_failure_does_not_crash(self) -> None:
        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()
        config_loader.load_config.side_effect = RuntimeError("config missing")

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", _make_entry())
        assert env == {}

    def test_provider_without_api_key_env(self) -> None:
        """Provider with empty api_key_env should not forward anything."""
        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()

        model_config = ModelConfig(
            default="ollama:llama3",
            providers={
                "ollama": ProviderConfig(
                    base_url="http://localhost:11434",
                    api_key_env="",
                ),
            },
        )
        config_loader.load_config.return_value = MagicMock(models=model_config)

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", _make_entry())
        assert env["AGENT_MODEL"] == "ollama:llama3"
        # No extra keys forwarded for ollama
        assert len(env) == 1
