"""Unit tests for HookExecutor internals: close, filtering, validation, security.

These tests exercise individual methods of HookExecutor without running
real subprocesses or HTTP requests, focusing on input validation,
security checks, and resource cleanup.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.hooks import HookDefinition, HookEvent, HookType
from agent_nexus.platform.hooks.executor import HookExecutor, _is_private_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cmd_hook(
    *,
    event: HookEvent = HookEvent.PRE_EXECUTION,
    command: str | None = "echo hello",
    block_on_failure: bool = False,
    enabled: bool = True,
    matcher: str | None = None,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.COMMAND,
        event=event,
        command=command,
        block_on_failure=block_on_failure,
        matcher=matcher,
        enabled=enabled,
    )


def _http_hook(
    *,
    url: str | None = "https://example.com/hook",
    event: HookEvent = HookEvent.POST_EXECUTION,
    block_on_failure: bool = False,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.HTTP,
        event=event,
        url=url,
        block_on_failure=block_on_failure,
    )


# ======================================================================
# A) close() async resource cleanup (P0)
# ======================================================================


class TestClose:
    """HookExecutor.close() manages HTTP client lifecycle."""

    @pytest.mark.asyncio
    async def test_close_no_http_client_is_noop(self) -> None:
        """close() on an executor that never created an HTTP client is safe."""
        executor = HookExecutor()
        assert executor._http_client is None
        await executor.close()
        # Still None — no crash, no allocation
        assert executor._http_client is None

    @pytest.mark.asyncio
    async def test_close_calls_aclose_on_open_client(self) -> None:
        """close() aclose()s the HTTP client when it is open."""
        executor = HookExecutor()
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        executor._http_client = mock_client

        await executor.close()
        mock_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_idempotent_when_already_closed(self) -> None:
        """Calling close() twice does not double-aclose()."""
        executor = HookExecutor()
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        executor._http_client = mock_client

        await executor.close()
        # After close, mark as closed
        mock_client.is_closed = True

        await executor.close()
        # aclose was called exactly once
        mock_client.aclose.assert_awaited_once()


# ======================================================================
# B) get_hooks_for_event() filtering
# ======================================================================


class TestGetHooksForEventFiltering:
    """get_hooks_for_event filters by event, matcher, and enabled status."""

    def test_returns_only_matching_event_hooks(self) -> None:
        hooks = [
            _cmd_hook(event=HookEvent.PRE_EXECUTION),
            _cmd_hook(event=HookEvent.POST_EXECUTION),
            _cmd_hook(event=HookEvent.PRE_EXECUTION),
            _cmd_hook(event=HookEvent.ON_ERROR),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["echo"])

        pre = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION)
        assert len(pre) == 2
        assert all(h.event == HookEvent.PRE_EXECUTION for h in pre)

    def test_fnmatch_matcher_filters_correctly(self) -> None:
        """Matcher uses fnmatch: 'read_*' matches 'read_file' but not 'write_file'."""
        hooks = [
            _cmd_hook(matcher="read_*"),
            _cmd_hook(matcher="write_*"),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["echo"])

        result = executor.get_hooks_for_event(
            HookEvent.PRE_EXECUTION, matcher="read_file"
        )
        assert len(result) == 1
        assert result[0].matcher == "read_*"

    def test_matcher_none_on_hook_matches_everything(self) -> None:
        """Hooks with matcher=None match regardless of the query matcher."""
        hooks = [
            _cmd_hook(matcher=None),
            _cmd_hook(matcher="specific_*"),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["echo"])

        result = executor.get_hooks_for_event(
            HookEvent.PRE_EXECUTION, matcher="anything"
        )
        assert len(result) == 1
        assert result[0].matcher is None

    def test_disabled_hooks_excluded(self) -> None:
        """Hooks with enabled=False are not returned."""
        hooks = [
            _cmd_hook(enabled=True),
            _cmd_hook(enabled=False),
            _cmd_hook(enabled=True),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["echo"])

        result = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION)
        assert len(result) == 2
        assert all(h.enabled for h in result)

    def test_no_matcher_returns_all_enabled_for_event(self) -> None:
        """When no matcher is passed, all enabled hooks for that event are returned."""
        hooks = [
            _cmd_hook(matcher="read_*"),
            _cmd_hook(matcher="write_*"),
            _cmd_hook(matcher=None),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["echo"])

        result = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION)
        assert len(result) == 3


# ======================================================================
# C) _validate_command_args() validation
# ======================================================================


class TestValidateCommandArgs:
    """_validate_command_args validates COMMAND hook inputs."""

    def test_hook_with_no_command_returns_error(self) -> None:
        """COMMAND hook missing 'command' field returns an error."""
        hook = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command=None,
        )
        args, err = HookExecutor._validate_command_args(hook)
        assert args is None
        assert err is not None
        assert err.passed is False
        assert "missing" in err.error.lower()

    def test_hook_with_malformed_shell_string_returns_error(self) -> None:
        """Unbalanced quotes cause shlex.split to raise ValueError."""
        hook = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command='echo "unclosed',
        )
        args, err = HookExecutor._validate_command_args(hook)
        assert args is None
        assert err is not None
        assert err.passed is False
        assert "malformed" in err.error.lower()

    def test_hook_with_empty_parsed_args_returns_error(self) -> None:
        """Whitespace-only command parses to empty list -> error."""
        hook = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command="   ",
        )
        args, err = HookExecutor._validate_command_args(hook)
        assert args is None
        assert err is not None
        assert err.passed is False
        assert "empty" in err.error.lower()

    def test_valid_command_returns_args_no_error(self) -> None:
        """Valid command string returns parsed args and no error."""
        hook = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command="echo hello world",
        )
        args, err = HookExecutor._validate_command_args(hook)
        assert err is None
        assert args == ["echo", "hello", "world"]


# ======================================================================
# D) _check_command_allowlist() security
# ======================================================================


class TestCheckCommandAllowlist:
    """_check_command_allowlist enforces command allowlist."""

    def test_empty_allowlist_rejects_all(self) -> None:
        """No allowed_commands means every command is rejected."""
        executor = HookExecutor(allowed_commands=[])
        hook = _cmd_hook(command="echo hello")
        result = executor._check_command_allowlist(hook, "echo")
        assert result is not None
        assert result.passed is False
        assert "not in allowlist" in result.error

    def test_command_in_allowlist_allowed(self) -> None:
        """Command that is in allowlist passes."""
        executor = HookExecutor(allowed_commands=["git", "npm"])
        hook = _cmd_hook(command="git status")
        result = executor._check_command_allowlist(hook, "git")
        assert result is None  # None means allowed

    def test_command_not_in_allowlist_rejected(self) -> None:
        """Command not in allowlist is rejected even when allowlist is non-empty."""
        executor = HookExecutor(allowed_commands=["git", "npm"])
        hook = _cmd_hook(command="rm -rf /")
        result = executor._check_command_allowlist(hook, "rm")
        assert result is not None
        assert result.passed is False
        assert "rm" in result.error


# ======================================================================
# E) _validate_http_url() security
# ======================================================================


class TestValidateHttpUrl:
    """_validate_http_url rejects missing, wrong-scheme, and private URLs."""

    def test_missing_url_returns_error(self) -> None:
        """HTTP hook without url field is rejected."""
        hook = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.POST_EXECUTION,
            url=None,
        )
        result = HookExecutor._validate_http_url(hook)
        assert result is not None
        assert result.passed is False
        assert "missing" in result.error.lower()

    def test_non_http_scheme_returns_error(self) -> None:
        """Non-http(s) URL scheme is rejected."""
        hook = _http_hook(url="ftp://evil.com/payload")
        result = HookExecutor._validate_http_url(hook)
        assert result is not None
        assert result.passed is False
        assert "unsupported scheme" in result.error.lower()

    def test_private_ip_loopback_returns_error(self) -> None:
        """URL pointing to 127.0.0.1 is rejected (SSRF protection)."""
        hook = _http_hook(url="http://127.0.0.1/admin")
        result = HookExecutor._validate_http_url(hook)
        assert result is not None
        assert result.passed is False
        assert "private" in result.error.lower()

    def test_private_ip_10_range_returns_error(self) -> None:
        """URL pointing to 10.x.x.x is rejected (private network)."""
        hook = _http_hook(url="http://10.0.0.1/internal")
        result = HookExecutor._validate_http_url(hook)
        assert result is not None
        assert result.passed is False
        assert "private" in result.error.lower()

    def test_localhost_hostname_returns_error(self) -> None:
        """URL pointing to localhost is rejected."""
        hook = _http_hook(url="http://localhost:8080/hook")
        result = HookExecutor._validate_http_url(hook)
        assert result is not None
        assert result.passed is False
        assert "private" in result.error.lower()

    def test_valid_public_url_passes(self) -> None:
        """Valid public https URL returns None (no error)."""
        hook = _http_hook(url="https://example.com/webhook")
        result = HookExecutor._validate_http_url(hook)
        assert result is None


# ======================================================================
# F) _is_private_url() helper
# ======================================================================


class TestIsPrivateUrl:
    """_is_private_url detects private/internal IP ranges and hostnames."""

    def test_192_168_range_is_private(self) -> None:
        assert _is_private_url("http://192.168.1.1/secret") is True

    def test_10_range_is_private(self) -> None:
        assert _is_private_url("http://10.0.0.1/secret") is True

    def test_172_16_range_is_private(self) -> None:
        assert _is_private_url("http://172.16.0.1/secret") is True

    def test_172_31_range_is_private(self) -> None:
        """172.31.x.x is the last valid 172.16-31 private range."""
        assert _is_private_url("http://172.31.255.255/secret") is True

    def test_172_32_is_not_private(self) -> None:
        """172.32.x.x is outside the private 172.16-31 range."""
        assert _is_private_url("http://172.32.0.1/page") is False

    def test_localhost_is_private(self) -> None:
        assert _is_private_url("http://localhost/secret") is True

    def test_loopback_127_is_private(self) -> None:
        assert _is_private_url("http://127.0.0.1/secret") is True

    def test_public_ip_is_not_private(self) -> None:
        assert _is_private_url("http://93.184.216.34/page") is False

    def test_invalid_hostname_returns_false(self) -> None:
        """A hostname that is not an IP and not in blocked list returns False."""
        assert _is_private_url("http://example.com/page") is False

    def test_google_metadata_is_private(self) -> None:
        """GCP metadata endpoint is blocked."""
        assert _is_private_url("http://metadata.google.internal/computeMetadata/v1/") is True
