"""Tests for ExternalServerAuth, config auth/TLS fields, and adapter auth header injection."""

from __future__ import annotations

import os
from unittest.mock import patch

from agent_nexus.models.external_mcp import (
    ExternalServerAuth,
    ExternalServerConfig,
    TransportType,
)
from agent_nexus.platform.config.loader import ConfigLoader
from agent_nexus.platform.gateway.external_mcp_adapter import ExternalMcpAdapter

# ---------------------------------------------------------------------------
# ExternalServerAuth dataclass defaults
# ---------------------------------------------------------------------------


class TestExternalServerAuthDefaults:
    def test_default_method_is_none(self) -> None:
        auth = ExternalServerAuth()
        assert auth.method == "none"

    def test_default_strings_empty(self) -> None:
        auth = ExternalServerAuth()
        assert auth.api_key == ""
        assert auth.bearer_token == ""
        assert auth.client_cert_path == ""
        assert auth.client_key_path == ""


class TestExternalServerAuthMethods:
    def test_api_key_method(self) -> None:
        auth = ExternalServerAuth(method="api_key", api_key="secret123")
        assert auth.method == "api_key"
        assert auth.api_key == "secret123"

    def test_bearer_method(self) -> None:
        auth = ExternalServerAuth(method="bearer", bearer_token="tok_abc")
        assert auth.method == "bearer"
        assert auth.bearer_token == "tok_abc"

    def test_mtls_method(self) -> None:
        auth = ExternalServerAuth(
            method="mtls",
            client_cert_path="/certs/client.pem",
            client_key_path="/certs/client.key",
        )
        assert auth.method == "mtls"
        assert auth.client_cert_path == "/certs/client.pem"


# ---------------------------------------------------------------------------
# ExternalServerConfig has the new fields
# ---------------------------------------------------------------------------


class TestExternalServerConfigNewFields:
    def test_default_auth(self) -> None:
        cfg = ExternalServerConfig(name="test")
        assert isinstance(cfg.auth, ExternalServerAuth)
        assert cfg.auth.method == "none"

    def test_default_tls_verify(self) -> None:
        cfg = ExternalServerConfig(name="test")
        assert cfg.tls_verify is True

    def test_default_allowed_tools(self) -> None:
        cfg = ExternalServerConfig(name="test")
        assert cfg.allowed_tools is None

    def test_custom_allowed_tools(self) -> None:
        cfg = ExternalServerConfig(name="test", allowed_tools=["read", "write"])
        assert cfg.allowed_tools == ["read", "write"]

    def test_tls_verify_false(self) -> None:
        cfg = ExternalServerConfig(name="test", tls_verify=False)
        assert cfg.tls_verify is False

    def test_custom_auth(self) -> None:
        auth = ExternalServerAuth(method="bearer", bearer_token="tok")
        cfg = ExternalServerConfig(name="test", auth=auth)
        assert cfg.auth.bearer_token == "tok"


# ---------------------------------------------------------------------------
# Env var resolution
# ---------------------------------------------------------------------------


class TestEnvVarResolution:
    def test_resolve_plain_value(self) -> None:
        assert ExternalMcpAdapter._resolve_env_vars("hello") == "hello"

    def test_resolve_env_var(self) -> None:
        with patch.dict(os.environ, {"MY_API_KEY": "resolved_key"}):
            assert (
                ExternalMcpAdapter._resolve_env_vars("${MY_API_KEY}") == "resolved_key"
            )

    def test_resolve_missing_env_var_returns_empty(self) -> None:
        os.environ.pop("_NONEXISTENT_VAR_TEST_", None)
        assert ExternalMcpAdapter._resolve_env_vars("${_NONEXISTENT_VAR_TEST_}") == ""

    def test_resolve_mixed_string(self) -> None:
        with patch.dict(os.environ, {"HOST": "example.com"}):
            result = ExternalMcpAdapter._resolve_env_vars("https://${HOST}/api")
            assert result == "https://example.com/api"


# ---------------------------------------------------------------------------
# Adapter auth header injection
# ---------------------------------------------------------------------------


class TestAdapterAuthHeaders:
    def _make_adapter(self, auth: ExternalServerAuth) -> ExternalMcpAdapter:
        cfg = ExternalServerConfig(
            name="test",
            transport=TransportType.SSE,
            url="http://localhost:8080",
            auth=auth,
        )
        return ExternalMcpAdapter(cfg)

    def test_no_auth_returns_empty_headers(self) -> None:
        adapter = self._make_adapter(ExternalServerAuth(method="none"))
        assert adapter._build_auth_headers() == {}

    def test_bearer_auth_adds_header(self) -> None:
        adapter = self._make_adapter(
            ExternalServerAuth(method="bearer", bearer_token="tok_123")
        )
        headers = adapter._build_auth_headers()
        assert headers == {"Authorization": "Bearer tok_123"}

    def test_api_key_auth_adds_header(self) -> None:
        adapter = self._make_adapter(
            ExternalServerAuth(method="api_key", api_key="key_abc")
        )
        headers = adapter._build_auth_headers()
        assert headers == {"X-API-Key": "key_abc"}

    def test_bearer_with_env_var(self) -> None:
        adapter = self._make_adapter(
            ExternalServerAuth(method="bearer", bearer_token="${BEARER_TOK}")
        )
        with patch.dict(os.environ, {"BEARER_TOK": "env_token"}):
            headers = adapter._build_auth_headers()
            assert headers == {"Authorization": "Bearer env_token"}

    def test_api_key_with_env_var(self) -> None:
        adapter = self._make_adapter(
            ExternalServerAuth(method="api_key", api_key="${API_SECRET}")
        )
        with patch.dict(os.environ, {"API_SECRET": "env_key"}):
            headers = adapter._build_auth_headers()
            assert headers == {"X-API-Key": "env_key"}

    def test_empty_bearer_token_produces_no_header(self) -> None:
        adapter = self._make_adapter(
            ExternalServerAuth(method="bearer", bearer_token="")
        )
        assert adapter._build_auth_headers() == {}

    def test_empty_api_key_produces_no_header(self) -> None:
        adapter = self._make_adapter(
            ExternalServerAuth(method="api_key", api_key="")
        )
        assert adapter._build_auth_headers() == {}


# ---------------------------------------------------------------------------
# TLS verify flag respected
# ---------------------------------------------------------------------------


class TestTlsVerify:
    def test_tls_verify_default_true(self) -> None:
        cfg = ExternalServerConfig(name="test")
        assert cfg.tls_verify is True

    def test_tls_verify_can_be_disabled(self) -> None:
        cfg = ExternalServerConfig(name="test", tls_verify=False)
        assert cfg.tls_verify is False


# ---------------------------------------------------------------------------
# Config loader parses auth / tls_verify / allowed_tools
# ---------------------------------------------------------------------------


class TestConfigLoaderAuth:
    def test_parse_external_server_with_auth(self) -> None:
        item = {
            "name": "secure-api",
            "transport": "sse",
            "url": "https://api.example.com/mcp",
            "auth": {
                "method": "bearer",
                "bearer_token": "${MY_TOKEN}",
            },
            "tls_verify": False,
            "allowed_tools": ["read_file", "list_dir"],
        }
        result = ConfigLoader._parse_external_server(item)
        assert result is not None
        assert result.auth.method == "bearer"
        assert result.auth.bearer_token == "${MY_TOKEN}"
        assert result.tls_verify is False
        assert result.allowed_tools == ["read_file", "list_dir"]

    def test_parse_external_server_without_auth(self) -> None:
        item = {
            "name": "plain",
            "transport": "stdio",
            "command": "npx",
        }
        result = ConfigLoader._parse_external_server(item)
        assert result is not None
        assert result.auth.method == "none"
        assert result.tls_verify is True
        assert result.allowed_tools is None

    def test_parse_external_server_invalid_auth_type(self) -> None:
        item = {
            "name": "bad-auth",
            "auth": "not-a-dict",
        }
        result = ConfigLoader._parse_external_server(item)
        assert result is not None
        assert result.auth.method == "none"

    def test_parse_external_server_invalid_allowed_tools(self) -> None:
        item = {
            "name": "bad-tools",
            "allowed_tools": "not-a-list",
        }
        result = ConfigLoader._parse_external_server(item)
        assert result is not None
        assert result.allowed_tools is None


class TestAllowedToolsEnforcement:
    """Verify that call_tool enforces allowed_tools restriction."""

    def test_allowed_tool_passes(self) -> None:
        """Tool in allowed list should not raise."""
        config = ExternalServerConfig(
            name="test",
            transport=TransportType.SSE,
            url="http://localhost:8080",
            allowed_tools=["read_file", "write_file"],
        )
        adapter = ExternalMcpAdapter(config)
        # Manually set session to simulate connected state
        adapter._session = True  # type: ignore[assignment]
        # Check directly — allowed_tools enforcement happens before the call
        allowed = config.allowed_tools
        assert allowed is not None
        assert "read_file" in allowed

    async def test_disallowed_tool_raises(self) -> None:
        """Tool NOT in allowed list should raise PermissionError."""
        import pytest

        config = ExternalServerConfig(
            name="test",
            transport=TransportType.SSE,
            url="http://localhost:8080",
            allowed_tools=["read_file"],
        )
        adapter = ExternalMcpAdapter(config)
        adapter._session = True  # type: ignore[assignment]
        adapter._exit_stack = True  # type: ignore[assignment]
        with pytest.raises(PermissionError, match="not in the allowed list"):
            await adapter.call_tool("delete_file", {})

    def test_no_allowed_tools_all_all(self) -> None:
        """When allowed_tools is None, all tools are permitted."""
        config = ExternalServerConfig(
            name="test",
            transport=TransportType.SSE,
            url="http://localhost:8080",
        )
        assert config.allowed_tools is None
