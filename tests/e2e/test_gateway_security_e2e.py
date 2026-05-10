"""E2E: Gateway Security — AuditLogger + GatewayAuthenticator end-to-end.

TRUE E2E tests verifying gateway security components:
  real SQLite database -> real AuditLogger -> real AuditEvent -> query back
  real GatewayAuthenticator -> SHA256 key verification -> client identity

All objects are real. Only tmp_path is used for SQLite file isolation.

Test sections:
  1. AuditLogger: record, query, export, rotation
  2. GatewayAuthenticator: valid/invalid keys, disabled state
  3. ToolAccessChecker: role-based access and rate limiting
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from agent_nexus.platform.gateway.audit import (
    AuditEvent,
    AuditFilter,
    AuditLogger,
)
from agent_nexus.platform.gateway.auth import (
    AuthenticatedClient,
    GatewayAuthConfig,
    GatewayAuthenticator,
    ToolAccessChecker,
    ToolAccessPolicy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    """Provide a temp SQLite database path."""
    return str(tmp_path / "test_audit.db")


@pytest.fixture()
async def audit_logger(db_path: str) -> AsyncGenerator[AuditLogger, None]:
    """Provide a real AuditLogger with temp SQLite."""
    logger = AuditLogger(db_path)
    yield logger


@pytest.fixture()
def auth_enabled() -> GatewayAuthenticator:
    """Authenticator with a known test key."""
    test_key = "test-secret-key-12345"
    key_hash = hashlib.sha256(test_key.encode()).hexdigest()
    config = GatewayAuthConfig(enabled=True, keys=[key_hash])
    return GatewayAuthenticator(config)


@pytest.fixture()
def auth_disabled() -> GatewayAuthenticator:
    """Authenticator with auth disabled (default)."""
    config = GatewayAuthConfig(enabled=False)
    return GatewayAuthenticator(config)


# ===========================================================================
# 1. AuditLogger: record, query, export, rotation
# ===========================================================================


class TestAuditLogger:
    """AuditLogger records events to real SQLite and queries them back."""

    async def test_log_tool_call_and_query(self, audit_logger: AuditLogger) -> None:
        """Log a tool_call event and query it back from SQLite."""
        event = AuditEvent(
            event_type="tool_call",
            client_id="client-1",
            agent_id="code-reviewer",
            tool_name="analyze_code",
            request_summary="Analyze main.py",
            response_status="success",
            duration_ms=42.5,
            metadata={"language": "python"},
        )
        await audit_logger.log(event)

        filt = AuditFilter(event_types=["tool_call"], limit=10)
        results = await audit_logger.query(filt)

        assert len(results) == 1
        retrieved = results[0]
        assert retrieved.event_type == "tool_call"
        assert retrieved.client_id == "client-1"
        assert retrieved.agent_id == "code-reviewer"
        assert retrieved.tool_name == "analyze_code"
        assert retrieved.request_summary == "Analyze main.py"
        assert retrieved.response_status == "success"
        assert retrieved.duration_ms == pytest.approx(42.5)
        assert retrieved.metadata == {"language": "python"}

    async def test_log_multiple_events_query_by_type(self, audit_logger: AuditLogger) -> None:
        """Log multiple event types and filter by type."""
        events = [
            AuditEvent(event_type="auth_success", client_id="client-1"),
            AuditEvent(event_type="auth_failure", client_id="client-2"),
            AuditEvent(event_type="tool_call", client_id="client-1", tool_name="read"),
            AuditEvent(event_type="tool_call", client_id="client-1", tool_name="write"),
            AuditEvent(event_type="agent_error", agent_id="agent-x"),
        ]
        for event in events:
            await audit_logger.log(event)

        # Filter for tool_call only
        filt = AuditFilter(event_types=["tool_call"])
        results = await audit_logger.query(filt)
        assert len(results) == 2
        assert all(r.event_type == "tool_call" for r in results)

        # Filter for auth events
        filt2 = AuditFilter(event_types=["auth_success", "auth_failure"])
        results2 = await audit_logger.query(filt2)
        assert len(results2) == 2

    async def test_query_by_client_id(self, audit_logger: AuditLogger) -> None:
        """Filter events by client_id."""
        for i in range(5):
            await audit_logger.log(AuditEvent(event_type="tool_call", client_id=f"client-{i % 2}"))

        filt = AuditFilter(client_id="client-0")
        results = await audit_logger.query(filt)
        assert len(results) == 3
        assert all(r.client_id == "client-0" for r in results)

    async def test_query_by_agent_id(self, audit_logger: AuditLogger) -> None:
        """Filter events by agent_id."""
        await audit_logger.log(AuditEvent(event_type="agent_activation", agent_id="doc-writer"))
        await audit_logger.log(AuditEvent(event_type="agent_activation", agent_id="code-reviewer"))

        filt = AuditFilter(agent_id="doc-writer")
        results = await audit_logger.query(filt)
        assert len(results) == 1
        assert results[0].agent_id == "doc-writer"

    async def test_query_by_tool_name(self, audit_logger: AuditLogger) -> None:
        """Filter events by tool_name."""
        await audit_logger.log(AuditEvent(event_type="tool_call", tool_name="search"))
        await audit_logger.log(AuditEvent(event_type="tool_call", tool_name="analyze"))

        filt = AuditFilter(tool_name="search")
        results = await audit_logger.query(filt)
        assert len(results) == 1
        assert results[0].tool_name == "search"

    async def test_query_by_time_range(self, audit_logger: AuditLogger) -> None:
        """Filter events by timestamp range."""
        now = time.time()
        await audit_logger.log(AuditEvent(event_type="tool_call", timestamp=now - 100))
        await audit_logger.log(AuditEvent(event_type="tool_call", timestamp=now))
        await audit_logger.log(AuditEvent(event_type="tool_call", timestamp=now + 100))

        # Only recent events
        filt = AuditFilter(since=now - 1, until=now + 1)
        results = await audit_logger.query(filt)
        assert len(results) == 1

    async def test_query_limit(self, audit_logger: AuditLogger) -> None:
        """Query respects the limit parameter."""
        for _ in range(20):
            await audit_logger.log(AuditEvent(event_type="tool_call", client_id="client-1"))

        filt = AuditFilter(limit=5)
        results = await audit_logger.query(filt)
        assert len(results) == 5

    async def test_request_summary_truncated(self, db_path: str) -> None:
        """request_summary is truncated to 200 chars."""
        logger = AuditLogger(db_path)
        long_summary = "x" * 300
        event = AuditEvent(
            event_type="tool_call",
            request_summary=long_summary,
        )
        await logger.log(event)

        filt = AuditFilter(limit=1)
        results = await logger.query(filt)
        assert len(results) == 1
        assert results[0].request_summary is not None
        assert len(results[0].request_summary) == 200

    async def test_export_json(self, audit_logger: AuditLogger) -> None:
        """Export audit events as JSON string."""
        since = time.time() - 1
        await audit_logger.log(AuditEvent(event_type="tool_call", client_id="export-test"))

        json_str = await audit_logger.export("json", since)
        assert "export-test" in json_str
        assert "tool_call" in json_str

    async def test_export_csv(self, audit_logger: AuditLogger) -> None:
        """Export audit events as CSV string."""
        since = time.time() - 1
        await audit_logger.log(AuditEvent(event_type="tool_call", client_id="csv-test"))

        csv_str = await audit_logger.export("csv", since)
        assert "csv-test" in csv_str
        assert "tool_call" in csv_str
        assert "event_id" in csv_str  # header row

    async def test_export_csv_empty(self, audit_logger: AuditLogger) -> None:
        """Export with no matching events returns empty string."""
        csv_str = await audit_logger.export("csv", time.time() + 100)
        assert csv_str == ""

    async def test_rotation_by_size(self, tmp_path: Path) -> None:
        """Audit log rotates when database exceeds max_size_mb."""
        db_path = str(tmp_path / "rotation_test.db")
        # Use very small max_size (0.001 MB = 1 KB) to trigger rotation quickly
        logger = AuditLogger(db_path, max_size_mb=0.001)

        # Insert events until rotation triggers
        for i in range(600):
            event = AuditEvent(
                event_type="tool_call",
                client_id=f"client-{i}",
                request_summary=f"Event number {i} with some padding data",
            )
            # Use synchronous insert to avoid event loop overhead
            logger._insert_event(event)

        # Rotation check happens in _rotate_if_needed which is called via log()
        # Force a rotation check
        logger._rotate_if_needed()

        # After rotation, the database should still be functional
        filt = AuditFilter(limit=5)
        results = logger._query_events(filt)
        # The fresh db should be empty (or nearly so) after rotation
        # But the archived file should exist
        import os

        bak_files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".bak")]
        assert len(bak_files) >= 1, "Expected at least one .bak archive file"

        # Fresh db should work for new inserts
        new_event = AuditEvent(event_type="auth_success", client_id="post-rotation")
        logger._insert_event(new_event)
        results = logger._query_events(AuditFilter(client_id="post-rotation"))
        assert len(results) == 1


# ===========================================================================
# 2. GatewayAuthenticator: valid/invalid keys, disabled state
# ===========================================================================


class TestGatewayAuthenticator:
    """GatewayAuthenticator verifies API keys against SHA256 hashes."""

    def test_valid_key_succeeds(self, auth_enabled: GatewayAuthenticator) -> None:
        """Known valid key authenticates successfully."""
        client = auth_enabled.authenticate("test-secret-key-12345")
        assert client.client_id.startswith("key-")
        assert len(client.client_id) == 12  # "key-" + 8-char hash prefix

    def test_invalid_key_raises_permission_error(self, auth_enabled: GatewayAuthenticator) -> None:
        """Wrong key raises PermissionError."""
        with pytest.raises(PermissionError, match="Invalid API key"):
            auth_enabled.authenticate("wrong-key")

    def test_empty_key_raises_permission_error(self, auth_enabled: GatewayAuthenticator) -> None:
        """Empty key raises PermissionError."""
        with pytest.raises(PermissionError, match="Invalid API key"):
            auth_enabled.authenticate("")

    def test_disabled_by_default(self, auth_disabled: GatewayAuthenticator) -> None:
        """Auth disabled returns anonymous client regardless of key."""
        client = auth_disabled.authenticate("anything")
        assert client.client_id == "anonymous"

    def test_disabled_ignores_key_value(self, auth_disabled: GatewayAuthenticator) -> None:
        """When disabled, even empty string authenticates as anonymous."""
        client = auth_disabled.authenticate("")
        assert client.client_id == "anonymous"

    def test_hash_key_deterministic(self) -> None:
        """hash_key produces consistent SHA256 output."""
        h1 = GatewayAuthenticator.hash_key("my-key")
        h2 = GatewayAuthenticator.hash_key("my-key")
        assert h1 == h2
        assert h1 == hashlib.sha256(b"my-key").hexdigest()

    def test_hash_key_different_inputs(self) -> None:
        """Different inputs produce different hashes."""
        h1 = GatewayAuthenticator.hash_key("key-a")
        h2 = GatewayAuthenticator.hash_key("key-b")
        assert h1 != h2

    def test_multiple_keys_accepted(self) -> None:
        """Authenticator accepts any of multiple configured keys."""
        key1_hash = GatewayAuthenticator.hash_key("key-one")
        key2_hash = GatewayAuthenticator.hash_key("key-two")
        config = GatewayAuthConfig(enabled=True, keys=[key1_hash, key2_hash])
        auth = GatewayAuthenticator(config)

        client1 = auth.authenticate("key-one")
        assert client1.client_id.startswith("key-")

        client2 = auth.authenticate("key-two")
        assert client2.client_id.startswith("key-")

        # Different keys produce different client_ids
        assert client1.client_id != client2.client_id

    def test_from_env_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env reads GATEWAY_AUTH_ENABLED and GATEWAY_AUTH_KEYS."""
        key_hash = GatewayAuthenticator.hash_key("env-test-key")
        monkeypatch.setenv("GATEWAY_AUTH_ENABLED", "true")
        monkeypatch.setenv("GATEWAY_AUTH_KEYS", key_hash)

        auth = GatewayAuthenticator.from_env()
        client = auth.authenticate("env-test-key")
        assert client.client_id.startswith("key-")

    def test_from_env_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env defaults to disabled when env vars not set."""
        monkeypatch.delenv("GATEWAY_AUTH_ENABLED", raising=False)
        monkeypatch.delenv("GATEWAY_AUTH_KEYS", raising=False)

        auth = GatewayAuthenticator.from_env()
        client = auth.authenticate("anything")
        assert client.client_id == "anonymous"

    def test_from_env_custom_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env respects custom prefix."""
        key_hash = GatewayAuthenticator.hash_key("custom-key")
        monkeypatch.setenv("MY_GATEWAY_AUTH_ENABLED", "true")
        monkeypatch.setenv("MY_GATEWAY_AUTH_KEYS", key_hash)

        auth = GatewayAuthenticator.from_env(prefix="MY_GATEWAY")
        client = auth.authenticate("custom-key")
        assert client.client_id.startswith("key-")


# ===========================================================================
# 3. ToolAccessChecker: role-based access and rate limiting
# ===========================================================================


class TestToolAccessChecker:
    """ToolAccessChecker enforces role-based tool access and rate limits."""

    def test_no_policies_allows_all(self) -> None:
        """No policies means all tools are allowed."""
        checker = ToolAccessChecker()
        client = AuthenticatedClient(client_id="anyone", roles=["default"])
        assert checker.is_tool_allowed(client, "any-tool") is True

    def test_matching_role_allows_tool(self) -> None:
        """Client with matching role can access allowed tool."""
        policy = ToolAccessPolicy(
            client_roles=["admin"],
            tools_allowed=["admin_*"],
            tools_denied=[],
        )
        checker = ToolAccessChecker(policies=[policy])
        admin = AuthenticatedClient(client_id="admin-1", roles=["admin"])

        assert checker.is_tool_allowed(admin, "admin_dashboard") is True

    def test_deny_overrides_allow(self) -> None:
        """Deny list takes priority over allow list."""
        policy = ToolAccessPolicy(
            client_roles=["user"],
            tools_allowed=["*"],
            tools_denied=["admin_*"],
        )
        checker = ToolAccessChecker(policies=[policy])
        user = AuthenticatedClient(client_id="user-1", roles=["user"])

        assert checker.is_tool_allowed(user, "read_file") is True
        assert checker.is_tool_allowed(user, "admin_settings") is False

    def test_unmatched_role_denied(self) -> None:
        """Client with no matching role is denied."""
        policy = ToolAccessPolicy(
            client_roles=["admin"],
            tools_allowed=["*"],
            tools_denied=[],
        )
        checker = ToolAccessChecker(policies=[policy])
        guest = AuthenticatedClient(client_id="guest-1", roles=["guest"])

        assert checker.is_tool_allowed(guest, "read_file") is False

    def test_glob_pattern_matching(self) -> None:
        """Tool names are matched with glob patterns."""
        policy = ToolAccessPolicy(
            client_roles=["dev"],
            tools_allowed=["code_*", "file_read"],
            tools_denied=["code_exec_*"],
        )
        checker = ToolAccessChecker(policies=[policy])
        dev = AuthenticatedClient(client_id="dev-1", roles=["dev"])

        assert checker.is_tool_allowed(dev, "code_review") is True
        assert checker.is_tool_allowed(dev, "file_read") is True
        assert checker.is_tool_allowed(dev, "code_exec_bash") is False
        assert checker.is_tool_allowed(dev, "file_write") is False

    def test_rate_limit_allows_up_to_cap(self) -> None:
        """Rate limiter allows calls up to the configured cap."""
        policy = ToolAccessPolicy(
            client_roles=["default"],
            tools_allowed=["*"],
            tools_denied=[],
            rate_limit=5,
        )
        checker = ToolAccessChecker(policies=[policy])

        for _ in range(5):
            assert checker.check_rate_limit("client-1") is True

        # 6th call should be rate limited
        assert checker.check_rate_limit("client-1") is False

    def test_rate_limit_per_client_isolation(self) -> None:
        """Rate limits are tracked per client independently."""
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

        # client-b is not affected by client-a's limit
        assert checker.check_rate_limit("client-b") is True

    def test_rate_limit_default_100(self) -> None:
        """Without explicit rate_limit, default is 100/min."""
        checker = ToolAccessChecker()

        # First 100 should all pass
        for _ in range(100):
            assert checker.check_rate_limit("heavy-client") is True

        # 101st should be rejected
        assert checker.check_rate_limit("heavy-client") is False

    def test_multiple_roles_match_first_policy(self) -> None:
        """Client with multiple roles matches if any role is in policy."""
        policy = ToolAccessPolicy(
            client_roles=["viewer", "editor"],
            tools_allowed=["read_*", "write_*"],
            tools_denied=[],
        )
        checker = ToolAccessChecker(policies=[policy])
        client = AuthenticatedClient(client_id="multi", roles=["viewer"])

        assert checker.is_tool_allowed(client, "read_docs") is True
