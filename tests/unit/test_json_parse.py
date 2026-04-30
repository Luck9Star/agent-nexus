"""Unit tests for agent_nexus.platform.agency.json_parse module.

Validates that:
- strip_markdown_fence correctly removes ```json ... ``` wrappers
- strip_markdown_fence handles no-fence, language-less, and nested cases
- robust_json_parse extracts valid JSON from various LLM output formats
- robust_json_parse returns None for empty/invalid inputs
- robust_json_parse handles nested objects, arrays, unicode, and extra text
"""
from __future__ import annotations

import json

import pytest

from agent_nexus.platform.agency.json_parse import (
    robust_json_parse,
    strip_markdown_fence,
)


# ---------------------------------------------------------------------------
# strip_markdown_fence
# ---------------------------------------------------------------------------


class TestStripMarkdownFence:
    """Tests for strip_markdown_fence function."""

    def test_removes_json_fence(self):
        text = "```json\n{\"key\": \"value\"}\n```"
        result = strip_markdown_fence(text)
        assert result == '{"key": "value"}'

    def test_removes_fence_without_language_tag(self):
        text = "```\n{\"key\": \"value\"}\n```"
        result = strip_markdown_fence(text)
        assert result == '{"key": "value"}'

    def test_no_fence_returns_stripped_text(self):
        text = '  {"key": "value"}  '
        result = strip_markdown_fence(text)
        assert result == '{"key": "value"}'

    def test_fence_with_extra_whitespace(self):
        text = "```json   \n   {\"key\": \"value\"}   \n   ```"
        result = strip_markdown_fence(text)
        assert result == '{"key": "value"}'

    def test_plain_text_no_fence(self):
        text = "just some plain text"
        result = strip_markdown_fence(text)
        assert result == "just some plain text"

    def test_fence_with_multiline_content(self):
        content = '{\n  "a": 1,\n  "b": 2\n}'
        text = f"```json\n{content}\n```"
        result = strip_markdown_fence(text)
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_nested_code_blocks_inner_fence_matches_first(self):
        """Non-greedy regex matches up to the first closing ```.

        With ```json\n```python\n...\n```\n```, the regex matches
        ```json to the first ``` (which starts ```python), yielding
        an empty capture. This is expected behavior for this regex.
        """
        inner = "```python\nprint('hi')\n```"
        text = f"```json\n{inner}\n```"
        result = strip_markdown_fence(text)
        # Non-greedy (.*?) matches up to the first ```, which is
        # the start of ```python — so the captured group is empty.
        assert result == ""

    def test_empty_fence_content(self):
        text = "```json\n\n```"
        result = strip_markdown_fence(text)
        assert result == ""

    def test_text_before_and_after_fence(self):
        """When there's text around the fence, only the fenced content is returned."""
        text = "Here is the JSON:\n```json\n{\"a\": 1}\n```\nThat's it."
        result = strip_markdown_fence(text)
        assert result == '{"a": 1}'

    def test_fence_with_no_newline(self):
        text = "```json{\"a\": 1}```"
        result = strip_markdown_fence(text)
        # The regex allows optional newline; content should still be extracted
        assert "a" in result


# ---------------------------------------------------------------------------
# robust_json_parse
# ---------------------------------------------------------------------------


class TestRobustJsonParse:
    """Tests for robust_json_parse function."""

    def test_valid_json_string(self):
        text = '{"name": "test", "value": 42}'
        result = robust_json_parse(text)
        assert result == {"name": "test", "value": 42}

    def test_json_in_markdown_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = robust_json_parse(text)
        assert result == {"key": "value"}

    def test_json_with_extra_text_before_and_after(self):
        text = 'Here is the result:\n{"status": "ok"}\nEnd of output.'
        result = robust_json_parse(text)
        assert result == {"status": "ok"}

    def test_truncated_json_returns_none(self):
        """Incomplete/truncated JSON should return None."""
        text = '{"key": "value", "nested": {"inner": '
        result = robust_json_parse(text)
        assert result is None

    def test_nested_json_objects(self):
        text = '{"outer": {"inner": {"deep": true}}}'
        result = robust_json_parse(text)
        assert result == {"outer": {"inner": {"deep": True}}}

    def test_json_array_returns_none(self):
        """robust_json_parse only returns dict objects, not arrays."""
        text = '[1, 2, 3]'
        result = robust_json_parse(text)
        assert result is None

    def test_empty_string_returns_none(self):
        result = robust_json_parse("")
        assert result is None

    def test_whitespace_only_returns_none(self):
        result = robust_json_parse("   \n\t  ")
        assert result is None

    def test_unicode_characters(self):
        text = '{"name": "测试", "emoji": "🎉"}'
        result = robust_json_parse(text)
        assert result == {"name": "测试", "emoji": "🎉"}

    def test_multiple_json_objects_extracts_first(self):
        """When multiple JSON objects appear, the first one should be returned."""
        text = '{"first": true} some text {"second": true}'
        result = robust_json_parse(text)
        assert result == {"first": True}

    def test_none_equivalent_empty(self):
        """Empty string is falsy and should return None."""
        result = robust_json_parse("")
        assert result is None

    def test_plain_text_no_json_returns_none(self):
        result = robust_json_parse("This is just plain text with no JSON at all.")
        assert result is None

    def test_json_with_boolean_and_null(self):
        text = '{"active": true, "deleted": false, "parent": null}'
        result = robust_json_parse(text)
        assert result == {"active": True, "deleted": False, "parent": None}

    def test_json_with_numbers(self):
        text = '{"int": 42, "float": 3.14, "neg": -7}'
        result = robust_json_parse(text)
        assert result == {"int": 42, "float": 3.14, "neg": -7}

    def test_fence_with_extra_text_outside(self):
        """Fenced JSON with surrounding text should extract the JSON."""
        text = 'Result:\n```json\n{"status": "ok"}\n```\nDone.'
        result = robust_json_parse(text)
        assert result == {"status": "ok"}

    def test_json_string_value_containing_brace(self):
        """A brace inside a JSON string value should not confuse raw_decode."""
        text = '{"msg": "use { for objects"}'
        result = robust_json_parse(text)
        assert result == {"msg": "use { for objects"}
