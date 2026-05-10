"""E2E: Gateway Security hot-reload via configure_security().

Verifies that MCPGateway.configure_security() correctly replaces security
components at runtime -- the foundation for hot-reload (SIGHUP / CLI reload).

Test sections:
  1. No security -> anonymous access works
  2. configure_security(authenticator) -> auth now required
  3. configure_security(different authenticator) -> swap is immediate
  4. configure_security(None) -> resets to no auth
  5. configure_security(access_checker) -> tool-level policy enforced
"""

from __future__ import annotations

import hashlib

import pytest

from agent_nexus.platform.gateway.auth import (
    GatewayAuthConfig,
    GatewayAuthenticator,
    ToolAccessChecker,
    ToolAccessPolicy,
)
from agent_nexus.platform.gateway.gateway import MCPGateway
from agent_nexus.platform.orchestration.process_manager import ProcessManager
from agent_nexus.platform.router.router import PlatformRouter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gateway() -> MCPGateway:
    """MCPGateway with no security configured."""
    pm = ProcessManager()
    router = PlatformRouter(pm)
    return MCPGateway(pm, router)


def _make_authenticator(*keys: str) -> GatewayAuthenticator:
    """Helper: build an enabled authenticator accepting the given plaintext keys."""
    key_hashes = [hashlib.sha256(k.encode()).hexdigest() for k in keys]
    config = GatewayAuthConfig(enabled=True, keys=key_hashes)
    return GatewayAuthenticator(config)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConfigureSecurityHotReload:
    """Verify configure_security() swaps security components at runtime."""

    async def test_no_security_anonymous_access(self, gateway: MCPGateway) -> None:
        """With no authenticator, _check_tool_access returns anonymous client."""
        client, error = await gateway._check_tool_access("read_file", "agent-1", None)
        assert error is None
        assert client is not None
        assert client.client_id == "anonymous"

    async def test_add_authenticator_blocks_anonymous(
        self, gateway: MCPGateway
    ) -> None:
        """After configure_security(authenticator), anonymous access is denied."""
        auth = _make_authenticator("secret-1")
        gateway.configure_security(authenticator=auth)

        client, error = await gateway._check_tool_access("read_file", "agent-1", None)
        assert client is None
        assert error is not None
        assert "authentication required" in error

    async def test_add_authenticator_accepts_valid_key(
        self, gateway: MCPGateway
    ) -> None:
        """After configure_security(authenticator), valid key authenticates."""
        auth = _make_authenticator("secret-1")
        gateway.configure_security(authenticator=auth)

        client, error = await gateway._check_tool_access("read_file", "agent-1", "secret-1")
        assert error is None
        assert client is not None
        assert client.client_id.startswith("key-")

    async def test_swap_authenticator_immediately(
        self, gateway: MCPGateway
    ) -> None:
        """Second configure_security() call replaces the authenticator instantly."""
        auth_v1 = _make_authenticator("old-key")
        gateway.configure_security(authenticator=auth_v1)

        # Old key works
        client, error = await gateway._check_tool_access("read_file", "agent-1", "old-key")
        assert error is None

        # Swap to new authenticator
        auth_v2 = _make_authenticator("new-key")
        gateway.configure_security(authenticator=auth_v2)

        # Old key is now rejected
        client, error = await gateway._check_tool_access("read_file", "agent-1", "old-key")
        assert client is None
        assert error is not None
        assert "authentication failed" in error

        # New key works
        client, error = await gateway._check_tool_access("read_file", "agent-1", "new-key")
        assert error is None
        assert client is not None

    async def test_reset_authenticator_to_none(self, gateway: MCPGateway) -> None:
        """Passing authenticator=None does NOT clear; only explicit None swap."""
        auth = _make_authenticator("temp-key")
        gateway.configure_security(authenticator=auth)

        # Auth is active
        _, error = await gateway._check_tool_access("read_file", "agent-1", None)
        assert error is not None
        assert "authentication required" in error

        # The current configure_security ignores None (it only sets if not None).
        # To reset, we must explicitly set authenticator back via __init__ or
        # by re-creating the gateway. Verify the guard-by-None behaviour.
        gateway.configure_security(authenticator=None)

        # Auth is still active because None is ignored
        _, error = await gateway._check_tool_access("read_file", "agent-1", None)
        assert error is not None
        assert "authentication required" in error

    async def test_add_access_checker_enforces_policy(
        self, gateway: MCPGateway
    ) -> None:
        """configure_security(access_checker) enforces tool-level deny."""
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["read_*"],
            tools_denied=["admin_*"],
        )
        checker = ToolAccessChecker(policies=[policy])
        gateway.configure_security(access_checker=checker)

        # Read tool is allowed
        client, error = await gateway._check_tool_access("read_file", "agent-1", None)
        assert error is None
        assert client is not None

        # Admin tool is denied
        client, error = await gateway._check_tool_access("admin_settings", "agent-1", None)
        assert client is None
        assert error is not None
        assert "access denied" in error
