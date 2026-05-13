"""Unit tests for GatewayAuth, ToolAccessPolicy, and ToolAccessChecker."""

from __future__ import annotations

import hashlib

import pytest

from agent_nexus.platform.gateway.auth import (
    AuthenticatedClient,
    GatewayAuthConfig,
    GatewayAuthenticator,
    ToolAccessChecker,
    ToolAccessPolicy,
)

# ============================================================================
# GatewayAuthConfig defaults
# ============================================================================


class TestGatewayAuthConfig:
    def test_enabled_with_keys(self) -> None:
        config = GatewayAuthConfig(enabled=True, keys=["abc123"])
        assert config.enabled is True
        assert config.keys == ["abc123"]


# ============================================================================
# GatewayAuthenticator.authenticate
# ============================================================================


class TestGatewayAuthenticatorAuthenticate:
    def test_disabled_returns_anonymous(self) -> None:
        auth = GatewayAuthenticator(GatewayAuthConfig())
        client = auth.authenticate("any-key")
        assert client.client_id == "anonymous"
        assert client.roles == ["default"]

    def test_enabled_valid_key(self) -> None:
        key = "my-secret-key"
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        config = GatewayAuthConfig(enabled=True, keys=[key_hash])
        auth = GatewayAuthenticator(config)
        client = auth.authenticate(key)
        assert client.client_id.startswith("key-")
        assert client.authenticated_at > 0

    def test_enabled_invalid_key_raises(self) -> None:
        config = GatewayAuthConfig(enabled=True, keys=["deadbeef"])
        auth = GatewayAuthenticator(config)
        with pytest.raises(PermissionError, match="Invalid API key"):
            auth.authenticate("wrong-key")

    def test_multiple_keys(self) -> None:
        key1 = "key-one"
        key2 = "key-two"
        hash1 = hashlib.sha256(key1.encode()).hexdigest()
        hash2 = hashlib.sha256(key2.encode()).hexdigest()
        config = GatewayAuthConfig(enabled=True, keys=[hash1, hash2])
        auth = GatewayAuthenticator(config)
        # Both keys should work
        assert auth.authenticate(key1).client_id.startswith("key-")
        assert auth.authenticate(key2).client_id.startswith("key-")


# ============================================================================
# GatewayAuthenticator.from_env
# ============================================================================


class TestFromEnv:
    def test_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        key = "env-key"
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
        monkeypatch.setenv("GATEWAY_AUTH_KEYS", key_hash)
        auth = GatewayAuthenticator.from_env()
        client = auth.authenticate(key)
        assert client.client_id.startswith("key-")

    def test_defaults_to_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_AUTH_ENABLED", raising=False)
        monkeypatch.delenv("GATEWAY_AUTH_KEYS", raising=False)
        auth = GatewayAuthenticator.from_env()
        client = auth.authenticate("anything")
        assert client.client_id == "anonymous"

    def test_custom_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        key = "custom-key"
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        monkeypatch.setenv("MY_AUTH_ENABLED", "true")
        monkeypatch.setenv("MY_AUTH_KEYS", key_hash)
        auth = GatewayAuthenticator.from_env(prefix="MY")
        client = auth.authenticate(key)
        assert client.client_id.startswith("key-")

    def test_multiple_keys_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h1 = hashlib.sha256(b"k1").hexdigest()
        h2 = hashlib.sha256(b"k2").hexdigest()
        monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
        monkeypatch.setenv("GATEWAY_AUTH_KEYS", f"{h1}, {h2}")
        auth = GatewayAuthenticator.from_env()
        assert auth.authenticate("k1").client_id.startswith("key-")
        assert auth.authenticate("k2").client_id.startswith("key-")


# ============================================================================
# ToolAccessChecker.is_tool_allowed
# ============================================================================


class TestToolAccessCheckerAllowed:
    def _make_client(self, roles: list[str] | None = None) -> AuthenticatedClient:
        return AuthenticatedClient(
            client_id="test-client",
            roles=roles or ["default"],
        )

    def test_no_policies_allows_all(self) -> None:
        checker = ToolAccessChecker()
        client = self._make_client()
        assert checker.is_tool_allowed(client, "any_tool") is True

    def test_allowed_glob(self) -> None:
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["search_*", "list_*"],
            tools_denied=[],
        )
        checker = ToolAccessChecker(policies=[policy])
        client = self._make_client()
        assert checker.is_tool_allowed(client, "search_agents") is True
        assert checker.is_tool_allowed(client, "list_tools") is True
        assert checker.is_tool_allowed(client, "delete_agent") is False

    def test_denied_takes_priority(self) -> None:
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["*"],
            tools_denied=["admin_*"],
        )
        checker = ToolAccessChecker(policies=[policy])
        client = self._make_client()
        assert checker.is_tool_allowed(client, "admin_delete") is False
        assert checker.is_tool_allowed(client, "search_agents") is True

    def test_role_not_matched_denies(self) -> None:
        policy = ToolAccessPolicy(
            client_roles=["admin"],
            tools_allowed=["*"],
            tools_denied=[],
        )
        checker = ToolAccessChecker(policies=[policy])
        client = self._make_client(roles=["guest"])
        assert checker.is_tool_allowed(client, "any_tool") is False

    def test_exact_name_match(self) -> None:
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["search_agents"],
            tools_denied=[],
        )
        checker = ToolAccessChecker(policies=[policy])
        client = self._make_client()
        assert checker.is_tool_allowed(client, "search_agents") is True
        assert checker.is_tool_allowed(client, "search_tools") is False


# ============================================================================
# ToolAccessChecker.check_rate_limit
# ============================================================================


class TestToolAccessCheckerRateLimit:
    def test_within_limit(self) -> None:
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["*"],
            tools_denied=[],
            rate_limit=5,
        )
        checker = ToolAccessChecker(policies=[policy])
        for _ in range(5):
            assert checker.check_rate_limit("client-1") is True

    def test_over_limit(self) -> None:
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["*"],
            tools_denied=[],
            rate_limit=3,
        )
        checker = ToolAccessChecker(policies=[policy])
        for _ in range(3):
            assert checker.check_rate_limit("client-1") is True
        assert checker.check_rate_limit("client-1") is False

    def test_default_limit_100(self) -> None:
        # No rate_limit set in policy -> default 100
        checker = ToolAccessChecker()
        # 100 calls should all succeed
        for _ in range(100):
            assert checker.check_rate_limit("client-1") is True
        # 101st should fail
        assert checker.check_rate_limit("client-1") is False

    def test_independent_clients(self) -> None:
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["*"],
            tools_denied=[],
            rate_limit=2,
        )
        checker = ToolAccessChecker(policies=[policy])
        assert checker.check_rate_limit("client-a") is True
        assert checker.check_rate_limit("client-a") is True
        assert checker.check_rate_limit("client-a") is False
        # client-b is independent
        assert checker.check_rate_limit("client-b") is True


# ============================================================================
# Glob pattern matching (indirect via is_tool_allowed)
# ============================================================================


class TestGlobPatternMatching:
    def _make_checker(self, allowed: list[str], denied: list[str]) -> ToolAccessChecker:
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=allowed,
            tools_denied=denied,
        )
        return ToolAccessChecker(policies=[policy])

    def test_prefix_glob(self) -> None:
        checker = self._make_checker(["search_*"], [])
        client = AuthenticatedClient(client_id="c", roles=["default"])
        assert checker.is_tool_allowed(client, "search_agents") is True
        assert checker.is_tool_allowed(client, "list_agents") is False

    def test_question_mark_single_char(self) -> None:
        checker = self._make_checker(["tool_?"], [])
        client = AuthenticatedClient(client_id="c", roles=["default"])
        assert checker.is_tool_allowed(client, "tool_a") is True
        assert checker.is_tool_allowed(client, "tool_ab") is False
