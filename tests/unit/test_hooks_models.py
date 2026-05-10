"""Unit tests for agent_nexus.models.hooks module."""


import pytest
from pydantic import ValidationError

from agent_nexus.models.hooks import (
    AggregatedHookResult,
    HookDefinition,
    HookEvent,
    HookExecution,
    HookType,
)
from agent_nexus.platform.hooks.executor import HookExecutor

# ---------------------------------------------------------------------------
# HookDefinition
# ---------------------------------------------------------------------------


class TestHookDefinition:
    def test_construction_command(self):
        hd = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command="test -f input.docx",
        )
        assert hd.type is HookType.COMMAND
        assert hd.event is HookEvent.PRE_EXECUTION
        assert hd.command == "test -f input.docx"

    def test_with_matcher(self):
        hd = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_TOOL_USE,
            matcher="file_write*",
        )
        assert hd.matcher == "file_write*"


# ---------------------------------------------------------------------------
# HookExecution
# ---------------------------------------------------------------------------


class TestHookExecution:
    def test_with_output(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        he = HookExecution(
            hook=hook,
            passed=True,
            output="File exists",
            duration_ms=12.5,
        )
        assert he.output == "File exists"
        assert he.duration_ms == 12.5

    def test_failed_execution(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        he = HookExecution(
            hook=hook,
            passed=False,
            blocked=True,
            error="Command exited with code 1",
            duration_ms=50.0,
        )
        assert he.passed is False
        assert he.blocked is True
        assert he.error is not None


# ---------------------------------------------------------------------------
# AggregatedHookResult
# ---------------------------------------------------------------------------


class TestAggregatedHookResult:
    def test_with_results(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        exec1 = HookExecution(hook=hook, passed=True)
        exec2 = HookExecution(hook=hook, passed=False, blocked=True, error="fail")
        ahr = AggregatedHookResult(
            event=HookEvent.PRE_EXECUTION,
            results=[exec1, exec2],
            blocked=True,
            errors=["fail"],
        )
        assert len(ahr.results) == 2
        assert ahr.blocked is True
        assert len(ahr.errors) == 1

    def test_not_blocked_when_all_pass(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        exec1 = HookExecution(hook=hook, passed=True)
        ahr = AggregatedHookResult(
            event=HookEvent.POST_EXECUTION,
            results=[exec1],
        )
        assert ahr.blocked is False


# ---------------------------------------------------------------------------
# Validation constraint tests (iter22)
# ---------------------------------------------------------------------------


class TestHookDefinitionValidation:
    """Field constraint tests for HookDefinition."""

    def test_timeout_seconds_rejects_zero(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            HookDefinition(
                type=HookType.COMMAND,
                event=HookEvent.PRE_EXECUTION,
                timeout_seconds=0,
            )

    def test_timeout_seconds_accepts_positive(self):
        hd = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            timeout_seconds=0.001,
        )
        assert hd.timeout_seconds == 0.001


class TestHookExecutionValidation:
    """Field constraint tests for HookExecution.duration_ms."""

    def test_duration_ms_rejects_negative(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            HookExecution(hook=hook, passed=True, duration_ms=-0.1)


# ---------------------------------------------------------------------------
# Semantic validation tests (iter30)
# ---------------------------------------------------------------------------


class TestHookExecutionSemanticValidation:
    """HookExecution rejects contradictory passed+blocked state."""

    def test_passed_and_blocked_raises(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        with pytest.raises(ValidationError, match="passed and blocked cannot both be True"):
            HookExecution(hook=hook, passed=True, blocked=True)


class TestHookExecutionErrorType:
    """iter101 regression: error_type carries exception class name."""

    def test_error_type_on_failure(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        he = HookExecution(
            hook=hook,
            passed=False,
            blocked=True,
            error="Command exited with code 1",
            error_type="CalledProcessError",
        )
        assert he.error_type == "CalledProcessError"


# ---------------------------------------------------------------------------
# iter122 regression: HTTP hook SSRF guard
# ---------------------------------------------------------------------------


class TestHTTPHookSSRFGuard:
    """HTTP hooks with non-http/https schemes are rejected at runtime."""

    @pytest.mark.asyncio
    async def test_file_scheme_rejected(self):
        """file:// scheme is rejected to prevent SSRF."""
        hook = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.POST_EXECUTION,
            url="file:///etc/passwd",
        )
        executor = HookExecutor(allowed_commands=[])
        result = await executor._execute_http(hook, {})
        assert result.passed is False
        assert "unsupported scheme" in result.error

    @pytest.mark.asyncio
    async def test_ftp_scheme_rejected(self):
        """ftp:// scheme is rejected to prevent SSRF."""
        hook = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.POST_EXECUTION,
            url="ftp://internal-server/secrets",
        )
        executor = HookExecutor(allowed_commands=[])
        result = await executor._execute_http(hook, {})
        assert result.passed is False
        assert "unsupported scheme" in result.error

    @pytest.mark.asyncio
    async def test_https_scheme_accepted(self):
        """https:// scheme passes the SSRF guard (may fail on network)."""
        hook = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.POST_EXECUTION,
            url="https://httpbin.org/post",
            timeout_seconds=0.1,
        )
        executor = HookExecutor(allowed_commands=[])
        result = await executor._execute_http(hook, {})
        # https passes the guard; may fail on network timeout but
        # the error should NOT be "unsupported scheme"
        if not result.passed:
            assert "unsupported scheme" not in result.error
