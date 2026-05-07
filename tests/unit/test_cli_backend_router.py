"""Unit tests for CLIRouter — 4-strategy routing with fallback chain."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
from agent_nexus.platform.agency.cli_backend.registry import CLIBackendRegistry
from agent_nexus.platform.agency.cli_backend.router import CLIRouter
from agent_nexus.platform.agency.cli_backend.types import BackendConfig, RoutingConfig


def _make_registry_with(
    names: list[str],
) -> tuple[CLIBackendRegistry, dict[str, GenericCLIBackend]]:
    registry = CLIBackendRegistry()
    backends = {}
    for name in names:
        config = BackendConfig(command=name, args=[])
        backend = GenericCLIBackend(config)
        registry.register(name, backend)
        backends[name] = backend
    return registry, backends


class TestCLIRouterExplicit:
    def test_explicit_backend(self):
        registry, _ = _make_registry_with(["claude-code", "gemini-cli"])
        router = CLIRouter(config=RoutingConfig(default="claude-code"), registry=registry)
        result = router.resolve(explicit_backend="gemini-cli")
        assert result.name == "gemini-cli"

    def test_explicit_backend_not_found_raises(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(config=RoutingConfig(default="claude-code"), registry=registry)
        with pytest.raises(KeyError):
            router.resolve(explicit_backend="nonexistent")


class TestCLIRouterModelRules:
    def test_model_rule_match(self):
        registry, _ = _make_registry_with(["claude-code", "gemini-cli"])
        router = CLIRouter(
            config=RoutingConfig(
                default="claude-code",
                model_rules={"anthropic:*": "claude-code", "google:*": "gemini-cli"},
            ),
            registry=registry,
        )
        result = router.resolve(model_string="anthropic:claude-sonnet-4-20250514")
        assert result.name == "claude-code"

    def test_model_rule_no_match_falls_to_default(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(
            config=RoutingConfig(default="claude-code", model_rules={"google:*": "gemini-cli"}),
            registry=registry,
        )
        result = router.resolve(model_string="openai:gpt-4o")
        assert result.name == "claude-code"


class TestCLIRouterDefault:
    def test_default_fallback(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(config=RoutingConfig(default="claude-code"), registry=registry)
        result = router.resolve()
        assert result.name == "claude-code"


class TestCLIRouterFallback:
    @patch("shutil.which", side_effect=lambda cmd: f"/bin/{cmd}" if cmd == "gemini-cli" else None)
    def test_fallback_chain_on_unavailable(self, mock_which):
        registry, backends = _make_registry_with(["claude-code", "gemini-cli", "codex-cli"])
        router = CLIRouter(
            config=RoutingConfig(
                default="claude-code",
                fallback_enabled=True,
                fallback_chain=["gemini-cli", "codex-cli"],
            ),
            registry=registry,
        )
        result = router.resolve_with_fallback()
        assert result.name == "gemini-cli"

    def test_fallback_disabled_raises(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(
            config=RoutingConfig(default="claude-code", fallback_enabled=False),
            registry=registry,
        )
        with pytest.raises(RuntimeError, match="Fallback disabled"):
            router.resolve_with_fallback(explicit_backend="nonexistent")
