"""Unit tests for McpToolAdapter.execute() structured output field.

Covers the JSON parsing of response.content into the `structured` return
field while preserving backward compatibility of `output` (str) and
`success` (bool).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

# ============================================================================
# Helpers
# ============================================================================


def _make_adapter(server_name: str = "test-agent", tool_name: str = "do_thing") -> McpToolAdapter:
    """Create a minimal McpToolAdapter."""
    return McpToolAdapter(
        server_name=server_name,
        tool_schema={"name": tool_name, "description": "test tool"},
    )


def _mock_handle(alive: bool = True) -> MagicMock:
    """Create a mock AgentHandle."""
    from unittest.mock import AsyncMock

    from agent_nexus.platform.orchestration.process_manager import AgentHandle

    handle = MagicMock(spec=AgentHandle)
    handle.name = "test-agent"
    handle.is_alive = alive
    handle.ipc = MagicMock()
    handle.ipc.send_chat = AsyncMock()
    handle.ipc.receive_until_result = AsyncMock()
    return handle


# ============================================================================
# Tests
# ============================================================================


class TestStructuredJsonParsing:
    """JSON content in response.content is parsed into structured dict."""

    @pytest.mark.asyncio
    async def test_valid_json_object_parsed(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='{"key": "value", "count": 42}',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] == {"key": "value", "count": 42}
        assert result["output"] == '{"key": "value", "count": 42}'
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_non_json_content_structured_none(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="plain text response",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] is None
        assert result["output"] == "plain text response"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_empty_content_structured_none(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] is None
        assert result["output"] == ""
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_default_content_structured_none(self) -> None:
        """response.content uses Pydantic default '' when not set — not valid JSON."""
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            # content defaults to "" per Pydantic model
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] is None
        assert result["output"] == ""
        assert result["success"] is True


class TestOutputBackwardCompat:
    """output field must remain a string for backward compatibility."""

    @pytest.mark.asyncio
    async def test_output_is_string_for_json_content(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='{"result": true}',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert isinstance(result["output"], str)
        assert result["output"] == '{"result": true}'

    @pytest.mark.asyncio
    async def test_output_is_string_for_plain_text(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="hello world",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert isinstance(result["output"], str)
        assert result["output"] == "hello world"

    @pytest.mark.asyncio
    async def test_output_is_string_for_empty(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert isinstance(result["output"], str)
        assert result["output"] == ""


class TestSuccessFieldUnaffected:
    """success field is not changed by structured parsing."""

    @pytest.mark.asyncio
    async def test_success_true_for_json_content(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='{"ok": true}',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_success_false_for_error_response(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.ERROR,
            error="something failed",
        )
        result = await adapter.execute(handle, {})

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success_false_for_dead_agent(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle(alive=False)
        result = await adapter.execute(handle, {})

        assert result["success"] is False


class TestComplexJsonContent:
    """Nested objects, arrays, and edge-case JSON values."""

    @pytest.mark.asyncio
    async def test_nested_json_object(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        nested = '{"outer": {"inner": [1, 2, 3], "flag": true}}'
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content=nested,
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] == {"outer": {"inner": [1, 2, 3], "flag": True}}
        assert result["output"] == nested

    @pytest.mark.asyncio
    async def test_json_array_content(self) -> None:
        """JSON arrays (not objects) are also valid structured data."""
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='[{"id": 1}, {"id": 2}]',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] == [{"id": 1}, {"id": 2}]
        assert isinstance(result["output"], str)

    @pytest.mark.asyncio
    async def test_json_boolean_content(self) -> None:
        """JSON boolean literals parse as bool, not dict."""
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="true",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] is True
        assert result["output"] == "true"

    @pytest.mark.asyncio
    async def test_json_number_content(self) -> None:
        """JSON number literals parse as int/float."""
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="42",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] == 42
        assert result["output"] == "42"

    @pytest.mark.asyncio
    async def test_json_null_content(self) -> None:
        """JSON null literal parses as None."""
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="null",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        # json.loads("null") returns None — same as "not JSON" fallback.
        # This is expected: we cannot distinguish JSON null from non-JSON.
        assert result["structured"] is None
        assert result["output"] == "null"


class TestUnicodeJsonContent:
    """Unicode characters in JSON content are handled correctly."""

    @pytest.mark.asyncio
    async def test_unicode_json(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='{"message": "你好世界", "emoji": "🎉"}',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] == {"message": "你好世界", "emoji": "🎉"}
        assert result["output"] == '{"message": "你好世界", "emoji": "🎉"}'


class TestLargeJsonContent:
    """Large JSON payloads are handled without issues."""

    @pytest.mark.asyncio
    async def test_large_json_array(self) -> None:
        import json

        large_data = [{"id": i, "value": f"item_{i}"} for i in range(1000)]
        large_json = json.dumps(large_data)

        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content=large_json,
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] == large_data
        assert len(result["structured"]) == 1000  # type: ignore[arg-type]
        assert result["success"] is True


class TestCallerStructuredAccess:
    """Verify callers can read the structured field from execute() results."""

    @pytest.mark.asyncio
    async def test_caller_reads_structured_dict(self) -> None:
        """Simulates a caller that accesses structured data."""
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='{"files": ["a.py", "b.py"], "count": 2}',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        # Caller accesses structured data
        structured = result["structured"]
        assert structured is not None
        assert structured["count"] == 2
        assert "a.py" in structured["files"]

    @pytest.mark.asyncio
    async def test_caller_handles_none_structured(self) -> None:
        """Caller checks for None and falls back to output string."""
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="not json",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        # Caller pattern: prefer structured, fall back to output
        data = result["structured"] if result["structured"] is not None else result["output"]
        assert data == "not json"

    @pytest.mark.asyncio
    async def test_error_result_has_structured_none(self) -> None:
        """Error results from dead agents include structured=None."""
        adapter = _make_adapter()
        handle = _mock_handle(alive=False)
        result = await adapter.execute(handle, {})

        assert result["success"] is False
        assert result["structured"] is None
        assert "error" in result


class TestPartialJsonNotParsed:
    """Content that looks like partial/malformed JSON is not parsed."""

    @pytest.mark.asyncio
    async def test_truncated_json(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='{"key": "value"',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] is None
        assert result["output"] == '{"key": "value"'

    @pytest.mark.asyncio
    async def test_json_with_trailing_text(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content='{"ok": true} extra text',
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] is None
        assert result["output"] == '{"ok": true} extra text'

    @pytest.mark.asyncio
    async def test_xml_like_content(self) -> None:
        adapter = _make_adapter()
        handle = _mock_handle()
        handle.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="<result>ok</result>",
            status="completed",
        )
        result = await adapter.execute(handle, {})

        assert result["structured"] is None
        assert result["output"] == "<result>ok</result>"
