"""Unit tests for agent_nexus.models.hooks module."""

import json
from datetime import datetime

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
# HookType enum
# ---------------------------------------------------------------------------


class TestHookType:
    def test_members(self):
        assert set(HookType) == {
            HookType.COMMAND,
            HookType.HTTP,
            HookType.PROMPT,
            HookType.AGENT,
        }

    def test_values(self):
        assert HookType.COMMAND == "command"
        assert HookType.HTTP == "http"
        assert HookType.PROMPT == "prompt"
        assert HookType.AGENT == "agent"

    def test_from_string(self):
        assert HookType("command") is HookType.COMMAND
        assert HookType("http") is HookType.HTTP

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            HookType("unknown")


# ---------------------------------------------------------------------------
# HookEvent enum
# ---------------------------------------------------------------------------


class TestHookEvent:
    def test_members(self):
        assert set(HookEvent) == {
            HookEvent.PRE_EXECUTION,
            HookEvent.POST_EXECUTION,
            HookEvent.PRE_TOOL_USE,
            HookEvent.POST_TOOL_USE,
            HookEvent.ON_ERROR,
            HookEvent.ON_EVOLUTION,
        }

    def test_values(self):
        assert HookEvent.PRE_EXECUTION == "pre_execution"
        assert HookEvent.POST_EXECUTION == "post_execution"
        assert HookEvent.PRE_TOOL_USE == "pre_tool_use"
        assert HookEvent.POST_TOOL_USE == "post_tool_use"
        assert HookEvent.ON_ERROR == "on_error"
        assert HookEvent.ON_EVOLUTION == "on_evolution"

    def test_all_six_events(self):
        assert len(HookEvent) == 6

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            HookEvent("unknown")


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

    def test_construction_http(self):
        hd = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.POST_EXECUTION,
            url="https://hooks.example.com/notify",
        )
        assert hd.url == "https://hooks.example.com/notify"

    def test_construction_prompt(self):
        hd = HookDefinition(
            type=HookType.PROMPT,
            event=HookEvent.PRE_EXECUTION,
            prompt="Validate input format",
            model="haiku",
        )
        assert hd.prompt == "Validate input format"
        assert hd.model == "haiku"

    def test_construction_agent(self):
        hd = HookDefinition(
            type=HookType.AGENT,
            event=HookEvent.ON_EVOLUTION,
            prompt="Assess quality of evolved skill",
            model="sonnet",
        )
        assert hd.type is HookType.AGENT

    def test_defaults(self):
        hd = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        assert hd.config == {}
        assert hd.enabled is True
        assert hd.block_on_failure is False
        assert hd.timeout_seconds == 10.0
        assert hd.matcher is None
        assert hd.command is None
        assert hd.url is None
        assert hd.prompt is None
        assert hd.model is None

    def test_with_matcher(self):
        hd = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_TOOL_USE,
            matcher="file_write*",
        )
        assert hd.matcher == "file_write*"

    def test_disabled_hook(self):
        hd = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            enabled=False,
        )
        assert hd.enabled is False

    def test_block_on_failure(self):
        hd = HookDefinition(
            type=HookType.PROMPT,
            event=HookEvent.PRE_EXECUTION,
            block_on_failure=True,
            timeout_seconds=30.0,
        )
        assert hd.block_on_failure is True
        assert hd.timeout_seconds == 30.0

    def test_serialization_round_trip(self):
        hd = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.POST_EXECUTION,
            url="https://example.com/hook",
            config={"headers": {"Authorization": "Bearer token"}},
        )
        data = hd.model_dump()
        hd2 = HookDefinition(**data)
        assert hd2 == hd

    def test_json_serialization(self):
        hd = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command="echo hello",
        )
        json_str = hd.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["type"] == "command"
        assert parsed["event"] == "pre_execution"
        hd2 = HookDefinition.model_validate_json(json_str)
        assert hd2 == hd


# ---------------------------------------------------------------------------
# HookExecution
# ---------------------------------------------------------------------------


class TestHookExecution:
    def test_construction(self):
        hook = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            command="echo hello",
        )
        he = HookExecution(hook=hook, passed=True)
        assert he.passed is True
        assert he.blocked is False
        assert he.output is None
        assert he.error is None
        assert he.duration_ms == 0.0
        assert isinstance(he.executed_at, datetime)

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

    def test_serialization_round_trip(self):
        hook = HookDefinition(type=HookType.PROMPT, event=HookEvent.ON_ERROR)
        he = HookExecution(
            hook=hook,
            passed=True,
            output="ok",
            duration_ms=100.0,
        )
        data = he.model_dump()
        he2 = HookExecution(**data)
        assert he2 == he


# ---------------------------------------------------------------------------
# AggregatedHookResult
# ---------------------------------------------------------------------------


class TestAggregatedHookResult:
    def test_default_construction(self):
        ahr = AggregatedHookResult(event=HookEvent.PRE_EXECUTION)
        assert ahr.event is HookEvent.PRE_EXECUTION
        assert ahr.results == []
        assert ahr.blocked is False
        assert ahr.errors == []

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

    def test_serialization_round_trip(self):
        hook = HookDefinition(type=HookType.HTTP, event=HookEvent.ON_ERROR)
        exec1 = HookExecution(hook=hook, passed=True)
        ahr = AggregatedHookResult(
            event=HookEvent.ON_ERROR,
            results=[exec1],
        )
        data = ahr.model_dump()
        ahr2 = AggregatedHookResult(**data)
        assert ahr2 == ahr

    def test_json_serialization(self):
        ahr = AggregatedHookResult(event=HookEvent.ON_EVOLUTION)
        json_str = ahr.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["event"] == "on_evolution"
        ahr2 = AggregatedHookResult.model_validate_json(json_str)
        assert ahr2 == ahr


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

    def test_timeout_seconds_rejects_negative(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            HookDefinition(
                type=HookType.COMMAND,
                event=HookEvent.PRE_EXECUTION,
                timeout_seconds=-1.5,
            )

    def test_timeout_seconds_accepts_positive(self):
        hd = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            timeout_seconds=0.001,
        )
        assert hd.timeout_seconds == 0.001

    def test_timeout_seconds_default_valid(self):
        hd = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        assert hd.timeout_seconds == 10.0


class TestHookExecutionValidation:
    """Field constraint tests for HookExecution.duration_ms."""

    def test_duration_ms_rejects_negative(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            HookExecution(hook=hook, passed=True, duration_ms=-0.1)

    def test_duration_ms_accepts_zero(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        he = HookExecution(hook=hook, passed=True, duration_ms=0.0)
        assert he.duration_ms == 0.0

    def test_duration_ms_accepts_positive(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        he = HookExecution(hook=hook, passed=True, duration_ms=150.7)
        assert he.duration_ms == 150.7


# ---------------------------------------------------------------------------
# Semantic validation tests (iter30)
# ---------------------------------------------------------------------------


class TestHookExecutionSemanticValidation:
    """HookExecution rejects contradictory passed+blocked state."""

    def test_passed_and_blocked_raises(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        with pytest.raises(ValidationError, match="passed and blocked cannot both be True"):
            HookExecution(hook=hook, passed=True, blocked=True)

    def test_passed_not_blocked_ok(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        exe = HookExecution(hook=hook, passed=True, blocked=False)
        assert exe.passed and not exe.blocked

    def test_not_passed_blocked_ok(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        exe = HookExecution(hook=hook, passed=False, blocked=True)
        assert exe.blocked and not exe.passed


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

    def test_error_type_defaults_none(self):
        hook = HookDefinition(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        he = HookExecution(hook=hook, passed=True)
        assert he.error_type is None


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
