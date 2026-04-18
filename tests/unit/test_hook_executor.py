"""Unit tests for agent_nexus.platform.hooks.executor module."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.hooks import (
    AggregatedHookResult,
    HookDefinition,
    HookEvent,
    HookExecution,
    HookType,
)
from agent_nexus.platform.hooks.executor import HookExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cmd_hook(
    *,
    event: HookEvent = HookEvent.PRE_EXECUTION,
    command: str = "echo hello",
    block_on_failure: bool = False,
    timeout_seconds: float = 5.0,
    matcher: str | None = None,
    enabled: bool = True,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.COMMAND,
        event=event,
        command=command,
        block_on_failure=block_on_failure,
        timeout_seconds=timeout_seconds,
        matcher=matcher,
        enabled=enabled,
    )


def _http_hook(
    *,
    event: HookEvent = HookEvent.POST_EXECUTION,
    url: str = "https://example.com/hook",
    block_on_failure: bool = False,
    timeout_seconds: float = 5.0,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.HTTP,
        event=event,
        url=url,
        block_on_failure=block_on_failure,
        timeout_seconds=timeout_seconds,
    )


def _prompt_hook(
    *,
    event: HookEvent = HookEvent.PRE_EXECUTION,
    prompt: str = "Validate input",
    block_on_failure: bool = False,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.PROMPT,
        event=event,
        prompt=prompt,
        block_on_failure=block_on_failure,
    )


def _agent_hook(
    *,
    event: HookEvent = HookEvent.POST_EXECUTION,
    prompt: str = "Deep review",
    block_on_failure: bool = False,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.AGENT,
        event=event,
        prompt=prompt,
        block_on_failure=block_on_failure,
    )


# ---------------------------------------------------------------------------
# 1. Empty hooks
# ---------------------------------------------------------------------------


class TestEmptyHooks:
    def test_empty_hooks_list(self) -> None:
        executor = HookExecutor()
        assert executor.get_hooks_for_event(HookEvent.PRE_EXECUTION) == []

    @pytest.mark.asyncio
    async def test_execute_event_no_hooks(self) -> None:
        executor = HookExecutor()
        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert isinstance(result, AggregatedHookResult)
        assert result.event == HookEvent.PRE_EXECUTION
        assert result.results == []
        assert result.blocked is False
        assert result.errors == []


# ---------------------------------------------------------------------------
# 2. from_yaml
# ---------------------------------------------------------------------------


class TestFromYaml:
    def test_parse_hooks_yaml(self, tmp_path: Path) -> None:
        yaml_content = """
pre_execution:
  - type: command
    command: "echo hello"
    block_on_failure: false
    timeout_seconds: 5
  - type: prompt
    prompt: "Check input"
    block_on_failure: true
post_execution:
  - type: http
    url: "https://example.com/notify"
"""
        yaml_file = tmp_path / "hooks.yaml"
        yaml_file.write_text(yaml_content)

        executor = HookExecutor.from_yaml(yaml_file)
        hooks = executor._hooks

        assert len(hooks) == 3

        pre_hooks = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION)
        assert len(pre_hooks) == 2
        assert pre_hooks[0].type == HookType.COMMAND
        assert pre_hooks[0].command == "echo hello"
        assert pre_hooks[1].type == HookType.PROMPT
        assert pre_hooks[1].prompt == "Check input"
        assert pre_hooks[1].block_on_failure is True

        post_hooks = executor.get_hooks_for_event(HookEvent.POST_EXECUTION)
        assert len(post_hooks) == 1
        assert post_hooks[0].url == "https://example.com/notify"

    def test_from_yaml_nonexistent_file(self, tmp_path: Path) -> None:
        executor = HookExecutor.from_yaml(tmp_path / "nonexistent.yaml")
        assert executor._hooks == []

    def test_from_yaml_unknown_event_skipped(self, tmp_path: Path) -> None:
        yaml_content = """
unknown_event:
  - type: command
    command: "echo skip"
pre_execution:
  - type: command
    command: "echo keep"
"""
        yaml_file = tmp_path / "hooks.yaml"
        yaml_file.write_text(yaml_content)

        executor = HookExecutor.from_yaml(yaml_file)
        assert len(executor._hooks) == 1
        assert executor._hooks[0].command == "echo keep"


# ---------------------------------------------------------------------------
# 3. get_hooks_for_event
# ---------------------------------------------------------------------------


class TestGetHooksForEvent:
    def test_filters_by_event(self) -> None:
        hooks = [
            _cmd_hook(event=HookEvent.PRE_EXECUTION),
            _cmd_hook(event=HookEvent.POST_EXECUTION),
            _cmd_hook(event=HookEvent.PRE_EXECUTION),
        ]
        executor = HookExecutor(hooks=hooks)

        pre = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION)
        assert len(pre) == 2
        post = executor.get_hooks_for_event(HookEvent.POST_EXECUTION)
        assert len(post) == 1

    def test_applies_fnmatch_matcher(self) -> None:
        hooks = [
            _cmd_hook(matcher="read_*"),
            _cmd_hook(matcher="write_*"),
            _cmd_hook(matcher=None),  # no matcher = matches everything
        ]
        executor = HookExecutor(hooks=hooks)

        read_hooks = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION, matcher="read_file")
        assert len(read_hooks) == 2  # "read_*" matches + no matcher matches
        assert hooks[0] in read_hooks
        assert hooks[2] in read_hooks

        write_hooks = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION, matcher="write_file")
        assert len(write_hooks) == 2  # "write_*" matches + no matcher matches

    def test_only_enabled_hooks(self) -> None:
        hooks = [
            _cmd_hook(enabled=True),
            _cmd_hook(enabled=False),
            _cmd_hook(enabled=True),
        ]
        executor = HookExecutor(hooks=hooks)

        result = executor.get_hooks_for_event(HookEvent.PRE_EXECUTION)
        assert len(result) == 2
        assert all(h.enabled for h in result)


# ---------------------------------------------------------------------------
# 4. COMMAND hook execution
# ---------------------------------------------------------------------------


class TestCommandHook:
    @pytest.mark.asyncio
    async def test_command_hook_success(self) -> None:
        hook = _cmd_hook(command="echo hello")
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is False
        assert len(result.results) == 1
        assert result.results[0].passed is True
        assert result.results[0].output == "hello"

    @pytest.mark.asyncio
    async def test_command_hook_failure_with_block(self) -> None:
        hook = _cmd_hook(command="false", block_on_failure=True)
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is True
        assert len(result.results) == 1
        assert result.results[0].passed is False
        assert result.results[0].blocked is True
        assert len(result.errors) == 1

    @pytest.mark.asyncio
    async def test_command_hook_failure_without_block(self) -> None:
        hook = _cmd_hook(command="false", block_on_failure=False)
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is False
        assert len(result.results) == 1
        assert result.results[0].passed is False
        assert result.results[0].blocked is False

    @pytest.mark.asyncio
    async def test_command_hook_timeout(self) -> None:
        hook = _cmd_hook(command="sleep 10", timeout_seconds=0.2, block_on_failure=True)
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is True
        assert result.results[0].passed is False
        assert "timed out" in result.results[0].error

    @pytest.mark.asyncio
    async def test_command_hook_missing_command(self) -> None:
        hook = HookDefinition(
            type=HookType.COMMAND,
            event=HookEvent.PRE_EXECUTION,
            block_on_failure=True,
        )
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is True
        assert result.results[0].passed is False
        assert "missing" in result.results[0].error.lower()

    @pytest.mark.asyncio
    async def test_command_hook_receives_context_via_stdin(self) -> None:
        """Verify context is passed as JSON via stdin."""
        hook = _cmd_hook(command="cat")
        executor = HookExecutor(hooks=[hook])
        ctx = {"tool": "read_file", "args": {"path": "/tmp/test.py"}}

        result = await executor.execute_event(HookEvent.PRE_EXECUTION, context=ctx)
        assert result.results[0].passed is True
        # cat echoes stdin back to stdout
        assert json.loads(result.results[0].output) == ctx


# ---------------------------------------------------------------------------
# 5. HTTP hook execution
# ---------------------------------------------------------------------------


class TestHttpHook:
    @pytest.mark.asyncio
    async def test_http_hook_success(self) -> None:
        hook = _http_hook()
        executor = HookExecutor(hooks=[hook])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor.execute_event(HookEvent.POST_EXECUTION)

        assert result.blocked is False
        assert len(result.results) == 1
        assert result.results[0].passed is True

    @pytest.mark.asyncio
    async def test_http_hook_non_2xx(self) -> None:
        hook = _http_hook(block_on_failure=True)
        executor = HookExecutor(hooks=[hook])

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor.execute_event(HookEvent.POST_EXECUTION)

        assert result.blocked is True
        assert result.results[0].passed is False
        assert "500" in result.results[0].error

    @pytest.mark.asyncio
    async def test_http_hook_connection_error(self) -> None:
        hook = _http_hook(block_on_failure=False)
        executor = HookExecutor(hooks=[hook])

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = Exception("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor.execute_event(HookEvent.POST_EXECUTION)

        assert result.blocked is False
        assert result.results[0].passed is False
        assert "Connection refused" in result.results[0].error

    @pytest.mark.asyncio
    async def test_http_hook_missing_url(self) -> None:
        hook = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.POST_EXECUTION,
            block_on_failure=True,
        )
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.POST_EXECUTION)
        assert result.blocked is True
        assert "missing" in result.results[0].error.lower()


# ---------------------------------------------------------------------------
# 6. PROMPT and AGENT stub hooks
# ---------------------------------------------------------------------------


class TestStubHooks:
    @pytest.mark.asyncio
    async def test_prompt_hook_passes(self) -> None:
        hook = _prompt_hook(prompt="Validate input format")
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is False
        assert result.results[0].passed is True
        assert result.results[0].output == "Validate input format"

    @pytest.mark.asyncio
    async def test_agent_hook_passes(self) -> None:
        hook = _agent_hook(prompt="Deep quality review")
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.POST_EXECUTION)
        assert result.blocked is False
        assert result.results[0].passed is True
        assert result.results[0].output == "Deep quality review"

    @pytest.mark.asyncio
    async def test_prompt_hook_no_prompt_text(self) -> None:
        hook = HookDefinition(
            type=HookType.PROMPT,
            event=HookEvent.PRE_EXECUTION,
        )
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.results[0].passed is True
        assert result.results[0].output is not None


# ---------------------------------------------------------------------------
# 7. Blocking behavior & error collection
# ---------------------------------------------------------------------------


class TestBlockingBehavior:
    @pytest.mark.asyncio
    async def test_stops_on_blocked_hook(self) -> None:
        """When a blocking hook fails, remaining hooks are skipped."""
        hooks = [
            _cmd_hook(command="false", block_on_failure=True),
            _cmd_hook(command="echo after"),
        ]
        executor = HookExecutor(hooks=hooks)

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is True
        # Only first hook executed; second was skipped
        assert len(result.results) == 1
        assert result.results[0].passed is False

    @pytest.mark.asyncio
    async def test_collects_errors(self) -> None:
        """Non-blocking failures are collected but don't block."""
        hooks = [
            _cmd_hook(command="false", block_on_failure=False),
            _cmd_hook(command="echo ok"),
        ]
        executor = HookExecutor(hooks=hooks)

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is False
        assert len(result.results) == 2
        assert len(result.errors) == 1
        assert result.results[1].passed is True

    @pytest.mark.asyncio
    async def test_multiple_non_blocking_failures(self) -> None:
        """Multiple non-blocking failures all get collected."""
        hooks = [
            _cmd_hook(command="false", block_on_failure=False),
            _cmd_hook(command="false", block_on_failure=False),
        ]
        executor = HookExecutor(hooks=hooks)

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is False
        assert len(result.results) == 2
        assert len(result.errors) == 2

    @pytest.mark.asyncio
    async def test_mixed_hooks_blocking_stops_early(self) -> None:
        """A blocking PROMPT stub never fails, but COMMAND blocking does."""
        hooks = [
            _prompt_hook(block_on_failure=False),
            _cmd_hook(command="false", block_on_failure=True),
            _agent_hook(),  # should not execute
        ]
        executor = HookExecutor(hooks=hooks)

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert result.blocked is True
        assert len(result.results) == 2  # prompt + failed command
        assert result.results[0].passed is True
        assert result.results[1].passed is False
