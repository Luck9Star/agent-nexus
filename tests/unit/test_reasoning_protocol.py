"""Unit tests for the Structured Reasoning Protocol.

Tests the reasoning protocol tag extraction, stripping, and system prompt
generation for LLMExecutor.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_nexus.platform.agency.executor import (
    LLMExecutor,
    _extract_reasoning_tags,
    _strip_reasoning_tags,
)
from agent_nexus.platform.agency.registry import ExpertRegistry


def _make_registry() -> ExpertRegistry:
    """Minimal registry with one expert profile."""
    registry = ExpertRegistry()
    registry.add(
        "agency.test-expert",
        {
            "id": "agency.test-expert",
            "name": "Test Expert",
            "capabilities": ["code_review"],
            "profile": {"body": "You are a test expert."},
            "output_contract": {
                "artifact_type": "report",
                "required_sections": ["summary", "findings"],
            },
        },
        ["code_review"],
    )
    return registry


def _make_executor(*, reasoning_protocol: bool = False) -> LLMExecutor:
    """Create LLMExecutor with a mock client to avoid API key resolution."""
    mock_client = MagicMock()
    return LLMExecutor(
        registry=_make_registry(),
        reasoning_protocol=reasoning_protocol,
        client=mock_client,
    )


# ---------------------------------------------------------------------------
# 1. Tag extraction
# ---------------------------------------------------------------------------


class TestExtractReasoningTags:
    """_extract_reasoning_tags correctly extracts thinking and summary content."""

    def test_extract_reasoning_tags_both(self):
        """Input with both tags returns correct content."""
        text = (
            "<thinking>I need to analyze this carefully</thinking>\n"
            "<summary>Key finding: needs refactoring</summary>\n"
            "## summary\nSome analysis"
        )
        thinking, summary = _extract_reasoning_tags(text)
        assert thinking == "I need to analyze this carefully"
        assert summary == "Key finding: needs refactoring"

    def test_extract_reasoning_tags_missing(self):
        """Input without tags returns (None, None)."""
        text = "## summary\nJust a regular response without tags"
        thinking, summary = _extract_reasoning_tags(text)
        assert thinking is None
        assert summary is None

    def test_extract_reasoning_tags_only_thinking(self):
        """Input with only thinking tag returns (content, None)."""
        text = "<thinking>Deep analysis here</thinking>\n## summary\nResult"
        thinking, summary = _extract_reasoning_tags(text)
        assert thinking == "Deep analysis here"
        assert summary is None

    def test_extract_reasoning_tags_only_summary(self):
        """Input with only summary tag returns (None, content)."""
        text = "<summary>Brief finding</summary>\n## summary\nResult"
        thinking, summary = _extract_reasoning_tags(text)
        assert thinking is None
        assert summary == "Brief finding"

    def test_extract_reasoning_tags_summary_mentioned_in_thinking(self):
        """Summary tag name inside thinking block should not be falsely captured."""
        text = (
            "<thinking>I should use <summary> tags to structure output</thinking>\n"
            "<summary>Real finding: refactoring needed</summary>\n"
            "## summary\nActual content"
        )
        thinking, summary = _extract_reasoning_tags(text)
        assert "structure output" in thinking
        assert summary == "Real finding: refactoring needed"


# ---------------------------------------------------------------------------
# 2. Tag stripping
# ---------------------------------------------------------------------------


class TestStripReasoningTags:
    """_strip_reasoning_tags removes tags without damaging ## sections."""

    def test_strip_reasoning_tags(self):
        """Stripped text preserves ## sections intact."""
        text = (
            "<thinking>Some thought</thinking>\n"
            "<summary>A finding</summary>\n"
            "## summary\nThe actual analysis\n"
            "## findings\n- Item 1"
        )
        result = _strip_reasoning_tags(text)
        assert "<thinking>" not in result
        assert "<summary>" not in result
        assert "## summary" in result
        assert "## findings" in result
        assert "The actual analysis" in result

    def test_strip_reasoning_tags_no_tags(self):
        """Text without tags is returned unchanged (except whitespace)."""
        text = "## summary\nPlain text\n## findings\n- Item 1"
        result = _strip_reasoning_tags(text)
        assert "## summary" in result
        assert "## findings" in result


# ---------------------------------------------------------------------------
# 3. System prompt generation
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    """_build_system_prompt produces correct output for protocol on/off."""

    def test_build_system_prompt_with_protocol(self):
        """Protocol ON produces three-stage instruction."""
        executor = _make_executor(reasoning_protocol=True)
        prompt = executor._build_system_prompt(
            name="Test Expert",
            body="You are a test expert.",
            capabilities=["code_review"],
            required_sections=["summary", "findings"],
            reasoning_protocol=True,
        )
        assert "Think" in prompt
        assert "Summarize" in prompt
        assert "Structure" in prompt
        assert "<thinking>" in prompt
        assert "<summary>" in prompt
        assert "Use exactly these heading names so they can be parsed" not in prompt

    def test_build_system_prompt_without_protocol(self):
        """Protocol OFF produces same output as before."""
        executor = _make_executor()
        prompt = executor._build_system_prompt(
            name="Test Expert",
            body="You are a test expert.",
            capabilities=["code_review"],
            required_sections=["summary", "findings"],
            reasoning_protocol=False,
        )
        assert "Your response must include these sections as ## markdown headings" in prompt
        assert "Use exactly these heading names so they can be parsed" in prompt
        assert "Follow this response protocol strictly" not in prompt


# ---------------------------------------------------------------------------
# 4. Section parsing after stripping
# ---------------------------------------------------------------------------


class TestParseSectionsAfterStrip:
    """Tags are stripped before _parse_sections, so headings parse correctly."""

    def test_parse_sections_after_strip(self):
        """After stripping tags, ## headings parse correctly."""
        executor = _make_executor(reasoning_protocol=True)

        raw_response = (
            "<thinking>Let me think about this</thinking>\n"
            "<summary>Need to refactor</summary>\n"
            "## summary\nThis is the real summary\n"
            "## findings\n- Finding 1\n- Finding 2"
        )

        clean = _strip_reasoning_tags(raw_response)
        sections = executor._parse_sections(clean, ["summary", "findings"])

        assert sections["summary"] == "This is the real summary"
        assert "Finding 1" in sections["findings"]
        assert "Let me think" not in str(sections)
